"""一次用户任务的自主循环；界面仅消费事件，不参与执行控制。"""

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4
from dataclasses import replace
from dataclasses import asdict

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
                 config=None, journal=None, before_request=None, allowed_tools=None, hooks=None) -> None:
        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations <= 0:
            raise ValueError("max_iterations 必须是正整数")
        self.provider, self.executor, self.max_iterations = provider, executor, max_iterations
        self.prompt_state = prompt_state or PromptState(executor.context.root)
        config = config or getattr(provider, 'config', None)
        if context_manager is None and config is None:
            raise ValueError('必须提供含 context_window 的配置或上下文管理器')
        self.context = context_manager or ContextManager(executor.context.root,
            context_window=config.context_window, max_output_tokens=config.max_output_tokens, protocol=config.protocol)
        self.journal, self.before_request = journal, before_request
        self.last_task = None
        self.storage_blocked = False
        self.allowed_tools = allowed_tools
        self.prompt_state.allowed_tools = self.effective_tools(self.prompt_state.mode)
        if hooks is None:
            from .hooks.runtime import create_runtime
            from .permissions.runtime import PermissionManager
            permissions = getattr(executor, 'permissions', None) or PermissionManager(executor.context.root)
            hooks = create_runtime(executor.context.root, permissions, lambda: self.prompt_state.mode)
        self.hooks = hooks

    def effective_tools(self, mode):
        if self.allowed_tools:
            return self.allowed_tools()
        names = self.executor.registry.names()
        if mode == 'plan':
            names &= {'read_file', 'glob_files', 'search_code'}
        return names | ({"load_skill"} if "load_skill" in self.executor.registry.names() else set())

    async def run(self, question: str, *, history: list[Message], mode: AgentMode,
                  cancel_event: asyncio.Event | None = None, budget=None, run_id=None,
                  turn_owned=False, parent_run_id='') -> AsyncIterator[AgentEvent]:
        """直接 Agent 调用也具备边界；会话和 Skill 可声明已有顶层所有者。"""
        from .skills.budget import TaskBudget
        cancel = cancel_event if cancel_event is not None else asyncio.Event()
        run_id = run_id or uuid4().hex
        budget = budget or TaskBudget(self.max_iterations, run_id)
        if self.prompt_state.mode != mode:
            self.prompt_state.enter_mode(mode)
        fields = self.hook_fields(budget, run_id, parent_run_id)
        if not turn_owned:
            await protected(self.hooks.start(cancel_event=cancel), cancel_event=cancel)
            await protected(self.hooks.emit('turn.start', **fields, message={'text': question}, cancel_event=cancel), cancel_event=cancel)
            await protected(self.hooks.emit('message.user', **fields, message={'text': question, 'role': 'user'}, cancel_event=cancel), cancel_event=cancel)
        source = self._run(question, history=history, mode=mode, cancel_event=cancel,
                           budget=budget, run_id=run_id, parent_run_id=parent_run_id)
        reason = None
        try:
            async for item in source:
                if item.kind == 'finished':
                    reason = item.reason
                yield item
        finally:
            if reason is None:
                cancel.set()
            await protected(source.aclose(), cancel_event=cancel)
            if not turn_owned:
                await protected(self.hooks.emit('turn.end', **self.hook_fields(budget, run_id, parent_run_id), reason=reason or 'cancelled', cancel_event=cancel), cancel_event=cancel)

    def hook_fields(self, budget, run_id, parent_run_id=''):
        fields = {'turn_id': budget.root_run_id, 'run_id': run_id,
                  'budget': {'used': budget.used, 'max_iterations': budget.limit}}
        if parent_run_id:
            fields['parent_run_id'] = parent_run_id
        if self.journal:
            fields['archive_session_id'] = getattr(self.journal, 'id', '')
        return fields

    async def aclose(self):
        await self.hooks.close()
        self.context.cache.close()

    async def _run(self, question: str, *, history: list[Message], mode: AgentMode,
                   cancel_event: asyncio.Event | None = None, budget=None, run_id=None,
                   parent_run_id='') -> AsyncIterator[AgentEvent]:
        cancel = cancel_event if cancel_event is not None else asyncio.Event()
        from .skills.budget import TaskBudget
        run_id = run_id or uuid4().hex
        budget = budget or TaskBudget(self.max_iterations, run_id)
        self.task_budget = budget
        iteration, unknown_count = budget.used, 0
        if self.prompt_state.mode != mode:
            self.prompt_state.enter_mode(mode)
        context = None
        committed = False
        force_compaction = recovered = False
        user = Message("user", question)
        self.current_user = user
        usage_records: list[TokenUsage] = []
        reason: StopReason = "max_iterations"
        detail = "达到模型请求上限，任务停止"
        allowed = lambda: self.effective_tools(mode)

        def event(kind, **fields):
            return AgentEvent(kind, run_id=run_id, iteration=budget.used, mode=mode,
                              permission_mode=getattr(getattr(self.executor, "permissions", None), "mode", "default"),
                              max_iterations=budget.limit, **fields)

        tools = self.executor.registry.definitions(allowed_tools=allowed())
        system = build_system_prompt()

        interaction_id = None
        tool_messages = {}
        evidence = []
        request_id = None
        hook_fields = lambda: self.hook_fields(budget, run_id, parent_run_id)

        def record(kind, payload):
            if self.journal:
                self.journal.append(kind, payload)

        def encoded(messages):
            from .sessions import encode_message
            try:
                return [encode_message(m) for m in messages]
            except (ValueError, TypeError):
                raise OSError('消息无法安全编码到会话存档') from None

        def group(message, results=()):
            return [*(() if committed else (user,)), context, message,
                    *(tool_messages.get(call.id) or Message('tool', tool_call_id=call.id, tool_result=result)
                      for call, result in zip(message.tool_calls, results))]

        def commit(message, results=(), *, saved=True):
            nonlocal committed, interaction_id
            messages = group(message, results)
            if saved and self.journal:
                record('history_commit', {'interaction_id': interaction_id, 'messages': encoded(messages)})
            history.extend(messages)
            committed = True
            self.prompt_state.commit(context)
            interaction_id = None

        try:
            if self.storage_blocked:
                raise OSError('存档已不可安全继续')
            record('task_started', {'task_id': run_id, 'input': question, 'mode': mode, 'user_id': user.id})
            while budget.remaining > 0 and not cancel.is_set():
                if request_id is None:
                    request_id = uuid4().hex
                    await self.hooks.emit('message.before_request', **hook_fields(), request_id=request_id, message={'text': question, 'role': 'user'}, cancel_event=cancel)
                if cancel.is_set():
                    break
                if self.before_request:
                    self.before_request()
                tools = self.executor.registry.definitions(allowed_tools=allowed())
                self.prompt_state.allowed_tools = allowed()
                spilled, warnings = self.context.spill(history)
                for warning in warnings:
                    yield event('context_compaction', phase='spill_failed', text=warning)
                if spilled:
                    yield event('context_compaction', phase='spill', spilled=spilled,
                                text=f'已将 {spilled} 项工具结果落盘，原文可按路径读取')
                selected = self.hooks.prompts.snapshot()
                estimate = self.context.estimate([*history, *(() if committed else (user,)),
                                                  self.prompt_state.peek_request(injections=selected)], system, tools)
                if force_compaction or not self.context.fits(estimate):
                    if self.context.circuit_open:
                        reason, detail = 'context_blocked', '自动摘要已熔断，请使用 /compact 单次尝试或新建会话'
                        break
                    yield event('context_compaction', phase='summary', purpose='summary',
                                estimated_before=estimate, failures=self.context.failures, spilled=self.context.cache.count,
                                text='正在准备结构化摘要')
                    def started():
                        nonlocal iteration
                        budget.take()
                        iteration = budget.used
                    result = await self.context.compact(self.provider, history, user, tools, system,
                        self.prompt_state.peek_request(force_full=True, injections=selected), cancel, on_start=started,
                        hooks=self.hooks, hook_fields=hook_fields)
                    if result.called:
                        usage_records.append(result.usage)
                        budget.usage.append(result.usage)
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
                    elif not budget.remaining:
                        break
                    elif not result.called or result.overflow or result.blocked or self.context.circuit_open:
                        reason, detail = 'context_blocked', result.text + '；可使用 /compact 或核对窗口配置'
                        break
                    else:
                        force_compaction = True
                    continue
                yield AgentEvent("progress", run_id=run_id, iteration=budget.used + 1, mode=mode,
                                 permission_mode=getattr(getattr(self.executor, "permissions", None), "mode", "default"),
                                 phase="model", max_iterations=budget.limit)
                if cancel.is_set():
                    break
                request_snapshot = None
                request_started = False
                async def request():
                    nonlocal context, request_snapshot, iteration, request_started
                    if cancel.is_set():
                        return
                    selected = self.hooks.prompts.snapshot()
                    preview = self.prompt_state.peek_request(injections=selected)
                    estimate = self.context.estimate([*history, *(() if committed else (user,)), preview], system, tools)
                    if not self.context.fits(estimate):
                        raise ContextLimitError('工作请求准备后超出上下文预算')
                    budget.take()
                    iteration = budget.used
                    context = self.prompt_state.begin_request(injections=selected)
                    self.hooks.prompts.consume(selected)
                    request_started = True
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
                source = collector.events(run_id=run_id, iteration=budget.used + 1, mode=mode)
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
                if not request_started and isinstance(failure, ContextLimitError):
                    force_compaction = True
                    continue
                usage_records.append(collector.usage)
                budget.usage.append(collector.usage)
                yield event("usage", usage=collector.usage)
                if cancel.is_set():
                    break
                if failure is not None:
                    if isinstance(failure, ContextLimitError) and not displayed:
                        if not budget.remaining:
                            break
                        if recovered:
                            reason, detail = 'context_blocked', '摘要恢复后的工作请求仍超限，请核对 context_window 与 max_output_tokens'
                            break
                        force_compaction = True
                        request_id = None
                        continue
                    reason, detail = "stream_error", str(failure)
                    break
                self.context.estimator.observe(request_snapshot, collector.usage)
                response = collector.response.message
                await self.hooks.emit('message.after_response', **hook_fields(), request_id=request_id, message={'text': response.content, 'role': 'assistant', 'tool_calls': [{'id': call.id, 'name': call.name, 'arguments': call.arguments} for call in response.tool_calls]}, cancel_event=cancel)
                request_id = None
                if not response.tool_calls:
                    commit(response)
                    reason, detail = "model_done", "模型已结束本次任务"
                    break
                names = self.executor.registry.names()
                unknown_count = unknown_count + 1 if all(call.name not in names for call in response.tool_calls) else 0
                interaction_id = uuid4().hex
                tool_messages = {}
                if self.journal:
                    record('interaction_started', {'interaction_id': interaction_id,
                                                    'messages': encoded(group(response))})
                def save_result(call, result):
                    message = Message('tool', tool_call_id=call.id, tool_result=result)
                    tool_messages[call.id] = message
                    evidence.append(message)
                    if self.journal:
                        record('tool_result', {'interaction_id': interaction_id, 'message': encoded([message])[0]})
                scheduler = ToolScheduler(self.executor, on_result=save_result, hooks=self.hooks, hook_fields=hook_fields)
                yield event("progress", phase="tools")
                batch = scheduler.run(response.tool_calls, allowed_tools=allowed, run_id=run_id,
                                      iteration=iteration, mode=mode, cancel_event=cancel)
                try:
                    async for item in batch:
                        yield item
                finally:
                    await protected(batch.aclose(), cancel_event=cancel)
                    # 无 await 的成对提交，重复取消不能留下孤立调用。
                    try:
                        if scheduler.storage_error:
                            raise scheduler.storage_error
                        commit(response, scheduler.results)
                    except OSError:
                        commit(response, scheduler.results, saved=False)
                        raise
                if any(result.error and result.error["code"] == "cancelled" for result in scheduler.results):
                    cancel.set()
                if cancel.is_set():
                    break
                if unknown_count >= 3:
                    reason, detail = "unknown_tool_limit", "连续 3 轮请求的工具全部未注册，任务停止"
                    break
        except asyncio.CancelledError:
            cancel.set()
        except OSError:
            self.storage_blocked = True
            reason, detail = 'context_blocked', '会话存档写入失败，已完成操作可能有副作用；停止后续工具和模型请求，请检查磁盘'
        if cancel.is_set():
            reason, detail = "cancelled", "用户取消任务；已完成的操作和结果保留，请按需检查实际状态"
        total = _total_usage(budget.usage if run_id == budget.root_run_id else usage_records)
        try:
            usage = asdict(total)
            usage['incomplete_fields'] = sorted(total.incomplete_fields)
            record('task_finished', {'task_id': run_id, 'reason': reason, 'mode': mode,
                                    'state': self.context.state(), 'usage': usage})
        except OSError:
            self.storage_blocked = True
            reason, detail = 'context_blocked', '任务收尾存档失败，停止继续请求；已完成操作和内存结果保留，请检查磁盘'
        self.last_task = {'session_id': getattr(self.journal, 'id', ''), 'task_id': run_id,
                          'user_message': user, 'final_message': history[-1] if reason == 'model_done' else None,
                          'tools': tuple(evidence), 'mode': mode, 'reason': reason}
        yield event("finished", reason=reason, text=detail, usage=total)
