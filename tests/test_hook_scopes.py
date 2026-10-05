"""共享 once 与基础设施，提示队列按父任务或子身份隔离。"""

import asyncio

from conftest import async_test
from test_hook_runtime import make_runtime, rule
from test_permission_runtime import manager


def scope(runtime, name, **options):
    assert hasattr(runtime, "scope"), "Hook 尚未提供独立运行视图"
    return runtime.scope(name, **options)


@async_test
async def test_prompt_queues_isolate_parent_siblings_and_survive_parent_segments(tmp_path):
    runtime = make_runtime(tmp_path, [rule(0)])
    parent, child, sibling = [scope(runtime, name) for name in ("parent", "child", "sibling")]
    try:
        await child.emit("turn.end", run_id="child-run")
        assert [item.text for item in child.prompts.snapshot()] == ["prompt-0"]
        assert parent.prompts.snapshot() == sibling.prompts.snapshot() == ()
        await parent.emit("turn.end", run_id="parent-segment-1")
        resumed = scope(runtime, "parent")
        assert [item.text for item in resumed.prompts.snapshot()] == ["prompt-0"]
        await child.close()
        assert not runtime.closed
        assert child.prompts.snapshot() == ()
        await sibling.emit("turn.end", run_id="sibling-run")
        assert [item.text for item in sibling.prompts.snapshot()] == ["prompt-0"]
        assert [item.text for item in parent.prompts.snapshot()] == ["prompt-0"]
    finally:
        await runtime.close()


@async_test
async def test_concurrent_scopes_share_session_once_claim(tmp_path):
    runtime = make_runtime(tmp_path, [rule(0, once=True)])
    scopes = [scope(runtime, f"task-{index}") for index in range(10)]
    try:
        await asyncio.gather(*(view.emit("turn.end") for view in scopes))
        assert sum(len(view.prompts.snapshot()) for view in scopes) == 1
        for view in scopes:
            await view.close()
        after_reset = scope(runtime, "new-parent")
        await after_reset.emit("turn.end")
        assert after_reset.prompts.snapshot() == ()
        assert len(runtime.once) == 1
    finally:
        await runtime.close()


@async_test
async def test_new_child_hook_uses_its_noninteractive_permission_and_mode(tmp_path):
    calls = []
    async def forbidden_input(request, cancel):
        calls.append(request)
        return "once"
    parent = manager(tmp_path, responder=forbidden_input)
    runtime = make_runtime(tmp_path, [rule(0, "command", command="touch blocked"), rule(1)])
    child = scope(runtime, "child", permissions=parent.fork("strict"))
    try:
        await child.emit("turn.end")
        assert calls == []
        assert not (tmp_path / "blocked").exists()
        assert child.event("turn.end").data["permission_mode"] == "strict"
        assert [item.text for item in child.prompts.snapshot()] == ["prompt-1"]
        plan = scope(runtime, "plan", current_mode=lambda: "plan")
        await plan.emit("turn.end")
        assert not (tmp_path / "blocked").exists()
        assert [item.text for item in plan.prompts.snapshot()] == ["prompt-1"]
    finally:
        await runtime.close()
