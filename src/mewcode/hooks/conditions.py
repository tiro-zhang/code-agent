"""扁平条件在适用字段上求值，不执行用户表达式。"""

from dataclasses import dataclass, field
import json
import re

from ..matching import match_value
from .events import validate_field
from .models import HookConfigError


@dataclass(frozen=True)
class AtomicCondition:
    field: str
    match: str
    value_json: str = field(repr=False)
    negate: bool = False

    def matches(self, data: dict) -> bool:
        subject = data
        for part in self.field.split("."):
            if not isinstance(subject, dict) or part not in subject:
                return False
            subject = subject[part]
        value = json.loads(self.value_json)
        if self.match != "exact" and not isinstance(subject, str):
            return False
        if self.match == "exact" and type(subject) is not type(value):
            return False
        tool = data.get("tool", {})
        path = self.field == "tool.target_path" or (
            self.field == "tool.arguments.path" and tool.get("name") in {"read_file", "write_file", "edit_file"})
        result = match_value(subject, value, self.match, path=path)
        return not result if self.negate else result


@dataclass(frozen=True)
class Condition:
    operator: str
    atoms: tuple[AtomicCondition, ...]

    def matches(self, event) -> bool:
        data = event.data
        values = (atom.matches(data) for atom in self.atoms)
        return all(values) if self.operator == "all" else any(values)


def parse_condition(raw, event: str) -> Condition:
    if not isinstance(raw, dict) or len(raw) != 1 or next(iter(raw), None) not in {"all", "any"}:
        raise HookConfigError("if 只能声明一层 all 或 any")
    operator, entries = next(iter(raw.items()))
    if not isinstance(entries, list) or not entries:
        raise HookConfigError("条件列表必须非空")
    atoms = []
    for entry in entries:
        if not isinstance(entry, dict) or not {"field", "match", "value"} <= entry.keys() or not entry.keys() <= {"field", "match", "value", "negate"}:
            raise HookConfigError("原子条件必须声明 field/match/value，可选 negate")
        validate_field(event, entry["field"])
        match, value = entry["match"], entry["value"]
        if match not in ("exact", "glob", "regex"):
            raise HookConfigError("match 必须为 exact/glob/regex")
        if type(entry.get("negate", False)) is not bool:
            raise HookConfigError("negate 必须为布尔值")
        if match != "exact" and not isinstance(value, str):
            raise HookConfigError("glob/regex 的 value 必须为字符串")
        if match == "regex":
            try:
                re.compile(value)
            except re.error:
                raise HookConfigError("非法正则表达式") from None
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            raise HookConfigError("value 必须是有限 JSON 值") from None
        atoms.append(AtomicCondition(entry["field"], match, encoded, entry.get("negate", False)))
    return Condition(operator, tuple(atoms))
