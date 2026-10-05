"""系统入口在主进程提交，串行后动态复查白名单。"""

import json
import pickle

from conftest import async_test, collect, permission_bypass
from mewcode.skills.tool import LoadSkill
from mewcode.tools import default_registry
from mewcode.tools.base import ToolContext, ToolResult
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.scheduler import ToolScheduler
from mewcode.types import ToolCall


@async_test
async def test_system_route_updates_main_state_before_same_batch_edit(tmp_path):
    registry = default_registry()
    assert "load_skill" in registry.names()
    assert "load_skill" not in registry.names(read_only=True)
    assert [t.name for t in registry.definitions(allowed_tools=frozenset())] == ["load_skill"]
    pickle.loads(pickle.dumps(registry))
    allowed = set(registry.names())
    events, loaded = [], []
    async def handler(arguments, *, cancel_event, on_event):
        loaded.append(arguments["name"])
        allowed.intersection_update({"read_file"})
        return ToolResult.success({"loaded": True})
    ex = ToolExecutor(registry, ToolContext(tmp_path), permissions=permission_bypass(tmp_path),
                      system_handlers={"load_skill": handler})
    (tmp_path / "a").write_text("before")
    scheduler = ToolScheduler(ex)
    events = await collect(scheduler.run([
        ToolCall("load", "load_skill", '{"name":"readonly"}'),
        ToolCall("edit", "edit_file", '{"path":"a","old_text":"before","new_text":"after"}')],
        allowed_tools=lambda: frozenset(allowed), run_id="root", iteration=1, mode="execute"))
    assert loaded == ["readonly"]
    assert scheduler.results[0].ok
    assert scheduler.results[1].error["code"] == "tool_not_allowed"
    assert (tmp_path / "a").read_text() == "before"
    assert [e.tool_call_id for e in events if e.kind == "tool_result"] == ["load", "edit"]


@async_test
async def test_load_permission_deny_and_exact_grants(tmp_path):
    permissions = permission_bypass(tmp_path)
    config = tmp_path / ".mewcode" / "permissions.yaml"
    config.parent.mkdir()
    config.write_text('rules:\n  - effect: deny\n    rule: load_skill(*)\n    match: glob\n')
    loaded = []
    async def handler(arguments, **kwargs):
        loaded.append(arguments)
        return ToolResult.success("loaded")
    ex = ToolExecutor(default_registry(), ToolContext(tmp_path), permissions=permissions,
                      system_handlers={"load_skill": handler})
    result = await ex.execute("load_skill", '{"name":"a"}', allowed_tools=frozenset())
    assert result.error["code"] == "permission_denied"
    assert result.error["details"]["not_started"]
    assert loaded == []
    config.unlink()
    permissions.mode = "default"
    decisions = []
    async def approve(request, cancel):
        decisions.append(request.arguments)
        return "permanent"
    permissions.responder = approve
    assert (await ex.execute("load_skill", '{"name":"a"}')).ok
    assert (await ex.execute("load_skill", '{"name":"a"}')).ok
    assert len(decisions) == 1
    assert (await ex.execute("load_skill", '{"name":"b"}')).ok
    assert len(decisions) == 2


def test_load_schema_rejects_mixed_resource_and_execution():
    registry = default_registry()
    for values in ({"name":"a", "resource":"guide", "args":"bad"},
                   {"name":"a", "history":True}, {"name":"a", "history":-1},
                   {"name":"a", "resource":"guide", "history":0}):
        result = registry.invoke("load_skill", json.dumps(values), ToolContext(__import__('pathlib').Path.cwd()))
        assert result.error["code"] == "invalid_arguments"
