"""一次用户任务的自主循环；界面仅消费事件，不参与执行控制。"""

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4
from dataclasses import replace

from .async_utils import protected, next_event as _next_event
from .collector import StreamCollector
from .context.manager import ContextManager
from .prompts import PromptState, build_system_prompt
from .tools.executor import ToolExecutor
from .tools.scheduler import ToolScheduler
from .types import AgentEvent, AgentMode, ContextLimitError, Message, Provider, ProviderError, StopReason, TokenUsage



def _total_usage(records: list[TokenUsage]) -> TokenUsage:
    def total(field):
        known = [getattr(record, field) for record in records if getattr(record, field) is not None]
        return sum(known) if known else None
    fields = ("input_tokens", "output_tokens", "total_input_tokens", "cache_read_tokens",
              "cache_miss_tokens", "cache_write_tokens")
    return TokenUsage(**{field: total(field) for field in fields},
                      complete=bool(records) and all(record.complete for record in records),
                      cache_complete=bool(records) and all(record.cache_complete for record in records),
                      incomplete_fields=frozenset(field for field in fields if
                          any(getattr(record, field) is None or field in record.incomplete_fields for record in records)))


class Agent:
    def __init__(self, provider: Provider, executor: ToolExecutor, *, max_iterations: int = 20,
                 prompt_state: PromptState | None = None, context_manager: ContextManager | None = None,
                 config=None) -> None:
        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations <= 0:
            raise ValueError("max_iterations 必须是正整数")
        self.provider, self.executor, self.max_iterations = provider, executor, max_iterations
        self.prompt_state = prompt_state or PromptState(executor.context.root)
        config = config or getattr(provider, 'config', None)
        if context_manager is None and config is None:
            raise ValueError('必须提供含 context_window 的配置或上下文管理器')
        self.context = context_manager or ContextManager(executor.context.root,
            context_window=config.context_window, max_output_tokens=config.max_output_tokens, protocol=config.protocol)

    async def run(self, question: str, *, history: list[Message], mode: AgentMode,
                  cancel_event: asyncio.Event | None = None) -> AsyncIterator[AgentEvent]:
        cancel = cancel_event if cancel_event is not None else asyncio.Event()
        run_id, iteration, unknown_count = uuid4().hex, 0, 0
        if self.prompt_state.mode != mode:
            self.prompt_state.enter_mode(mode)
        context = None
        committed = False
        force_compaction = recovered = False
        user = Message("user", question)
        usage_records: list[TokenUsage] = []
        reason: StopReason = "max_iterations"
        detail = "达到模型请求上限，任务停止"
        allowed = self.executor.registry.names(read_only=True) if mode == "plan" else self.executor.registry.names()

        def event(kind, **fields):
            return AgentEvent(kind, run_id=run_id, iteration=iteration, mode=mode,
                              permission_mode=getattr(getattr(self.executor, "permissions", None), "mode", "default"),
                              max_iterations=self.max_iterations, **fields)

        tools = self.executor.registry.definitions(allowed_tools=allowed)
        system = build_system_prompt()

        def commit(message, results=()):
            nonlocal committed
            if not committed:
                history.append(user)
                committed = True
            history.append(context)
            self.prompt_state.commit(context)
            history.append(message)
            history.extend(Message("tool", tool_call_id=call.id, tool_result=result)
                           for call, result in zip(message.tool_calls, results))

        try:
            while iteration < self.max_iterations and not cancel.is_set():
                spilled, warnings = self.context.spill(history)
                for warning in warnings:
                    yield event('context_compaction', phase='spill_failed', text=warning)
                if spilled:
                    yield event('context_compaction', phase='spill', spilled=spilled,
                                text=f'已将 {spilled} 项工具结果落盘，原文可按路径读取')
                estimate = self.context.estimate([*history, *(() if committed else (user,)),
                                                  self.prompt_state.peek_request()], system, tools)
                if force_compaction or not self.context.fits(estimate):
                    if self.context.circuit_open:
                        reason, detail = 'context_blocked', '自动摘要已熔断，请使用 /compact 单次尝试或新建会话'
                        break
                    yield event('context_compaction', phase='summary', purpose='summary',
                                estimated_before=estimate, failures=self.context.failures, spilled=self.context.cache.count,
                                text='正在准备结构化摘要')
                    def started():
                        nonlocal iteration
                        iteration += 1
                    result = await self.context.compact(self.provider, history, user, tools, system,
                        self.prompt_state.peek_request(force_full=True), cancel, on_start=started)
                    if result.called:
                        usage_records.append(result.usage)
                        yield event('usage', usage=result.usage, purpose='summary')
                    yield event('context_compaction', phase='success' if result.success else 'cancelled' if result.cancelled else 'failed',
                                purpose='summary', text=result.text, estimated_before=result.before,
                                estimated_after=result.after, failures=self.context.failures,
                                circuit_open=self.context.circuit_open, spilled=self.context.cache.count)
                    if result.cancelled:
                        break
                    if result.success:
                        self.prompt_state.history_trimmed()
                        recovered |= force_compaction
                        force_compaction = False
                    elif iteration >= self.max_iterations:
                        break
                    elif not result.called or result.overflow or result.blocked or self.context.circuit_open:
                        reason, detail = 'context_blocked', result.text + '；可使用 /compact 或核对窗口配置'
                        break
                    else:
                        force_compaction = True
                    continue
                yield AgentEvent("progress", run_id=run_id, iteration=iteration + 1, mode=mode,
                                 permission_mode=getattr(getattr(self.executor, "permissions", None), "mode", "default"),
                                 phase="model", max_iterations=self.max_iterations)
                if cancel.is_set():
                    break
                iteration += 1
                request_snapshot = None
                async def request():
                    nonlocal context, request_snapshot
                    if cancel.is_set():
                        return
                    context = self.prompt_state.begin_request()
                    messages = [*history, *(() if committed else (user,)), context]
                    request_snapshot = self.context.estimator.snapshot(messages, system, tools)
                    stream = self.provider.stream(messages, tools=tools, tool_choice="auto", system_prompt=system)
                    try:
                        async for item in stream:
                            yield item
                    finally:
                        close = getattr(stream, "aclose", None)
                        if close is not None:
                            await close()

                collector = StreamCollector(request())
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
                        yield replace(fragment, permission_mode=getattr(
                            getattr(self.executor, "permissions", None), "mode", "default"))
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
                        if iteration >= self.max_iterations:
                            break
                        if recovered:
                            reason, detail = 'context_blocked', '摘要恢复后的工作请求仍超限，请核对 context_window 与 max_output_tokens'
                            break
                        force_compaction = True
                        continue
                    reason, detail = "stream_error", str(failure)
                    break
                self.context.estimator.observe(request_snapshot, collector.usage)
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
