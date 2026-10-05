"""后台管理的容量、同一执行转换、结果通道及受控取消。"""

import asyncio
import importlib
from importlib.util import find_spec
import json

import pytest

from conftest import async_test
from mewcode.tools.base import ToolError


def api():
    assert find_spec("mewcode.tasks") is not None, "缺少会话后台任务管理器"
    return importlib.import_module("mewcode.tasks.manager")


@async_test
async def test_default_capacity_bounds_running_and_queue_without_starting_overflow():
    module = api()
    manager = module.TaskManager()
    parent = manager.new_parent("原目标")
    gate = asyncio.Event()
    started = []
    async def runner(record):
        started.append(record.task_id)
        await gate.wait()
        return module.TaskOutcome("model_done", "结果")
    records = [manager.submit(parent.task_id, runner, type="defined", background=True) for _ in range(36)]
    try:
        await asyncio.sleep(0)
        assert len(started) == 4
        assert sum(record.state == "queued" for record in records) == 32
        with pytest.raises(ToolError) as error:
            manager.submit(parent.task_id, runner, type="defined", background=True)
        assert error.value.code == "agent_queue_full"
        assert error.value.details["not_started"]
        gate.set()
        await asyncio.gather(*(manager.wait_terminal(record.task_id) for record in records))
        assert len(started) == 36
        assert all(record.state == "completed" for record in records)
        assert len(manager.inbox.peek(parent.task_id)) == 36
    finally:
        await manager.aclose()


@pytest.mark.parametrize("switch", ["timeout", "manual", "explicit"])
@async_test
async def test_three_background_entries_keep_one_execution_and_one_notification(switch):
    module = api()
    manager = module.TaskManager(foreground_timeout=0.01)
    parent = manager.new_parent("原目标")
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def runner(record):
        calls.append(record.task_id)
        started.set()
        await release.wait()
        return module.TaskOutcome("model_done", "真实结果")
    record = manager.submit(parent.task_id, runner, type="defined", background=switch == "explicit")
    try:
        waiter = asyncio.create_task(manager.wait(record.task_id))
        await started.wait()
        if switch == "manual":
            assert manager.detach(record.task_id)
        receipt = await waiter
        assert receipt["task_id"] == record.task_id
        assert receipt["display_mode"] == "background"
        assert receipt["state"] == "running"
        assert calls == [record.task_id]
        release.set()
        await manager.wait_terminal(record.task_id)
        notices = manager.inbox.peek(parent.task_id)
        assert [notice["task_id"] for notice in notices] == [record.task_id]
        assert "真实结果" in notices[0]["content"]
        assert calls == [record.task_id]
    finally:
        await manager.aclose()


@async_test
async def test_completed_foreground_wins_detach_race_without_duplicate_inbox():
    module = api()
    manager = module.TaskManager(foreground_timeout=0)
    parent = manager.new_parent("原目标")
    async def runner(record):
        return module.TaskOutcome("model_done", "测试失败的真实说明", evidence=({"ok":False},))
    record = manager.submit(parent.task_id, runner, type="defined")
    try:
        await manager.wait_terminal(record.task_id)
        assert not manager.detach(record.task_id)
        result = await manager.wait(record.task_id)
        assert result["state"] == "completed"
        assert result["reason"] == "model_done"
        assert "测试失败" in result["content"]
        assert manager.inbox.peek(parent.task_id) == ()
    finally:
        await manager.aclose()


@async_test
async def test_cancel_waiter_does_not_own_or_restart_child():
    module = api()
    manager = module.TaskManager()
    parent = manager.new_parent("原目标")
    gate = asyncio.Event()
    async def runner(record):
        await gate.wait()
        return module.TaskOutcome("model_done", "结果")
    record = manager.submit(parent.task_id, runner, type="defined")
    try:
        waiter = asyncio.create_task(manager.wait(record.task_id))
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        assert record.state == "running"
        assert not record.cancel.is_set()
        assert manager.detach(record.task_id)
        gate.set()
        await manager.wait_terminal(record.task_id)
        assert len(manager.inbox.peek(parent.task_id)) == 1
    finally:
        await manager.aclose()


@async_test
async def test_parent_cancel_only_stops_its_running_and_queued_children():
    module = api()
    manager = module.TaskManager(max_running=1, max_queued=4)
    first, other = manager.new_parent("第一目标"), manager.new_parent("另一目标")
    gate = asyncio.Event()
    async def runner(record):
        if record.parent_task_id == first.task_id:
            await record.cancel.wait()
            return module.TaskOutcome("cancelled", "取消，实际副作用保留")
        await gate.wait()
        return module.TaskOutcome("model_done", "另一结果")
    a = manager.submit(first.task_id, runner, type="defined", background=True)
    b = manager.submit(first.task_id, runner, type="defined", background=True)
    c = manager.submit(other.task_id, runner, type="defined", background=True)
    try:
        await manager.cancel_parent(first.task_id)
        assert (a.state, b.state) == ("cancelled", "cancelled")
        assert not first.wake_allowed
        with pytest.raises(ToolError):
            manager.submit(first.task_id, runner, type="defined")
        assert c.state in {"running", "queued"}
        assert not c.cancel.is_set()
        gate.set()
        await manager.wait_terminal(c.task_id)
        assert c.state == "completed"
    finally:
        await manager.aclose()


@async_test
async def test_failure_isolated_and_large_notification_preserves_full_local_result():
    module = api()
    manager = module.TaskManager(max_running=2, summary_limit=1024)
    parent = manager.new_parent("原目标")
    retained = []
    class Cache:
        def close(self):
            retained.append("closed")
    cache = Cache()
    manager.retain(cache)
    async def failing(record):
        raise RuntimeError("不能展示的传输秘密")
    async def good(record):
        return module.TaskOutcome("model_done", "中" * 2000, evidence=({"cache_path":"evidence"},))
    a = manager.submit(parent.task_id, failing, type="defined", background=True)
    b = manager.submit(parent.task_id, good, type="fork", background=True, role="general", source="项目定义")
    await asyncio.gather(manager.wait_terminal(a.task_id), manager.wait_terminal(b.task_id))
    assert (a.state, b.state) == ("failed", "completed")
    notice = next(item for item in manager.inbox.peek(parent.task_id) if item["task_id"] == b.task_id)
    assert notice["truncated"]
    assert len(notice["content"].encode()) <= 1024
    assert (notice["parent_task_id"], notice["type"], notice["source"]) == (parent.task_id, "fork", "项目定义")
    assert notice["usage"]["input_tokens"] is None
    full = manager.get(b.task_id).report()
    assert json.loads(full["content"])["text"] == "中" * 2000
    assert "传输秘密" not in manager.get(a.task_id).report()["content"]
    assert retained == []
    await manager.aclose()
    assert retained == ["closed"]
