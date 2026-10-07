"""团队 Git 变更使用实际目录权限，拒绝不发布操作或隐藏降级。"""
from pathlib import Path

import pytest

from conftest import async_test
from mewcode.teams.integration import IntegrationService
from mewcode.tools.base import ToolError
from test_permission_runtime import policy, rule
from test_team_integration import git, member, passed, repository, service
from test_team_service import make_session


@async_test
@pytest.mark.parametrize('effect,code', [('deny', 'permission_denied'), ('ask', 'approval_required')])
async def test_service_git_permission_refuses_before_worktree_ref_or_operation(tmp_path, effect, code):
    session = make_session(tmp_path)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    goal = await session.teams.control({'action': 'goal', 'description': '权限边界'})
    team = session.teams.store.load('alpha')
    task = await session.teams.board().create(team.lead_id, goal['goal_id'], '待处理', code=False)
    before_task = task.to_dict()
    before_refs = git(tmp_path, 'show-ref')
    before_worktrees = git(tmp_path, 'worktree', 'list', '--porcelain')
    session.permissions.mode = 'default'
    policy(tmp_path, [rule(effect, 'execute_command(git *)')])
    try:
        with pytest.raises(ToolError) as caught:
            await session.teams.integrate({'action': 'integrate', 'task_id': task.task_id,
                                           'validation_command': 'true'})
        assert caught.value.code == code and caught.value.details['not_started']
        assert git(tmp_path, 'show-ref') == before_refs
        assert git(tmp_path, 'worktree', 'list', '--porcelain') == before_worktrees
        assert not (session.teams.store.team_path('alpha') / 'integration' / goal['goal_id']).exists()
        assert (await session.teams.board().get(team.lead_id, task.task_id)).to_dict() == before_task
    finally:
        await session.aclose()


@async_test
async def test_initialize_callback_denial_preserves_absent_storage_and_refs(tmp_path):
    root = repository(tmp_path / 'repo')
    seen = []
    async def deny(cwd, args):
        seen.append((cwd, args))
        raise ToolError('permission_denied', '当前策略拒绝 Git', not_started=True)
    svc = IntegrationService(root, tmp_path / 'private', 'goal', git(root, 'rev-parse', 'HEAD'), 'main', authorize=deny)
    refs = git(root, 'show-ref')
    with pytest.raises(ToolError, match='当前策略拒绝 Git'):
        await svc.initialize()
    assert seen[0][0] == root and 'worktree' in seen[0][1]
    assert not svc.root.exists() and git(root, 'show-ref') == refs


@async_test
@pytest.mark.parametrize('action', ['integrate', 'sync', 'finalize'])
async def test_operation_preflight_denial_keeps_exact_goal_and_input_records(tmp_path, action):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    commit = member(root, tmp_path / 'a', 'member-a', base, {'api.py': 'VALUE = 1\n'})
    await svc.initialize()
    required = [{'task_id': 'report', 'code': False, 'accepted': True, 'evidence': ['实测']}]
    if action == 'sync':
        merged = await svc.integrate('task-a', tmp_path / 'a', 'member-a', commit, accepted=True, validate=passed)
        git(root, 'worktree', 'add', '-b', 'member-b', str(tmp_path / 'b'), base)
    before = {p.name: p.read_bytes() for p in svc.root.glob('*.json')}
    refs = git(root, 'show-ref')
    worktrees = git(root, 'worktree', 'list', '--porcelain')
    async def deny(cwd, args):
        raise ToolError('approval_required', '没有当前 Git 批准', not_started=True)
    svc.authorize = deny
    with pytest.raises(ToolError) as caught:
        if action == 'integrate':
            await svc.integrate('task-a', tmp_path / 'a', 'member-a', commit, accepted=True, validate=passed)
        elif action == 'sync':
            await svc.sync_member(tmp_path / 'b', 'member-b', merged['result_head'])
        else:
            await svc.finalize(required, validate=passed)
    assert caught.value.code == 'approval_required' and caught.value.details['not_started']
    assert {p.name: p.read_bytes() for p in svc.root.glob('*.json')} == before
    assert git(root, 'show-ref') == refs and git(root, 'worktree', 'list', '--porcelain') == worktrees


@async_test
async def test_final_publication_rechecks_actual_update_ref_and_read_tree_policy(tmp_path):
    root = repository(tmp_path / 'repo')
    calls = []
    denied = False
    async def authorize(cwd, args):
        calls.append((cwd, args))
        if denied and ('update-ref' in args or 'read-tree' in args):
            raise ToolError('permission_denied', '验证后当前策略已拒绝发布', not_started=True)
    svc = IntegrationService(root, tmp_path / 'private', 'goal', git(root, 'rev-parse', 'HEAD'), 'main', authorize=authorize)
    await svc.initialize()
    base = git(root, 'rev-parse', 'HEAD')
    commit = member(root, tmp_path / 'a', 'member-a', base, {'api.py': 'VALUE = 1\n'})
    merged = await svc.integrate('task-a', tmp_path / 'a', 'member-a', commit, accepted=True, validate=passed)
    async def validate(cwd, head):
        nonlocal denied
        denied = True
        return {'ok': True, 'evidence': ['验证期间收紧策略']}
    result = await svc.finalize([{'task_id': 'task-a', 'code': True, 'accepted': True,
                                 'operation_id': merged['operation_id']}], validate=validate)
    assert result['state'] == 'needs_review'
    assert git(root, 'rev-parse', 'main') == base
    assert not (root / 'api.py').exists() and git(root, 'status', '--porcelain') == ''
    assert any(cwd == root and 'update-ref' in args for cwd, args in calls)
    assert any(cwd == root and 'read-tree' in args for cwd, args in calls)


@async_test
async def test_policy_recheck_before_merge_propagates_original_error_without_operation(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    await svc.initialize()
    base = git(root, 'rev-parse', 'HEAD')
    commit = member(root, tmp_path / 'a', 'member-a', base, {'api.py': 'VALUE = 1\n'})
    before = {p.name: p.read_bytes() for p in svc.root.glob('*.json')}
    checks = 0
    rejection = ToolError('permission_denied', '执行前策略已收紧', not_started=True)
    async def authorize(cwd, args):
        nonlocal checks
        if 'merge' in args:
            checks += 1
            if checks == 2:
                raise rejection
    svc.authorize = authorize
    with pytest.raises(ToolError) as caught:
        await svc.integrate('task-a', tmp_path / 'a', 'member-a', commit, accepted=True, validate=passed)
    assert caught.value is rejection
    assert {p.name: p.read_bytes() for p in svc.root.glob('*.json')} == before
    assert git(svc.worktree_root, 'rev-parse', 'HEAD') == base


@async_test
@pytest.mark.parametrize('cancel', [False, True])
async def test_started_operation_cleanup_does_not_request_new_permission(tmp_path, cancel):
    import asyncio
    root = repository(tmp_path / 'repo')
    revoked = False
    async def authorize(cwd, args):
        if revoked:
            raise ToolError('approval_required', '收尾不得等新批准', not_started=True)
    svc = IntegrationService(root, tmp_path / 'private', 'goal', git(root, 'rev-parse', 'HEAD'), 'main', authorize=authorize)
    await svc.initialize()
    base = git(root, 'rev-parse', 'HEAD')
    commit = member(root, tmp_path / 'a', 'member-a', base, {'api.py': 'VALUE = 1\n'})
    async def validate(cwd, head):
        nonlocal revoked
        revoked = True
        if cancel:
            raise asyncio.CancelledError()
        return {'ok': False, 'evidence': ['当前验证失败']}
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await svc.integrate('task-a', tmp_path / 'a', 'member-a', commit, accepted=True, validate=validate)
    else:
        assert (await svc.integrate('task-a', tmp_path / 'a', 'member-a', commit, accepted=True, validate=validate))['state'] == 'rolled_back'
    assert (await svc.status())['state'] == 'ready'
    assert git(svc.worktree_root, 'rev-parse', 'HEAD') == base
    assert git(tmp_path / 'a', 'rev-parse', 'HEAD') == commit


@async_test
async def test_explicit_rollback_requires_new_authorization_and_keeps_conflict(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    await svc.initialize()
    base = git(root, 'rev-parse', 'HEAD')
    first = member(root, tmp_path / 'a', 'member-a', base, {'shared.txt': 'a\n'})
    second = member(root, tmp_path / 'b', 'member-b', base, {'shared.txt': 'b\n'})
    await svc.integrate('task-a', tmp_path / 'a', 'member-a', first, accepted=True, validate=passed)
    conflict = await svc.integrate('task-b', tmp_path / 'b', 'member-b', second, accepted=True, validate=passed)
    before = {p.name: p.read_bytes() for p in svc.root.glob('*.json')}
    content = (svc.worktree_root / 'shared.txt').read_bytes()
    async def deny(cwd, args):
        assert cwd == svc.worktree_root and 'merge' in args and '--abort' in args
        raise ToolError('permission_denied', '现在不许显式回滚', not_started=True)
    svc.authorize = deny
    with pytest.raises(ToolError):
        await svc.rollback(conflict['operation_id'])
    assert {p.name: p.read_bytes() for p in svc.root.glob('*.json')} == before
    assert (svc.worktree_root / 'shared.txt').read_bytes() == content


@async_test
@pytest.mark.parametrize('unknown_edit', [False, True])
async def test_commit_policy_denial_restores_only_owned_index_transaction(tmp_path, unknown_edit):
    root = repository(tmp_path / 'repo')
    checks = 0
    async def authorize(cwd, args):
        nonlocal checks
        if 'update-ref' in args:
            checks += 1
            if checks == 3:
                if unknown_edit:
                    (root / 'api.py').write_text('索引更新后的用户修改\n')
                raise ToolError('permission_denied', 'ref 提交前撤回授权', not_started=True)
    svc = IntegrationService(root, tmp_path / 'private', 'goal', git(root, 'rev-parse', 'HEAD'), 'main', authorize=authorize)
    await svc.initialize()
    base = git(root, 'rev-parse', 'HEAD')
    commit = member(root, tmp_path / 'a', 'member-a', base, {'api.py': 'VALUE = 1\n'})
    merged = await svc.integrate('task-a', tmp_path / 'a', 'member-a', commit, accepted=True, validate=passed)
    result = await svc.finalize([{'task_id': 'task-a', 'code': True, 'accepted': True,
                                 'operation_id': merged['operation_id']}], validate=passed)
    assert checks == 3 and result['state'] == 'needs_review'
    assert git(root, 'rev-parse', 'main') == base
    if unknown_edit:
        assert (root / 'api.py').read_text() == '索引更新后的用户修改\n'
        assert '未知后续编辑' in result['failure']
    else:
        assert git(root, 'status', '--porcelain') == ''
        assert not (root / 'api.py').exists()
    assert git(svc.worktree_root, 'rev-parse', 'HEAD') == merged['result_head']
    assert git(tmp_path / 'a', 'rev-parse', 'HEAD') == commit


@async_test
@pytest.mark.parametrize('effect,code', [('deny', 'permission_denied'), ('ask', 'approval_required')])
async def test_service_accepted_code_merge_denial_preserves_task_and_receipt(tmp_path, effect, code):
    from conftest import ScriptedProvider
    session = make_session(tmp_path)
    session.provider_factory = lambda config: ScriptedProvider([])
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    await session.teams.control({'action': 'goal', 'description': '接纳后权限仍需检查'})
    member_record = await session.teams.member({'action': 'spawn', 'name': 'worker', 'role': 'general', 'backend': 'inprocess'})
    task = await session.teams.task({'action': 'create', 'title': '真实代码', 'code': True})
    task = await session.teams.task({'action': 'reassign', 'task_id': task['task_id'], 'member_id': member_record['member_id']})
    directory = Path(member_record['workspace_root'])
    (directory / 'api.py').write_text('VALUE = 1\n')
    git(directory, 'add', 'api.py')
    git(directory, '-c', 'user.name=测试', '-c', 'user.email=test@example.invalid', 'commit', '-m', '成果')
    board = session.teams.board()
    submitted = await board.submit(member_record['member_id'], task['task_id'], task['claim_id'],
        {'summary': '真实代码', 'verified': True, 'evidence': ['本次产物'], 'branch': member_record['branch'],
         'commit': git(directory, 'rev-parse', 'HEAD')})
    accepted = await session.teams.task({'action': 'accept', 'task_id': submitted.task_id,
        'expected_revision': submitted.revision, 'reason': '独立检查', 'validation_command': 'test -f api.py'})
    team = session.teams.store.load('alpha')
    integration = session.teams.store.team_path('alpha') / 'integration' / team.active_goal_id
    before = {p.name: p.read_bytes() for p in integration.glob('*.json')}
    refs = git(tmp_path, 'show-ref')
    session.permissions.mode = 'default'
    policy(tmp_path, [rule(effect, 'execute_command(git *)')])
    try:
        with pytest.raises(ToolError) as caught:
            await session.teams.integrate({'action': 'integrate', 'task_id': submitted.task_id,
                                           'validation_command': 'test -f api.py'})
        assert caught.value.code == code and caught.value.details['not_started']
        assert (await board.get(team.lead_id, submitted.task_id)).to_dict() == accepted
        assert {p.name: p.read_bytes() for p in integration.glob('*.json')} == before
        assert git(tmp_path, 'show-ref') == refs
        assert (directory / 'api.py').read_text() == 'VALUE = 1\n'
    finally:
        await session.aclose()
