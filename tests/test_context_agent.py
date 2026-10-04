"""工作、摘要和恢复共用有界请求预算。"""
from dataclasses import replace
from conftest import async_test, collect
from mewcode.types import ContextLimitError, Message
from test_agent_loop import make_agent, run, answer
from test_context_partition import history_for_task
from test_context_summary import response


def force_limit(agent, history):
    request = [*history, Message('user', '新任务'), agent.prompt_state.peek_request()]
    from mewcode.prompts import build_system_prompt
    tools = agent.executor.registry.definitions()
    agent.context.estimator.observe(agent.context.estimator.snapshot(request, build_system_prompt(), tools),
                                     __import__('mewcode.types', fromlist=['TokenUsage']).TokenUsage(total_input_tokens=120000))


@async_test
async def test_real_overflow_summarizes_once_then_continues_without_replaying(tmp_path):
    user, history = history_for_task()
    agent, provider = make_agent(tmp_path, [[ContextLimitError('超限')], response(), answer()])
    events, history = await run(agent, history, question='新任务')
    assert events[-1].reason == 'model_done' and events[-1].iteration == 3
    assert len(provider.requests) == 3 and provider.requests[1][1]['tool_choice'] == 'none'
    assert not any(e.kind == 'tool_started' for e in events)
    assert any(e.purpose == 'summary' and e.kind == 'usage' for e in events)
    assert 'PRIVATE' not in str(events)
    assert agent.prompt_state.request_sequence == 2
    assert any(m.context_kind == 'summary' for m in history)


@async_test
async def test_three_failures_circuit_persists_across_tasks(tmp_path):
    user, history = history_for_task()
    agent, provider = make_agent(tmp_path, [[ContextLimitError('超限')], response('坏摘要'), response('坏摘要'), response('坏摘要')])
    events, history = await run(agent, history)
    assert agent.context.failures == 3 and agent.context.circuit_open
    assert events[-1].reason == 'context_blocked' and len(provider.requests) == 4
    # 低于门槛仍可工作，普通新任务不解除熔断。
    provider.responses = iter([answer()])
    events, _ = await run(agent, [], question='短任务')
    assert events[-1].reason == 'model_done' and agent.context.failures == 3


@async_test
async def test_last_budget_can_commit_summary_but_not_work(tmp_path):
    _, history = history_for_task()
    agent, provider = make_agent(tmp_path, [[ContextLimitError('超限')], response()], max_iterations=2)
    events, history = await run(agent, history)
    assert events[-1].reason == 'max_iterations' and len(provider.requests) == 2
    assert history[0].context_kind == 'summary'
    assert all(m.content != '任务' for m in history if m.role == 'user')


@async_test
async def test_second_work_overflow_and_summary_overflow_never_loop(tmp_path):
    for responses, expected in [([[ContextLimitError('超限')], response(), [ContextLimitError('再次')]], 3),
                                ([[ContextLimitError('超限')], [ContextLimitError('摘要超限')]], 2)]:
        _, history = history_for_task()
        agent, provider = make_agent(tmp_path, responses)
        events, _ = await run(agent, history)
        assert events[-1].reason == 'context_blocked' and len(provider.requests) == expected


@async_test
async def test_protected_input_blocks_without_call_or_failure(tmp_path):
    agent, provider = make_agent(tmp_path, [])
    events, history = await run(agent, question='文' * 128000)
    assert events[-1].reason == 'context_blocked' and not provider.requests and not history
    assert agent.context.failures == 0


@async_test
async def test_usage_anchor_triggers_preflight_summary_at_equality(tmp_path):
    _, history = history_for_task()
    agent, provider = make_agent(tmp_path, [response(), answer()])
    force_limit(agent, history)
    events, _ = await run(agent, history, question='新任务')
    assert events[-1].reason == 'model_done' and len(provider.requests) == 2
    assert provider.requests[0][1]['tool_choice'] == 'none'
    assert agent.prompt_state.request_sequence == 1


@async_test
async def test_large_tool_result_spills_before_next_work_request(tmp_path):
    import json
    from test_agent_loop import calls, tool
    (tmp_path / 'large').write_text('文' * 10000)
    agent, provider = make_agent(tmp_path, [calls(tool('read', arguments=json.dumps({'path':'large'}))), answer()])
    events, history = await run(agent)
    assert len(provider.requests) == 2 and events[-1].reason == 'model_done'
    assert next(m for m in provider.requests[1][0] if m.role == 'tool').cache_path
    assert next(e.result for e in events if e.kind == 'tool_result').data['content'] == '文' * 10000
    assert any(e.phase == 'spill' for e in events)
    agent.context.cache.close()
