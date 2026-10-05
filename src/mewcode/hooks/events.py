"""固定事件目录和不受后续修改影响的 JSON 快照。"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from ..tools.paths import checked_path
from ..tools.base import ToolError

from .models import HookConfigError


COMMON_FIELDS = frozenset({
    "event", "time", "session_id", "archive_session_id", "turn_id", "run_id",
    "parent_run_id", "request_id", "mode", "permission_mode", "budget.used", "budget.max_iterations",
})
TOOL_FIELDS = frozenset({"tool.call_id", "tool.name", "tool.arguments", "tool.target_path"})
RESULT_FIELDS = frozenset({"tool.result", "tool.result.ok", "tool.result.error", "tool.result.error.code",
                           "tool.result.error.message", "tool.result.error.details", "tool.result.data", "tool.result.truncated"})
EVENT_FIELDS = {
    "session.start": frozenset({"source"}),
    "session.end": frozenset({"reason"}),
    "turn.start": frozenset({"message.text"}),
    "turn.end": frozenset({"reason"}),
    "message.user": frozenset({"message.text", "message.role"}),
    "message.before_request": frozenset({"message.text", "message.role"}),
    "message.after_response": frozenset({"message.text", "message.role", "message.tool_calls"}),
    "tool.before": TOOL_FIELDS,
    "tool.after": TOOL_FIELDS | RESULT_FIELDS,
    "mode.changed": frozenset({"mode_change.from", "mode_change.to"}),
    "context.before_compact": frozenset({"context.purpose", "context.estimated_tokens", "context.threshold"}),
    "context.after_compact": frozenset({"context.purpose", "context.result", "context.reason", "context.estimated_tokens", "context.threshold"}),
}


def tool_snapshot(executor, call_id, name, raw, *, arguments=None, result=None):
    """只有已通过参数校验的调用才复制参数；原始输入不伪装成有效参数。"""
    data = {'call_id': call_id, 'name': name}
    try:
        if arguments is None:
            _, arguments = executor.registry.prepare(name, raw)
        data['arguments'] = arguments
        if name in {'read_file', 'write_file', 'edit_file'}:
            path = checked_path(arguments['path'], executor.context)
            data['target_path'] = path.relative_to(executor.context.root).as_posix()
    except (ToolError, OSError, ValueError):
        pass
    if result is not None:
        data['result'] = result.to_dict()
    return data


def validate_field(event: str, name: str) -> None:
    if not isinstance(name, str) or any(not part or part.isdecimal() for part in name.split(".")):
        raise HookConfigError("field 必须是点分对象字段，不支持列表下标")
    fields = COMMON_FIELDS | EVENT_FIELDS[event]
    if name in fields:
        return
    if event.startswith("tool.") and name.startswith("tool.arguments."):
        return
    if event == "tool.after" and name.startswith(("tool.result.error.details.", "tool.result.data.")):
        return
    raise HookConfigError("field 不属于该事件的字段目录")


@dataclass(frozen=True)
class HookEvent:
    event: str
    _json: str = field(repr=False)

    @classmethod
    def create(cls, event: str, *, session_id: str, mode: str = "execute",
               permission_mode: str = "default", **fields):
        if event not in EVENT_FIELDS:
            raise HookConfigError("未知生命周期事件")
        data = {**fields, "event": event, "session_id": session_id,
                "time": datetime.now(timezone.utc).isoformat(), "mode": mode, "permission_mode": permission_mode}
        return cls(event, json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")))

    @property
    def data(self) -> dict:
        return json.loads(self._json)

    def to_json(self) -> str:
        return self._json
