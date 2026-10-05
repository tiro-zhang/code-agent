"""会话及顶层任务 Hook 无需终端消费者。"""

import asyncio

import pytest
import yaml

from conftest import ScriptedProvider, async_test, collect
from mewcode.types import Message, ProviderEvent, ToolCall
from test_skills_session import session, response


def configure(tmp_path, rules):
    path = tmp_path / ".mewcode/hooks.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump({"version": 1, "hooks": rules}, allow_unicode=True))


def observe(chat):
    events = []
    dispatch = chat.hooks.dispatch
    async def capture(event, **options):
        events.append(event.data)
        return await dispatch(event, **options)
    chat.hooks.dispatch = capture
    return events


@async_test
async def test_session_and_turn_without_ui_reset_keeps_once(tmp_path):
    configure(tmp_path, [{"event": "session.start", "once": True, "action": {"type": "prompt", "text": "startup"}}])
    chat = session(tmp_path, ScriptedProvider([response(), response()]))
    events = observe(chat)
    await collect(chat.ask("first"))
    chat.reset()
    await collect(chat.ask("second"))
    await chat.aclose()
    await chat.aclose()
    names = [item["event"] for item in events]
    assert names.count("session.start") == names.count("session.end") == 1
    assert names.count("turn.start") == names.count("turn.end") == names.count("message.user") == 2
    assert len(chat.hooks.once) == 1
    assert {item["session_id"] for item in events} == {chat.hooks.session_id}
    assert chat.hooks.closed


@pytest.mark.parametrize("kind,want", [("normal", "model_done"), ("error", "stream_error"), ("budget", "max_iterations"), ("cancel", "cancelled")])
@async_test
async def test_turn_end_carries_actual_reason(tmp_path, kind, want):
    async def cancel_stream():
        cancel.set()
        yield ProviderEvent("text_delta", text="partial")
    cancel = asyncio.Event()
    provider = ScriptedProvider([[RuntimeError("private")]] if kind == "error" else
        [response(calls=(ToolCall("r", "read_file", '{"path":"a"}'),))] if kind == "budget" else
        [cancel_stream] if kind == "cancel" else [response()])
    (tmp_path / "a").write_text("a")
    chat = session(tmp_path, provider, max_iterations=1)
    events = observe(chat)
    await collect(chat.ask("work", cancel_event=cancel))
    ended = [item for item in events if item["event"] == "turn.end"]
    assert len(ended) == 1 and ended[0]["reason"] == want
    await chat.aclose()


@async_test
async def test_mode_changed_only_after_commit_and_no_task(tmp_path):
    chat = session(tmp_path, ScriptedProvider([]))
    events = observe(chat)
    await chat.set_mode("plan")
    await chat.set_mode("plan")
    await chat.set_mode("execute")
    assert [item["mode_change"] for item in events if item["event"] == "mode.changed"] == [
        {"from": "execute", "to": "plan"}, {"from": "plan", "to": "execute"}]
    assert not any(item["event"].startswith(("turn.", "message.")) for item in events)
    await chat.aclose()


@async_test
async def test_manual_compaction_noop_has_pair_without_work_request(tmp_path):
    chat = session(tmp_path, ScriptedProvider([]))
    events = observe(chat)
    await collect(chat.compact())
    pairs = [item for item in events if item["event"].startswith("context.")]
    assert [item["event"] for item in pairs] == ["context.before_compact", "context.after_compact"]
    assert pairs[-1]["context"]["purpose"] == "manual"
    assert pairs[-1]["context"]["result"] == "noop"
    assert not any(item["event"].startswith("message.") for item in events)
    await chat.aclose()


@async_test
async def test_already_cancelled_manual_compaction_still_has_cancelled_pair(tmp_path):
    chat = session(tmp_path, ScriptedProvider([]))
    events = observe(chat)
    cancel = asyncio.Event()
    cancel.set()
    await collect(chat.compact(cancel_event=cancel))
    pairs = [item for item in events if item['event'].startswith('context.')]
    assert len(pairs) == 2 and pairs[-1]['context']['result'] == 'cancelled'
    assert not chat.provider.requests
    await chat.aclose()


@async_test
async def test_explicit_resume_starts_fresh_runtime_once_and_closes_before_journal(tmp_path):
    from test_skills_persistence import CONFIG
    configure(tmp_path, [{'event': 'session.start', 'once': True, 'action': {'type': 'prompt', 'text': 'startup'}}])
    original = session(tmp_path, ScriptedProvider([response()]), config=CONFIG, persistent=True)
    await collect(original.ask('first'))
    identity, runtime_id = original.session_id, original.hooks.session_id
    await original.aclose()
    restored = session(tmp_path, ScriptedProvider([]), resume=identity, config=CONFIG)
    events = observe(restored)
    close = restored.journal.close
    def close_journal():
        assert restored.hooks.closed
        close()
    restored.journal.close = close_journal
    await restored.start()
    await restored.start()
    start = next(item for item in events if item['event'] == 'session.start')
    assert start['source'] == 'resume' and start['archive_session_id'] == identity
    assert start['session_id'] != runtime_id and len(restored.hooks.once) == 1
    assert len(restored.hooks.prompts.snapshot()) == 1 and not restored.provider.requests
    await restored.aclose()


@async_test
async def test_switching_plan_reaps_background_before_mode_commit(tmp_path):
    from test_hook_actions import script
    from test_tool_execution import wait_file
    configure(tmp_path, [{'event': 'session.start', 'async': True, 'action': {'type': 'command',
        'command': script(tmp_path, "import pathlib,time;pathlib.Path('started').touch();time.sleep(30);pathlib.Path('leaked').touch()")}}])
    chat = session(tmp_path, ScriptedProvider([]))
    await chat.start()
    await wait_file(tmp_path / 'started')
    await asyncio.wait_for(chat.set_mode('plan'), 2)
    assert chat.mode == 'plan' and chat.hooks.background_count == 0
    assert not (tmp_path / 'leaked').exists()
    await chat.aclose()


@async_test
async def test_concurrent_close_waits_for_same_cleanup_before_session_handles(tmp_path):
    from test_hook_actions import script
    from test_skills_persistence import CONFIG
    configure(tmp_path, [{'event': 'session.end', 'action': {'type': 'command',
        'command': script(tmp_path, "import time;time.sleep(.1)")}}])
    chat = session(tmp_path, ScriptedProvider([]), persistent=True, config=CONFIG)
    events = observe(chat)
    await chat.start()
    close = chat.journal.close
    def close_journal():
        assert chat.hooks.closed
        close()
    chat.journal.close = close_journal
    await asyncio.gather(chat.aclose(), chat.aclose())
    assert sum(item['event'] == 'session.end' for item in events) == 1


@async_test
async def test_task_cancel_during_turn_start_still_closes_root_turn(tmp_path):
    from test_hook_actions import script
    from test_tool_execution import wait_file
    configure(tmp_path, [{'event': 'turn.start', 'action': {'type': 'command',
        'command': script(tmp_path, "import pathlib,time;pathlib.Path('starting').touch();time.sleep(30)")}}])
    chat = session(tmp_path, ScriptedProvider([]))
    events = observe(chat)
    task = asyncio.create_task(collect(chat.ask('work')))
    try:
        await wait_file(tmp_path / 'starting')
        task.cancel()
        result = await asyncio.wait_for(task, 3)
        assert result[-1].reason == 'cancelled' and not chat.provider.requests
        assert sum(item['event'] == 'turn.start' for item in events) == sum(item['event'] == 'turn.end' for item in events) == 1
        assert chat._hook_turn is None
    finally:
        await chat.aclose()


@async_test
async def test_task_cancel_during_compaction_before_emits_cancelled_after(tmp_path):
    from test_hook_actions import script
    from test_tool_execution import wait_file
    configure(tmp_path, [{'event': 'context.before_compact', 'action': {'type': 'command',
        'command': script(tmp_path, "import pathlib,time;pathlib.Path('compacting').touch();time.sleep(20)")}}])
    chat = session(tmp_path, ScriptedProvider([]))
    events = observe(chat)
    cancel = asyncio.Event()
    task = asyncio.create_task(chat.context.compact(chat.provider, [], None, (), '',
        Message('context', ''), cancel, hooks=chat.hooks))
    try:
        await wait_file(tmp_path / 'compacting')
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        pairs = [item for item in events if item['event'].startswith('context.')]
        assert len(pairs) == 2 and pairs[-1]['context']['result'] == 'cancelled'
        assert not chat.provider.requests
    finally:
        await chat.aclose()
