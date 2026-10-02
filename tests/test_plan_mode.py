"""只读规划、修订失效和最新计划的一次执行。"""
import asyncio
import pytest
from conftest import ScriptedProvider, async_test, collect
from mewcode.session import ChatSession
from mewcode.tools import default_registry
from mewcode.tools.base import ToolContext
from mewcode.tools.executor import ToolExecutor
from mewcode.types import ContextLimitError, Message, ProviderError, ToolCall
from test_agent_loop import answer, calls, tool


def session(root, responses, **options):
    provider = ScriptedProvider(responses)
    return ChatSession(provider, executor=ToolExecutor(default_registry(), ToolContext(root)), **options), provider


@async_test
async def test_bare_plan_preserves_history_and_makes_no_request(tmp_path):
    chat, provider = session(tmp_path, [answer("旧答")])
    assert chat.mode == "execute"
    await collect(chat.ask("旧问"))
    before = tuple(chat.history)
    chat.enter_plan()
    assert chat.mode == "plan" and tuple(chat.history) == before
    assert len(provider.requests) == 1


@async_test
async def test_plan_filters_api_and_execution_for_all_side_effect_tools(tmp_path):
    forbidden = [ToolCall("w", "write_file", '{"path":"x","content":"bad"}'),
                 ToolCall("e", "edit_file", '{"path":"a","old_text":"old","new_text":"bad"}'),
                 ToolCall("s", "execute_command", '{"command":"touch shell"}')]
    (tmp_path / "a").write_text("old")
    chat, provider = session(tmp_path, [calls(*forbidden), answer("目标、步骤、验证"), answer("修订计划")])
    chat.enter_plan()
    events = await collect(chat.ask("规划"))
    assert events[-1].reason == "model_done" and not any(e.kind == "tool_started" for e in events)
    assert [m.tool_result.error["code"] for m in chat.history if m.role == "tool"] == ["tool_not_allowed"] * 3
    await collect(chat.ask("修订"))
    assert chat.mode == "plan" and not (tmp_path / "x").exists() and not (tmp_path / "shell").exists()
    assert (tmp_path / "a").read_text() == "old"
    for _, options in provider.requests:
        assert {d.name for d in options["tools"]} == {"read_file", "glob_files", "search_code"}
        assert "目标" in options["system_prompt"] and "验证" in options["system_prompt"]


@async_test
async def test_do_executes_latest_plan_directly_with_new_budget_and_all_tools(tmp_path):
    chat, provider = session(tmp_path, [calls(tool()), answer("目标A、步骤A、验证A"),
        answer("目标B、步骤B、验证B"),
        calls(tool("write", "write_file", '{"path":"b","content":"done"}')), answer("执行完成"), answer("普通答复")], max_iterations=2)
    chat.enter_plan()
    await collect(chat.ask("任务A"))
    await collect(chat.ask("改为任务B"))
    events = await collect(chat.execute_plan())
    assert events[-1].reason == "model_done" and events[-1].iteration == 2
    assert (tmp_path / "b").read_text() == "done" and chat.mode == "execute"
    messages, options = provider.requests[3]
    execution = [m for m in messages if m.role == "user"][-1]
    assert "目标B、步骤B、验证B" in execution.content and "任务B" in execution.content
    assert any(m.content == "任务A" for m in messages[:-1])
    assert len(options["tools"]) == 6
    from mewcode.session import PlanStateError
    with pytest.raises(PlanStateError): await collect(chat.execute_plan())
    await collect(chat.ask("下一问"))
    assert len(provider.requests[-1][1]["tools"]) == 6


@pytest.mark.parametrize("failure", ["stream_error", "cancelled", "max_iterations", "unknown_tool_limit"])
@async_test
async def test_unsuccessful_refinement_invalidates_previous_plan(tmp_path, failure):
    responses = [answer("旧的有效计划")]
    cancel = asyncio.Event()
    if failure == "stream_error": responses += [[ProviderError("断流")]]
    elif failure == "max_iterations": responses += [calls(tool())] * 3
    elif failure == "unknown_tool_limit": responses += [calls(tool("u", "absent", "{}"))] * 3
    else: cancel.set()
    chat, provider = session(tmp_path, responses, max_iterations=3)
    chat.enter_plan()
    await collect(chat.ask("初次计划"))
    events = await collect(chat.ask("修订", cancel_event=cancel))
    assert events[-1].reason == failure
    from mewcode.session import PlanStateError
    count = len(provider.requests)
    with pytest.raises(PlanStateError): await collect(chat.execute_plan())
    assert len(provider.requests) == count and chat.mode == "plan"


@pytest.mark.parametrize("plan_mode", [False, True])
@async_test
async def test_do_without_plan_is_safe_and_does_not_switch_mode(tmp_path, plan_mode):
    from mewcode.session import PlanStateError
    chat, provider = session(tmp_path, [])
    if plan_mode: chat.enter_plan()
    before = chat.mode
    with pytest.raises(PlanStateError, match="计划"): await collect(chat.execute_plan())
    assert provider.requests == [] and chat.mode == before and chat.history == []


@async_test
async def test_enter_plan_clears_pending_and_recovery_still_uses_read_tools(tmp_path):
    chat, provider = session(tmp_path, [answer("旧计划"), [ContextLimitError("超限")], answer("新计划")])
    chat.enter_plan()
    await collect(chat.ask("计划1"))
    chat.enter_plan()
    from mewcode.session import PlanStateError
    with pytest.raises(PlanStateError): await collect(chat.execute_plan())
    await collect(chat.ask("计划2"))
    assert len(provider.requests) == 3 and all(len(o["tools"]) == 3 for _, o in provider.requests)
    fresh, _ = session(tmp_path, [])
    assert fresh.mode == "execute" and fresh.history == []
    with pytest.raises(PlanStateError): await collect(fresh.execute_plan())
