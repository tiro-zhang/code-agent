"""事件独立快照与字段目录。"""

import pytest


def test_event_keeps_original_snapshot_and_ids():
    from mewcode.hooks.events import HookEvent
    tool = {"name": "edit_file", "call_id": "call-1", "arguments": {"path": "link/a.py"},
            "target_path": "src/private/a.py"}
    event = HookEvent.create("tool.before", session_id="session-1", turn_id="turn-1",
                             run_id="child-1", parent_run_id="parent-1", tool=tool)
    tool["arguments"]["path"] = "changed"
    data = event.data
    data["tool"]["target_path"] = "changed"
    assert event.data["tool"]["arguments"]["path"] == "link/a.py"
    assert event.data["tool"]["target_path"] == "src/private/a.py"
    assert event.data["parent_run_id"] == "parent-1"
    assert event.data["tool"]["call_id"] == "call-1"
    assert "message" not in event.data
    assert "time" in event.data


def test_event_rejects_unknown_event_and_non_json():
    from mewcode.hooks.events import HookEvent
    with pytest.raises(ValueError):
        HookEvent.create("unknown", session_id="a")
    with pytest.raises(ValueError):
        HookEvent.create("turn.start", session_id="a", budget={"used": float("nan")})
