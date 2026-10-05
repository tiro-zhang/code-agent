"""只读规划与显式模式切换；切换本身不执行任务。"""
import asyncio
import pytest
from conftest import ScriptedProvider, async_test, collect, permission_bypass
from mewcode.session import ChatSession
from mewcode.tools import default_registry
from mewcode.tools.base import ToolContext
from mewcode.tools.executor import ToolExecutor
from mewcode.types import ContextLimitError, Message, ProviderError, ToolCall
from test_agent_loop import answer, calls, tool


def session(root, responses, **options):
    provider = ScriptedProvider(responses)
    return ChatSession(provider, executor=ToolExecutor(default_registry(), ToolContext(root), permissions=permission_bypass(root)), **options), provider


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
        assert {d.name for d in options["tools"]} == {"read_file", "glob_files", "search_code", "load_skill", "agent"}
        assert "目标" in options["system_prompt"] and "验证" in options["system_prompt"]


@async_test
async def test_do_switches_only_and_next_explicit_task_uses_all_tools(tmp_path):
    chat, provider = session(tmp_path, [answer("计划A"), answer("计划B"),
        calls(tool("write", "write_file", '{"path":"b","content":"done"}')), answer("执行完成")])
    chat.enter_plan()
    await collect(chat.ask("任务A"))
    await collect(chat.ask("改为任务B"))
    before = tuple(chat.history)
    chat.enter_execute()
    assert tuple(chat.history) == before and len(provider.requests) == 2
    assert chat.mode == "execute" and chat.prompt_state.request_sequence == 0
    events = await collect(chat.ask("请执行任务B"))
    assert events[-1].reason == "model_done" and (tmp_path / "b").read_text() == "done"
    assert len(provider.requests[2][1]["tools"]) == 8
    assert [m.content for m in provider.requests[2][0] if m.role == "user"][-1] == "请执行任务B"
    sequence = chat.prompt_state.request_sequence
    chat.enter_execute()
    assert chat.prompt_state.request_sequence == sequence and len(provider.requests) == 4


@pytest.mark.parametrize("failure", ["stream_error", "cancelled", "max_iterations", "unknown_tool_limit"])
@async_test
async def test_do_after_unsuccessful_refinement_only_switches(tmp_path, failure):
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
    count = len(provider.requests)
    chat.enter_execute()
    assert len(provider.requests) == count and chat.mode == "execute"


@pytest.mark.parametrize("plan_mode", [False, True])
@async_test
async def test_do_without_plan_switches_without_request(tmp_path, plan_mode):
    chat, provider = session(tmp_path, [])
    if plan_mode: chat.enter_plan()
    chat.enter_execute()
    assert provider.requests == [] and chat.mode == "execute" and chat.history == []


@async_test
async def test_enter_plan_restarts_period_and_recovery_still_uses_read_tools(tmp_path):
    from test_context_summary import response
    from test_context_partition import history_for_task
    chat, provider = session(tmp_path, [answer("旧计划"), [ContextLimitError("超限")], response(), answer("新计划")])
    chat.enter_plan()
    await collect(chat.ask("计划1"))
    chat.enter_plan()
    assert chat.prompt_state.request_sequence == 0
    _, earlier = history_for_task()
    chat.history[:0] = earlier
    await collect(chat.ask("计划2"))
    assert len(provider.requests) == 4 and [len(o["tools"]) for _, o in provider.requests] == [5, 5, 0, 5]
    fresh, _ = session(tmp_path, [])
    assert fresh.mode == "execute" and fresh.history == []
    fresh.enter_execute()
    assert fresh.prompt_state.request_sequence == 0


def test_mode_archive_failure_keeps_mode_and_period(tmp_path):
    chat, _ = session(tmp_path, [])
    chat.enter_plan()
    class BrokenJournal:
        def append(self, *args):
            raise OSError("磁盘错误")
    chat.journal = BrokenJournal()
    with pytest.raises(OSError):
        chat.enter_execute()
    assert chat.mode == chat.prompt_state.mode == "plan"
