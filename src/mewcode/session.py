"""会话历史的所有者；单次任务交由 Agent 编排。"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from .agent import Agent
from .async_utils import protected
from .prompts import PromptState
from .tools import default_registry
from .tools.base import ToolContext, strict_json
from .tools.executor import ToolExecutor
from .types import AgentEvent, AgentMode, Message, Provider, ToolCall


class PlanStateError(RuntimeError):
    """没有可执行的待执行计划，可直接提示用户。"""


@dataclass(frozen=True)
class _PlanSnapshot:
    task: str
    answer: str
    context: tuple[Message, ...]


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
                 approval_responder=None) -> None:
        self.provider = provider
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
        self._pending_plan: _PlanSnapshot | None = None
        self.prompt_state = PromptState(self.executor.context.root)
        self.agent = Agent(provider, self.executor, max_iterations=max_iterations, prompt_state=self.prompt_state)

    def permission_command(self, question: str) -> str:
        """只在空闲期执行的本地控制，不触碰历史或计划状态。"""
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
        self.mode = "plan"
        self.prompt_state.enter_mode("plan")
        self._pending_plan = None

    async def ask(self, question: str, *, cancel_event: asyncio.Event | None = None) -> AsyncIterator[AgentEvent]:
        cancel = cancel_event if cancel_event is not None else asyncio.Event()
        if self.mode == "plan":
            self._pending_plan = None
        source = self.agent.run(question, history=self.history, mode=self.mode, cancel_event=cancel)
        try:
            async for event in source:
                if self.mode == "plan" and event.kind == "finished" and event.reason == "model_done":
                    self._pending_plan = _PlanSnapshot(question, self.history[-1].content, tuple(self.history))
                yield event
        finally:
            await protected(source.aclose(), cancel_event=cancel)

    async def execute_plan(self, *, cancel_event: asyncio.Event | None = None) -> AsyncIterator[AgentEvent]:
        if self._pending_plan is None:
            raise PlanStateError("没有有效的待执行计划，请先使用 /plan 生成计划")
        snapshot = self._pending_plan
        # 取出与消费标记不包含 await，重复 /do 无法重放已启动计划。
        self._pending_plan = None
        self.mode = "execute"
        self.prompt_state.enter_mode("execute")
        question = ("请直接执行以下最新计划，按需核对当前文件状态，完成后验证结果。\n"
                    f"任务上下文：{snapshot.task}\n最新计划：\n{snapshot.answer}")
        source = self.ask(question, cancel_event=cancel_event)
        try:
            async for event in source:
                yield event
        finally:
            await protected(source.aclose(), cancel_event=cancel_event)
