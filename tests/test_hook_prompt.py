"""注入参与预算，预览及维护不消费，失败请求按尝试消费。"""

import asyncio

from conftest import ScriptedProvider, async_test, collect
from mewcode.types import Message, ProviderEvent
from test_hook_lifecycle import configure, observe
from test_skills_session import session, response


@async_test
async def test_static_prompt_is_next_work_context_and_system_is_fixed(tmp_path):
    configure(tmp_path, [{"event": "session.start", "action": {"type": "prompt", "text": "hook-marker"}}])
    provider = ScriptedProvider([response(), response()])
    chat = session(tmp_path, provider)
    await chat.start()
    chat.context_status()
    await collect(chat.compact())
    assert len(chat.hooks.prompts.snapshot()) == 1 and not provider.requests
    await collect(chat.ask("first"))
    await collect(chat.ask("second"))
    first, second = provider.requests
    assert "hook-marker" in first[0][-1].content
    assert "hook-marker" not in second[0][-1].content
    assert first[1]["system_prompt"] == second[1]["system_prompt"]
    assert "hook-marker" not in first[1]["system_prompt"]
    assert any(item.role == "context" and "hook-marker" in item.content for item in chat.history)
    await chat.aclose()


@async_test
async def test_cancel_before_start_preserves_prompt_failure_after_start_consumes(tmp_path):
    configure(tmp_path, [{"event": "session.start", "action": {"type": "prompt", "text": "pending"}}])
    chat = session(tmp_path, ScriptedProvider([[RuntimeError("failed")]]))
    await chat.start()
    cancel = asyncio.Event()
    source = chat.ask("work", cancel_event=cancel)
    async for item in source:
        if item.kind == "progress" and item.phase == "model":
            cancel.set()
    assert len(chat.hooks.prompts.snapshot()) == 1
    await collect(chat.ask("retry"))
    assert not chat.hooks.prompts.snapshot() and chat.prompt_state.request_sequence == 1
    await chat.aclose()


@async_test
async def test_large_injection_blocks_before_provider_request(tmp_path):
    from dataclasses import replace
    from test_skills_persistence import CONFIG
    configure(tmp_path, [{"event": "message.before_request", "action": {"type": "prompt", "text": "文" * 20000}}])
    provider = ScriptedProvider([])
    chat = session(tmp_path, provider, config=replace(CONFIG, context_window=20000, max_output_tokens=1000))
    events = observe(chat)
    result = await collect(chat.ask("work"))
    assert result[-1].reason == "context_blocked" and not provider.requests
    assert sum(item["event"] == "message.before_request" for item in events) == 1
    assert len(chat.agent.hooks.prompts.snapshot()) == 1
    await chat.aclose()


@async_test
async def test_preflight_compaction_preserves_intent_and_new_after_compact_prompt(tmp_path):
    from test_context_partition import history_for_task
    from test_context_summary import response as summary_response
    from mewcode.prompts import build_system_prompt
    from mewcode.types import TokenUsage
    configure(tmp_path, [
        {'event': 'message.before_request', 'action': {'type': 'prompt', 'text': 'intent-marker'}},
        {'event': 'context.after_compact', 'action': {'type': 'prompt', 'text': 'compact-marker'}}])
    chat = session(tmp_path, ScriptedProvider([summary_response(), response()]))
    _, history = history_for_task()
    chat.history[:] = history
    chat._before_request()
    chat.context.estimator.observe(chat.context.estimator.snapshot(history, build_system_prompt(),
        chat.executor.registry.definitions(allowed_tools=chat.effective_tools())), TokenUsage(total_input_tokens=120000))
    events = observe(chat)
    result = await collect(chat.ask('新任务'))
    assert result[-1].reason == 'model_done' and len(chat.provider.requests) == 2
    assert sum(item['event'] == 'message.before_request' for item in events) == 1
    summary, work = chat.provider.requests
    assert summary[1]['tool_choice'] == 'none' and 'intent-marker' not in str(summary[0])
    assert work[0][-1].content.count('intent-marker') == work[0][-1].content.count('compact-marker') == 1
    assert not chat.hooks.prompts.snapshot()
    await chat.aclose()


@async_test
async def test_server_overflow_retry_is_new_intent_but_summary_is_not(tmp_path):
    from mewcode.types import ContextLimitError
    from test_context_partition import history_for_task
    from test_context_summary import response as summary_response
    configure(tmp_path, [{'event': 'message.before_request', 'action': {'type': 'prompt', 'text': 'retry-marker'}}])
    chat = session(tmp_path, ScriptedProvider([[ContextLimitError('overflow')], summary_response(), response()]))
    _, history = history_for_task()
    chat.history[:] = history
    events = observe(chat)
    result = await collect(chat.ask('新任务'))
    intents = [item for item in events if item['event'] == 'message.before_request']
    assert result[-1].reason == 'model_done' and len(chat.provider.requests) == 3
    assert len(intents) == 2 and intents[0]['request_id'] != intents[1]['request_id']
    complete = [item for item in events if item['event'] == 'message.after_response']
    assert len(complete) == 1 and complete[0]['request_id'] == intents[-1]['request_id']
    assert 'retry-marker' not in str(chat.provider.requests[1][0])
    await chat.aclose()
