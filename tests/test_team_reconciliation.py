"""供应商声明稳定及实际 Git 成功后的服务恢复对账。"""
from pathlib import Path

import pytest

from conftest import async_test
from test_team_security import active_team
from test_team_lead_resume import reopen
from test_team_integration import git
from mewcode.providers.tool_messages import anthropic_tools, openai_tools
from mewcode.teams.integration import IntegrationService
from mewcode.teams.tasks import TaskBoard


@async_test
async def test_collaboration_schema_matches_both_protocols_and_roster_changes(tmp_path):
    session, runtime = await active_team(tmp_path)
    try:
        registry = session.executor.registry
        definitions = registry.definitions(allowed_tools=session.effective_tools(), system_passthrough=False)
        before = {tool.name: tool.input_schema for tool in definitions if tool.name.startswith('team')}
        assert set(before) == {'team', 'team_member', 'team_task', 'team_message', 'team_integrate'}
        anth = {tool['name']: tool['input_schema'] for tool in anthropic_tools(definitions) if tool['name'].startswith('team')}
        oai = {tool['function']['name']: tool['function']['parameters'] for tool in openai_tools(definitions)
               if tool['function']['name'].startswith('team')}
        assert before == anth == oai
        await session.teams.member({'action': 'spawn', 'name': 'bob', 'role': 'general', 'backend': 'inprocess'})
        after = {tool.name: tool.input_schema for tool in registry.definitions(allowed_tools=session.effective_tools(),
                    system_passthrough=False) if tool.name.startswith('team')}
        assert before == after
    finally:
        await session.aclose()


@pytest.mark.parametrize('boundary', ['integrate', 'finalize'])
@async_test
async def test_actual_git_publish_before_task_save_recovers_service_without_replay(tmp_path, monkeypatch, boundary):
    session, runtime = await active_team(tmp_path)
    root = session.executor.context.root
    (root/'.git/info/exclude').write_text('.mewcode/\nuser/\n')
    task = await session.teams.task({'action': 'create', 'title': '恢复成果'})
    task = await session.teams.task({'action': 'reassign', 'task_id': task['task_id'], 'member_id': runtime.member.member_id})
    member_root = Path(runtime.member.workspace_root)
    (member_root/'result.txt').write_text('完整成果\n')
    git(member_root, 'add', 'result.txt')
    git(member_root, '-c', 'user.name=测试', '-c', 'user.email=test@example.test', 'commit', '-m', '成果')
    submitted = await session.teams.board().submit(runtime.member.member_id, task['task_id'], task['claim_id'],
        {'summary': '成果', 'verified': True, 'evidence': ['真实提交'], 'branch': runtime.member.branch,
         'commit': git(member_root, 'rev-parse', 'HEAD')})
    accepted = await session.teams.task({'action': 'accept', 'task_id': task['task_id'],
        'expected_revision': submitted.revision, 'reason': '独立验证', 'validation_command': 'test -f result.txt'})
    team = session.teams.store.load('alpha')
    goal = team.goals[team.active_goal_id]
    original_integrate, original_complete = TaskBoard.integrate, TaskBoard.complete
    async def fail(*args, **kwargs):
        raise OSError('Git 已发布，任务状态写入前故障')
    if boundary == 'finalize':
        await session.teams.integrate({'action': 'integrate', 'task_id': accepted['task_id'],
                                       'validation_command': 'test -f result.txt'})
    monkeypatch.setattr(TaskBoard, 'integrate' if boundary == 'integrate' else 'complete', fail)
    with pytest.raises(OSError):
        await session.teams.integrate({'action': boundary, 'task_id': accepted['task_id'],
                                       'validation_command': 'test -f result.txt'})
    service = IntegrationService(root, session.teams.store.team_path('alpha')/'integration',
                                 goal.goal_id, goal.baseline_commit, goal.target_branch)
    before = git(service.worktree_root, 'rev-parse', 'HEAD')
    if boundary == 'finalize':
        (root/'user-after.txt').write_text('用户后续操作\n')
        git(root, 'add', 'user-after.txt')
        git(root, '-c', 'user.name=测试', '-c', 'user.email=test@example.test', 'commit', '-m', '发布后用户操作')
    user_head = git(root, 'rev-parse', 'HEAD')
    monkeypatch.setattr(TaskBoard, 'integrate', original_integrate)
    monkeypatch.setattr(TaskBoard, 'complete', original_complete)
    await session.aclose()
    resumed = reopen(session)
    async def no_mutation(*args, **kwargs):
        pytest.fail('恢复只能核查事实，不能再次启动 Git 变更')
    monkeypatch.setattr(resumed.teams, 'authorize_git', no_mutation)
    try:
        await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
        saved = await resumed.teams.board().get(team.lead_id, task['task_id'])
        assert saved.state == ('completed' if boundary == 'finalize' else 'integrated')
        assert saved.integrated_commit == before
        assert git(root, 'rev-parse', 'HEAD') == user_head
        assert git(service.worktree_root, 'rev-parse', 'HEAD') == before
        assert not resumed.provider.requests and not resumed.teams.runners
        if boundary == 'finalize':
            assert (root/'user-after.txt').read_text() == '用户后续操作\n'
            assert resumed.teams.store.load('alpha').goals[goal.goal_id].state == 'completed'
    finally:
        await resumed.aclose()


@async_test
async def test_needs_review_cannot_be_reactivated_by_user_input(tmp_path):
    import asyncio
    session, runtime = await active_team(tmp_path)
    try:
        await session.teams.quiesce()
        await session.teams.store.update_team('alpha', lambda team: setattr(team, 'status', 'needs_review'))
        session.teams.activated = False
        from mewcode.tools.base import ToolError
        with pytest.raises(ToolError) as error:
            await session.teams.parent_for_input('继续原任务', asyncio.Event())
        assert error.value.details['not_started']
        assert not session.teams.activated
        assert session.teams.store.load('alpha').status == 'needs_review'
        assert not session.provider.requests and not session.teams.runners
    finally:
        await session.aclose()
    assert session.teams.store.load('alpha').status == 'needs_review'


@async_test
async def test_lead_notice_explains_current_coordinator_assignment(tmp_path, monkeypatch):
    monkeypatch.setenv('MEWCODE_COORDINATOR', '1')
    session, runtime = await active_team(tmp_path, coordinator=True)
    try:
        notice = session.teams.lead_notice()
        assert '"coordinator": true' in notice
        assert '代码修改和冲突内容交给成员' in notice
    finally:
        await session.aclose()


@async_test
async def test_new_goal_task_start_safely_syncs_frozen_baseline_without_dependencies(tmp_path):
    session, runtime = await active_team(tmp_path)
    root = session.executor.context.root
    identity, member_root = runtime.member.member_id, Path(runtime.member.workspace_root)
    await session.teams.cancel_goal()
    (root/'new-interface.txt').write_text('用户的新目标基线\n')
    git(root, 'add', 'new-interface.txt')
    git(root, '-c', 'user.name=测试', '-c', 'user.email=test@example.test', 'commit', '-m', '用户新基线')
    goal = await session.teams.control({'action': 'goal', 'description': '原目录复用新目标'})
    task = await session.teams.task({'action': 'create', 'title': '新目标无依赖代码任务'})
    task = await session.teams.task({'action': 'reassign', 'task_id': task['task_id'], 'member_id': identity})
    await session.teams.ensure_receiver(identity)
    fresh = session.teams.runners[identity]
    try:
        assert not (member_root/'new-interface.txt').exists()
        fresh.reconcile_control()
        fresh.restore_budget()
        started = await fresh.start_task(task['task_id'], task['claim_id'])
        assert started.state == 'running'
        assert started.synced_commit == goal['baseline_commit']
        assert (member_root/'new-interface.txt').read_text() == '用户的新目标基线\n'
        assert fresh.member.member_id == identity and not fresh.provider.requests
        service = IntegrationService(root, session.teams.store.team_path('alpha')/'integration',
                                     goal['goal_id'], goal['baseline_commit'], goal['target_branch'])
        assert not (await service.status())['operation_records']
    finally:
        await session.aclose()
