"""展示历史与当前运行状态拥有独立且有界的生命周期。"""

import json

from mewcode.terminal.state import TerminalState
from mewcode.tools.base import ToolResult
from mewcode.types import AgentEvent, ToolCall, TokenUsage


def begin(state, run, title='问题', **kwargs):
    state.begin_task(mode='execute', permission_mode='default', max_iterations=20,
                     title=title, **kwargs)
    state.update(AgentEvent('progress', run_id=run, phase='model', iteration=1))


def tool(state, run, identity='same', text='内容'):
    state.update(AgentEvent('tool_call', run_id=run, tool_call_id=identity,
        tool_name='read_file', call=ToolCall(identity, 'read_file', json.dumps({'path': 'a.py'}))))
    state.update(AgentEvent('tool_result', run_id=run, tool_call_id=identity,
        tool_name='read_file', result=ToolResult.success({'content': text, 'path': 'a.py'})))


def finish(state, run, reason='model_done'):
    state.update(AgentEvent('finished', run_id=run, reason=reason, usage=TokenUsage(10, 3)))


def test_two_turns_retain_single_snapshots_and_distinct_call_identity():
    state = TerminalState('private')
    begin(state, 'one', '读取private')
    tool(state, 'one')
    first = state.history.current
    finish(state, 'one', 'cancelled')
    begin(state, 'two', '/prompt original')
    tool(state, 'two')
    second = state.history.current
    assert state.history.turns == (first, second)
    assert first.title == '读取[已隐藏]' and first.status == '取消'
    assert first.calls[0].id != second.calls[0].id
    assert first.calls[0].arguments.body and first.calls[0].result.body
    assert state.history.get(first.id) is first
    assert state.phase == 'model' and state.run_id == 'two'
    assert state.details_text('tools').count('内容') == 1


def test_redacted_thinking_sources_keep_distinct_selection_ids():
    state = TerminalState('secret')
    begin(state, 'run')
    state.details.think(('secret', 1), '来源 secret', '第一份思考')
    state.details.think(('[已隐藏]', 1), '来源 [已隐藏]', '第二份思考')
    thoughts = state.history.current.thoughts
    assert len({item.id for item in thoughts}) == 2
    assert all('secret' not in item.id for item in thoughts)
    from mewcode.terminal.browser import DetailsBrowser
    browser = DetailsBrowser(state.history)
    browser.open()
    browser.handle('tab')
    browser.handle('down')
    browser.handle('enter')
    assert '第二份思考' in browser.render(110, 20)


def test_child_same_id_and_auto_resume_have_explicit_source():
    state = TerminalState()
    begin(state, 'one')
    tool(state, 'one')
    child = AgentEvent('tool_call', run_id='child', tool_call_id='same',
        skill_name='审阅', tool_name='read_file', call=ToolCall('same', 'read_file', '{}'))
    state.update(AgentEvent('skill_event', run_id='one', child_event=child))
    turn = state.history.current
    assert len(turn.calls) == 2 and len({call.id for call in turn.calls}) == 2
    assert turn.calls[1].source == '审阅' and turn.calls[1].original_id == 'same'
    finish(state, 'one')
    begin(state, 'two', source='自动接续', parent_id='one')
    assert state.history.current.title == '自动接续'
    assert state.history.current.parent_id == 'one'
    assert state.history.current.source == '自动接续'


def test_confirmed_late_result_only_updates_old_record_without_current_statistics():
    state = TerminalState()
    begin(state, 'one')
    state.update(AgentEvent('tool_call', run_id='one', tool_call_id='pending',
        tool_name='read_file', call=ToolCall('pending', 'read_file', '{}')))
    first = state.history.current
    finish(state, 'one', 'cancelled')
    begin(state, 'two')
    phase, usage = state.phase, state._total_usage
    late = AgentEvent('tool_result', run_id='one', tool_call_id='pending',
        tool_name='read_file', result=ToolResult.success({'content': '迟到'}))
    assert state.update(late) == []
    assert '迟到' in first.calls[0].result.body
    state.update(late)
    state.update(AgentEvent('tool_result', run_id='one', tool_call_id='unknown',
        tool_name='read_file', result=ToolResult.success({})))
    assert len(first.calls) == 1 and state.phase == phase and state._total_usage is usage
    assert state.run_id == 'two'


def test_retention_reset_and_incomplete_segment_close():
    state = TerminalState()
    ids = []
    for number in range(11):
        begin(state, str(number))
        ids.append(state.history.current.id)
        finish(state, str(number))
    assert len(state.history.turns) == 10 and state.history.get(ids[0]) is None
    begin(state, 'active')
    assert len(state.history.turns) == 11
    state.finish_task(reason='interrupted', text='通道失败')
    assert len(state.history.turns) == 10 and state.history.current.finished
    assert state.history.current.reason == 'interrupted' and state.phase == 'idle'
    state.reset_task()
    assert not state.history.turns and state.history.current is None


def test_cross_turn_budget_evicts_oldest_finished_body_before_current():
    state = TerminalState()
    state.history.details.body_limit = 180
    begin(state, 'old')
    tool(state, 'old', text='旧' * 30)
    first = state.history.current
    finish(state, 'old')
    begin(state, 'new')
    tool(state, 'new', text='新' * 30)
    current = state.history.current
    assert state.history.details.body_bytes <= 180
    assert first.calls[0].result.evicted
    assert not current.calls[0].result.evicted
    assert '已不可用' in state.history.text(first.id)


def test_call_metadata_and_current_active_index_are_bounded():
    state = TerminalState()
    state.history.call_limit = 3
    begin(state, 'run')
    for number in range(8):
        tool(state, 'run', identity=str(number))
    turn = state.history.current
    assert turn.total_calls == 8 and len(turn.calls) <= 3 and len(state._tools) <= 3
    assert turn.index_evicted and state.history.metadata_bytes <= state.history.metadata_limit
    assert [call.number for call in turn.calls] == [6, 7, 8]
    before = turn.total_calls
    state.update(AgentEvent('tool_result', run_id='run', tool_call_id='0',
        tool_name='read_file', result=ToolResult.success({})))
    assert turn.total_calls == before


def test_shared_body_view_and_page_caches_are_bounded_and_invalidated():
    from mewcode.terminal.details import DetailStore
    details = DetailStore('', body_limit=100, tool_limit=64, view_cache_limit=240)
    details.put('raw', '原始', '中' * 30)
    assert details.entries[('tool', 'raw')].truncated
    assert len(details.entries[('tool', 'raw')].body.encode()) <= 64
    formatted = details.cache_view(('raw', 1), '格式' * 30)
    assert details.body_bytes <= 100 and formatted == details.get_view(('raw', 1))
    assert details.cache_pages('page', ['小'])
    assert details.get_pages('page') == ['小']
    assert not details.cache_pages('huge', ['大' * 100])
    assert details.get_pages('page') is None
    details.clear_view()
    assert details.get_view(('raw', 1)) is None
    assert details.view_bytes == 0


def test_current_plain_status_does_not_dump_old_turns():
    state = TerminalState()
    begin(state, 'one', '旧问题')
    tool(state, 'one', text='上一轮独占正文')
    finish(state, 'one')
    begin(state, 'two', '新问题')
    tool(state, 'two', text='当前独占正文')
    output = state.status_text(root='.', model='test', mode='execute', permission_mode='default')
    assert '当前独占正文' in output and '上一轮独占正文' not in output


def test_metadata_limit_covers_thinking_and_long_ids_and_evicted_calls_stay_absent():
    state = TerminalState()
    state.history.metadata_limit = 18000
    begin(state, 'run')
    tool(state, 'run', identity='a' * 30000)
    tool(state, 'run', identity='b' * 30000)
    turn = state.history.current
    assert state.history.metadata_bytes <= 18000 and turn.index_evicted
    before = turn.total_calls
    tool(state, 'run', identity='a' * 30000)
    assert turn.total_calls == before
    from mewcode.terminal.projection import TerminalProjection
    projection = TerminalProjection(state)
    for number in range(40):
        projection.accept(AgentEvent('thinking_delta', run_id='run', iteration=number, text='思考'))
    assert state.history.metadata_bytes <= 18000


def test_projection_batches_bind_turn_and_keep_child_source_boundaries():
    from mewcode.terminal.projection import TerminalProjection
    state = TerminalState()
    begin(state, 'main')
    projection = TerminalProjection(state)
    visible = []
    for child in ('child-one', 'child-two'):
        event = AgentEvent('tool_result', run_id=child, skill_name='相同Skill', tool_call_id='same',
                          tool_name='read_file', result=ToolResult.success({'path': '/a.py'}))
        visible.extend(projection.accept(AgentEvent('skill_event', run_id='main', child_event=event)))
    visible.extend(projection.accept(AgentEvent('text_delta', run_id='main', text='正文')))
    batches = [event for event in visible if event.text.startswith('工具>')]
    assert len(batches) == 2 and all(state.history.current.id in event.text for event in batches)
    assert all(event.run_id == 'main' for event in batches)
    assert 'child-one' in batches[0].text and 'child-two' in batches[1].text
    assert not projection.accept(AgentEvent('skill_event', run_id='main', child_event=event))


def test_1500_distinct_calls_are_counted_exactly_with_bounded_tombstones():
    state = TerminalState()
    begin(state, 'run')
    for number in range(1500):
        tool(state, 'run', identity=f'id-{number}')
    turn = state.history.current
    assert turn.total_calls == 1500 and turn.total_calls_complete
    assert state.history.metadata_bytes <= state.history.metadata_limit
    assert len(turn.calls) <= 1024 and len(state._tools) == len(turn.calls)
    assert len(turn._seen) == 1500
    for number in (0, 500, 1499):
        tool(state, 'run', identity=f'id-{number}')
    assert turn.total_calls == 1500
    finish(state, 'run')
    assert not turn._seen and state.history.metadata_bytes <= state.history.metadata_limit


def test_identity_budget_saturation_is_explicit_and_reset_preserves_browser_reference():
    state = TerminalState()
    history = state.history
    history.tombstone_limit = 3
    begin(state, 'run')
    for number in range(5):
        tool(state, 'run', identity=str(number))
    turn = history.current
    assert turn.total_calls == 3 and not turn.total_calls_complete
    assert turn.unindexed_events == 4 and turn.index_evicted
    assert '至少' in history.text() and '无法确认新身份' in history.text()
    state.reset_task()
    assert state.history is history and history.current is None and not history.turns
    state.update(AgentEvent('tool_result', run_id='run', tool_call_id='late',
        result=ToolResult.success({})))
    assert not history.turns


def test_index_saturation_keeps_independent_failure_visible():
    from mewcode.terminal.projection import TerminalProjection
    state = TerminalState()
    state.history.tombstone_limit = 1
    begin(state, 'run')
    tool(state, 'run', identity='known')
    projection = TerminalProjection(state)
    events = projection.accept(AgentEvent('tool_result', run_id='run', tool_call_id='omitted',
        tool_name='execute_command', result=ToolResult.failure('timeout', '仍须可见的超时')))
    assert len(events) == 1 and '仍须可见的超时' in events[0].text
    assert '索引未保留' in events[0].text


def test_late_permission_request_does_not_rewind_history_call_status():
    from types import SimpleNamespace
    state = TerminalState()
    begin(state, 'run')
    request = SimpleNamespace(id='req')
    state.update(AgentEvent('permission_resolved', run_id='run', tool_call_id='id',
        tool_name='write_file', permission_request=request, permission_decision='once'))
    state.update(AgentEvent('permission_requested', run_id='run', tool_call_id='id',
        tool_name='write_file', permission_request=request))
    assert state.history.current.calls[0].status == '已批准，未启动'


def test_long_call_identity_normalization_is_stable_across_projection_state_history():
    from mewcode.terminal.history import bounded_id
    from mewcode.terminal.projection import TerminalProjection
    identity = 'x' * 600
    normalized = bounded_id(identity)
    assert len(normalized) <= 512 and bounded_id(normalized) == normalized
    state = TerminalState()
    begin(state, 'run')
    projection = TerminalProjection(state)
    projection.accept(AgentEvent('tool_call', run_id='run', tool_call_id=identity,
        tool_name='read_file', call=ToolCall(identity, 'read_file', '{}')))
    projection.accept(AgentEvent('tool_result', run_id='run', tool_call_id=identity,
        tool_name='read_file', result=ToolResult.success({})))
    assert len(state.history.current.calls) == 1
    assert state.history.current.calls[0].result is not None
    assert '#1' in projection.flush()[0].text


def test_redacted_labels_keep_distinct_safe_selection_ids_and_result_truncation_fact():
    state = TerminalState('private')
    begin(state, 'run')
    tool(state, 'run', identity='private')
    state.update(AgentEvent('tool_result', run_id='run', tool_call_id='[已隐藏]',
        tool_name='read_file', result=ToolResult.success({'content': '部分'}, truncated=True)))
    calls = state.history.current.calls
    assert len({call.id for call in calls}) == 2
    assert all('private' not in call.id for call in calls)
    assert calls[1].original_result_truncated and calls[1].attention


def test_background_status_and_maintenance_notices_do_not_start_turns():
    from mewcode.terminal.projection import TerminalProjection
    state = TerminalState()
    projection = TerminalProjection(state)
    for kind in ('task_status', 'memory_update', 'skill_loaded', 'history_trimmed'):
        projection.accept(AgentEvent(kind, run_id='background', text='后台通知'))
    projection.accept(AgentEvent('progress', run_id='maintenance', phase='summary'), maintenance=True)
    assert state.history.current is None and not state.history.turns


def test_error_side_effect_unknown_survives_evicted_result_body():
    state = TerminalState()
    state.history.details.body_limit = 1
    begin(state, 'run')
    state.update(AgentEvent('tool_result', run_id='run', tool_call_id='cancelled',
        tool_name='execute_command', result=ToolResult.failure('cancelled', '执行中取消',
        details={'side_effects_may_have_occurred': True, 'not_started': False})))
    call = state.history.current.calls[0]
    assert call.result.evicted and call.attention
    assert call.references['side_effects_may_have_occurred'] is True
    assert call.references['not_started'] is False
