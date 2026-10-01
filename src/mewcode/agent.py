"""一次用户任务的自主循环；界面仅消费事件，不参与执行控制。"""

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

from .async_utils import protected
from .collector import StreamCollector
from .tools.executor import ToolExecutor
from .tools.scheduler import ToolScheduler
from .types import AgentEvent, AgentMode, ContextLimitError, Message, Provider, ProviderError, StopReason, TokenUsage

EXECUTE_PROMPT = "你是 MewCode 终端编程助手。按需使用工具完成用户任务，读取工具结果后调整行动，完成检查后给出答复。"
PLAN_PROMPT = ("当前是只读规划模式，仅允许读取、找文件和搜索代码。不能写入、修改文件或执行 shell。"
               "先按需探索；每次最终答复都给出完整的当前计划，包括目标、实施步骤和验证方式。"
               "修订时结合已有任务上下文，输出完整的新计划。")


def _drop_oldest_turns(history: list[Message], count: int, *, keep_last_turn: bool) -> int:
    starts = [index for index, message in enumerate(history) if message.role == "user"]
    count = min(count, max(len(starts) - int(keep_last_turn), 0))
    if count:
        end = starts[count] if count < len(starts) else len(history)
        del history[:end]
    return count


async def _next_event(stream, cancel: asyncio.Event):
    """同时观察网络等待和取消；关闭正在等待的迭代后才返回。"""
    pending = asyncio.create_task(anext(stream))
    waiter = asyncio.create_task(cancel.wait())
    try:
        await asyncio.wait((pending, waiter), return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        cancel.set()
    finally:
        waiter.cancel()
        if cancel.is_set():
            pending.cancel()
        await protected(asyncio.gather(waiter, return_exceptions=True), cancel_event=cancel)
        if cancel.is_set():
            pending.cancel()
            await protected(asyncio.gather(pending, return_exceptions=True), cancel_event=cancel)
    if cancel.is_set():
        raise asyncio.CancelledError
    return pending.result()


def _total_usage(records: list[TokenUsage]) -> TokenUsage:
    def total(field):
        known = [getattr(record, field) for record in records if getattr(record, field) is not None]
        return sum(known) if known else None
    return TokenUsage(total("input_tokens"), total("output_tokens"),
                      bool(records) and all(record.complete for record in records),
                      total("cache_read_tokens"), total("cache_write_tokens"))


class Agent:
    def __init__(self, provider: Provider, executor: ToolExecutor, *, max_iterations: int = 20) -> None:
        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations <= 0:
            raise ValueError("max_iterations 必须是正整数")
        self.provider, self.executor, self.max_iterations = provider, executor, max_iterations

    async def run(self, question: str, *, history: list[Message], mode: AgentMode,
                  cancel_event: asyncio.Event | None = None) -> AsyncIterator[AgentEvent]:
        cancel = cancel_event if cancel_event is not None else asyncio.Event()
        run_id, iteration, unknown_count = uuid4().hex, 0, 0
        committed = False
        drops = 1
        user = Message("user", question)
        usage_records: list[TokenUsage] = []
        reason: StopReason = "max_iterations"
        detail = "达到模型请求上限，任务停止"
        allowed = self.executor.registry.names(read_only=True) if mode == "plan" else self.executor.registry.names()

        def event(kind, **fields):
            return AgentEvent(kind, run_id=run_id, iteration=iteration, mode=mode,
                              max_iterations=self.max_iterations, **fields)

        def commit(message, results=()):
            nonlocal committed
            if not committed:
                history.append(user)
                committed = True
            history.append(message)
            history.extend(Message("tool", tool_call_id=call.id, tool_result=result)
                           for call, result in zip(message.tool_calls, results))

        try:
            while iteration < self.max_iterations and not cancel.is_set():
                yield AgentEvent("progress", run_id=run_id, iteration=iteration + 1, mode=mode,
                                 phase="model", max_iterations=self.max_iterations)
                if cancel.is_set():
                    break
                iteration += 1
                collector = StreamCollector(self.provider.stream(
                    [*history, *(() if committed else (user,))],
                    tools=self.executor.registry.definitions(allowed_tools=allowed),
                    tool_choice="auto", system_prompt=PLAN_PROMPT if mode == "plan" else EXECUTE_PROMPT))
                source = collector.events(run_id=run_id, iteration=iteration, mode=mode)
                failure = None
                displayed = False
                try:
                    while True:
                        try:
                            fragment = await _next_event(source, cancel)
                        except StopAsyncIteration:
                            break
                        displayed |= bool(fragment.text)
                        yield fragment
                except asyncio.CancelledError:
                    cancel.set()
                except ProviderError as error:
                    failure = error
                except Exception:
                    failure = ProviderError("模型响应处理失败，请检查服务和运行环境")
                finally:
                    await protected(source.aclose(), cancel_event=cancel)
                usage_records.append(collector.usage)
                yield event("usage", usage=collector.usage)
                if cancel.is_set():
                    break
                if failure is not None:
                    if isinstance(failure, ContextLimitError) and not displayed:
                        older_turns = sum(message.role == "user" for message in history) - int(committed)
                        if older_turns > 0:
                            if iteration >= self.max_iterations:
                                break
                            dropped = _drop_oldest_turns(history, drops, keep_last_turn=committed)
                            yield event("history_trimmed", text=f"上下文超限，已丢弃 {dropped} 轮较早对话并重试")
                            drops *= 2
                            continue
                    reason, detail = "stream_error", str(failure)
                    break
                drops = 1
                response = collector.response.message
                if not response.tool_calls:
                    commit(response)
                    reason, detail = "model_done", "模型已结束本次任务"
                    break
                names = self.executor.registry.names()
                unknown_count = unknown_count + 1 if all(call.name not in names for call in response.tool_calls) else 0
                scheduler = ToolScheduler(self.executor)
                yield event("progress", phase="tools")
                batch = scheduler.run(response.tool_calls, allowed_tools=allowed, run_id=run_id,
                                      iteration=iteration, mode=mode, cancel_event=cancel)
                try:
                    async for item in batch:
                        yield item
                finally:
                    await protected(batch.aclose(), cancel_event=cancel)
                    # 无 await 的成对提交，重复取消不能留下孤立调用。
                    commit(response, scheduler.results)
                if any(result.error and result.error["code"] == "cancelled" for result in scheduler.results):
                    cancel.set()
                if cancel.is_set():
                    break
                if unknown_count >= 3:
                    reason, detail = "unknown_tool_limit", "连续 3 轮请求的工具全部未注册，任务停止"
                    break
        except asyncio.CancelledError:
            cancel.set()
        if cancel.is_set():
            reason, detail = "cancelled", "用户取消任务；已完成的操作和结果保留，请按需检查实际状态"
        yield event("finished", reason=reason, text=detail, usage=_total_usage(usage_records))
