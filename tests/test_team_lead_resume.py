"""Lead 恢复使用团队持有的合法历史与原目标预算。"""
import asyncio
from dataclasses import replace

import pytest

from conftest import ScriptedProvider, async_test, collect
from test_team_service import make_session
from mewcode.session import ChatSession
from mewcode.tools.base import ToolResult
from mewcode.types import Message, ProviderEvent


def answer(text='已了解'):
    return [ProviderEvent('completed', message=Message('assistant', text))]


def reopen(previous):
    return ChatSession(ScriptedProvider([answer(), answer(), answer()]), executor=previous.executor,
                       config=previous.config, user_root=previous.user_root, memory_enabled=False)


@async_test
async def test_goal_user_inputs_keep_same_parent_and_budget(tmp_path):
    session = make_session(tmp_path)
    session.provider.responses = iter([answer(), answer()])
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    goal = await session.teams.control({'action': 'goal', 'description': '完成长期目标'})
    try:
        await collect(session.ask('先检查现状'))
        parent = session.teams.goal_parent
        await collect(session.ask('再检查下一项'))
        assert session.teams.goal_parent is parent
        assert session._task_context is parent and parent.budget.used == 2
        assert not parent.finished
        assert session.teams.store.load('alpha').goals[goal['goal_id']].budget_used == 2
    finally:
        await session.aclose()


@async_test
async def test_resume_restores_history_consumed_ids_budget_without_request(tmp_path):
    session = make_session(tmp_path)
    session.provider.responses = iter([answer('保存的 Lead 回复')])
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    await session.teams.control({'action': 'goal', 'description': '长期目标'})
    await collect(session.ask('目标补充'))
    team = session.teams.store.load('alpha')
    mailbox = session.teams.inbox()
    mailbox.notify = None
    delivery = await mailbox.send(team.lead_id, team.lead_id, '保存的通知')
    await session.teams.consume_lead()
    old_parent = session.teams.goal_parent
    await session.aclose()
    resumed = reopen(session)
    try:
        await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
        assert not resumed.provider.requests
        assert resumed.next_parent() is None
        assert delivery.message_id in resumed.teams.consumed_ids
        assert any(m.content == '保存的 Lead 回复' for m in resumed.history)
        assert sum(m.id == 'mail-' + delivery.message_id for m in resumed.history) == 1
        assert resumed.teams.goal_parent.task_id == old_parent.task_id
        assert resumed.teams.goal_parent.budget.used == 1
        assert resumed.teams.goal_parent.budget.limit == old_parent.budget.limit
        await collect(resumed.ask('明确继续'))
        assert resumed.teams.goal_parent.budget.used == 2
        assert resumed.teams.goal_parent.task_id == old_parent.task_id
    finally:
        await resumed.aclose()


@async_test
async def test_mail_ack_failure_reconciles_after_lead_resume(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    team = session.teams.store.load('alpha')
    mailbox = session.teams.inbox()
    mailbox.notify = None
    delivery = await mailbox.send(team.lead_id, team.lead_id, '只应消费一次')
    from mewcode.teams.mailbox import Mailbox
    original = Mailbox.ack
    async def fail(*args, **kwargs):
        raise OSError('模拟确认失败')
    monkeypatch.setattr(Mailbox, 'ack', fail)
    with pytest.raises(OSError):
        await session.teams.consume_lead()
    monkeypatch.setattr(Mailbox, 'ack', original)
    await session.aclose()
    resumed = reopen(session)
    try:
        await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
        await resumed.teams.consume_lead()
        assert sum(m.id == 'mail-' + delivery.message_id for m in resumed.history) == 1
        assert not await resumed.teams.inbox().read(team.lead_id)
    finally:
        await resumed.aclose()


@async_test
async def test_exhausted_goal_does_not_refresh_for_user_input(tmp_path):
    session = make_session(tmp_path)
    session.agent.max_iterations = 1
    session.provider.responses = iter([answer(), answer()])
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    await session.teams.control({'action': 'goal', 'description': '额度一次'})
    try:
        await collect(session.ask('第一项'))
        await collect(session.ask('第二项'))
        assert len(session.provider.requests) == 1
        assert session.teams.goal_parent.budget.used == 1
    finally:
        await session.aclose()


@async_test
async def test_bare_do_and_resume_do_not_wake_goal_or_workers(tmp_path):
    session = make_session(tmp_path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    await session.teams.control({'action': 'goal', 'description': '等待明确输入'})
    await session.set_mode('plan')
    await session.set_mode('execute')
    assert not session.provider.requests
    assert session.next_parent() is None
    await session.aclose()


@async_test
async def test_budget_reservation_saved_before_provider_and_survives_pause(tmp_path):
    session = make_session(tmp_path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    await session.teams.control({'action': 'goal', 'description': '请求先记录'})
    checked = []
    async def response():
        data = session.teams.store.read_json('alpha', session.teams.lead_control_path())
        checked.append(data['parent']['used'])
        yield ProviderEvent('completed', message=Message('assistant', '请求已保存'))
    session.provider.responses = iter([response])
    try:
        await collect(session.ask('检查先后顺序'))
        assert checked == [1]
    finally:
        await session.aclose()


@async_test
async def test_lead_snapshot_failure_blocks_request_and_preserves_unread_mail(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    await session.teams.control({'action': 'goal', 'description': '保存故障'})
    team = session.teams.store.load('alpha')
    mailbox = session.teams.inbox()
    mailbox.notify = None
    await mailbox.send(team.lead_id, team.lead_id, '未持久消费')
    original = session.teams.store.write_json
    def fail(name, relative, data):
        if str(relative).endswith('lead-control.json'):
            raise OSError('模拟检查点保存失败')
        return original(name, relative, data)
    monkeypatch.setattr(session.teams.store, 'write_json', fail)
    with pytest.raises(OSError):
        await collect(session.ask('不得发起请求'))
    assert not session.provider.requests
    assert await mailbox.read(team.lead_id)
    assert not session.teams.consumed_ids
    assert session.agent.storage_blocked
    monkeypatch.setattr(session.teams.store, 'write_json', original)
    await session.aclose()


@async_test
async def test_lead_cache_kept_in_team_private_directory(tmp_path):
    session = make_session(tmp_path)
    cache = session.context.cache
    path = cache.save(Message('tool', tool_result=ToolResult.success({'body': '长期原话'})), 'read_file')
    session.context.summary_files.add(path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    assert session.context.cache.storage_root.is_relative_to(session.teams.store.team_path('alpha'))
    await session.aclose()
    resumed = reopen(session)
    try:
        await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
        restored = next(iter(resumed.context.summary_files))
        assert resumed.context.cache.restore(restored).data['body'] == '长期原话'
        assert not resumed.provider.requests
    finally:
        await resumed.aclose()


@async_test
async def test_missing_lead_cache_refuses_resume(tmp_path):
    session = make_session(tmp_path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    path = session.context.cache.save(Message('tool', tool_result=ToolResult.success('必要原话')), 'read_file')
    session.context.summary_files.add(path)
    directory = session.context.cache.directory
    await session.aclose()
    (directory / path.split('/')[-1]).unlink()
    resumed = reopen(session)
    try:
        with pytest.raises((ValueError, OSError)):
            await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
        assert not resumed.provider.requests
    finally:
        await resumed.aclose()


@async_test
async def test_lead_prompt_protocol_help_preserves_existing_workspace_notice(tmp_path):
    session = make_session(tmp_path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    session.prompt_state.workspace_notice = '实际冲突目录：/tmp/actual-conflict'
    try:
        session._before_request()
        first = session.prompt_state.workspace_notice
        session._before_request()
        assert session.prompt_state.workspace_notice == first
        assert '实际冲突目录：/tmp/actual-conflict' in first
        for token in ('team goal', 'team_task create', 'team_member spawn', 'reassign',
                      'task_assignment', 'claim_id', 'plan_request', 'plan_decision',
                      'approved', 'validation_command', 'integrate', 'finalize'):
            assert token in first
        assert session.team_scope.member_id in first
        assert not session.provider.requests
    finally:
        await session.aclose()


@async_test
async def test_plan_stops_member_and_bare_execute_does_not_restart_it(tmp_path):
    session = make_session(tmp_path)
    session.provider_factory = lambda config: ScriptedProvider([answer('尚未运行')])
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    goal = await session.teams.control({'action': 'goal', 'description': '保留可恢复目标'})
    member = await session.teams.member({'action': 'spawn', 'name': 'alice', 'role': 'general', 'backend': 'inprocess'})
    try:
        await session.set_mode('plan')
        saved = session.teams.store.load('alpha')
        assert saved.status == 'active' and saved.mode == 'plan'
        assert saved.goals[goal['goal_id']].state == 'active'
        assert saved.members[member['member_id']].state == 'idle'
        assert saved.members[member['member_id']].resume_allowed
        assert not session.teams.runners
        await session.set_mode('execute')
        await asyncio.sleep(.3)
        assert not session.teams.runners and not session.provider.requests
    finally:
        await session.aclose()


@async_test
async def test_cancelled_goal_restores_same_identity_and_remaining_budget(tmp_path):
    session = make_session(tmp_path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    goal = await session.teams.control({'action': 'goal', 'description': '取消后明确接续'})
    cancel = asyncio.Event()
    async def cancel_request():
        cancel.set()
        yield ProviderEvent('completed', message=Message('assistant', '取消信号已触发'))
    session.provider.responses = iter([cancel_request])
    events = await collect(session.ask('取消这一段', cancel_event=cancel))
    assert any(event.kind == 'finished' and event.reason == 'cancelled' for event in events)
    parent = session.teams.goal_parent
    assert session.teams.store.load('alpha').goals[goal['goal_id']].state == 'cancelled'
    assert not parent.wake_allowed
    await session.aclose()
    resumed = reopen(session)
    try:
        await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
        assert resumed.teams.goal_parent.task_id == parent.task_id
        assert resumed.teams.goal_parent.budget.used == parent.budget.used
        assert resumed.next_parent() is None
        assert not resumed.provider.requests
    finally:
        await resumed.aclose()


@async_test
async def test_provider_failure_preserves_reserved_goal_budget(tmp_path):
    session = make_session(tmp_path)
    session.provider.responses = iter([[RuntimeError('模拟请求已经发出但失败')]])
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    await session.teams.control({'action': 'goal', 'description': '失败请求也计数'})
    try:
        await collect(session.ask('触发失败请求'))
    except RuntimeError:
        pass
    parent = session.teams.goal_parent
    assert parent.budget.used == 1
    await session.aclose()
    resumed = reopen(session)
    try:
        await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
        assert resumed.teams.goal_parent.budget.used == 1
        assert not resumed.provider.requests
    finally:
        await resumed.aclose()


@async_test
async def test_lead_signed_provider_content_refuses_changed_model(tmp_path):
    session = make_session(tmp_path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    session.history.append(Message('assistant', '签名状态', provider_content=({'type': 'reasoning', 'id': 'saved'},)))
    await session.aclose()
    resumed = reopen(session)
    resumed.config = replace(resumed.config, model='another-model')
    try:
        with pytest.raises((ValueError, OSError)):
            await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
        assert not resumed.provider.requests
    finally:
        await resumed.aclose()


@async_test
async def test_restore_budget_uses_max_shared_and_private_without_refresh(tmp_path):
    session = make_session(tmp_path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    goal = await session.teams.control({'action': 'goal', 'description': '共享预算优先'})
    await session.teams.store.update_team('alpha', lambda team: setattr(team.goals[goal['goal_id']], 'budget_used', 3))
    session.teams.goal_parent.budget.used = 3
    path = session.teams.lead_control_path()
    await session.aclose()
    data = session.teams.store.read_json('alpha', path)
    data['parent']['used'] = 1
    session.teams.store.write_json('alpha', path, data)
    resumed = reopen(session)
    try:
        await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
        assert resumed.teams.goal_parent.budget.used == 3
        assert resumed.teams.goal_parent.budget.limit == goal['budget_limit']
        assert not resumed.provider.requests
    finally:
        await resumed.aclose()


@async_test
async def test_lead_can_read_registered_private_cache_without_expanding_tool_root(tmp_path):
    import json
    session = make_session(tmp_path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    path = session.context.cache.save(Message('tool', tool_result=ToolResult.success('Lead 长期原话')), 'read_file')
    try:
        result = await session.executor.execute('read_file', json.dumps({'path': path}))
        assert result.ok and 'Lead 长期原话' in result.data['content']
        outside = await session.executor.execute('read_file', json.dumps({'path': str(session.teams.store.team_path('alpha') / 'team.json')}))
        assert not outside.ok or 'Lead 长期原话' not in outside.data.get('content', '')
        assert session.executor.context.root == tmp_path
    finally:
        await session.aclose()


@async_test
async def test_model_team_create_hands_off_real_tool_intent_before_result(tmp_path):
    import json
    from mewcode.types import ToolCall
    session = make_session(tmp_path)
    session.provider.responses = iter([
        [ProviderEvent('completed', message=Message('assistant', tool_calls=(
            ToolCall('create-call', 'team', '{"action":"create","name":"alpha"}'),)))],
        [ProviderEvent('completed', message=Message('assistant', tool_calls=(
            ToolCall('goal-call', 'team', '{"action":"goal","description":"真实模型目标"}'),)))],
        answer('真实批次已提交'),
    ])
    try:
        await collect(session.ask('建立团队完成目标'))
        records = [json.loads(line) for line in session.journal.path.read_text().splitlines()]
        intent = next(record for record in records if record['kind'] == 'interaction_started' and
                      record['payload']['messages'][-1]['tool_calls'][0]['id'] == 'create-call')
        result = next(record for record in records if record['kind'] == 'tool_result' and
                      record['payload']['message']['tool_call_id'] == 'create-call')
        assert intent['seq'] < result['seq']
        assert session.teams.goal_parent.budget.used == len(session.provider.requests)
    finally:
        await session.aclose()


@async_test
async def test_model_pause_keeps_journal_until_real_batch_result_committed(tmp_path):
    from mewcode.types import ToolCall
    session = make_session(tmp_path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    await session.teams.control({'action': 'goal', 'description': '暂停保存完整配对'})
    lead_id = session.team_scope.member_id
    parent_id = session.teams.goal_parent.task_id
    session.provider.responses = iter([
        [ProviderEvent('completed', message=Message('assistant', tool_calls=(
            ToolCall('pause-call', 'team', '{"action":"pause"}'),)))], answer('暂停已保存'),
    ])
    await collect(session.ask('明确暂停团队'))
    assert len(session.provider.requests)==1
    assert session.team_scope is None
    assert session.teams.store.load('alpha').status == 'paused'
    await session.aclose()
    resumed = reopen(session)
    try:
        await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
        assert resumed.team_scope.member_id == lead_id
        assert resumed.teams.goal_parent.task_id == parent_id
        assert any(m.tool_call_id == 'pause-call' and m.tool_result.ok for m in resumed.history)
        from mewcode.context.partition import validate_pairs
        validate_pairs(resumed.history)
        assert not resumed.provider.requests
    finally:
        await resumed.aclose()


@async_test
async def test_lead_resume_rejects_authoritative_journal_reference_tampering(tmp_path):
    session = make_session(tmp_path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    path = session.teams.lead_control_path()
    await session.aclose()
    data = session.teams.store.read_json('alpha', path)
    from mewcode.sessions import Journal
    team = session.teams.store.load('alpha')
    alternate = Journal.create(tmp_path, session.config.protocol, session.config.model,
                               storage_root=session.teams.store.member_path('alpha', team.lead_id))
    data['journal_id'] = alternate.id
    alternate.close()
    session.teams.store.write_json('alpha', path, data)
    resumed = reopen(session)
    try:
        with pytest.raises((ValueError, OSError)):
            await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
        assert not resumed.provider.requests
    finally:
        await resumed.aclose()


@async_test
async def test_detached_goal_budget_cannot_reserve_after_team_pause(tmp_path):
    session = make_session(tmp_path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    await session.teams.control({'action': 'goal', 'description': '暂停后拒绝遗留任务消费'})
    budget = session.teams.goal_parent.budget
    await session.teams.pause()
    try:
        with pytest.raises(RuntimeError):
            budget.take()
        assert budget.used == 0
    finally:
        await session.aclose()


@async_test
async def test_model_resume_stops_before_fresh_parent_can_refresh_goal_budget(tmp_path):
    from mewcode.types import ToolCall
    session = make_session(tmp_path)
    session.provider.responses = iter([answer('原目标上下文')])
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    await session.teams.control({'action': 'goal', 'description': '恢复不得绕过预算'})
    await collect(session.ask('原目标请求'))
    old_parent = session.teams.goal_parent
    await session.aclose()
    resumed = reopen(session)
    resumed.provider.responses = iter([
        [ProviderEvent('completed', message=Message('assistant', tool_calls=(
            ToolCall('resume-call', 'team', '{"action":"resume","name":"alpha"}'),)))],
        answer('不应自动发出这一次额外请求'),
    ])
    try:
        await collect(resumed.ask('恢复 alpha 团队'))
        assert len(resumed.provider.requests) == 1
        assert resumed.teams.goal_parent.task_id == old_parent.task_id
        assert resumed.teams.goal_parent.budget.used == old_parent.budget.used
        assert resumed.next_parent() is None
    finally:
        await resumed.aclose()


@async_test
async def test_cancelled_goal_allows_new_goal_keeps_member_tasks_and_old_mail_audit(tmp_path):
    session = make_session(tmp_path)
    session.provider.responses = iter([answer('旧目标 Lead 历史')])
    session.provider_factory = lambda config: ScriptedProvider([])
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    old_goal = await session.teams.control({'action': 'goal', 'description': '旧目标'})
    member = await session.teams.member({'action': 'spawn', 'name': 'alice', 'role': 'general', 'backend': 'inprocess'})
    identity = member['member_id']
    old_runtime = session.teams.runners[identity]
    old_runtime.session.history.append(Message('assistant', '旧成员历史'))
    old_runtime.session._checkpoint(old_runtime.session.history, old_runtime.session.context.state())
    task = await session.teams.task({'action': 'create', 'title': '旧任务保留', 'code': False})
    old = await session.teams.task({'action': 'reassign', 'task_id': task['task_id'], 'member_id': identity})
    mailbox = session.teams.inbox()
    mailbox.notify = None
    old_delivery = await mailbox.send(session.team_scope.member_id, identity, '旧领取消息只保留审计',
        type='task_assignment', fields={'goal_id': old_goal['goal_id'], 'task_id': old['task_id'], 'claim_id': old['claim_id']})
    await collect(session.ask('旧目标已经消耗一次请求'))
    old_budget = session.teams.goal_parent.budget
    await session.teams.cancel_goal()
    new_goal = await session.teams.control({'action': 'goal', 'description': '明确的新目标'})
    try:
        team = session.teams.store.load('alpha')
        assert old_goal['goal_id'] != new_goal['goal_id']
        assert team.goals[old_goal['goal_id']].state == 'cancelled'
        assert team.goals[old_goal['goal_id']].budget_used == old_budget.used == 1
        assert team.goals[new_goal['goal_id']].budget_used == 0
        assert team.members[identity].workspace_root == member['workspace_root']
        assert team.members[identity].session_ref == member['session_ref']
        assert team.members[identity].state == 'idle'
        assert len(team.members) == 2
        assert (await session.teams.board().get(team.lead_id, old['task_id'])).claim_id == old['claim_id']
        assert any(m.content == '旧目标 Lead 历史' for m in session.history)
        with pytest.raises(RuntimeError):
            old_budget.take()
        await session.teams.ensure_receiver(identity)
        runtime = session.teams.runners[identity]
        # 由实际 serve 消费旧信件，避免手工 receive 与后台接收争同一控制记录。
        for _ in range(200):
            if old_delivery.message_id in runtime.control.consumed_ids:
                break
            await asyncio.sleep(.01)
        assert old_delivery.message_id in runtime.control.consumed_ids
        assert not runtime.provider.requests
        assert runtime.control.task_id is None
        assert runtime.control.claim_id != old['claim_id']
        assert old_delivery.message_id in runtime.control.consumed_ids
        assert any(m.content == '旧成员历史' for m in runtime.session.history)
        new_task = await session.teams.task({'action': 'create', 'title': '新任务', 'code': False})
        assigned = await session.teams.task({'action': 'reassign', 'task_id': new_task['task_id'], 'member_id': identity})
        assert assigned['goal_id'] == new_goal['goal_id'] and assigned['claim_id'] != old['claim_id']
        runtime.reconcile_control()
        assert runtime.control.claim_id != old['claim_id']
    finally:
        await session.aclose()


@async_test
async def test_old_lead_budget_cannot_consume_when_shared_goal_binding_changes(tmp_path):
    session = make_session(tmp_path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    goal = await session.teams.control({'action': 'goal', 'description': '原绑定'})
    budget = session.teams.goal_parent.budget
    await session.teams.store.update_team('alpha', lambda team: setattr(team.goals[goal['goal_id']], 'state', 'cancelled'))
    await session.teams.store.start_goal('alpha', '外部新目标')
    try:
        with pytest.raises(RuntimeError):
            budget.take()
        assert budget.used == 0
    finally:
        # 仅模拟旧预算的异步竞争；还原清单以便原检查点正确收尾。
        await session.teams.store.update_team('alpha', lambda team: setattr(team, 'active_goal_id', goal['goal_id']))
        await session.aclose()


@async_test
async def test_resume_reconciles_integration_before_starting_monitor(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    await session.teams.control({'action': 'goal', 'description': '恢复先检查整合'})
    await session.aclose()
    resumed = reopen(session)
    calls = []
    async def recover():
        assert resumed.team_scope.lead
        assert resumed.teams.lead_journal is not None
        assert resumed.teams.monitor is None and not resumed.teams.runners
        assert not resumed.provider.requests
        calls.append('read_only_recover')
    monkeypatch.setattr(resumed.teams, 'recover_team_integration', recover)
    try:
        await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
        assert calls == ['read_only_recover']
        assert resumed.teams.monitor is not None
        assert not resumed.provider.requests
    finally:
        await resumed.aclose()


@async_test
async def test_goal_finalize_keeps_remaining_budget_for_actual_final_summary(tmp_path):
    from mewcode.types import ToolCall
    session = make_session(tmp_path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    goal = await session.teams.control({'action': 'goal', 'description': '整合后真实最终答复'})
    parent = session.teams.goal_parent
    async def completed_finalize(arguments, *, cancel_event, on_event=None, tool_call_id=''):
        await session.teams.store.update_team('alpha', lambda team: setattr(team.goals[goal['goal_id']], 'state', 'completed'))
        return ToolResult.success({'state': 'published'})
    session.executor.system_handlers['team_integrate'] = completed_finalize
    session.provider.responses = iter([
        [ProviderEvent('completed', message=Message('assistant', tool_calls=(
            ToolCall('finalize-call', 'team_integrate', '{"action":"finalize","validation_command":"true"}'),)))],
        answer('最终成果已整合并验证'),
    ])
    try:
        events = await collect(session.ask('完成整合并给出最终摘要'))
        assert len(session.provider.requests) == 2
        assert parent.budget.used == 2
        assert parent.finished and parent.reason == 'model_done'
        assert any(event.kind == 'task_finished' and event.reason == 'model_done' for event in events)
        assert any(m.content == '最终成果已整合并验证' for m in session.history)
        with pytest.raises(RuntimeError):
            parent.budget.take()
        assert parent.budget.used == 2
    finally:
        await session.aclose()
