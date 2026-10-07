"""可序列化、不可变的权限合同。"""

from dataclasses import dataclass


JSON_ARGUMENT_TOOLS = frozenset(('agent', 'team', 'team_member', 'team_task', 'team_message', 'team_integrate'))


@dataclass(frozen=True)
class Rule:
    effect: str
    tool: str
    pattern: str
    match: str
    source: str = ""


@dataclass(frozen=True)
class Approval:
    id: str
    root: str
    tool: str
    kind: str
    value: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "root": self.root,
            "tool": self.tool,
            "scope": {"kind": self.kind, "value": self.value},
        }


@dataclass(frozen=True)
class PolicySnapshot:
    rules: tuple[Rule, ...]
    approvals: tuple[Approval, ...]
    signature: str


@dataclass(frozen=True)
class Evaluation:
    effect: str | None
    rules: tuple[Rule, ...]
