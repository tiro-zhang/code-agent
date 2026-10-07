"""只读浏览状态与快照更新、分页边界。"""

from types import SimpleNamespace
import json

from prompt_toolkit.utils import get_cwidth

from mewcode.terminal.browser import DetailsBrowser


def test_saturated_identity_index_displays_lower_bound_and_omitted_events():
    from mewcode.terminal.state import TerminalState
    from test_terminal_history import begin, tool
    state = TerminalState()
    state.history.tombstone_limit = 1
    begin(state, 'run')
    tool(state, 'run', identity='first')
    tool(state, 'run', identity='second')
    view = DetailsBrowser(state.history)
    view.open()
    text = view.render(110, 15)
    assert '至少 1 次' in text and '未索引事件' in text


class Cache:
    def __init__(self):
        self.views = self.pages = None
        self.thoughts = ()

    def cache_view(self, key, value):
        self.views = key, value
        return value

    def get_view(self, key):
        return self.views[1] if self.views and self.views[0] == key else None

    def clear_view(self):
        self.views = None

    def cache_pages(self, key, value):
        self.pages = key, value
        return True

    def get_pages(self, key):
        return self.pages[1] if self.pages and self.pages[0] == key else None

    def clear_pages(self):
        self.pages = None


def entry(body, **changes):
    return SimpleNamespace(body=body, truncated=False, evicted=False, version=1, title='', **changes)


def call(identifier, *, failure=False, text='one\ntwo\nthree\n'):
    return SimpleNamespace(id=identifier, original_id=identifier, number=1, name='read_file',
        arguments=entry(json.dumps({'path': identifier + '.txt'})),
        result=entry(json.dumps({'ok': not failure, 'data': {'content': text}, 'error': None})),
        attention=failure, status='失败' if failure else '成功', source='主任务', operation=identifier + '.txt')


def turn(identifier, calls):
    return SimpleNamespace(id=identifier, title=identifier + '问题', started_at=0, status='执行段结束',
        source='用户', parent_id='', calls=calls, total_calls=len(calls), index_evicted=False,
        thinking=(), reason='')


def history():
    old = turn('old', [call('old1'), call('old2', failure=True)])
    recent = turn('recent', [call('recent1'), call('recent2')])
    result = SimpleNamespace(turns=[old, recent], current=recent, secret='', details=Cache())
    result.get = lambda identifier: next((t for t in result.turns if t.id == identifier), None)
    return result


def browser():
    h = history()
    return DetailsBrowser(h), h


def test_default_selection_layer_returns_and_reopening_keep_identity():
    view, h = browser()
    view.open()
    assert view.turn_id == 'recent' and view.call_id == 'recent1'
    view.handle('down')
    view.handle('enter')
    assert view.view == 'detail' and view.call_id == 'recent2'
    assert 'recent2' in view.render(80, 12)
    view.handle('escape')
    assert view.view == 'call_list'
    view.handle('escape')
    assert not view.is_open
    view.open()
    assert view.call_id == 'recent2'
    view.handle('t')
    view.handle('up')
    view.handle('enter')
    assert view.turn_id == 'old'
    view.handle('f2')
    assert not view.is_open


def test_list_search_exception_filter_and_unavailable_notice():
    view, h = browser()
    view.open()
    view.handle('t'); view.handle('up'); view.handle('enter')
    view.handle('e')
    assert 'old2' in view.render(90, 10) and 'old1' not in view.render(90, 10)
    view.handle('/')
    view.insert_search('missing\n2')
    assert view.searching and '\n' not in view.search_text
    view.handle('enter')
    assert '无匹配' in view.render(90, 10)
    view.handle('/'); view.handle('escape')
    assert not view.searching and view.is_open
    h.current.index_evicted = True
    view.handle('t'); view.handle('down'); view.handle('enter')
    assert '索引已移出' in view.render(90, 10)


def test_search_raw_and_logical_anchor_survive_resize_updates():
    view, h = browser()
    h.current.calls[0].result.body = json.dumps({'ok': True, 'data': {'content': '\n'.join(f'line{i} longword' for i in range(60))}})
    view.open(); view.handle('enter')
    view.render(30, 8)
    view.handle('pagedown')
    anchor = view.anchor
    view.render(15, 8)
    assert view.anchor == anchor
    view.handle('/')
    view.insert_search('line30')
    view.handle('enter')
    assert 'line30' in view.render(40, 8)
    matched = view.anchor
    view.handle('n')
    assert view.anchor != matched
    view.handle('N')
    assert view.anchor == matched
    view.handle('r'); assert '原始 JSON' in view.render(40, 8)
    view.handle('r'); assert view.anchor == matched
    new = turn('next', [call('next1')])
    h.turns.append(new); h.current = new
    assert view.turn_id == 'recent' and view.call_id == 'recent1'
    assert 'line30' in view.render(40, 8)


def test_evicted_body_and_removed_turn_do_not_silently_jump():
    view, h = browser()
    view.open(); view.handle('enter')
    h.current.calls[0].result.body = ''
    h.current.calls[0].result.evicted = True
    h.current.calls[0].result.version += 1
    assert '已移出' in view.render(90, 10) and view.call_id == 'recent1'
    h.turns.pop(); h.current = h.turns[-1]
    text = view.render(90, 10)
    assert '所选轮次已移出' in text and view.turn_id == 'old'


def test_thinking_and_small_height_keep_navigation():
    view, h = browser()
    view.open(); view.handle('tab')
    assert view.section == 'thinking' and '暂无 API 思考' in view.render(40, 7)
    assert 'F2' in view.render(10, 1)
    assert len(view.render(25, 5).splitlines()) <= 5


def test_fragmented_search_secret_never_displays_and_raw_identity_is_preserved():
    view, h = browser()
    h.secret = 'secret-key'
    view.open(); view.handle('/')
    view.insert_search('secret-'); view.insert_search('key')
    assert 'secret-key' not in view.render(90, 10)
    view.handle('enter')
    assert view._query == '[已隐藏]'
    view.handle('escape'); view.open(); view.handle('enter')
    assert 'recent1' in view._body()
    view.handle('r')
    assert 'recent1' in view._body() and '主任务' in view._body()


def test_thought_record_has_safe_searchable_body_and_eviction():
    view, h = browser()
    thought = SimpleNamespace(id='thought1', name='API 思考 1', source='主任务',
                              entry=entry('thought body\nsecond line'))
    h.current.thoughts = [thought]
    view.open(); view.handle('tab'); view.handle('enter')
    assert 'thought body' in view.render(80, 10)
    view.handle('/'); view.insert_search('second'); view.handle('enter')
    assert 'second line' in view.render(80, 10)
    thought.entry.body = ''
    thought.entry.evicted = True
    thought.entry.version += 1
    assert '已移出' in view.render(80, 10)


def test_selected_call_position_restores_after_switching_rounds():
    view, h = browser()
    view.open(); view.handle('enter'); view.render(15, 6); view.handle('pagedown')
    position = view.anchor
    view.handle('t'); view.handle('up'); view.handle('enter')
    view.handle('t'); view.handle('down'); view.handle('enter'); view.handle('enter')
    assert view.anchor == position


def real_state():
    from mewcode.terminal.state import TerminalState
    from mewcode.types import AgentEvent, ToolCall
    from mewcode.tools.base import ToolResult
    state = TerminalState('secret-key')
    state.begin_task(mode='execute', permission_mode='default', max_iterations=20, title='真实轮次')
    state.update(AgentEvent('progress', run_id='real', phase='model', iteration=1))
    for identity, result in [('ok', ToolResult.success({'content': '正文\nline two', 'path': '中文.txt'})),
                             ('fail', ToolResult.failure('timeout', '超时',
                                data={'cache_path': '/tmp/retained', 'side_effects_may_have_occurred': True}))]:
        state.update(AgentEvent('tool_call', run_id='real', tool_call_id=identity, tool_name='read_file',
                               call=ToolCall(identity, 'read_file', '{"path":"中文.txt"}')))
        state.update(AgentEvent('tool_result', run_id='real', tool_call_id=identity, tool_name='read_file', result=result))
    return state


def test_actual_history_view_budget_raw_search_and_runtime_stay_independent(monkeypatch):
    from pathlib import Path
    state = real_state()
    monkeypatch.setattr(Path, 'read_text', lambda *a, **k: (_ for _ in ()).throw(AssertionError('禁止缓存读取')))
    view = DetailsBrowser(state.history)
    phase, iteration = state.phase, state.iteration
    view.open(); view.handle('e'); view.handle('enter')
    assert view._call().status == '超时'
    assert '/tmp/retained' in view._body() and 'side_effects_may_have_occurred' in view._body()
    view.handle('/'); view.insert_search('retained'); view.handle('enter'); view.handle('n')
    assert 'retained' in view.render(80, 12)
    view.handle('r'); view.render(25, 7); view.handle('r'); view.render(80, 12)
    details = state.history.details
    assert details.body_bytes <= details.body_limit and details.view_bytes <= details.view_cache_limit
    assert state.phase == phase and state.iteration == iteration and state.run_id == 'real'


def test_actual_history_tiny_cache_retains_reference_and_eviction_reason():
    state = real_state()
    view = DetailsBrowser(state.history)
    view.open(); view.handle('down'); view.handle('enter')
    result = view._call().result
    state.history.details.body_limit = 1
    state.history.details._evict()
    assert result.evicted
    # 恢复足够容量后只展示现存身份与原因，不重载被移出的结果。
    state.history.details.body_limit = 4096
    text = view._body()
    assert '已移出' in text and '/tmp/retained' in text


def test_exception_filter_includes_incomplete_argument_snapshot():
    view, h = browser()
    h.current.calls[1].arguments.truncated = True
    view.open(); view.handle('e')
    text = view.render(90, 10)
    assert 'recent2' in text and 'recent1' not in text


def test_long_turn_titles_keep_state_source_and_resume_parent_visible():
    view, h = browser()
    h.current.title = '很长的问题标题' * 40
    h.current.started_at = '2026-10-07T05:17:16+00:00'
    h.current.status = '执行段结束'
    h.current.source = '自动接续'
    h.current.parent_id = 'parent_1234567890abcdef'
    view.open(); view.handle('t')
    for width in (110, 45):
        selected = next(line for line in view.render(width, 8).splitlines() if line.startswith('> '))
        assert '05:17:16' in selected and '结束' in selected
        assert '自动接续' in selected and 'abcdef' in selected
        assert '很长' in selected
        assert get_cwidth(selected) <= width


def test_long_call_operation_preserves_status_and_searches_full_metadata():
    view, h = browser()
    item = h.current.calls[1]
    item.name = 'execute_command'
    item.operation = 'printf LONG_' * 80 + 'OPERATION_NEEDLE'
    item.source = 'long-skill-source'
    item.status = '超时'
    item.original_id = 'complete-call-id-NEEDLE'
    view.open(); view.handle('down')
    for width in (110, 45):
        selected = next(line for line in view.render(width, 8).splitlines() if line.startswith('> '))
        assert '#1' in selected and '超时' in selected and 'execute_command' in selected
        assert get_cwidth(selected) <= width
    for query in ('OPERATION_NEEDLE', 'long-skill-source', 'complete-call-id-NEEDLE'):
        view.handle('/')
        view.search_text = ''
        view.insert_search(query); view.handle('enter')
        assert view.call_id == item.id and '无匹配' not in view.render(45, 8)
