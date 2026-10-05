"""父进程的权限判定与可取消人工授权，不执行目标操作。"""

import asyncio
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import json
import inspect
from pathlib import Path
from uuid import uuid4

from ..tools.base import ToolContext, ToolError
from ..tools.paths import checked_path
from .config import PermissionConfig, PermissionConfigError
from .grants import GrantStore
from .rules import apply_mode, merge_rules
from .shell import analyze_command
from ..mcp.config import canonical
from ..mcp.permissions import grant_value


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    tool: str
    arguments: dict
    targets: tuple[str, ...]
    reason: str
    mode: str
    external: tuple[str, str] | None = None
    connection_description: str = ""
    origin: tuple[str, str] | None = None


@dataclass(frozen=True)
class Authorization:
    arguments: dict
    targets: tuple[str, ...] | None = None
    skipped_files: int = 0


def cancelled() -> ToolError:
    return ToolError("cancelled", "任务取消，工具未启动", not_started=True)


async def interruptible(awaitable, cancel: asyncio.Event):
    """取消真正结束等待者，避免遗留锁持有者或输入消费者。"""
    operation = asyncio.ensure_future(awaitable)
    watcher = asyncio.create_task(cancel.wait())
    try:
        await asyncio.wait((operation, watcher), return_when=asyncio.FIRST_COMPLETED)
        if cancel.is_set():
            raise cancelled()
        return await operation
    except asyncio.CancelledError:
        cancel.set()
        raise
    finally:
        watcher.cancel()
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, watcher, return_exceptions=True)


class PermissionManager:
    def __init__(self, root: Path, *, mode: str = "default", responder=None,
                 user_path: Path | None = None) -> None:
        self.root = Path(root).resolve()
        self.context = ToolContext(self.root)
        self.config = PermissionConfig(self.root, user_path=user_path)
        self.grants = GrantStore(self.config)
        self.mode = mode
        self.responder = responder
        self._prompt_lock = asyncio.Lock()
        self.mcp_tools = {}

    def bind_mcp_tools(self, tools):
        self.mcp_tools = {tool.name: tool for tool in tools}

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        if value not in {"strict", "default", "bypass"}:
            raise ValueError("权限模式必须是 strict、default 或 bypass")
        self._mode = value

    def _snapshot(self):
        try:
            return self.config.load()
        except PermissionConfigError as error:
            raise ToolError("permission_config_error", str(error), not_started=True) from None

    def _evaluate(self, tool, arguments, targets, once, rejected):
        snapshot = self._snapshot()
        search = tool in {"glob_files", "search_code"}
        kind = "path"
        analysis = None
        if tool in self.mcp_tools:
            values = (grant_value(self.mcp_tools[tool], arguments),)
            kind = "mcp"
        elif tool == "execute_command":
            analysis = analyze_command(arguments["command"])
            values = (arguments["command"],)
            kind = "command"
        elif search:
            values = tuple(str(checked_path(path, self.context)) for path in targets or ())
        elif tool in {"read_file", "write_file", "edit_file"}:
            path = checked_path(arguments["path"], self.context)
            values = (str(path),)
            if tool in {"write_file", "edit_file"} and path in self.config.protected_paths():
                raise ToolError("permission_denied", "权限配置只能通过可信管理入口修改",
                                source="protected_config", not_started=True)
        else:
            # 注册表可包含扩展工具；没有专用目标适配时以完整参数授权。
            values = (json.dumps(arguments, sort_keys=True, ensure_ascii=False),)
            kind = "command"

        allowed, pending, denied = [], [], []
        reasons = []
        for value in dict.fromkeys(values):
            subject = (canonical(arguments) if kind == "mcp" else
                       str(Path(value).relative_to(self.root)) if kind == "path" else value)
            evaluation = merge_rules(snapshot.rules, tool,
                                     analysis.subjects if analysis else (subject,),
                                     allow_subject=subject,
                                     glob_allow=analysis.simple if analysis else True)
            effect = apply_mode(evaluation.effect, self.mode)
            if effect == "deny" or value in rejected:
                denied.append(value)
                if not search:
                    raise ToolError("permission_denied", "权限规则或用户决定禁止此操作",
                                    source="rule" if effect == "deny" else "user", not_started=True)
            elif effect == "allow" or value in once or self.grants.has(tool, kind, value, snapshot):
                allowed.append(value)
            else:
                pending.append(value)
                reasons.append("命中 ask 规则" if evaluation.effect == "ask" else
                               "严格模式要求授权" if self.mode == "strict" else "没有适用的放行规则")
        return kind, tuple(allowed), tuple(pending), tuple(denied), "；".join(dict.fromkeys(reasons))

    async def authorize(self, tool: str, arguments: dict, *, targets: tuple[str, ...] | None = None,
                        cancel_event: asyncio.Event | None = None,
                        notify: Callable[[dict], None] | None = None,
                        origin: tuple[str, str] | None = None) -> Authorization:
        cancel = cancel_event if cancel_event is not None else asyncio.Event()
        original = deepcopy(arguments)
        once, rejected = set(), set()
        search = tool in {"glob_files", "search_code"}

        async def emit(event):
            if notify:
                result = notify(event)
                if inspect.isawaitable(result):
                    await result

        def evaluate():
            if cancel.is_set():
                raise cancelled()
            if arguments != original:
                raise ToolError("permission_check_failed", "授权期间调用参数发生变化，请重新发起调用", not_started=True)
            return self._evaluate(tool, original, targets, once, rejected)

        async def ask():
            async with self._prompt_lock:
                kind, allowed, pending, denied, reason = evaluate()
                if not pending:
                    return
                if self.responder is None:
                    if not search:
                        raise ToolError("permission_denied", "需要人工授权，但没有可用的决策通道",
                                        source="no_approval_channel", not_started=True)
                    rejected.update(pending)
                    return
                external = self.mcp_tools.get(tool)
                request = ApprovalRequest(uuid4().hex, tool, deepcopy(original), pending, reason, self.mode,
                                          (external.server_name, external.original_name) if external else None,
                                          external.connection_description if external else "", origin)
                await emit({"kind": "permission_requested", "request": request})
                choice = await self.responder(request, cancel)
                if cancel.is_set():
                    raise cancelled()
                if request.arguments != original:
                    raise ToolError("permission_check_failed", "展示的调用参数被改变，请重新发起授权", not_started=True)
                warning = ""
                if choice in {"once", "session", "permanent"}:
                    # 重读规则并重解析路径后再应用批准。目标改变则回到下一次询问。
                    current_kind, current_allowed, current_pending, current_denied, _ = evaluate()
                    current = set(current_allowed) | set(current_pending)
                    approved = tuple(value for value in pending if value in current)
                    once.update(approved)
                    if approved and choice in {"session", "permanent"}:
                        warning = self.grants.remember(tool, current_kind, approved, choice) or ""
                else:
                    choice = "deny"
                    rejected.update(pending)
                await emit({"kind": "permission_resolved", "request": request,
                            "decision": choice, "warning": warning})

        while True:
            kind, allowed, pending, denied, reason = evaluate()
            if not pending:
                return Authorization(deepcopy(original), allowed if kind == "path" else None, len(denied))
            await interruptible(ask(), cancel)

    def authorize_noninteractive(self, tool: str, arguments: dict, *,
                                cancel_event: asyncio.Event | None = None) -> Authorization:
        """后台只能使用已有许可；黑名单、拒绝及有效批准与前台相同。"""
        if cancel_event is not None and cancel_event.is_set():
            raise cancelled()
        original = deepcopy(arguments)
        kind, allowed, pending, denied, _ = self._evaluate(tool, original, None, set(), set())
        if pending:
            raise ToolError("approval_required", "后台动作缺少非交互许可", not_started=True)
        return Authorization(original, allowed if kind == "path" else None, len(denied))
