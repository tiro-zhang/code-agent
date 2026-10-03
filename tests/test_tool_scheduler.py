"""用进程握手验证并发边界、结果顺序和取消收尾。"""

import asyncio
import json
import time
import pytest
from conftest import async_test, collect, permission_bypass
from mewcode.tools import default_registry
from mewcode.tools.base import ToolContext, ToolResult, object_schema
from mewcode.tools.executor import ToolExecutor
from mewcode.types import ToolCall
from test_tool_execution import wait_file


class Probe:
    name = "probe"
    description = "握手读取"
    read_only = True
    input_schema = object_schema({"slot": {"type": "integer"}}, ["slot"])

    def execute(self, arguments, context):
        slot = arguments["slot"]
        (context.root / f"started-{slot}").touch()
        while not (context.root / f"release-{slot}").exists():
            time.sleep(0.01)
        return ToolResult.success({"slot": slot})


def executor(root):
    registry = default_registry()
    registry.register(Probe())
    return ToolExecutor(registry, ToolContext(root), timeout=15, permissions=permission_bypass(root))


def call(slot):
    return ToolCall(str(slot), "probe", json.dumps({"slot": slot}))


def run(scheduler, calls, cancel=None, allowed=None):
    return scheduler.run(calls, allowed_tools=allowed, run_id="run", iteration=1,
                         mode="execute", cancel_event=cancel)


@async_test
async def test_four_active_workers_and_results_in_completion_order(tmp_path):
    from mewcode.tools.scheduler import ToolScheduler
    scheduler = ToolScheduler(executor(tmp_path))
    events = []
    async def consume():
        async for event in run(scheduler, [call(i) for i in range(5)]):
            events.append(event)
    task = asyncio.create_task(consume())
    try:
        await asyncio.gather(*(wait_file(tmp_path / f"started-{i}") for i in range(4)))
        assert not (tmp_path / "started-4").exists()
        (tmp_path / "release-3").touch()
        await wait_file(tmp_path / "started-4")
        (tmp_path / "release-4").touch()
        while len([e for e in events if e.kind == "tool_result"]) < 2:
            await asyncio.sleep(0.01)
        for i in range(3): (tmp_path / f"release-{i}").touch()
        await asyncio.wait_for(task, 10)
    finally:
        for i in range(5): (tmp_path / f"release-{i}").touch()
        await task
    assert [e.tool_call_id for e in events if e.kind == "tool_result"][:2] == ["3", "4"]
    assert [r.data["slot"] for r in scheduler.results] == list(range(5))
    assert len({e.tool_call_id for e in events if e.kind == "tool_result"}) == 5


@async_test
async def test_read_edit_read_and_shell_keep_model_order(tmp_path):
    from mewcode.tools.scheduler import ToolScheduler
    (tmp_path / "a").write_text("before")
    calls = [ToolCall("r1", "read_file", '{"path":"a"}'),
             ToolCall("e", "edit_file", '{"path":"a","old_text":"before","new_text":"after"}'),
             ToolCall("r2", "read_file", '{"path":"a"}'),
             ToolCall("s", "execute_command", '{"command":"cat a"}'),
             ToolCall("r3", "read_file", '{"path":"a"}')]
    scheduler = ToolScheduler(executor(tmp_path))
    events = await collect(run(scheduler, calls))
    assert scheduler.results[0].data["content"] == "before"
    assert scheduler.results[2].data["content"] == "after"
    assert scheduler.results[3].data["stdout"] == "after"
    assert [e.tool_call_id for e in events if e.kind == "tool_started"] == ["r1", "e", "r2", "s", "r3"]


@async_test
async def test_validation_and_tool_error_do_not_block_remaining_calls(tmp_path):
    from mewcode.tools.scheduler import ToolScheduler
    ex = executor(tmp_path)
    calls = [ToolCall("u", "absent", "{}"), ToolCall("b", "write_file", "{"),
             ToolCall("i", "read_file", "{"), ToolCall("f", "read_file", '{"path":"missing"}'),
             ToolCall("g", "glob_files", '{"pattern":"*"}')]
    scheduler = ToolScheduler(ex)
    events = await collect(run(scheduler, calls, allowed=ex.registry.names(read_only=True)))
    assert [r.error["code"] if r.error else "ok" for r in scheduler.results] == [
        "unknown_tool", "tool_not_allowed", "invalid_arguments", "file_not_found", "ok"]
    assert [e.tool_call_id for e in events if e.kind == "tool_started"] == ["f", "g"]
    assert [e.tool_call_id for e in events if e.kind == "tool_call"] == ["u", "b", "i", "f", "g"]
    assert len([e for e in events if e.kind == "tool_result"]) == 5


@async_test
async def test_cancel_fills_queued_results_and_handles_backpressure(tmp_path):
    from mewcode.tools.scheduler import ToolScheduler
    cancel = asyncio.Event()
    scheduler = ToolScheduler(executor(tmp_path))
    calls = [call(i) for i in range(6)] + [ToolCall("write", "write_file", '{"path":"forbidden","content":"x"}')]
    stream = run(scheduler, calls, cancel)
    events = [await anext(stream) for _ in calls]
    events.append(await anext(stream))
    await asyncio.gather(*(wait_file(tmp_path / f"started-{i}") for i in range(4)))
    cancel.set()
    # 消费者暂时不拉取；活动进程也必须响应取消。
    await asyncio.sleep(0.4)
    events += await asyncio.wait_for(collect(stream), 10)
    assert len([e for e in events if e.kind == "tool_result"]) == len(calls)
    assert all(r.error["code"] == "cancelled" for r in scheduler.results)
    assert all(r.error["details"]["not_started"] for r in scheduler.results[4:])
    assert not (tmp_path / "forbidden").exists()
    assert not (tmp_path / "started-4").exists()
    import multiprocessing
    assert multiprocessing.active_children() == []
