"""主会话通过真实工具分流，结果原调用配对。"""

import asyncio
from conftest import ScriptedProvider, async_test, collect
from mewcode.types import ToolCall
from test_skills_session import session, response


@async_test
async def test_defined_route_returns_child_result_without_streaming_child_body(tmp_path):
    provider = ScriptedProvider([response(calls=(ToolCall("a", "agent", '{"type":"defined","role":"explore","prompt":"检查目录"}'),)), response("子私有正文"), response("主最终答复")])
    chat = session(tmp_path, provider)
    try:
        events = await collect(chat.ask("委派"))
        result = next(event.result for event in events if event.kind == "tool_result")
        assert result.ok and result.data["state"] == "completed"
        assert "子私有正文" in result.data["content"]
        assert not any(event.text == "子私有正文" for event in events)
        assert len(chat.tasks.records) == 1
        assert len([message for message in chat.history if message.tool_call_id == "a"]) == 1
        assert chat.tasks.inbox.peek(result.data["parent_task_id"]) == ()
        assert "explore:" in provider.requests[0][0][-1].content
        assert "固定角色" not in provider.requests[0][1]["system_prompt"]
    finally:
        await chat.aclose()


@async_test
async def test_unknown_role_is_not_started_and_following_parent_request_runs(tmp_path):
    provider = ScriptedProvider([response(calls=(ToolCall("a", "agent", '{"type":"defined","role":"missing","prompt":"检查"}'),)), response("已处理错误")])
    chat = session(tmp_path, provider)
    try:
        events = await collect(chat.ask("委派"))
        result = next(event.result for event in events if event.kind == "tool_result")
        assert result.error["code"] == "agent_role_not_found"
        assert chat.tasks.records == {}
        assert len(provider.requests) == 2
    finally:
        await chat.aclose()


@async_test
async def test_background_receipt_then_resume_original_budget_once(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    async def child():
        started.set()
        await release.wait()
        for event in response("后台子结果"):
            yield event
    provider = ScriptedProvider([response(calls=(ToolCall("a", "agent", '{"type":"defined","role":"explore","prompt":"检查","background":true}'),)), child, response("主等待"), response("主合并结果")])
    chat = session(tmp_path, provider, max_iterations=3)
    try:
        first = await collect(chat.ask("原始目标"))
        record = next(iter(chat.tasks.records.values()))
        parent = chat.tasks.parent(record.parent_task_id)
        assert not parent.finished and parent.budget.used == 2
        await started.wait()
        release.set()
        await chat.tasks.wait_terminal(record.task_id)
        second = await collect(chat.resume_parent(parent.task_id))
        assert parent.budget.used == 3 and parent.finished
        assert first[-1].run_id != second[-1].run_id
        assert "后台子结果" in provider.requests[-1][0][-1].content
        assert chat.tasks.inbox.peek(parent.task_id) == ()
        assert len([m for m in chat.history if m.tool_call_id == "a"]) == 1
        assert len([event for event in first + second if event.kind == "task_finished"]) == 1
    finally:
        await chat.aclose()
