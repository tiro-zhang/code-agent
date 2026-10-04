"""真实调度器与脚本化供应商共同验证拒绝恢复和审批生命周期。"""

import asyncio
import multiprocessing

from conftest import ScriptedProvider, async_test, collect
from mewcode.agent import Agent
from mewcode.tools.scheduler import ToolScheduler
from mewcode.types import ToolCall
from test_agent_loop import answer, calls
from test_permission_execution import executor
from test_permission_runtime import policy, rule


@async_test
async def test_denied_call_pairs_result_and_agent_uses_allowed_alternative(tmp_path):
    policy(tmp_path, [rule("deny", "write_file(blocked)", "exact"),
                      rule("allow", "write_file(allowed)", "exact")])
    provider = ScriptedProvider([
        calls(ToolCall("bad", "write_file", '{"path":"blocked","content":"x"}')),
        calls(ToolCall("good", "write_file", '{"path":"allowed","content":"ok"}')),
        answer("替代操作完成")])
    history = []
    events = await collect(Agent(provider, executor(tmp_path)).run("尝试替代", history=history, mode="execute"))
    assert not (tmp_path / "blocked").exists()
    assert (tmp_path / "allowed").read_text() == "ok"
    assert len(provider.requests) == 3 and events[-1].reason == "model_done"
    assert [e.tool_call_id for e in events if e.kind == "tool_started"] == ["good"]
    assert [m.tool_call_id for m in history if m.role == "tool"] == ["bad", "good"]
    feedback = [m for m in provider.requests[1][0] if m.role == "tool"]
    assert feedback[0].tool_result.error["code"] == "permission_denied"


@async_test
async def test_denied_read_does_not_abort_parallel_batch(tmp_path):
    (tmp_path / "a").write_text("hidden")
    (tmp_path / "b").write_text("visible")
    policy(tmp_path, [rule("deny", "read_file(a)", "exact"), rule("allow", "read_file(b)", "exact")])
    scheduler = ToolScheduler(executor(tmp_path))
    events = await collect(scheduler.run([
        ToolCall("a", "read_file", '{"path":"a"}'), ToolCall("b", "read_file", '{"path":"b"}')],
        allowed_tools=None, run_id="test", iteration=1, mode="execute"))
    assert scheduler.results[0].error["code"] == "permission_denied"
    assert scheduler.results[1].data["content"] == "visible"
    assert [e.tool_call_id for e in events if e.kind == "tool_started"] == ["b"]


@async_test
async def test_approval_wait_is_outside_execution_timeout(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    async def approve(request, cancel):
        entered.set()
        await release.wait()
        return "once"
    runner = executor(tmp_path, responder=approve)
    # spawn 启动也计入执行预算，留出启动余量；审批等待仍明确超过预算。
    runner.timeout = 2
    task = asyncio.create_task(runner.execute("write_file", '{"path":"a","content":"ok"}'))
    await entered.wait()
    await asyncio.sleep(runner.timeout + 0.1)
    assert not task.done() and not (tmp_path / "a").exists()
    release.set()
    assert (await task).ok
    assert (tmp_path / "a").read_text() == "ok"


@async_test
async def test_closing_consumer_during_queued_approval_cleans_waiters(tmp_path):
    entered = asyncio.Event()
    async def approve(request, cancel):
        entered.set()
        await asyncio.Event().wait()
    scheduler = ToolScheduler(executor(tmp_path, responder=approve))
    batch = scheduler.run([ToolCall(str(i), "read_file", '{"path":"a"}') for i in range(6)],
                          allowed_tools=None, run_id="test", iteration=1, mode="execute")
    for _ in range(6):
        assert (await anext(batch)).kind == "tool_call"
    assert (await anext(batch)).kind == "permission_requested"
    await entered.wait()
    await asyncio.wait_for(batch.aclose(), 2)
    assert len(scheduler.results) == 6
    assert all(item.error["code"] == "cancelled" for item in scheduler.results)
    assert multiprocessing.active_children() == []
