"""本地父任务取消必须实际收尾其绑定的团队目标。"""
from conftest import async_test
from test_team_security import active_team


@async_test
async def test_cancel_parent_command_stops_bound_team_and_preserves_task_and_workspace(tmp_path):
    session, runtime = await active_team(tmp_path)
    team = session.teams.store.load('alpha')
    parent = session.teams.goal_parent
    task = await session.teams.task({'action': 'create', 'title': '取消保持证据', 'code': False})
    assigned = await session.teams.task({'action': 'reassign', 'task_id': task['task_id'],
                                        'member_id': runtime.member.member_id})
    from pathlib import Path
    artifact = Path(runtime.member.workspace_root) / 'preserved.txt'
    artifact.write_text('取消前已经发生的成果\n')
    try:
        output = await session.tasks_text('cancel-parent ' + parent.task_id)
        assert '已取消' in output
        restored = session.teams.store.load('alpha')
        assert restored.goals[team.active_goal_id].state == 'cancelled'
        assert restored.members[runtime.member.member_id].state == 'stopped'
        assert not session.teams.runners and not parent.wake_allowed
        assert artifact.read_text() == '取消前已经发生的成果\n'
        saved = await session.teams.board().get(team.lead_id, assigned['task_id'])
        assert saved.claim_id == assigned['claim_id'] and saved.owner_id == runtime.member.member_id
        assert not session.provider.requests and not runtime.provider.requests
    finally:
        await session.aclose()


@async_test
async def test_cancel_unrelated_parent_does_not_stop_team_goal(tmp_path):
    session, runtime = await active_team(tmp_path)
    team = session.teams.store.load('alpha')
    other = session.tasks.new_parent('另一个普通父任务')
    try:
        await session.tasks_text('cancel-parent ' + other.task_id)
        restored = session.teams.store.load('alpha')
        assert restored.goals[team.active_goal_id].state == 'active'
        assert runtime.member.member_id in session.teams.runners
        assert not session.teams.goal_parent.cancel.is_set()
    finally:
        await session.aclose()
