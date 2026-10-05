"""成功响应、拦截及完整后置通知的模型循环集成。"""

import json

import pytest

from conftest import ScriptedProvider, async_test, collect
from mewcode.types import Message, ProviderEvent, ToolCall
from test_hook_lifecycle import configure, observe
from test_hook_actions import script
from test_skills_session import session, response


@async_test
async def test_denial_is_paired_and_model_can_choose_alternative(tmp_path):
    command = script(tmp_path, "print('{\"decision\":\"deny\",\"reason\":\"protected\"}')")
    configure(tmp_path, [{"event": "tool.before", "if": {"all": [{"field": "tool.name", "match": "exact", "value": "write_file"}]},
                         "action": {"type": "command", "command": command}}])
    (tmp_path / "readable").write_text("alternative")
    provider = ScriptedProvider([response(calls=(ToolCall("denied", "write_file", '{"path":"target","content":"x"}'),)),
        response(calls=(ToolCall("alternative", "read_file", '{"path":"readable"}'),)), response()])
    chat = session(tmp_path, provider)
    events = observe(chat)
    shown = await collect(chat.ask("work"))
    assert shown[-1].reason == "model_done" and not (tmp_path / "target").exists()
    denied = next(item for item in chat.history if item.tool_call_id == "denied")
    assert denied.tool_result.error["code"] == "hook_denied"
    assert denied.tool_result.error["message"] == "protected"
    assert denied.tool_result.error["details"]["not_started"] is True
    assert not any(item.kind == "tool_started" and item.tool_call_id == "denied" for item in shown)
    assert any(item.tool_call_id == "denied" for item in provider.requests[1][0])
    assert sum(item["event"] == "tool.after" and item["tool"]["call_id"] == "denied" for item in events) == 1
    assert len(provider.requests) == 3
    await chat.aclose()


@pytest.mark.parametrize("name,arguments", [("unknown", "{}"), ("read_file", "{}"), ("write_file", '{"path":"target","content":"x"}')])
@async_test
async def test_unknown_invalid_and_plan_denied_have_after_only(tmp_path, name, arguments):
    chat = session(tmp_path, ScriptedProvider([response(calls=(ToolCall("id", name, arguments),)), response()]))
    chat.enter_plan()
    events = observe(chat)
    await collect(chat.ask("work"))
    tools = [item for item in events if item["event"].startswith("tool.")]
    assert len(tools) == 1 and tools[0]["event"] == "tool.after"
    assert tools[0]["tool"]["call_id"] == "id"
    assert tools[0]['tool']['result']['error']['details']['not_started'] is True
    if name == "read_file":
        assert "arguments" not in tools[0]["tool"]
    await chat.aclose()


@async_test
async def test_partial_stream_does_not_trigger_complete_response(tmp_path):
    chat = session(tmp_path, ScriptedProvider([[ProviderEvent("text_delta", text="partial"), RuntimeError("broken")]]))
    events = observe(chat)
    await collect(chat.ask("work"))
    assert sum(item["event"] == "message.before_request" for item in events) == 1
    assert not any(item["event"] == "message.after_response" for item in events)
    await chat.aclose()


@async_test
async def test_before_failure_does_not_replace_tool_result(tmp_path):
    configure(tmp_path, [{"event": "tool.before", "action": {"type": "command", "command": script(tmp_path, "import pathlib;pathlib.Path('checked').touch();raise RuntimeError('private')")}}])
    provider = ScriptedProvider([response(calls=(ToolCall("id", "write_file", '{"path":"target","content":"x"}'),)), response()])
    chat = session(tmp_path, provider)
    await collect(chat.ask("work"))
    assert (tmp_path / "checked").exists()
    assert (tmp_path / "target").read_text() == "x"
    assert next(item for item in chat.history if item.tool_call_id == "id").tool_result.ok
    await chat.aclose()


@async_test
async def test_snapshot_failure_is_isolated_before_request_and_tool(tmp_path):
    configure(tmp_path, [{'event': 'tool.before', 'action': {'type': 'prompt', 'text': 'unused'}}])
    chat = session(tmp_path, ScriptedProvider([response(calls=(ToolCall('id', 'write_file', '{"path":"target","content":"x"}'),)), response()]))
    event = chat.hooks.event
    def broken(name, **fields):
        if name in {'message.before_request', 'tool.before'}:
            raise ValueError('private snapshot')
        return event(name, **fields)
    chat.hooks.event = broken
    result = await collect(chat.ask('work'))
    assert result[-1].reason == 'model_done' and (tmp_path / 'target').read_text() == 'x'
    await chat.aclose()


@async_test
async def test_tool_snapshot_builder_failure_does_not_stop_result_or_followup(tmp_path, monkeypatch):
    chat = session(tmp_path, ScriptedProvider([response(calls=(ToolCall('id', 'write_file', '{"path":"target","content":"x"}'),)), response()]))
    def broken(*args, **options):
        raise RuntimeError('snapshot failed')
    monkeypatch.setattr('mewcode.hooks.events.tool_snapshot', broken)
    result = await collect(chat.ask('work'))
    assert result[-1].reason == 'model_done' and (tmp_path / 'target').read_text() == 'x'
    assert next(item for item in chat.history if item.tool_call_id == 'id').tool_result.ok
    await chat.aclose()


@async_test
async def test_cancel_foreground_hook_reaps_then_reports_all_calls_once(tmp_path):
    import asyncio
    from test_tool_execution import wait_file
    configure(tmp_path, [{'event': 'tool.before', 'action': {'type': 'command', 'command': script(tmp_path,
        "import pathlib,time;pathlib.Path('checking').touch();time.sleep(30);pathlib.Path('leaked').touch()")}}])
    chat = session(tmp_path, ScriptedProvider([response(calls=(
        ToolCall('one', 'write_file', '{"path":"one","content":"x"}'),
        ToolCall('two', 'write_file', '{"path":"two","content":"x"}')))]))
    events = observe(chat)
    cancel = asyncio.Event()
    task = asyncio.create_task(collect(chat.ask('work', cancel_event=cancel)))
    await wait_file(tmp_path / 'checking')
    task.cancel()
    task.cancel()
    result = await asyncio.wait_for(task, 3)
    assert result[-1].reason == 'cancelled' and len(chat.provider.requests) == 1
    assert not any((tmp_path / name).exists() for name in ('one', 'two', 'leaked'))
    after = [item for item in events if item['event'] == 'tool.after']
    assert len(after) == 2 and all(item['tool']['result']['error']['details']['not_started'] for item in after)
    assert all(item['tool']['result']['error']['code'] == 'cancelled' for item in after)
    await chat.aclose()
