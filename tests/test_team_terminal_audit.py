"""真实成员模型循环耗尽通信额度时，Lead 已验收成果保持不可降级。"""
import pytest

from conftest import async_test
from test_team_security import active_team
from mewcode.types import Message, ProviderEvent, ToolCall


@pytest.mark.parametrize('state', ['accepted', 'integrated', 'completed'])
@async_test
async def test_actual_member_tool_loop_exhaustion_keeps_lead_success_and_audits(tmp_path, state):
    session, runtime = await active_team(tmp_path)
    try:
        team = session.teams.store.load('alpha')
        board = session.teams.board()
        task = await board.create(team.lead_id, team.active_goal_id, '有限只读成果沟通', code=True, budget_limit=2)
        assigned = await session.teams.task({'action': 'reassign', 'task_id': task.task_id,
                                             'member_id': runtime.member.member_id})
        runtime.reconcile_control()
        runtime.restore_budget()
        runtime.save_control()
        await runtime.start_task(task.task_id, assigned['claim_id'])
        task = await board.submit(runtime.member.member_id, task.task_id, assigned['claim_id'],
            {'summary': '被 Lead 核查的提交', 'verified': True, 'evidence': ['真实基线 SHA'],
             'branch': runtime.member.branch, 'commit': team.baseline_commit})
        task = await board.accept(team.lead_id, task.task_id, task.revision, reason='Lead 独立核查')
        if state in {'integrated', 'completed'}:
            task = await board.integrate(team.lead_id, task.task_id, task.revision, commit=team.baseline_commit)
        if state == 'completed':
            task = await board.complete(team.lead_id, task.task_id, task.revision)
        result, integrated_commit = dict(task.result), task.integrated_commit
        runtime.provider.responses = iter([
            [ProviderEvent('completed', message=Message('assistant', tool_calls=(
                ToolCall(f'list-{index}', 'team_task', '{"action":"list"}'),)))]
            for index in range(2)
        ])
        await runtime.run('只读回应接口状态；两次模型请求后额度耗尽')
        saved = await board.get(team.lead_id, task.task_id)
        assert len(runtime.provider.requests) == 2
        assert runtime.budget.used == saved.budget_used == 2
        assert saved.state == state and saved.result == result
        assert saved.integrated_commit == integrated_commit
        assert saved.history[-1]['late'] and saved.history[-1]['reported_state'] == 'blocked'
        assert saved.history[-1]['reason'] == 'max_iterations'
        assert saved.history[-1]['claim_id'] == assigned['claim_id']
        assert session.teams.store.load('alpha').members[runtime.member.member_id].state == 'blocked'
        assert not runtime.busy and not runtime.session.agent.storage_blocked
        assert sum(m.tool_call_id in {'list-0', 'list-1'} and m.tool_result.ok for m in runtime.session.history) == 2
    finally:
        await session.aclose()
