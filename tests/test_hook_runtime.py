"""会话运行时的顺序、一次性和后台生命周期。"""

import asyncio
import logging

from conftest import async_test, permission_bypass


def make_runtime(tmp_path, rules, **options):
    from mewcode.hooks.runtime import HookRuntime
    from mewcode.hooks.actions import ActionRunner
    from mewcode.hooks.models import HookConfigSnapshot
    runner = ActionRunner(tmp_path, permissions=permission_bypass(tmp_path))
    return HookRuntime(HookConfigSnapshot(tmp_path, tuple(rules)), runner, **options)


def rule(index, kind="prompt", event="turn.end", **options):
    from mewcode.hooks.models import HookAction, HookRule
    controls = {name: options.pop(name) for name in ("once", "background", "condition") if name in options}
    if kind == "prompt":
        options.setdefault("text", f"prompt-{index}")
    return HookRule(event, HookAction(kind, **options), source="hooks.yaml", index=index, **controls)


@async_test
async def test_once_is_atomic_failure_counts_reset_keeps_and_restart_is_new(tmp_path):
    from test_hook_actions import script
    rules = [rule(0, once=True), rule(1, "command", command=script(tmp_path, "raise RuntimeError('secret')"), once=True)]
    runtime = make_runtime(tmp_path, rules)
    event = runtime.event("turn.end")
    await asyncio.gather(*(runtime.dispatch(event) for _ in range(20)))
    assert len(runtime.prompts.snapshot()) == 1
    assert len(runtime.once) == 2
    await runtime.dispatch(event)
    assert len(runtime.prompts.snapshot()) == 1
    another = make_runtime(tmp_path, rules)
    await another.dispatch(another.event("turn.end"))
    assert len(another.prompts.snapshot()) == 1
    await runtime.close()
    await another.close()


@async_test
async def test_failure_and_logging_failure_do_not_stop_later_rules(tmp_path, monkeypatch):
    from test_hook_actions import script
    runtime = make_runtime(tmp_path, [rule(0, "command", command=script(tmp_path, "raise RuntimeError('secret')")), rule(1)])
    def broken(*args, **kwargs):
        raise RuntimeError("logger failed")
    monkeypatch.setattr(logging.getLogger("mewcode.hooks"), "warning", broken)
    await runtime.dispatch(runtime.event("turn.end"))
    assert [item.text for item in runtime.prompts.snapshot()] == ["prompt-1"]
    await runtime.close()


@async_test
async def test_valid_deny_short_circuits_only_before(tmp_path):
    from test_hook_actions import script
    runtime = make_runtime(tmp_path, [rule(0, "command", event="tool.before", command=script(tmp_path, "print('{\"decision\":\"deny\",\"reason\":\"stop\"}')")), rule(1, event="tool.before")])
    result = await runtime.dispatch(runtime.event("tool.before"))
    assert result.decision == "deny" and result.reason == "stop"
    assert not runtime.prompts.snapshot()
    await runtime.close()


@async_test
async def test_plan_condition_and_background_ask_skips_do_not_consume_once(tmp_path):
    from mewcode.hooks.conditions import parse_condition
    from mewcode.permissions.runtime import PermissionManager
    condition = parse_condition({"all": [{"field": "mode", "match": "exact", "value": "plan"}]}, "turn.end")
    runtime = make_runtime(tmp_path, [rule(0, once=True, condition=condition), rule(1, "command", command="echo hi", once=True, background=True)])
    runtime.runner.permissions = PermissionManager(tmp_path, user_path=tmp_path / "empty.yaml")
    await runtime.dispatch(runtime.event("turn.end"))
    assert not runtime.once
    await runtime.dispatch(runtime.event("turn.end", mode="plan"))
    assert len(runtime.once) == 1
    await runtime.close()


@async_test
async def test_bounded_background_queue_does_not_consume_overflow_and_close_cleans(tmp_path):
    from test_hook_actions import script
    command = script(tmp_path, "import time;time.sleep(30)")
    runtime = make_runtime(tmp_path, [rule(i, "command", command=command, once=True, background=True) for i in range(4)], max_running=1, max_pending=1)
    await runtime.dispatch(runtime.event("turn.end"))
    assert len(runtime.once) == 2 and runtime.background_count <= 2
    await runtime.close()
    assert runtime.background_count == 0
    await runtime.dispatch(runtime.event("turn.end"))
    assert runtime.background_count == 0


@async_test
async def test_later_foreground_cancel_does_not_cancel_old_background(tmp_path):
    from test_hook_actions import script
    command = script(tmp_path, "import pathlib,time;time.sleep(.15);pathlib.Path('done').touch()")
    runtime = make_runtime(tmp_path, [rule(0, "command", command=command, background=True, once=True)])
    cancel = asyncio.Event()
    await runtime.dispatch(runtime.event("turn.end"), cancel_event=cancel)
    cancel.set()
    await runtime.drain_background()
    assert (tmp_path / "done").exists()
    await runtime.close()


@async_test
async def test_snapshot_and_session_boundaries_are_independent(tmp_path):
    runtime = make_runtime(tmp_path, [rule(0, event="session.start"), rule(1, event="session.end")])
    await runtime.start(source="resume")
    await runtime.start(source="new")
    assert len(runtime.prompts.snapshot()) == 1
    assert runtime.started
    await runtime.close()
    await runtime.close()
    assert runtime.closed and not runtime.prompts.snapshot()
