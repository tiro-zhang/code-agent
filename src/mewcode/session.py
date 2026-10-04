"""会话历史的所有者；单次任务交由 Agent 编排。"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4

from .agent import Agent
from .async_utils import protected
from .prompts import PromptState, build_system_prompt
from .tools import default_registry
from .tools.base import ToolContext, strict_json
from .tools.executor import ToolExecutor
from .types import AgentEvent, AgentMode, Message, Provider, ToolCall


def operation_summary(call: ToolCall) -> str:
    """只摘取路径、命令或模式，写入与替换正文不进入摘要。"""
    try:
        arguments = strict_json(call.arguments)
        if isinstance(arguments, dict):
            for key in ("path", "command", "pattern"):
                if isinstance(arguments.get(key), str):
                    return arguments[key]
    except (ValueError, RecursionError):
        pass
    return "校验参数"



class ChatSession:
    def __init__(self, provider: Provider, *, executor: ToolExecutor | None = None,
                 max_iterations: int = 20, permission_mode: str = "default",
                 approval_responder=None, config=None, persistent=False, resume=None,
                 user_root=None, memory_enabled=True, notify=None) -> None:
        self.provider = provider
        self.config = config or getattr(provider, 'config', None)
        self.journal = None
        self.memory = None
        self._notify = notify
        self.memory_usage = None
        self.restore_usage = None
        self.memory_tasks = {}
        self.memory_status = '尚未提取'
        self.warnings = []
        self.resumed = bool(resume)
        from .permissions.runtime import PermissionManager
        if executor is None:
            context = ToolContext(Path.cwd())
            permissions = PermissionManager(context.root, mode=permission_mode, responder=approval_responder)
            executor = ToolExecutor(default_registry(), context, permissions=permissions)
        self.executor = executor
        self.permissions = getattr(executor, "permissions", None)
        if self.permissions is None:
            self.permissions = PermissionManager(executor.context.root, mode=permission_mode,
                                                 responder=approval_responder)
        self.history: list[Message] = []
        self.mode: AgentMode = "execute"
        root = self.executor.context.root
        self.user_root = Path(user_root) if user_root is not None else Path.home() / ".mewcode"
        from .instructions import load_instructions
        instructions = load_instructions(root, user_root)
        self.warnings.extend(instructions.warnings)
        if persistent or resume:
            from .sessions import Journal, cleanup_expired
            self.warnings.extend(cleanup_expired(root))
            self.journal = (Journal.resume(root, resume, self.config.protocol, self.config.model,
                                          record_activity=False) if resume else
                            Journal.create(root, self.config.protocol, self.config.model))
            self.warnings.extend(self.journal.warnings)
        self.prompt_state = PromptState(root, custom_instructions=instructions.text)
        self.agent = Agent(provider, self.executor, max_iterations=max_iterations, prompt_state=self.prompt_state,
                           config=config, journal=self.journal, before_request=self.refresh_memory)
        self.context = self.agent.context
        try:
            if self.journal:
                from .context.spill import ResultCache
                self.context.cache.close()
                self.context.cache = ResultCache(root, session_id=self.journal.id, persistent=True)
                self.context.checkpoint = self._checkpoint
                if resume:
                    projection = self.journal.projection
                    self.history[:] = projection.history
                    self.mode = projection.mode
                    self.prompt_state.enter_mode(self.mode)
                    self.context.restore_state(projection.state)
                    self.context.cache.reopen(projection.cache_paths)
                    required = self.context.summary_files | {m.cache_path for m in self.history if m.cache_path}
                    try:
                        for path in required:
                            self.context.cache.restore(path)
                    except (OSError, ValueError, KeyError, StopIteration, TypeError):
                        raise ValueError('恢复所需结果缓存缺失或损坏，请核查存档与实际状态') from None
                    last = projection.last_activity
                    gap = datetime.now(timezone.utc) - last
                    if gap > timedelta(hours=24):
                        candidate = [*self.history, Message('context',
                            f'会话最后活动于 {last.isoformat()}，距今 {gap.days} 天 {gap.seconds // 3600} 小时。涉及当前文件时请重新读取，历史内容仅为当时快照。',
                            context_kind='resume')]
                        self._checkpoint(candidate, self.context.state())
                        self.history[:] = candidate
            if self.journal and memory_enabled:
                from .memory import MemoryManager
                self.memory = MemoryManager(root, user_root=user_root, provider=provider,
                                            config=self.config, notify=self._memory_notification)
                self.refresh_memory()
        except Exception:
            self.close()
            raise

    @property
    def session_id(self):
        return self.journal.id if self.journal else ''

    def _checkpoint(self, history, state):
        from .sessions import encode_message
        try:
            self.journal.append('history_checkpoint', {'history': [encode_message(m) for m in history], 'state': state})
        except OSError:
            self.agent.storage_blocked = True
            raise

    def refresh_memory(self):
        if self.memory:
            self.prompt_state.update_memory(self.memory.refresh())

    def _memory_notification(self, notification):
        kind = notification.get('kind')
        if kind == 'usage':
            self.memory_usage = notification.get('usage')
        elif kind == 'restore_usage':
            self.restore_usage = notification.get('usage')
        elif kind == 'memory_update':
            self.memory_status = notification.get('text', '')
            if notification.get('task_id'):
                self.memory_tasks[notification['task_id']] = notification.get('status', 'unknown')
        if notification.get('kind') == 'usage' and self.journal:
            usage = asdict(notification['usage'])
            usage['incomplete_fields'] = sorted(usage['incomplete_fields'])
            try:
                self.journal.append('maintenance_finished', {'purpose': 'memory', 'usage': usage,
                    'task_id': notification.get('task_id', '')})
            except OSError:
                self.warnings.append('记忆维护用量存档失败')
        if getattr(self, '_notify', None):
            self._notify(notification)

    def permission_command(self, question: str) -> str:
        """只在空闲期执行的本地控制，不触碰对话历史。"""
        parts = question.split()
        usage = "用法：/permissions；/permissions mode strict|default|bypass；/permissions revoke session|permanent"
        if parts == ["/permissions"]:
            snapshot = self.permissions.config.load()
            root = str(self.executor.context.root)
            permanent = [grant for grant in snapshot.approvals if str(grant.root) == root]
            session = self.permissions.grants.session
            return (f"权限模式> {self.permissions.mode} · 项目 {root}\n"
                    f"会话授权> {len(session)} 项 · {session}\n永久授权> {len(permanent)} 项 · {permanent}")
        if len(parts) == 3 and parts[1] == "mode" and parts[2] in {"strict", "default", "bypass"}:
            self.permissions.mode = parts[2]
            return f"权限模式> 已切换为 {parts[2]}"
        if len(parts) == 3 and parts[1] == "revoke" and parts[2] in {"session", "permanent"}:
            self.permissions.grants.revoke(parts[2])
            return f"权限> 已撤销当前项目的{'会话' if parts[2] == 'session' else '永久'}授权"
        raise ValueError(usage)

    def enter_plan(self) -> None:
        if self.journal:
            self.journal.append('mode_changed', {'mode': 'plan'})
        self.mode = "plan"
        self.prompt_state.enter_mode("plan")

    async def ask(self, question: str, *, cancel_event: asyncio.Event | None = None) -> AsyncIterator[AgentEvent]:
        cancel = cancel_event if cancel_event is not None else asyncio.Event()
        source = self.agent.run(question, history=self.history, mode=self.mode, cancel_event=cancel)
        try:
            async for event in source:
                if event.kind == 'finished' and event.reason == 'model_done' and self.memory:
                    if self.memory.enqueue(self.agent.last_task):
                        self.memory_tasks[event.run_id] = 'queued'
                yield event
        finally:
            await protected(source.aclose(), cancel_event=cancel)

    def enter_execute(self) -> None:
        """只切换模式；重复切换不重置当前请求周期。"""
        if self.mode == "execute":
            return
        if self.journal:
            self.journal.append('mode_changed', {'mode': 'execute'})
        self.mode = "execute"
        self.prompt_state.enter_mode("execute")

    def context_status(self) -> str:
        allowed = self.executor.registry.names(read_only=True) if self.mode == 'plan' else self.executor.registry.names()
        self.context.estimate([*self.history, self.prompt_state.peek_request()], build_system_prompt(),
                              self.executor.registry.definitions(allowed_tools=allowed))
        identity = f'会话> {self.session_id or "未启用存档"} · {"显式恢复" if self.resumed else "新建"}\n'
        pending = sum(status in {'queued', 'running'} for status in self.memory_tasks.values())
        maintenance = (f'\n记忆> 待处理 {pending} · {self.memory_status} · 索引版本 {self.memory.version[:12]}'
                       if self.memory else '')
        from .terminal.text import usage_text
        if self.memory_usage is not None:
            maintenance += '\n记忆维护实际用量> ' + usage_text(self.memory_usage)
        if self.restore_usage is not None:
            maintenance += '\n恢复摘要实际用量> ' + usage_text(self.restore_usage)
        return identity + self.context.status_text() + maintenance

    def close(self) -> None:
        """释放私有句柄；产品存档的缓存随存档保留。"""
        try:
            if hasattr(self, 'context'):
                self.context.cache.close()
        finally:
            if self.journal:
                self.journal.close()

    async def aclose(self):
        """后台任务停止后才释放存档，供应商由应用随后关闭。"""
        try:
            if self.memory:
                await self.memory.aclose(wait_seconds=2)
        finally:
            self.close()

    async def prepare_restore(self, *, cancel_event=None):
        """当前 MCP 工具确定后，仅一次独立恢复摘要，不执行历史任务。"""
        if not self.resumed:
            return
        self.refresh_memory()
        allowed = self.executor.registry.names(read_only=True) if self.mode == 'plan' else self.executor.registry.names()
        tools = self.executor.registry.definitions(allowed_tools=allowed)
        system = build_system_prompt()
        reminder = self.prompt_state.peek_request(force_full=True)
        estimate = self.context.estimate([*self.history, reminder], system, tools)
        if self.context.fits(estimate):
            if self.journal:
                self.journal.confirm_resume()
            return
        cancel = cancel_event or asyncio.Event()
        spilled, warnings = self.context.spill(self.history)
        self.warnings.extend(warnings)
        estimate = self.context.estimate([*self.history, reminder], system, tools)
        if self.context.fits(estimate):
            if spilled:
                self.warnings.append(f'恢复已将 {spilled} 项结果落盘，当前预算可继续')
            if self.journal:
                self.journal.confirm_resume()
            return
        user = next((m for m in reversed(self.history) if m.role == 'user'), None)
        manual_usage = self.context.manual_usage
        try:
            result = await self.context.compact(self.provider, self.history, user, tools, system, reminder,
                                                 cancel, manual=True)
        finally:
            # 恢复借用手动摘要的输入余量，用量仍归属于独立恢复用途。
            self.context.manual_usage = manual_usage
        self._maintenance_record('restore', result.usage, called=result.called)
        if result.called:
            self._memory_notification({'kind': 'restore_usage', 'usage': result.usage, 'purpose': 'restore'})
        if not result.success:
            raise ValueError('恢复无法继续：' + result.text)
        self.prompt_state.history_trimmed()
        if self.journal:
            self.journal.confirm_resume()

    def _maintenance_record(self, purpose, usage=None, *, called=False):
        if self.journal:
            data = asdict(usage) if called and usage else None
            if data is not None:
                data['incomplete_fields'] = sorted(data['incomplete_fields'])
            self.journal.append('maintenance_finished', {'purpose': purpose, 'state': self.context.state(), 'usage': data})

    async def compact(self, *, cancel_event: asyncio.Event | None = None) -> AsyncIterator[AgentEvent]:
        """独立维护操作，保留模式和工作任务记录。"""
        cancel = cancel_event if cancel_event is not None else asyncio.Event()
        run_id = uuid4().hex
        iteration = 0
        def event(kind, **fields):
            return AgentEvent(kind, run_id=run_id, iteration=iteration, max_iterations=1,
                              mode=self.mode, permission_mode=self.permissions.mode, purpose='summary', **fields)
        if cancel.is_set():
            self._maintenance_record('summary')
            yield event('context_compaction', phase='cancelled', text='摘要已取消，历史保留')
            return
        spilled, warnings = self.context.spill(self.history)
        for warning in warnings:
            yield event('context_compaction', phase='spill_failed', text=warning)
        yield event('context_compaction', phase='summary', spilled=spilled, text='正在准备手动摘要（最多一次请求）')
        user = next((m for m in reversed(self.history) if m.role == 'user'), None)
        allowed = self.executor.registry.names(read_only=True) if self.mode == 'plan' else self.executor.registry.names()
        def started():
            nonlocal iteration
            iteration = 1
        result = await self.context.compact(self.provider, self.history, user,
            self.executor.registry.definitions(allowed_tools=allowed), build_system_prompt(),
            self.prompt_state.peek_request(force_full=True), cancel, manual=True, on_start=started)
        self._maintenance_record('summary', result.usage, called=result.called)
        if result.called:
            yield event('usage', usage=result.usage)
        if result.success:
            self.prompt_state.history_trimmed()
        yield event('context_compaction', phase='success' if result.success else 'cancelled' if result.cancelled else 'failed' if result.called else 'noop',
                    text=result.text, estimated_before=result.before, estimated_after=result.after,
                    spilled=self.context.cache.count, failures=self.context.failures, circuit_open=self.context.circuit_open)
