"""独立对话隔离、共享请求预算及真实摘要回流。"""

import asyncio

from conftest import ScriptedProvider, async_test, collect
from mewcode.skills.history import background_history
from mewcode.types import Message, ToolCall, ProviderEvent
from mewcode.tools.base import ToolResult
from test_skills_catalog import entry
from test_skills_session import session, response


def isolated(path, name="child", extra="", body="子 SOP {{args}}"):
    entry(path, name, extra=extra, body=body)
    path.write_text(path.read_text().replace("mode: shared", "mode: isolated"))


def test_history_rounds_are_readonly_data_and_leave_signatures_unchanged():
    signed = Message("assistant", "操作", (ToolCall("r", "read_file", '{}'),), provider_content=({"signature":"private"},))
    messages = [Message("user", "第一轮"), Message("context", "旧 SOP", context_kind="runtime"),
                signed, Message("tool", tool_call_id="r", tool_result=ToolResult.success("证据")),
                Message("user", "第二轮"), Message("assistant", "第二结果"),
                Message("user", "正在执行"), Message("assistant", tool_calls=(ToolCall("pending", "load_skill", '{}'),))]
    assert background_history(messages, 0, current_user_id=messages[-2].id) == []
    recent = background_history(messages, 1, current_user_id=messages[-2].id)
    assert "第二结果" in recent[0].content and "第一轮" not in recent[0].content
    all_text = background_history(messages, "all", current_user_id=messages[-2].id)[0].content
    assert "第一轮" in all_text and "证据" in all_text
    assert "旧 SOP" not in all_text and "private" not in all_text and "pending" not in all_text
    assert signed.provider_content == ({"signature":"private"},)


@async_test
async def test_cancel_child_command_keeps_completed_effect_and_reaps_process(tmp_path):
    import json
    isolated(tmp_path / '.mewcode/skills/child.md')
    provider = ScriptedProvider([
        response(calls=(ToolCall('load', 'load_skill', '{"name":"child"}'),)),
        response(calls=(ToolCall('command', 'execute_command', json.dumps({
            'command': "printf started > started; sleep 30; printf leaked > leaked"})),)),
    ])
    chat = session(tmp_path, provider)
    cancel = asyncio.Event()
    task = asyncio.create_task(collect(chat.ask('执行', cancel_event=cancel)))
    try:
        async def wait_started():
            while not (tmp_path / 'started').exists():
                await asyncio.sleep(.01)
        await asyncio.wait_for(wait_started(), 5)
        cancel.set()
        events = await asyncio.wait_for(task, 5)
        assert events[-1].reason == 'cancelled'
        assert (tmp_path / 'started').read_text() == 'started'
        assert not (tmp_path / 'leaked').exists()
        result = next(m.tool_result for m in chat.history if m.tool_call_id == 'load')
        assert result.data['evidence'][0]['error']['code'] == 'cancelled'
        assert len(provider.requests) == 2 and not provider.closed
    finally:
        cancel.set()
        await task
        chat.close()


@async_test
async def test_cancel_child_permission_wait_never_starts_tool(tmp_path):
    from mewcode.permissions.runtime import PermissionManager
    isolated(tmp_path / '.mewcode/skills/child.md')
    waiting = asyncio.Event()
    async def responder(request, cancel):
        if request.tool == 'load_skill':
            return 'once'
        waiting.set()
        await asyncio.Event().wait()
    provider = ScriptedProvider([
        response(calls=(ToolCall('load', 'load_skill', '{"name":"child"}'),)),
        response(calls=(ToolCall('write', 'write_file', '{"path":"forbidden","content":"bad"}'),)),
    ])
    chat = session(tmp_path, provider)
    permissions = PermissionManager(tmp_path, responder=responder, user_path=tmp_path / 'user-permissions')
    chat.permissions = chat.executor.permissions = permissions
    cancel = asyncio.Event()
    task = asyncio.create_task(collect(chat.ask('执行', cancel_event=cancel)))
    try:
        await asyncio.wait_for(waiting.wait(), 3)
        cancel.set()
        events = await asyncio.wait_for(task, 3)
        assert events[-1].reason == 'cancelled'
        assert not (tmp_path / 'forbidden').exists()
        assert not any(e.child_event and e.child_event.kind == 'tool_started' for e in events)
        assert chat.permissions is permissions and not provider.closed
    finally:
        cancel.set()
        await task
        chat.close()


@async_test
async def test_cancel_during_child_event_backpressure_keeps_audited_evidence(tmp_path):
    import json
    from mewcode.skills.budget import TaskBudget
    from test_skills_persistence import CONFIG
    isolated(tmp_path / '.mewcode/skills/child.md')
    entry(tmp_path / '.mewcode/skills/shared.md', 'shared')
    provider = ScriptedProvider([response(calls=(ToolCall('actual', 'load_skill', '{"name":"shared"}'),))])
    chat = session(tmp_path, provider, persistent=True, config=CONFIG)
    chat._task_budget, chat._task_question = TaskBudget(20, 'parent'), '主目标'
    blocked, cancel = asyncio.Event(), asyncio.Event()
    async def notify(item):
        if item['kind'] == 'skill_event' and item['event'].kind == 'tool_result':
            blocked.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(chat.executor.execute('load_skill', '{"name":"child"}',
        cancel_event=cancel, on_event=notify))
    try:
        await asyncio.wait_for(blocked.wait(), 3)
        cancel.set()
        result = await asyncio.wait_for(task, 3)
        assert result.error['code'] == 'cancelled'
        assert result.data and result.data['child_run_id']
        assert result.data['evidence'][0]['call_id'] == 'actual'
        assert result.data['evidence'][0]['ok']
        records = [json.loads(line) for line in chat.journal.path.read_text().splitlines()]
        assert any(r['kind'] == 'child_event' and r['payload']['kind'] == 'task_finished' for r in records)
        assert not chat.skills.active and len(provider.requests) == 1
    finally:
        cancel.set()
        await task
        chat.close()


@async_test
async def test_selected_model_usage_and_client_ownership(tmp_path):
    from dataclasses import replace
    from mewcode.types import TokenUsage
    from test_skills_persistence import CONFIG
    isolated(tmp_path / '.mewcode/skills/child.md', extra='model: small\n')
    def used(events, amount):
        return [ProviderEvent('usage', usage=TokenUsage(input_tokens=amount, output_tokens=amount)), *events]
    parent_provider = ScriptedProvider([
        used(response(calls=(ToolCall('load', 'load_skill', '{"name":"child"}'),)), 1),
        used(response('父完成'), 3)])
    child_provider = ScriptedProvider([used(response('子摘要'), 10)])
    configurations = []
    def factory(config):
        configurations.append(config)
        return child_provider
    config = replace(CONFIG, skill_models=(('small', 64000, 4096),))
    chat = session(tmp_path, parent_provider, config=config, provider_factory=factory)
    try:
        events = await collect(chat.ask('工作'))
        assert events[-1].usage.input_tokens == 14
        assert len(configurations) == 1 and configurations[0].model == 'small'
        assert configurations[0].context_window == 64000
        assert configurations[0].max_output_tokens == 4096
        assert configurations[0].api_key == CONFIG.api_key
        assert chat.context.window == 128000
        assert child_provider.closed and not parent_provider.closed
        assert chat.prompt_state.request_sequence == 2
    finally:
        chat.close()


@async_test
async def test_missing_model_does_not_call_or_fallback(tmp_path):
    from test_skills_persistence import CONFIG
    isolated(tmp_path / '.mewcode/skills/child.md', extra='model: missing\n')
    provider = ScriptedProvider([response(calls=(ToolCall('load', 'load_skill', '{"name":"child"}'),)), response()])
    chat = session(tmp_path, provider, config=CONFIG)
    try:
        await collect(chat.ask('目标'))
        result = next(m.tool_result for m in chat.history if m.tool_call_id == 'load')
        assert result.error['code'] == 'skill_model_unavailable'
        assert len(provider.requests) == 2
    finally:
        chat.close()


@async_test
async def test_child_models_do_not_have_ordinary_tool_total_timeout(tmp_path):
    isolated(tmp_path / '.mewcode/skills/child.md')
    async def slow_answer():
        await asyncio.sleep(.12)
        for event in response('成功摘要'):
            yield event
    provider = ScriptedProvider([response(calls=(ToolCall('load', 'load_skill', '{"name":"child"}'),)), slow_answer, response()])
    chat = session(tmp_path, provider)
    chat.executor.timeout = .02
    try:
        events = await collect(chat.ask('目标'))
        assert events[-1].reason == 'model_done'
        assert next(m.tool_result for m in chat.history if m.tool_call_id == 'load').ok
    finally:
        chat.close()


@async_test
async def test_cancel_child_stream_preserves_parent_pair_and_activation(tmp_path):
    isolated(tmp_path / '.mewcode/skills/child.md')
    entry(tmp_path / '.mewcode/skills/parent.md', 'parent')
    started = asyncio.Event()
    async def blocked():
        started.set()
        yield ProviderEvent('text_delta', '未完成片段')
        await asyncio.Event().wait()
    provider = ScriptedProvider([response(calls=(ToolCall('load', 'load_skill', '{"name":"child"}'),)), blocked])
    chat = session(tmp_path, provider)
    chat.skills.activate('parent')
    cancel = asyncio.Event()
    task = asyncio.create_task(collect(chat.ask('目标', cancel_event=cancel)))
    try:
        await asyncio.wait_for(started.wait(), 2)
        cancel.set()
        events = await asyncio.wait_for(task, 3)
        assert events[-1].reason == 'cancelled'
        result = next(m.tool_result for m in chat.history if m.tool_call_id == 'load')
        assert result.error['code'] == 'cancelled'
        assert '未完成片段' not in result.data['summary']
        assert [a.skill.name for a in chat.skills.active] == ['parent']
        assert provider.closed_streams == 2 and not provider.closed
    finally:
        cancel.set()
        await task
        chat.close()


@async_test
async def test_child_summary_only_and_global_budget(tmp_path):
    isolated(tmp_path / ".mewcode/skills/child.md")
    provider = ScriptedProvider([
        response(calls=(ToolCall("load", "load_skill", '{"name":"child","args":"参数"}'),)),
        response("子中间步骤", (ToolCall("read", "glob_files", '{"pattern":"*"}'),)),
        response("子最终摘要"),
    ])
    chat = session(tmp_path, provider, max_iterations=3)
    try:
        events = await collect(chat.ask("本次主目标"))
        assert len(provider.requests) == 3
        assert events[-1].reason == "max_iterations"
        assert events[-1].iteration == 3
        assert not any(m.content == "子中间步骤" or m.content == "子最终摘要" for m in chat.history)
        result = next(m.tool_result for m in chat.history if m.tool_call_id == "load")
        assert result.ok and result.data["summary"] == "子最终摘要"
        assert result.data["reason"] == "model_done"
        child_question = next(m.content for m in provider.requests[1][0] if m.role == "user")
        assert "本次主目标" in child_question and "参数" in child_question
        assert not chat.skills.active
        assert chat.prompt_state.request_sequence == 1
        assert len([e for e in events if e.kind == "finished"]) == 1
        assert any(e.kind == "skill_event" for e in events)
    finally:
        chat.close()


@async_test
async def test_no_remaining_budget_does_not_start_child(tmp_path):
    isolated(tmp_path / ".mewcode/skills/child.md")
    provider = ScriptedProvider([response(calls=(ToolCall("load", "load_skill", '{"name":"child"}'),))])
    chat = session(tmp_path, provider, max_iterations=1)
    try:
        await collect(chat.ask("执行"))
        assert len(provider.requests) == 1
        result = next(m.tool_result for m in chat.history if m.tool_call_id == "load")
        assert result.error["code"] == "skill_budget_exhausted"
        assert result.error["details"]["not_started"]
    finally:
        chat.close()


@async_test
async def test_parent_two_plus_child_three_exhaust_same_five_requests(tmp_path):
    isolated(tmp_path / '.mewcode/skills/child.md')
    read = lambda identity: response(calls=(ToolCall(identity, 'glob_files', '{"pattern":"*"}'),))
    provider = ScriptedProvider([read('parent-read'),
        response(calls=(ToolCall('load', 'load_skill', '{"name":"child"}'),)),
        read('child-one'), read('child-two'), response('子三次摘要')])
    chat = session(tmp_path, provider, max_iterations=5)
    try:
        events = await collect(chat.ask('共享额度'))
        assert events[-1].reason == 'max_iterations' and events[-1].iteration == 5
        assert len(provider.requests) == 5
        assert chat.prompt_state.request_sequence == 2
        result = next(m.tool_result for m in chat.history if m.tool_call_id == 'load')
        assert result.ok and result.data['summary'] == '子三次摘要'
    finally:
        chat.close()


@async_test
async def test_recovery_missing_child_return_warns_without_replaying(tmp_path):
    import json
    from test_skills_persistence import CONFIG
    isolated(tmp_path / '.mewcode/skills/child.md')
    chat = session(tmp_path, ScriptedProvider([response('实际子结果')]), persistent=True, config=CONFIG)
    await collect(chat.run_skill('child'))
    path, identity = chat.journal.path, chat.session_id
    chat.close()
    records = [json.loads(line) for line in path.read_text().splitlines()]
    boundary = next(i for i, r in enumerate(records) if r['kind'] == 'history_commit')
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records[:boundary]))
    provider = ScriptedProvider([])
    chat = session(tmp_path, provider, resume=identity, config=CONFIG)
    try:
        assert any('主回流未完成' in w for w in chat.warnings)
        assert not chat.history and not provider.requests
        assert any(r['kind'] == 'child_event' and r['payload']['kind'] == 'task_finished' for r in records[:boundary])
    finally:
        chat.close()


@async_test
async def test_child_cap_and_nested_isolation(tmp_path):
    entry(tmp_path / ".mewcode/skills/parent.md", "parent", "allowed-tools: [read_file]\n", "父正文")
    isolated(tmp_path / ".mewcode/skills/child.md", extra="allowed-tools: [write_file, read_file]\n")
    provider = ScriptedProvider([
        response(calls=(ToolCall("load", "load_skill", '{"name":"child"}'),)),
        response(calls=(ToolCall("nested", "load_skill", '{"name":"child"}'),
                        ToolCall("write", "write_file", '{"path":"bad","content":"bad"}'))),
        response("未执行写入"), response("主完成"),
    ])
    chat = session(tmp_path, provider)
    chat.skills.activate("parent")
    try:
        await collect(chat.ask("工作"))
        assert {t.name for t in provider.requests[1][1]["tools"]} == {"read_file", "load_skill"}
        assert "父正文" not in provider.requests[1][0][-1].content
        results = [m.tool_result for m in provider.requests[2][0] if m.role == "tool"]
        assert [r.error["code"] for r in results] == ["skill_nested_isolated", "tool_not_allowed"]
        assert not (tmp_path / "bad").exists()
        assert [a.skill.name for a in chat.skills.active] == ["parent"]
    finally:
        chat.close()


@async_test
async def test_child_audit_is_separate_and_does_not_restore_child_messages(tmp_path):
    import json
    from test_skills_persistence import CONFIG
    isolated(tmp_path / ".mewcode/skills/child.md")
    provider = ScriptedProvider([
        response(calls=(ToolCall("load", "load_skill", '{"name":"child"}'),)),
        response("子中间", (ToolCall("tool", "glob_files", '{"pattern":"*"}'),)),
        response("摘要"), response("父完成"),
    ])
    chat = session(tmp_path, provider, persistent=True, config=CONFIG)
    identity, path = chat.session_id, chat.journal.path
    await collect(chat.ask("父目标"))
    chat.close()
    records = [json.loads(line) for line in path.read_text().splitlines()]
    child = [r['payload'] for r in records if r['kind'] == 'child_event']
    assert any(r['kind'] == 'interaction_started' for r in child)
    assert any(r['kind'] == 'tool_result' for r in child)
    assert len({r['run_id'] for r in child}) == 1
    assert all(r['parent_task_id'] for r in child)
    chat = session(tmp_path, ScriptedProvider([]), resume=identity, config=CONFIG)
    try:
        assert not any(m.content == "子中间" for m in chat.history)
        assert chat.history[-1].content == "父完成"
    finally:
        chat.close()


@async_test
async def test_child_audit_failure_stops_parent_without_side_effects(tmp_path, monkeypatch):
    from test_skills_persistence import CONFIG
    isolated(tmp_path / '.mewcode/skills/child.md')
    provider = ScriptedProvider([
        response(calls=(ToolCall('load', 'load_skill', '{"name":"child"}'),)),
        response(calls=(ToolCall('write', 'write_file', '{"path":"must-not-exist","content":"x"}'),)),
        response('不应发出'),
    ])
    chat = session(tmp_path, provider, persistent=True, config=CONFIG)
    append = chat.journal.append
    def fail_child_intent(kind, payload, **options):
        if kind == 'child_event' and payload['kind'] == 'interaction_started':
            raise OSError('模拟子审计存储失败')
        return append(kind, payload, **options)
    monkeypatch.setattr(chat.journal, 'append', fail_child_intent)
    try:
        events = await collect(chat.ask('目标'))
        assert events[-1].reason == 'context_blocked'
        assert len(provider.requests) == 2
        assert chat.agent.storage_blocked
        assert not (tmp_path / 'must-not-exist').exists()
    finally:
        chat.close()


@async_test
async def test_child_summary_checkpoint_failure_stops_same_batch_and_parent(tmp_path, monkeypatch):
    from mewcode.types import ContextLimitError
    from test_context_summary import response as summary_response
    from test_skills_persistence import CONFIG
    isolated(tmp_path / '.mewcode/skills/child.md')
    entry(tmp_path / '.mewcode/skills/after.md', 'after')
    provider = ScriptedProvider([
        response(calls=(ToolCall('load', 'load_skill', '{"name":"child"}'),
                        ToolCall('after', 'load_skill', '{"name":"after"}'))),
        *[response('文' * 4000, (ToolCall(f'c{i}', 'load_skill', '{"name":"missing"}'),)) for i in range(8)],
        [ContextLimitError('超过窗口')], summary_response(), response('不应继续'),
    ])
    chat = session(tmp_path, provider, persistent=True, config=CONFIG)
    append, failures = chat.journal.append, []
    def fail_checkpoint(kind, payload, **options):
        if kind == 'child_event' and payload['kind'] == 'history_checkpoint':
            failures.append(kind)
            raise OSError('模拟子摘要检查点写入失败')
        return append(kind, payload, **options)
    monkeypatch.setattr(chat.journal, 'append', fail_checkpoint)
    try:
        events = await collect(chat.ask('任务'))
        assert failures
        assert chat.agent.storage_blocked
        assert not chat.skills.active
        assert events[-1].reason == 'context_blocked'
        assert len(provider.requests) == 11
    finally:
        chat.close()


@async_test
async def test_child_spill_evidence_survives_end_reset_and_resume(tmp_path):
    from test_skills_persistence import CONFIG
    isolated(tmp_path / '.mewcode/skills/child.md')
    (tmp_path / 'large').write_text('文' * 14000)
    provider = ScriptedProvider([
        response(calls=(ToolCall('load', 'load_skill', '{"name":"child"}'),)),
        response(calls=(ToolCall('large', 'read_file', '{"path":"large"}'),)),
        response('摘要'), response('父完成')])
    chat = session(tmp_path, provider, persistent=True, config=CONFIG)
    identity = chat.session_id
    await collect(chat.ask('读大文件'))
    paths = set(chat.context.cache.paths)
    assert paths and all('/child_' in p for p in paths)
    assert chat.context.summary_files >= paths
    chat.reset()
    chat.close()
    chat = session(tmp_path, ScriptedProvider([]), resume=identity, config=CONFIG)
    try:
        assert all(chat.context.cache.restore(p).ok for p in paths)
        assert not chat.history
    finally:
        chat.close()
