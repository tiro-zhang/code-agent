"""会话历史、上下文恢复与统一请求预算。"""
import pytest
from conftest import ScriptedProvider, async_test, collect
from mewcode.session import ChatSession
from mewcode.types import ContextLimitError, Message, ProviderEvent
from test_agent_loop import answer, calls, tool


@async_test
async def test_previous_answer_is_sent_and_thinking_is_not_answer():
    provider = ScriptedProvider([[ProviderEvent("thinking_delta", "思考"), *answer("答复")], answer("第二答")])
    session = ChatSession(provider)
    await collect(session.ask("第一问"))
    events = await collect(session.ask("第二问"))
    assert events[-1].reason == "model_done"
    assert tuple(m for m in provider.requests[1][0] if m.role != "context") == (Message("user", "第一问"), Message("assistant", "答复"), Message("user", "第二问"))
    assert "思考" not in repr(session.history)


@pytest.mark.parametrize("response", [[ProviderEvent("text_delta", "残缺")], answer("")])
@async_test
async def test_incomplete_or_empty_first_response_does_not_commit(response):
    session = ChatSession(ScriptedProvider([response]))
    events = await collect(session.ask("问"))
    assert events[-1].reason == "stream_error" and session.history == []


@async_test
async def test_context_recovery_drops_whole_turns_in_one_two_four_steps():
    overflow = [ContextLimitError("上下文超限")]
    provider = ScriptedProvider([overflow, overflow, overflow, answer()])
    session = ChatSession(provider)
    session.history = [m for i in range(8) for m in [Message("user", f"旧{i}"), Message("assistant", "旧答")]]
    events = await collect(session.ask("新任务"))
    assert [e.text.split("轮")[0].strip()[-1] for e in events if e.kind == "history_trimmed"] == ["1", "2", "4"]
    assert events[-1].iteration == 4 and events[-1].reason == "model_done"
    assert session.history[0].content == "旧7"


@pytest.mark.parametrize("prior_text", [False, True])
@async_test
async def test_context_error_without_older_turn_or_after_text_does_not_retry(prior_text):
    response = ([ProviderEvent("text_delta", "部分")] if prior_text else []) + [ContextLimitError("上下文超限")]
    provider = ScriptedProvider([response])
    session = ChatSession(provider)
    if prior_text: session.history = [Message("user", "旧"), Message("assistant", "旧答")]
    before = tuple(session.history)
    events = await collect(session.ask("新"))
    assert events[-1].reason == "stream_error" and len(provider.requests) == 1
    assert tuple(session.history) == before


@async_test
async def test_context_retry_uses_budget_and_does_not_add_summary():
    provider = ScriptedProvider([[ContextLimitError("超限")], [ContextLimitError("超限")], answer("不可请求")])
    session = ChatSession(provider, max_iterations=2)
    session.history = [m for i in range(4) for m in [Message("user", f"旧{i}"), Message("assistant", "旧答")]]
    events = await collect(session.ask("新"))
    assert events[-1].reason == "max_iterations" and events[-1].iteration == 2
    assert len(provider.requests) == 2
    assert len([e for e in events if e.kind == "usage"]) == 2


@async_test
async def test_recovery_preserves_current_multi_stage_task_and_unknown_count():
    unknown = calls(tool("u", "absent", "{}"))
    provider = ScriptedProvider([unknown, unknown, [ContextLimitError("超限")], unknown])
    session = ChatSession(provider)
    session.history = [Message("user", "旧"), Message("assistant", "旧答")]
    events = await collect(session.ask("当前任务"))
    assert events[-1].reason == "unknown_tool_limit" and events[-1].iteration == 4
    assert [m.role for m in provider.requests[-1][0] if m.role != "context"] == ["user", "assistant", "tool", "assistant", "tool"]
    assert session.history[0].content == "当前任务"
