"""会话级预算与一次摘要事务；代理决定何时重试以及消耗任务额度。"""
import asyncio
from dataclasses import dataclass, field
import re

from ..async_utils import next_event, protected
from ..collector import StreamCollector
from ..types import ContextLimitError, Message, ProviderError, TokenUsage
from .estimate import Estimator, dump, estimate_text
from .partition import partition, validate_pairs
from .spill import ResultCache, spill_history
from .summary import BOUNDARY, SUMMARY_PROMPT, InvalidSummary, parse_summary, transcript


@dataclass
class CompactionResult:
    success: bool = False
    called: bool = False
    cancelled: bool = False
    overflow: bool = False
    blocked: bool = False
    text: str = ''
    before: int = 0
    after: int | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)


class ContextManager:
    def __init__(self, root, *, context_window, max_output_tokens, protocol):
        if type(context_window) is not int or type(max_output_tokens) is not int or max_output_tokens <= 0 or context_window <= max_output_tokens + 13000:
            raise ValueError('context_window 必须大于 max_output_tokens + 13000')
        self.window, self.output = context_window, max_output_tokens
        self.estimator = Estimator(protocol)
        self.cache = ResultCache(root)
        self.failures = 0
        self.version = 0
        self.quotes = {}
        self.summary_files: set[str] = set()
        self.last_estimate: int | None = None
        self.manual_usage: TokenUsage | None = None
        self.checkpoint = None

    def state(self, **changes):
        value = {'version': self.version, 'failures': self.failures, 'circuit_open': self.circuit_open,
                 'quotes': self.quotes, 'summary_files': sorted(self.summary_files),
                 'cache_paths': sorted(self.cache.paths)}
        value.update(changes)
        return value

    def restore_state(self, state):
        self.version = state.get('version', 0)
        self.failures = state.get('failures', 0)
        self.quotes = state.get('quotes', {})
        self.summary_files = set(state.get('summary_files', []))
        self.estimator.invalidate()

    @property
    def circuit_open(self):
        return self.failures >= 3

    def fits(self, estimate, *, manual=False):
        return estimate + self.output + (3000 if manual else 13000) < self.window

    def spill(self, history):
        candidate = list(history)
        result = spill_history(candidate, self.cache)
        if result[0]:
            if self.checkpoint:
                self.checkpoint(candidate, self.state())
            history[:] = candidate
            self.estimator.invalidate()
        return result

    def estimate(self, messages, system, tools):
        self.last_estimate = self.estimator.estimate(messages, system, tools)
        return self.last_estimate

    def status_text(self):
        value = self.last_estimate if self.last_estimate is not None else '未知'
        result = (f'上下文> 输入估算 {value} · 窗口 {self.window} · 输出预留 {self.output} · 自动余量 13000 · 手动摘要余量 3000\n'
                  f'上下文> 落盘结果 {self.cache.count} · 摘要版本 {self.version} · 连续失败 {self.failures}/3 · '
                  f'自动摘要 {"已熔断（/compact 可单次尝试）" if self.circuit_open else "可用"}')
        if self.manual_usage is not None:
            from ..terminal.text import usage_text
            result += '\n手动摘要实际用量> ' + usage_text(self.manual_usage)
        return result

    async def compact(self, provider, history, user, tools, system, reminder, cancel, *, manual=False,
                      on_start=None, hooks=None, hook_fields=None, purpose=None):
        """摘要维护与普通工作请求分开，Hook 只观察本次压缩尝试。"""
        purpose = purpose or ('manual' if manual else 'auto')
        result = None
        def fields():
            return hook_fields() if callable(hook_fields) else (hook_fields or {})
        try:
            if hooks is not None:
                await hooks.emit('context.before_compact', **fields(), context={'purpose': purpose, 'estimated_tokens': self.last_estimate, 'threshold': self.window}, cancel_event=cancel)
            result = await self._compact(provider, history, user, tools, system, reminder, cancel,
                                         manual=manual, on_start=on_start)
            return result
        finally:
            if hooks is not None:
                status = ('cancelled' if cancel.is_set() or (result and result.cancelled) else
                          'success' if result and result.success else 'failed' if result is None or result.called else 'noop')
                await protected(hooks.emit('context.after_compact', **fields(), context={'purpose': purpose, 'result': status, 'reason': status, 'estimated_tokens': self.last_estimate, 'threshold': self.window}, cancel_event=cancel), cancel_event=cancel)

    async def _compact(self, provider, history, user, tools, system, reminder, cancel, *, manual=False,
                       on_start=None):
        pending = [user] if user and all(m.id != user.id for m in history) else []
        baseline = tuple(history)
        result = CompactionResult(before=self.estimate([*history, *pending, reminder], system, tools))
        if cancel.is_set():
            result.cancelled = True
            result.text = '摘要已取消，历史保留'
            return result
        if self.circuit_open and not manual:
            result.text = '自动摘要已熔断，请使用 /compact 单次尝试或新建会话'
            return result
        try:
            parts = partition(history, user.id if user else None, self.estimator.protocol)
            if not parts.older:
                result.text = '没有可摘要的较早内容；当前任务和近期原文全部保留'
                return result
            boundary = Message('context', BOUNDARY, context_kind='boundary')
            protected_input = [*parts.recent, boundary, *pending, reminder]
            if not self.fits(self.estimator.snapshot(protected_input, system, tools).tokens):
                result.text = '不可压缩的当前任务、近期原文或固定提示过大；请缩短输入或核对窗口配置'
                return result
            files = self.summary_files | {m.cache_path for m in parts.older if m.cache_path}
            try:
                for path in files | {m.cache_path for m in parts.recent if m.cache_path}:
                    self.cache.restore(path)
                required_files = {self.cache.write_index(files)} if len(files) > 15 else files
            except (OSError, ValueError, KeyError, StopIteration):
                result.blocked = True
                result.text = '缓存文件不可用或索引写入失败，无法安全摘要；请检查文件和磁盘'
                return result
            if estimate_text(dump(self.quotes)) + estimate_text(dump(sorted(required_files))) + 100 > 2000:
                result.blocked = True
                result.text = '必须保留的关键原话与文件索引超过摘要上限，无法安全压缩'
                return result
            sources = dict(self.quotes)
            sources.update({m.id: [m.content] for m in history if m.role == 'user'})
            if user:
                sources[user.id] = [user.content]
            messages = transcript(parts.older, user, self.quotes, required_files)
            if not self.fits(self.estimator.snapshot(messages, SUMMARY_PROMPT, ()).tokens, manual=manual):
                result.text = '摘要输入加输出预留和安全余量超出窗口；请核对配置或新建会话'
                return result
        except ValueError:
            result.text = '历史工具交互不完整，无法安全摘要'
            return result
        if on_start:
            on_start()
        result.called = True
        collector = StreamCollector(provider.stream(messages, tools=(), tool_choice='none', system_prompt=SUMMARY_PROMPT))
        stream = collector.events(run_id='', iteration=0, mode='execute')
        stage = '模型响应'
        try:
            while True:
                try:
                    # 草稿、正文及思考仅在本次临时收集器内，不发布为工作事件。
                    await next_event(stream, cancel)
                except StopAsyncIteration:
                    break
            if cancel.is_set():
                raise asyncio.CancelledError
            response = collector.response.message
            if response.tool_calls:
                raise ValueError('摘要返回了工具调用，未执行')
            stage = '格式与原话来源'
            summary, quotes = parse_summary(response.content, sources, self.quotes)
            stage = '缓存文件索引'
            mentioned = set(re.findall(r'\.mewcode/context/[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+\.jsonl', summary))
            if not required_files <= mentioned or not mentioned <= files | required_files:
                raise ValueError('摘要缓存文件索引不完整或来源不明')
            try:
                for path in files | required_files | {m.cache_path for m in parts.recent if m.cache_path}:
                    self.cache.restore(path)
            except (OSError, ValueError, KeyError, StopIteration):
                raise OSError('缓存不可用') from None
            stage = '候选历史与预算'
            candidate = [Message('context', summary, context_kind='summary'), *parts.recent, boundary]
            validate_pairs(candidate)
            after = self.estimator.snapshot([*candidate, *pending, reminder], system, tools).tokens
            before_chars = self.estimator.snapshot([*history, *pending, reminder], system, tools).tokens
            if after >= before_chars or not self.fits(after):
                raise ValueError('摘要没有缩减收益或下一工作请求仍超预算')
            if tuple(history) != baseline:
                raise ValueError('摘要期间历史已变化，候选结果丢弃')
            # 校验后无 await 地共同提交状态，取消不会留下半份摘要。
            if self.checkpoint:
                self.checkpoint(candidate, self.state(version=self.version + 1, failures=0,
                    circuit_open=False, quotes=quotes, summary_files=sorted(files)))
            history[:] = candidate
            self.quotes, self.summary_files = quotes, files
            self.failures = 0
            self.version += 1
            self.estimator.invalidate()
            self.last_estimate = after
            result.success, result.after, result.text = True, after, '上下文摘要已提交；后续文件细节需重新读取'
        except asyncio.CancelledError:
            cancel.set()
            result.cancelled, result.text = True, '摘要已取消，历史和失败计数保留'
        except OSError:
            result.blocked = True
            result.text = '摘要缓存或存档提交失败，历史保留；请检查文件和磁盘'
        except Exception as error:
            self.failures += 1
            result.overflow = isinstance(error, ContextLimitError)
            # 不把模型输出或不可信异常正文显示到终端。
            result.text = ('摘要服务拒绝了超长输入，请核对窗口配置' if result.overflow else
                           f'摘要失败：{str(error) if isinstance(error, (ProviderError, InvalidSummary)) else stage + "校验未通过"}，历史保留')
        finally:
            await protected(stream.aclose(), cancel_event=cancel)
            result.usage = collector.usage
            if manual:
                self.manual_usage = collector.usage
        return result
