"""Hook 配置、动作和诊断的不可变合同。"""

from dataclasses import dataclass, field
from pathlib import Path


class HookConfigError(ValueError):
    """可安全记录的配置错误，不包含用户原始值。"""


@dataclass(frozen=True)
class HookDiagnostic:
    source: str
    index: int | None
    event: str
    message: str


@dataclass(frozen=True)
class HookAction:
    type: str
    command: str = field(default="", repr=False)
    timeout_seconds: int = 30
    text: str = field(default="", repr=False)
    url: str = field(default="", repr=False)
    method: str = "POST"
    headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    agent: str = field(default="", repr=False)
    prompt: str = field(default="", repr=False)


@dataclass(frozen=True)
class HookRule:
    event: str
    action: HookAction
    condition: object = None
    once: bool = False
    background: bool = False
    source: str = ""
    index: int = 0

    @property
    def identity(self) -> str:
        return f"{self.source}#hooks[{self.index}]"


@dataclass(frozen=True)
class HookConfigSnapshot:
    root: Path
    rules: tuple[HookRule, ...] = ()
    diagnostics: tuple[HookDiagnostic, ...] = ()


@dataclass(frozen=True)
class ActionResult:
    status: str = "ok"
    decision: str = ""
    reason: str = field(default="", repr=False)
    source: str = ""
