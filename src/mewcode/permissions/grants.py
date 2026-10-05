"""工具与精确目标范围绑定的存续授权；不参与规则优先级。"""

from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from .config import PermissionConfig, PermissionConfigError, TOOLS
from .models import Approval, PolicySnapshot
from ..mcp.tools import is_mcp_alias
from ..mcp.permissions import validate_grant
from .targets import canonical_object


class GrantStore:
    def __init__(self, config: PermissionConfig):
        self.config = config
        self._session: dict[tuple[str, str, str], Approval] = {}

    @property
    def session(self) -> tuple[Approval, ...]:
        return tuple(self._session.values())

    def copy_for(self, config: PermissionConfig):
        """仅复制本会话记录；永久批准仍由每次读取的策略决定。"""
        result = GrantStore(config)
        result._session = self._session.copy()
        return result

    def _target(self, tool: str, kind: str, value: str) -> str:
        if is_mcp_alias(tool):
            if kind != "mcp":
                raise ValueError("外部工具授权必须使用 mcp 范围")
            return validate_grant(tool, value)
        if tool not in TOOLS or not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise ValueError("授权工具或精确目标无效")
        if tool == "agent":
            if kind != "command":
                raise ValueError("Agent 授权必须使用精确规范参数")
            return canonical_object(value)
        if tool in {"execute_command", "load_skill"}:
            if kind != "command":
                raise ValueError("命令授权必须使用精确 command")
            return value
        if kind != "path" or not Path(value).is_absolute():
            raise ValueError("文件授权必须使用真实绝对 path")
        target = Path(value).resolve()
        if not target.is_relative_to(self.config.root):
            raise ValueError("文件授权目标不能超出项目根")
        return str(target)

    def has(self, tool: str, kind: str, value: str, snapshot: PolicySnapshot) -> bool:
        try:
            value = self._target(tool, kind, value)
        except (ValueError, OSError, RuntimeError):
            return False
        key = (tool, kind, value)
        if key in self._session:
            return True
        root = str(self.config.root)
        return any(
            approval.root == root and (approval.tool, approval.kind, approval.value) == key
            for approval in snapshot.approvals
        )

    def remember(self, tool: str, kind: str, values: Sequence[str], duration: str) -> str | None:
        if duration not in ("session", "permanent"):
            raise ValueError("存续授权仅接受 session 或 permanent")
        # 先完整验证所有目标，避免无效集合留下部分授权。
        targets = tuple(dict.fromkeys(self._target(tool, kind, value) for value in values))
        approvals = tuple(Approval(str(uuid4()), str(self.config.root), tool, kind, value) for value in targets)
        if duration == "session":
            for approval in approvals:
                self._session.setdefault((tool, kind, approval.value), approval)
            return None
        try:
            self.config.save_approvals(approvals)
        except PermissionConfigError as error:
            return f"永久授权保存失败，当前操作仅按本次批准处理：{error}"
        return None

    def revoke(self, duration: str) -> None:
        if duration == "session":
            self._session.clear()
        elif duration == "permanent":
            self.config.revoke_permanent()
        else:
            raise ValueError("撤销范围仅接受 session 或 permanent")
