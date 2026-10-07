"""临时展示与模型消息分离，预算和字段白名单不影响真实运行。"""

from mewcode.types import AgentEvent, Message, TokenUsage, ToolCall
from mewcode.tools.base import ToolResult


def test_projection_preserves_segments_tool_identity_and_usage():
    from mewcode.web.projection import Projection
    view = Projection('session')
    view.input('输入\n', 'operation')
    view.event(AgentEvent('text_delta', run_id='parent', text='第一段', iteration=1))
    view.event(AgentEvent('tool_call', run_id='parent', iteration=1,
                         call=ToolCall('same', 'read_file', '{"path":"README.md"}')))
    view.event(AgentEvent('tool_started', run_id='parent', iteration=1, tool_call_id='same'))
    view.event(AgentEvent('tool_result', run_id='parent', iteration=1, tool_call_id='same',
                         result=ToolResult.success({'content': '结果', 'provider_content': '隐私'})))
    view.event(AgentEvent('text_delta', run_id='parent', text='第二段', iteration=2))
    view.event(AgentEvent('usage', run_id='parent', usage=TokenUsage(10, 2, True)))
    view.event(AgentEvent('usage', run_id='maintenance', purpose='memory', usage=TokenUsage(9, 1, True)))
    state = view.snapshot()
    assert [m['text'] for m in state['messages'] if m['role'] == 'assistant'] == ['第一段', '第二段']
    assert state['runs'][0]['calls'][0]['status'] == 'completed'
    assert 'provider_content' not in str(state)
    assert state['runs'][0]['usage']['input_tokens'] == 10
    assert state['runs'][1]['usage_by_purpose']['memory']['input_tokens'] == 9


def test_memory_maintenance_uses_its_own_usage_without_overwriting_parent_work():
    from mewcode.web.projection import Projection
    view = Projection('session')
    parent = 'parent_work'
    work_usage, memory_usage = TokenUsage(5528, 178, True), TokenUsage(696, 477, True)
    view.event(AgentEvent('usage', run_id=parent, usage=work_usage))
    view.event(AgentEvent('memory_update', run_id=parent, purpose='memory', phase='queued'))
    queued = view.snapshot()['runs']
    assert len(queued) == 2
    assert queued[1]['usage'] == {} and queued[1]['usage_by_purpose'] == {}
    view.event(AgentEvent('usage', run_id=parent, purpose='memory', usage=memory_usage))
    view.event(AgentEvent('memory_update', run_id=parent, purpose='memory', phase='updated', text='记忆已更新'))
    # 工作终结可能晚于维护通知到达，不能改写维护状态或用量。
    view.event(AgentEvent('finished', run_id=parent, usage=work_usage, reason='model_done'))
    work, memory = view.snapshot()['runs']
    assert work['id'] == parent and work['phase'] == 'completed'
    assert work['usage']['input_tokens'] == 5528 and work['usage']['output_tokens'] == 178
    assert set(work['usage_by_purpose']) == {'work'}
    assert memory['id'] != parent and memory['parent_run_id'] == parent
    assert memory['phase'] == 'updated' and memory['title'] == '摘要／维护'
    assert memory['usage']['input_tokens'] == 696 and memory['usage']['output_tokens'] == 477
    assert set(memory['usage_by_purpose']) == {'memory'}


def test_maintenance_purposes_have_independent_segments_and_keep_unknown_usage_unknown():
    from mewcode.web.projection import Projection
    view = Projection('session')
    for purpose in ('work', 'summary', 'restore', 'memory'):
        view.event(AgentEvent('finished', run_id='shared', purpose=purpose, parent_run_id='parent',
                             usage=TokenUsage(None, None, False) if purpose == 'restore'
                             else TokenUsage(10, 2, True)))
    runs = view.snapshot()['runs']
    assert len(runs) == 4 and len({run['id'] for run in runs}) == 4
    assert all(run['parent_run_id'] == 'parent' and run['phase'] == 'completed' for run in runs)
    for run, purpose in zip(runs, ('work', 'summary', 'restore', 'memory')):
        assert set(run['usage_by_purpose']) == {purpose}
        assert run['usage'] == run['usage_by_purpose'][purpose]
    assert runs[2]['usage']['input_tokens'] is None and not runs[2]['usage']['complete']


def test_projection_caps_body_thinking_calls_and_marks_missing_content():
    from mewcode.web.projection import Projection
    view = Projection('session', body_limit=1024, thinking_limit=100, detail_limit=100, call_limit=2)
    view.event(AgentEvent('thinking_delta', run_id='r', text='思考' * 100))
    for i in range(3):
        view.event(AgentEvent('tool_call', run_id='r', call=ToolCall(str(i), 'tool', 'x' * 500)))
    view.event(AgentEvent('text_delta', run_id='r', text='内容' * 500))
    state = view.snapshot()
    assert len(state['runs'][0]['thinking'].encode()) <= 100
    assert len(state['runs'][0]['calls']) == 2
    assert len(state['messages'][0]['text'].encode()) <= 1024
    assert state['messages'][0]['truncated'] and state['warnings']


def test_projection_does_not_copy_raw_provider_message_and_keeps_only_recent_runs():
    from mewcode.web.projection import Projection
    view = Projection('session')
    for i in range(15):
        view.event(AgentEvent('text_delta', run_id=str(i), text=str(i)))
        view.event(AgentEvent('finished', run_id=str(i), reason='model_done',
                             message=Message('assistant', provider_content=({'secret': 'secret'},))))
    state = view.snapshot()
    assert len(state['runs']) == 10 and len(state['messages']) == 10
    assert 'secret' not in str(state)
    assert state['warnings']


def test_secret_split_between_stream_chunks_is_redacted_in_accumulated_view():
    from mewcode.web.projection import Projection
    from mewcode.web.security import Redactor
    view = Projection('session', redact=Redactor('demo-secret').text)
    for kind in ('text_delta', 'thinking_delta'):
        view.event(AgentEvent(kind, run_id='r', text='demo-'))
        view.event(AgentEvent(kind, run_id='r', text='secret'))
    assert 'demo-secret' not in str(view.snapshot())


def body_bytes(state):
    import json
    def size(value):
        if value is None:
            return 0
        return len((value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)).encode())
    total = sum(size(message.get(key)) for message in state['messages'] for key in ('text', 'call', 'result'))
    for run in state['runs']:
        total += size(run.get('text'))
        total += sum(size(call.get(key)) for call in run['calls'] for key in ('arguments', 'result'))
    return total


def metadata_bytes(state):
    from copy import deepcopy
    import json
    metadata = deepcopy(state)
    for message in metadata['messages']:
        for key in ('text', 'call', 'result'):
            message.pop(key, None)
    for run in metadata['runs']:
        for key in ('text', 'thinking'):
            run.pop(key, None)
        for call in run['calls']:
            for key in ('arguments', 'result'):
                call.pop(key, None)
    return len(json.dumps(metadata, ensure_ascii=False).encode())


def test_body_budget_includes_seeded_details_run_notices_and_omission_markers():
    from mewcode.web.projection import Projection
    view = Projection('session', body_limit=600, detail_limit=200)
    view.seed({'items': [{'id': 'historic', 'role': 'tool', 'kind': 'tool_result',
                         'text': '历史正文' * 80, 'call': {'id': 'old', 'arguments': '参' * 100},
                         'result': {'output': '旧结果' * 100, 'result_id': 'signed-result'}}]})
    for i in range(4):
        view.event(AgentEvent('display_line', run_id=f'run-{i}', text='维护文字' * 100))
        view.event(AgentEvent('tool_call', run_id=f'run-{i}', call=ToolCall('c', 'read_file', '{"path":"文"}')))
        view.event(AgentEvent('tool_result', run_id=f'run-{i}', tool_call_id='c',
                             result=ToolResult.success({'content': '结果' * 100})))
    state = view.snapshot()
    assert body_bytes(state) <= 600
    assert state['warnings']
    historic = next(item for item in state['messages'] if item['id'] == 'historic')
    assert historic['truncated'] and historic.get('result_id') == 'signed-result'


def test_result_detail_limit_includes_json_wrapper_overhead_and_escaping():
    import json
    from mewcode.web.projection import Projection
    view = Projection('session', detail_limit=128)
    view.event(AgentEvent('tool_call', run_id='r', call=ToolCall('c', 'read_file', '{"path":"a"}')))
    view.event(AgentEvent('tool_result', run_id='r', tool_call_id='c',
                         result=ToolResult.success({'content': '\\"\n' * 300})))
    result = view.snapshot()['runs'][0]['calls'][0]['result']
    assert result['truncated']
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 128


def test_metadata_has_separate_budget_and_trims_oldest_entries():
    from mewcode.web.projection import Projection
    view = Projection('session', body_limit=100_000, metadata_limit=1800)
    for i in range(50):
        view.input('', f'user-{i}')
    view.event(AgentEvent('tool_call', run_id='current', call=ToolCall('c', 'read_file', '{"path":"a"}')))
    state = view.snapshot()
    assert metadata_bytes(state) <= 1800
    assert state['messages'][-1]['id'] == 'input:user-49'
    assert all(item['id'] != 'input:user-0' for item in state['messages'])
    assert state['warnings'] and state['runs'][0]['id'] == 'current'


def test_all_run_identities_and_global_thinking_are_bounded_without_fake_completion():
    from mewcode.web.projection import Projection
    view = Projection('session', thinking_limit=100)
    for i in range(25):
        view.event(AgentEvent('thinking_delta', run_id=f'r-{i}', text='思考' * 30))
    state = view.snapshot()
    assert len(state['runs']) <= 11
    assert all(run['phase'] == 'running' for run in state['runs'])
    assert sum(len(run['thinking'].encode()) for run in state['runs']) <= 100
    assert state['runs'][-1]['id'] == 'r-24' and state['warnings']


def test_each_delta_does_not_serialize_previous_messages_or_the_whole_transcript(monkeypatch):
    import mewcode.web.projection as module
    view = module.Projection('session')
    view.input('历史' * 100_000, 'old')
    serialized = []
    original = module.json.dumps
    def measured(value, *args, **options):
        text = original(value, *args, **options)
        serialized.append(len(text))
        return text
    monkeypatch.setattr(module.json, 'dumps', measured)
    for _ in range(100):
        view.event(AgentEvent('text_delta', run_id='current', text='新'))
    assert max(serialized, default=0) < 4096
    assert view.snapshot()['messages'][-1]['text'] == '新' * 100


def test_seed_keeps_result_id_and_redacts_json_encoded_arguments_and_keys():
    import json
    from mewcode.web.projection import Projection
    from mewcode.web.security import Redactor
    view = Projection('session', redact=Redactor('秘密token').text)
    encoded = json.dumps({'path': '秘密token', '秘密token': 'v', 'api_key': '不要暴露'})
    view.seed({'items': [{'id': 'a', 'role': 'assistant', 'text': '', 'call': [
        {'id': 'c', 'name': 'read_file', 'arguments': encoded}]},
        {'id': 'b', 'role': 'tool', 'text': '', 'call': {'id': 'c'},
         'result': {'ok': True, 'output': '已保留', 'result_id': 'signed'}}]})
    view.event(AgentEvent('tool_call', run_id='r', call=ToolCall('new', 'read_file', encoded)))
    state = view.snapshot()
    assert '秘密token' not in str(state) and '不要暴露' not in str(state)
    assert state['messages'][1]['result']['result_id'] == 'signed'
    assert state['messages'][0]['call'][0]['id'] == 'c'
    assert json.loads(state['runs'][0]['calls'][0]['arguments'])['path'] == '[已隐藏]'


def test_tool_status_keeps_denial_timeout_cancellation_and_unknown_side_effects_distinct():
    from mewcode.web.projection import Projection
    cases = [('permission_denied', {}, 'denied'), ('mcp_timeout', {}, 'timed_out'),
             ('cancelled', {}, 'cancelled'), ('cancelled', {'side_effects': 'unknown'}, 'side_effect_unknown'),
             ('failed', {}, 'failed')]
    view = Projection('session')
    for i, (code, details, expected) in enumerate(cases):
        view.event(AgentEvent('tool_call', run_id='r', iteration=i, call=ToolCall('same', 'read_file', '{}')))
        view.event(AgentEvent('tool_result', run_id='r', iteration=i, tool_call_id='same',
                             result=ToolResult.failure(code, '结果', details=details)))
        assert view.snapshot()['runs'][0]['calls'][-1]['status'] == expected


def test_default_call_window_caps_1024_without_rewriting_pending_state():
    from mewcode.web.projection import Projection
    view = Projection('session')
    for i in range(1025):
        view.event(AgentEvent('tool_call', run_id='r', call=ToolCall(str(i), 'read_file', '{}')))
    state = view.snapshot()
    assert len(state['runs'][0]['calls']) == 1024
    assert state['runs'][0]['calls'][-1]['source_id'] == '1023'
    assert all(call['status'] == 'proposed' for call in state['runs'][0]['calls'])
    assert state['runs'][0]['calls_truncated'] and state['warnings']


def test_window_eviction_releases_body_and_identity_caches_for_following_runs():
    from mewcode.web.projection import Projection
    view = Projection('session', body_limit=1100)
    for i in range(11):
        view.event(AgentEvent('text_delta', run_id=f'r{i}', text='x' * 100))
        view.event(AgentEvent('finished', run_id=f'r{i}', reason='model_done'))
    view.event(AgentEvent('text_delta', run_id='current', text='y' * 100))
    state = view.snapshot()
    assert len(state['runs']) == 11
    assert state['messages'][-1]['text'] == 'y' * 100
    assert not state['messages'][-1].get('truncated')
    assert all(message['run_id'] != 'r0' for message in state['messages'])
    assert body_bytes(state) == 1100


def test_seed_and_reconciliation_count_metadata_and_leave_retained_references_valid():
    from mewcode.web.projection import Projection
    view = Projection('session', metadata_limit=1100)
    view.input('旧输入', 'old')
    old = view.messages[0]
    for i in range(20):
        view.input('', str(i))
    assert old['text'] == '旧输入'
    latest = view.messages[-1]
    view.reconcile_message(latest, 'committed-' + 'a' * 300)
    assert latest['complete'] and latest['id'].startswith('committed-')
    assert metadata_bytes(view.snapshot()) <= 1100


def test_budget_invariants_hold_across_mixed_event_updates_and_history_seed():
    from mewcode.web.projection import Projection
    from random import Random
    random = Random(7)
    view = Projection('session', body_limit=1200, thinking_limit=80, detail_limit=150,
                      metadata_limit=3200, call_limit=4)
    for i in range(120):
        identity = f'r-{i // 8}'
        kind = random.choice(['text_delta', 'thinking_delta', 'tool_call', 'tool_result', 'display_line', 'finished'])
        call = ToolCall(str(i % 3), 'read_file', '{"path":"' + '文' * 60 + '"}')
        view.event(AgentEvent(kind, run_id=identity, text='文字' * random.randrange(80),
            iteration=i % 2, call=call, tool_call_id=str(i % 3), reason='model_done',
            result=ToolResult.success({'content': '结果' * 100})))
        state = view.snapshot()
        assert body_bytes(state) <= 1200
        assert metadata_bytes(state) <= 3200
        assert sum(len(run['thinking'].encode()) for run in state['runs']) <= 80
        assert len(state['runs']) <= 11 and sum(run['phase'] == 'completed' for run in state['runs']) <= 10
        for run in state['runs']:
            assert len(run['calls']) <= 4
