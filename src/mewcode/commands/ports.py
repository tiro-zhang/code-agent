"""命令使用的能力协议与交给应用消费的结果。"""
from dataclasses import dataclass
from typing import Protocol
from .registry import CommandRegistry


@dataclass(frozen=True)
class CommandResult:
    kind: str = 'handled'
    text: str = ''


class CommandUsageError(ValueError):
    """参数不合法；分发器从元数据补充用法。"""


class CommandContext(Protocol):
    registry: CommandRegistry
    enhanced: bool

    def show_message(self, text: str) -> None: ...
    async def clear_screen(self) -> None: ...
    def set_mode(self, mode: str) -> None: ...
    def refresh_status(self) -> None: ...
    def status_text(self) -> str: ...
    def session_text(self, *, listing: bool) -> str: ...
    def memory_text(self, action: str, scope: str | None = None, identity: str | None = None) -> str: ...
    def permission_text(self, args: str) -> str: ...
