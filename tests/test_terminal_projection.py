"""降噪投影和最近详情的行为合同。"""

import json

from mewcode.terminal.state import TerminalState
from mewcode.tools.base import ToolResult
from mewcode.types import AgentEvent, ToolCall, TokenUsage


def projection():
    from mewcode.terminal.projection import TerminalProjection
    return TerminalProjection(TerminalState('private-key'))


def call(p, identity, name='read_file', path=None, result=None):
    p.accept(AgentEvent('tool_call', run_id='r', tool_call_id=identity, tool_name=name,
        call=ToolCall(identity, name, json.dumps({'path': path}))))
    return p.accept(AgentEvent('tool_result', run_id='r', tool_call_id=identity, tool_name=name,
        result=result if result is not None else ToolResult.success({'path': path})))


def test_success_batch_flushes_before_answer_and_deduplicates():
    p = projection()
    for i in range(3):
        assert not call(p, str(i), path=f'/project/{i}.py')
    assert not call(p, 'search', 'search_code')
    assert not p.accept(AgentEvent('tool_result', run_id='r', tool_call_id='search',
        tool_name='search_code', result=ToolResult.success({})))
    events = p.accept(AgentEvent('text_delta', run_id='r', text='回答'))
    assert len(events) == 2 and '已读取 3 个文件' in events[0].text
    assert '完成 1 次搜索' in events[0].text and events[1].text == '回答'
    assert all(str(i) in p.state.details_text('tools') for i in range(3))


def test_duplicate_target_and_unknown_identity_do_not_invent_file_count():
    p = projection()
    call(p, '1', path='/project/a')
    call(p, '2', path='/project/a')
    text = p.flush()[0].text
    assert '1 个文件' in text and '2 次调用' in text
    call(p, '3', path='relative')
    assert '1 次读取' in p.flush()[0].text


def test_limited_success_and_warning_remain_individual_between_batches():
    p = projection()
    call(p, '1', path='/project/a')
    events = call(p, '2', 'search_code', result=ToolResult.success(
        {'permission_limited': True, 'skipped_files': 2}))
    assert len(events) == 2 and '已读取' in events[0].text and '搜索范围受限' in events[1].text
    warning = AgentEvent('permission_resolved', run_id='r', tool_call_id='3',
        permission_decision='permanent', warning='永久保存失败')
    assert '永久未保存' in p.accept(warning)[0].text
    assert not p.accept(warning)


def test_thinking_is_collapsed_and_split_secret_never_leaks():
    p = projection()
    for text in ['想一想 private-', 'key\x1b[31m\n答案']:
        assert not p.accept(AgentEvent('thinking_delta', run_id='r', text=text))
    assert '思考' in p.state.compact_status()
    p.accept(AgentEvent('text_delta', run_id='r', text='正文'))
    details = p.state.details_text('thinking')
    assert '[已隐藏]' in details and 'private-' not in details and '\x1b' not in details
    assert not p.accept(AgentEvent('thinking_delta', run_id='r', purpose='summary', text='内部摘要'))
    assert '内部摘要' not in p.state.details_text('thinking')


def test_details_budget_evicts_body_but_retains_identity_and_reference():
    from mewcode.terminal.details import DetailStore
    details = DetailStore('', body_limit=30, thinking_limit=12, tool_limit=24)
    details.put('one', '调用 #1 · cache/file.json', '中文' * 8)
    details.put('two', '调用 #2', '第二次结果' * 5)
    details.think('r', '思考', '思' * 12)
    assert details.body_bytes <= 30 and details.thinking_bytes <= 12
    text = details.text('tools')
    assert '#1' in text and 'cache/file.json' in text and '已不可用' in text
    assert '截断' in details.text('thinking')
    assert '\ufffd' not in text


def test_parent_child_usage_maintenance_and_new_task_lifecycle():
    p = projection()
    p.accept(AgentEvent('progress', run_id='r', phase='model', iteration=1))
    p.accept(AgentEvent('skill_event', run_id='r', child_event=AgentEvent('usage',
        run_id='child', skill_name='test', iteration=2, usage=TokenUsage(20, 3))))
    p.accept(AgentEvent('usage', run_id='r', iteration=2, usage=TokenUsage(20, 3)))
    assert p.state._total_usage.input_tokens == 20
    p.accept(AgentEvent('thinking_delta', run_id='r', text='保留'))
    assert not p.accept(AgentEvent('memory_update', text='记忆更新完成'), maintenance=True)
    assert '保留' in p.state.details_text('thinking')
    p.accept(AgentEvent('progress', run_id='next', phase='model', iteration=1))
    assert '保留' not in p.state.details_text('thinking')
    assert not p.accept(AgentEvent('text_delta', run_id='r', text='迟到正文'))
    p.state.reset_task()
    assert '暂无' in p.state.details_text('tools')


def test_approval_first_page_contains_action_and_has_no_duplicate_decisions():
    from types import SimpleNamespace
    from mewcode.terminal.approval import ApprovalView
    request = SimpleNamespace(id='id', tool='write_file', targets=('/project/a',),
        arguments={'path': 'a', 'content': 'REVIEW THIS CONTENT'}, reason='需要批准', mode='strict')
    view = ApprovalView(request, root='/project')
    first = view.page('summary', 0, width=100, height=8)[0]
    assert 'REVIEW THIS CONTENT' in first and '/project/a' in first
    assert '1 拒绝' not in view.summary()
    assert '未来同一路径' in view.detail('scope')
    request.tool = 'execute_command'
    request.arguments = {'command': 'echo ' + 'x' * 300}
    view = ApprovalView(request, root='/project')
    assert request.arguments['command'] in view.summary()


def test_real_read_result_identity_and_duplicate_finish():
    p = projection()
    for i in range(3):
        call(p, str(i), path=f'{i}.py', result=ToolResult.success(
            {'path': f'{i}.py', 'content': 'data', 'start_line': 1, 'end_line': 1}))
    finish = AgentEvent('finished', run_id='r', reason='model_done', usage=TokenUsage())
    events = p.accept(finish)
    assert len(events) == 2 and '3 个文件' in events[0].text
    assert not p.accept(finish)


def test_json_escaped_secrets_are_removed_from_bounded_details():
    from mewcode.terminal.details import DetailStore
    # 密钥可能带引号或反斜杠；JSON 序列化后仍须隐藏。
    secret = 'fictional\\"secret'
    details = DetailStore(secret)
    details.put('a', '参数', json.dumps({'payload': secret, 'nested': [secret]}))
    body = details.text('tools')
    assert '[已隐藏]' in body and 'fictional' not in body


def test_hook_notice_preserves_details_usage_tool_batch_and_original_association():
    p = projection()
    call(p, 'one', path='/project/a')
    p.accept(AgentEvent('usage', run_id='r', usage=TokenUsage(20, 3)))
    details, usage = p.state.details, p.state._total_usage
    text = p.state.details_text('tools')
    visible = p.accept(AgentEvent('hook_notice', run_id='old', parent_run_id='root',
        hook_source='hooks.yaml#hooks[2]', hook_event='tool.after', text='后台通知private-key\x1b[31m'))
    assert len(visible) == 1 and visible[0].run_id == 'old' and visible[0].parent_run_id == 'root'
    assert 'Hook' in visible[0].text and 'hooks[2]' in visible[0].text and 'tool.after' in visible[0].text
    assert '\x1b' not in visible[0].text and 'private-key' not in visible[0].text
    assert p.state.details is details and p.state._total_usage == usage
    assert p.state.details_text('tools') == text
    assert len(p.flush()) == 1 and len(p.state._tools) == 1
