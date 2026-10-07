"""用真实 Git 验证团队整合边界，避免替身隐藏 ref／索引行为。"""

import asyncio
import json
from pathlib import Path
import subprocess

import pytest

from conftest import async_test
from mewcode.tools.base import ToolError
from mewcode.teams.integration import IntegrationService


def git(root, *args):
    result = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def repository(root):
    root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.email', 'team@example.invalid')
    git(root, 'config', 'user.name', '团队测试')
    (root / 'shared.txt').write_text('base\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', '基线')
    return root


def member(root, directory, branch, base, files):
    git(root, 'worktree', 'add', '-b', branch, str(directory), base)
    for name, text in files.items():
        (directory / name).write_text(text)
    git(directory, 'add', '.')
    git(directory, 'commit', '-m', '成果')
    return git(directory, 'rev-parse', 'HEAD')


async def passed(path, commit):
    return {'ok': True, 'evidence': {'commit': commit, 'directory': str(path)}}


async def failed(path, commit):
    return {'ok': False, 'evidence': '验证失败'}


def service(tmp_path, root, goal='goal-one'):
    return IntegrationService(root, tmp_path / 'private', goal, git(root, 'rev-parse', 'HEAD'), 'main')


@async_test
async def test_immutable_input_and_dependency_sync_leave_user_branch_unchanged(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    first = member(root, tmp_path / 'a', 'member-a', base, {'api.py': 'VALUE = 1\n'})
    await svc.initialize()
    (tmp_path / 'a' / 'later.py').write_text('LATER = 2\n')
    git(tmp_path / 'a', 'add', '.')
    git(tmp_path / 'a', 'commit', '-m', '验收之后的新修改')
    result = await svc.integrate('task-a', tmp_path / 'a', 'member-a', first, accepted=True, validate=passed)
    assert result['state'] == 'published'
    tree = Path((await svc.status())['worktree_root'])
    assert (tree / 'api.py').read_text() == 'VALUE = 1\n'
    assert not (tree / 'later.py').exists()
    git(root, 'worktree', 'add', '-b', 'member-b', str(tmp_path / 'b'), base)
    synced = await svc.sync_member(tmp_path / 'b', 'member-b', result['result_head'])
    assert synced['state'] == 'synced'
    assert (tmp_path / 'b' / 'api.py').exists()
    assert git(root, 'rev-parse', 'main') == base


@async_test
async def test_input_wrong_repository_unknown_branch_unaccepted_and_dirty_sync(tmp_path):
    root = repository(tmp_path / 'repo')
    other = repository(tmp_path / 'other')
    svc = service(tmp_path, root)
    await svc.initialize()
    base = git(root, 'rev-parse', 'HEAD')
    commit = member(root, tmp_path / 'a', 'member-a', base, {'api.py': 'VALUE = 1\n'})
    for directory, branch, identity, accepted in [
        (other, 'main', git(other, 'rev-parse', 'HEAD'), True),
        (tmp_path / 'a', 'unknown', commit, True),
        (tmp_path / 'a', 'member-a', 'HEAD', True),
        (tmp_path / 'a', 'member-a', commit, False),
    ]:
        with pytest.raises(ToolError):
            await svc.integrate('task-a', directory, branch, identity, accepted=accepted, validate=passed)
    result = await svc.integrate('task-a', tmp_path / 'a', 'member-a', commit, accepted=True, validate=passed)
    git(root, 'worktree', 'add', '-b', 'member-b', str(tmp_path / 'b'), base)
    (tmp_path / 'b' / 'shared.txt').write_text('unknown local\n')
    with pytest.raises(ToolError):
        await svc.sync_member(tmp_path / 'b', 'member-b', result['result_head'])
    assert (tmp_path / 'b' / 'shared.txt').read_text() == 'unknown local\n'


@async_test
async def test_second_conflict_rollback_preserves_first_and_all_member_refs(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'shared.txt': 'first\n'})
    b = member(root, tmp_path / 'b', 'b', base, {'shared.txt': 'second\n'})
    await svc.initialize()
    first = await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    second = await svc.integrate('b', tmp_path / 'b', 'b', b, accepted=True, validate=passed)
    assert second['state'] == 'conflict'
    with pytest.raises(ToolError):
        await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    rolled = await svc.rollback(second['operation_id'])
    assert rolled['state'] == 'rolled_back'
    tree = Path((await svc.status())['worktree_root'])
    assert git(tree, 'rev-parse', 'HEAD') == first['result_head']
    assert git(tree, 'status', '--porcelain') == ''
    assert git(root, 'rev-parse', 'a') == a
    assert git(root, 'rev-parse', 'b') == b


@async_test
async def test_conflict_handoff_rejects_double_writer_and_finishes_in_same_directory(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'shared.txt': 'first\n'})
    b = member(root, tmp_path / 'b', 'b', base, {'shared.txt': 'second\n'})
    await svc.initialize()
    await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    conflict = await svc.integrate('b', tmp_path / 'b', 'b', b, accepted=True, validate=passed)
    grant = await svc.handoff_conflict(conflict['operation_id'], 'resolver')
    with pytest.raises(ToolError):
        await svc.handoff_conflict(conflict['operation_id'], 'other')
    with pytest.raises(ToolError):
        await svc.finish_conflict(conflict['operation_id'], 'other', grant['token'], validate=passed)
    with pytest.raises(ToolError):
        await svc.rollback(conflict['operation_id'])
    tree = Path(grant['workspace_root'])
    (tree / 'shared.txt').write_text('resolved\n')
    git(tree, 'add', 'shared.txt')
    git(tree, 'commit', '-m', '成员解决冲突')
    result = await svc.finish_conflict(conflict['operation_id'], 'resolver', grant['token'], validate=passed)
    assert result['state'] == 'published'
    assert (tree / 'shared.txt').read_text() == 'resolved\n'


@async_test
async def test_validation_failure_rolls_back_only_current_commit(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'first\n'})
    b = member(root, tmp_path / 'b', 'b', base, {'b.txt': 'second\n'})
    await svc.initialize()
    first = await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    second = await svc.integrate('b', tmp_path / 'b', 'b', b, accepted=True, validate=failed)
    assert second['state'] == 'rolled_back'
    tree = Path((await svc.status())['worktree_root'])
    assert git(tree, 'rev-parse', 'HEAD') == first['result_head']
    assert (tree / 'a.txt').exists() and not (tree / 'b.txt').exists()


@async_test
async def test_integration_lock_rejects_other_instance_without_git_side_effect(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    peer = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    commit = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'first\n'})
    await svc.initialize()
    entered, release = asyncio.Event(), asyncio.Event()

    async def waiting(path, identity):
        entered.set()
        await release.wait()
        return {'ok': True}

    execution = asyncio.create_task(svc.integrate('a', tmp_path / 'a', 'a', commit, accepted=True, validate=waiting))
    await entered.wait()
    try:
        with pytest.raises(ToolError):
            await peer.integrate('b', tmp_path / 'a', 'a', commit, accepted=True, validate=passed)
    finally:
        release.set()
        await execution


@async_test
async def test_final_candidate_gates_and_checked_out_branch_index_update(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    commit = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'first\n'})
    await svc.initialize()
    op = await svc.integrate('a', tmp_path / 'a', 'a', commit, accepted=True, validate=passed)
    tasks = [{'task_id': 'a', 'accepted': True, 'code': True, 'operation_id': op['operation_id']}]
    with pytest.raises(ToolError):
        await svc.finalize([dict(tasks[0], accepted=False)], validate=passed)
    result = await svc.finalize(tasks, validate=failed)
    assert result['state'] == 'validation_failed'
    assert git(root, 'rev-parse', 'HEAD') == base
    result = await svc.finalize(tasks, validate=passed)
    assert result['state'] == 'published'
    assert git(root, 'rev-parse', 'HEAD') == op['result_head']
    assert git(root, 'status', '--porcelain') == ''
    assert (root / 'a.txt').read_text() == 'first\n'


@async_test
async def test_final_dirty_target_and_external_movement_do_not_overwrite(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    commit = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'first\n'})
    await svc.initialize()
    op = await svc.integrate('a', tmp_path / 'a', 'a', commit, accepted=True, validate=passed)
    tasks = [{'task_id': 'a', 'accepted': True, 'code': True, 'operation_id': op['operation_id']}]
    (root / 'shared.txt').write_text('local\n')
    with pytest.raises(ToolError):
        await svc.finalize(tasks, validate=passed)
    assert (root / 'shared.txt').read_text() == 'local\n'
    git(root, 'add', '.')
    git(root, 'commit', '-m', '用户并发提交')
    user_head = git(root, 'rev-parse', 'HEAD')
    with pytest.raises(ToolError):
        await svc.finalize(tasks, validate=passed)
    assert git(root, 'rev-parse', 'HEAD') == user_head


@async_test
async def test_final_external_update_during_candidate_validation_is_preserved(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    commit = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'first\n'})
    await svc.initialize()
    op = await svc.integrate('a', tmp_path / 'a', 'a', commit, accepted=True, validate=passed)

    async def concurrent(path, identity):
        (root / 'user.txt').write_text('user\n')
        git(root, 'add', '.')
        git(root, 'commit', '-m', '验证期间用户提交')
        return {'ok': True}

    tasks = [{'task_id': 'a', 'accepted': True, 'code': True, 'operation_id': op['operation_id']}]
    result = await svc.finalize(tasks, validate=concurrent)
    assert result['state'] == 'needs_review'
    assert (root / 'user.txt').exists() and not (root / 'a.txt').exists()


@async_test
async def test_recovery_reconciles_git_success_without_replaying_and_preserves_user_update(tmp_path, monkeypatch):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    commit = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'first\n'})
    await svc.initialize()
    save = svc._save_operation

    def crash(operation):
        if operation['state'] == 'git_applied':
            raise RuntimeError('模拟 Git 成功之后进程退出')
        save(operation)

    monkeypatch.setattr(svc, '_save_operation', crash)
    with pytest.raises(RuntimeError):
        await svc.integrate('a', tmp_path / 'a', 'a', commit, accepted=True, validate=passed)
    tree = Path((await svc.status())['worktree_root'])
    actual = git(tree, 'rev-parse', 'HEAD')
    peer = service(tmp_path, root)
    recovered = await peer.recover()
    assert recovered['state'] == 'needs_review'
    assert git(tree, 'rev-parse', 'HEAD') == actual
    assert recovered['operations'][-1]['actual_head'] == actual
    assert git(root, 'rev-parse', 'main') == base


@async_test
async def test_physical_conflict_lease_blocks_lead_and_other_member_until_stopped(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'shared.txt': 'first\n'})
    b = member(root, tmp_path / 'b', 'b', base, {'shared.txt': 'second\n'})
    await svc.initialize()
    await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    conflict = await svc.integrate('b', tmp_path / 'b', 'b', b, accepted=True, validate=passed)
    grant = await svc.handoff_conflict(conflict['operation_id'], 'resolver')
    lease = svc.acquire_conflict_lease(conflict['operation_id'], 'resolver', grant['token'])
    try:
        with pytest.raises(ToolError):
            svc.acquire_conflict_lease(conflict['operation_id'], 'resolver', grant['token'])
        with pytest.raises(ToolError):
            await svc.release_conflict(conflict['operation_id'], 'resolver', grant['token'])
        with pytest.raises(ToolError):
            await svc.finish_conflict(conflict['operation_id'], 'resolver', grant['token'], validate=passed)
    finally:
        lease.close()
    await svc.release_conflict(conflict['operation_id'], 'resolver', grant['token'])
    assert (await svc.rollback(conflict['operation_id']))['state'] == 'rolled_back'


@async_test
async def test_rollback_refuses_unknown_edits_after_conflict_and_blocks_further_work(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'shared.txt': 'first\n'})
    b = member(root, tmp_path / 'b', 'b', base, {'shared.txt': 'second\n'})
    await svc.initialize()
    await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    conflict = await svc.integrate('b', tmp_path / 'b', 'b', b, accepted=True, validate=passed)
    tree = Path((await svc.status())['worktree_root'])
    (tree / 'shared.txt').write_text('unknown externally edited conflict\n')
    result = await svc.rollback(conflict['operation_id'])
    assert result['state'] == 'needs_review'
    assert result['rollback']['ok'] is False
    assert (tree / 'shared.txt').read_text() == 'unknown externally edited conflict\n'
    with pytest.raises(ToolError):
        await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)


@async_test
async def test_failed_abort_is_reported_and_never_claims_rolled_back(tmp_path, monkeypatch):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'shared.txt': 'first\n'})
    b = member(root, tmp_path / 'b', 'b', base, {'shared.txt': 'second\n'})
    await svc.initialize()
    await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    conflict = await svc.integrate('b', tmp_path / 'b', 'b', b, accepted=True, validate=passed)
    original = svc._git

    async def failure(directory, *args, **kwargs):
        if args == ('merge', '--abort'):
            raise ToolError('test_abort', '模拟 Git abort 失败')
        return await original(directory, *args, **kwargs)

    monkeypatch.setattr(svc, '_git', failure)
    result = await svc.rollback(conflict['operation_id'])
    assert result['state'] == 'needs_review'
    assert (await svc.status())['state'] == 'needs_review'
    assert git(root, 'rev-parse', 'a') == a and git(root, 'rev-parse', 'b') == b


@async_test
async def test_validator_exception_and_cancellation_roll_back_managed_current_operation(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'first\n'})
    await svc.initialize()

    async def broken(path, commit):
        raise RuntimeError('验证程序失败')

    result = await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=broken)
    assert result['state'] == 'rolled_back'
    entered = asyncio.Event()

    async def waits(path, commit):
        entered.set()
        await asyncio.Event().wait()

    execution = asyncio.create_task(svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=waits))
    await entered.wait()
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution
    tree = Path((await svc.status())['worktree_root'])
    assert git(tree, 'rev-parse', 'HEAD') == base
    assert git(tree, 'status', '--porcelain') == ''


@async_test
async def test_sync_conflict_aborts_without_losing_member_commits(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'shared.txt': 'first\n'})
    b = member(root, tmp_path / 'b', 'b', base, {'shared.txt': 'member local committed\n'})
    await svc.initialize()
    integrated = await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    with pytest.raises(ToolError):
        await svc.sync_member(tmp_path / 'b', 'b', integrated['result_head'])
    assert git(tmp_path / 'b', 'rev-parse', 'HEAD') == b
    assert git(tmp_path / 'b', 'status', '--porcelain') == ''
    assert (tmp_path / 'b' / 'shared.txt').read_text() == 'member local committed\n'


@async_test
async def test_final_unchecked_target_and_noncode_acceptance_evidence(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    await svc.initialize()
    base = git(root, 'rev-parse', 'HEAD')
    git(root, 'checkout', '-b', 'user-other')
    with pytest.raises(ToolError):
        await svc.finalize([{'task_id': 'research', 'accepted': True, 'code': False}], validate=passed)
    result = await svc.finalize([{'task_id': 'research', 'accepted': True, 'code': False, 'evidence': '已审阅报告'}], validate=passed)
    assert result['state'] == 'published'
    assert git(root, 'symbolic-ref', '--short', 'HEAD') == 'user-other'
    assert git(root, 'rev-parse', 'main') == base


@async_test
async def test_goal_identity_and_plan_mode_fail_before_creating_worktree(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    await svc.initialize()
    other = repository(tmp_path / 'other')
    wrong = IntegrationService(other, tmp_path / 'private', 'goal-one', git(other, 'rev-parse', 'HEAD'), 'main')
    with pytest.raises(ToolError):
        await wrong.initialize()
    readonly = IntegrationService(root, tmp_path / 'private', 'read-goal', git(root, 'rev-parse', 'HEAD'), 'main', current_mode=lambda: 'plan')
    with pytest.raises(ToolError):
        await readonly.initialize()
    assert not readonly.worktree_root.exists()


@async_test
async def test_recover_published_operation_after_goal_state_save_failure_without_remerge(tmp_path, monkeypatch):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'first\n'})
    await svc.initialize()
    original = svc._save

    def crash(state):
        if state['state'] == 'ready' and state['head'] != base:
            raise RuntimeError('发布操作记录后，目标快照写入之前退出')
        original(state)

    monkeypatch.setattr(svc, '_save', crash)
    with pytest.raises(RuntimeError):
        await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    actual = git(svc.worktree_root, 'rev-parse', 'HEAD')
    peer = service(tmp_path, root)
    recovered = await peer.recover()
    assert recovered['state'] == 'ready'
    assert recovered['head'] == actual
    assert recovered['active_operation'] is None
    assert git(svc.worktree_root, 'rev-parse', 'HEAD') == actual


@async_test
async def test_final_ignored_local_file_collision_is_not_overwritten(tmp_path):
    root = repository(tmp_path / 'repo')
    (root / '.gitignore').write_text('private.txt\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', '本地配置规则')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    git(root, 'worktree', 'add', '-b', 'a', str(tmp_path / 'a'), base)
    (tmp_path / 'a' / 'private.txt').write_text('member committed\n')
    git(tmp_path / 'a', 'add', '-f', 'private.txt')
    git(tmp_path / 'a', 'commit', '-m', '新增成果路径')
    a = git(tmp_path / 'a', 'rev-parse', 'HEAD')
    await svc.initialize()
    op = await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    (root / 'private.txt').write_text('unknown ignored local\n')
    tasks = [{'task_id': 'a', 'accepted': True, 'code': True, 'operation_id': op['operation_id']}]
    result = await svc.finalize(tasks, validate=passed)
    assert result['state'] == 'needs_review'
    assert (root / 'private.txt').read_text() == 'unknown ignored local\n'
    assert git(root, 'rev-parse', 'main') == base


@async_test
async def test_checked_out_target_ref_locked_during_index_update(tmp_path, monkeypatch):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'first\n'})
    await svc.initialize()
    op = await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    original = svc._git
    competing = []

    async def competition(directory, *args, **kwargs):
        if args[:2] == ('read-tree', '-u'):
            result = subprocess.run(['git', 'update-ref', 'refs/heads/main', a, base], cwd=root,
                                    capture_output=True, text=True)
            competing.append(result.returncode)
        return await original(directory, *args, **kwargs)

    monkeypatch.setattr(svc, '_git', competition)
    tasks = [{'task_id': 'a', 'accepted': True, 'code': True, 'operation_id': op['operation_id']}]
    result = await svc.finalize(tasks, validate=passed)
    assert result['state'] == 'published'
    assert len(competing) == 1 and competing[0] != 0
    assert git(root, 'rev-parse', 'HEAD') == op['result_head']
    assert git(root, 'status', '--porcelain') == ''


@async_test
async def test_final_publish_crash_recovers_without_touching_later_user_commit(tmp_path, monkeypatch):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'first\n'})
    await svc.initialize()
    op = await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    original = svc._save_operation

    def crash(operation):
        if operation['kind'] == 'finalize' and operation['state'] == 'published':
            raise RuntimeError('目标发布已执行，持久结果尚未写入')
        original(operation)

    monkeypatch.setattr(svc, '_save_operation', crash)
    tasks = [{'task_id': 'a', 'accepted': True, 'code': True, 'operation_id': op['operation_id']}]
    with pytest.raises(RuntimeError):
        await svc.finalize(tasks, validate=passed)
    (root / 'user.txt').write_text('later user\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', '故障之后用户继续修改')
    later = git(root, 'rev-parse', 'HEAD')
    peer = IntegrationService(root, tmp_path / 'private', 'goal-one', base, 'main')
    recovered = await peer.recover()
    assert recovered['state'] == 'needs_review'
    assert recovered['operations'][-1]['actual_target_head'] == later
    assert git(root, 'rev-parse', 'HEAD') == later
    assert (root / 'user.txt').read_text() == 'later user\n'


@async_test
async def test_published_goal_is_terminal_for_more_integrations(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'first\n'})
    await svc.initialize()
    op = await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    tasks = [{'task_id': 'a', 'accepted': True, 'code': True, 'operation_id': op['operation_id']}]
    assert (await svc.finalize(tasks, validate=passed))['state'] == 'published'
    with pytest.raises(ToolError):
        await svc.integrate('another', tmp_path / 'a', 'a', a, accepted=True, validate=passed)


@async_test
async def test_git_merge_does_not_run_repository_hooks(tmp_path):
    root = repository(tmp_path / 'repo')
    hook = root / '.git' / 'hooks' / 'post-merge'
    hook.write_text('#!/bin/sh\nprintf executed > hook-output\n')
    hook.chmod(0o700)
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'first\n'})
    await svc.initialize()
    op = await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    assert op['state'] == 'published'
    assert not (svc.worktree_root / 'hook-output').exists()
    assert op['side_effects']['hooks'] == 'disabled_for_managed_git'


@async_test
async def test_registered_input_remains_immutable_after_member_branch_reset(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'accepted content\n'})
    await svc.initialize()
    receipt = await svc.register_input('a', tmp_path / 'a', 'a', a, accepted=True)
    assert receipt['input_commit'] == a
    git(tmp_path / 'a', 'reset', '--hard', base)
    op = await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    assert op['state'] == 'published'
    assert (svc.worktree_root / 'a.txt').read_text() == 'accepted content\n'
    assert git(root, 'rev-parse', 'a') == base


@async_test
async def test_recover_corrupt_operation_blocks_without_modifying_git(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'first\n'})
    await svc.initialize()
    op = await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    (svc.root / (op['operation_id'] + '.json')).write_text(json.dumps({'version': 1, 'goal_id': 'goal-one', 'operation_id': op['operation_id']}))
    actual = git(svc.worktree_root, 'rev-parse', 'HEAD')
    result = await svc.recover()
    assert result['state'] == 'needs_review'
    assert git(svc.worktree_root, 'rev-parse', 'HEAD') == actual
    with pytest.raises(ToolError):
        await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)


@async_test
async def test_rollback_validation_deleted_path_preserves_unknown_ignored_replacement(tmp_path):
    root = repository(tmp_path / 'repo')
    (root / 'restored.txt').write_text('baseline tracked\n')
    (root / '.gitignore').write_text('restored.txt\n')
    git(root, 'add', '-f', 'restored.txt', '.gitignore')
    git(root, 'commit', '-m', '受保护路径')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    git(root, 'worktree', 'add', '-b', 'a', str(tmp_path / 'a'), base)
    git(tmp_path / 'a', 'rm', 'restored.txt')
    git(tmp_path / 'a', 'commit', '-m', '删除路径')
    a = git(tmp_path / 'a', 'rev-parse', 'HEAD')
    await svc.initialize()

    async def created(path, commit):
        (path / 'restored.txt').write_text('unknown ignored replacement\n')
        return {'ok': False}

    result = await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=created)
    assert result['state'] == 'needs_review'
    assert (svc.worktree_root / 'restored.txt').read_text() == 'unknown ignored replacement\n'


@async_test
async def test_sync_git_success_before_record_publish_requires_reconciliation_not_remerge(tmp_path, monkeypatch):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'first\n'})
    await svc.initialize()
    op = await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    git(root, 'worktree', 'add', '-b', 'b', str(tmp_path / 'b'), base)
    original = svc._save_sync

    def crash(operation):
        if operation['state'] == 'synced':
            raise RuntimeError('成员 Git 同步完成后、结果发布前退出')
        original(operation)

    monkeypatch.setattr(svc, '_save_sync', crash)
    with pytest.raises(RuntimeError):
        await svc.sync_member(tmp_path / 'b', 'b', op['result_head'])
    actual = git(tmp_path / 'b', 'rev-parse', 'HEAD')
    peer = service(tmp_path, root)
    recovered = await peer.recover()
    assert recovered['state'] == 'needs_review'
    assert recovered['sync_operations'][-1]['state'] == 'needs_review'
    assert recovered['sync_operations'][-1]['actual_head'] == actual
    assert git(tmp_path / 'b', 'rev-parse', 'HEAD') == actual


@async_test
async def test_registered_input_retry_after_branch_reset_is_idempotent(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'first\n'})
    await svc.initialize()
    first = await svc.register_input('a', tmp_path / 'a', 'a', a, accepted=True)
    git(tmp_path / 'a', 'reset', '--hard', base)
    second = await svc.register_input('a', tmp_path / 'a', 'a', a, accepted=True)
    assert first == second


@async_test
async def test_managed_worktree_creation_and_final_ref_transaction_disable_git_hooks(tmp_path):
    import shlex

    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    base = git(root, 'rev-parse', 'HEAD')
    a = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'first\n'})
    marker = tmp_path / 'hook-side-effect'
    for name in ('post-checkout', 'reference-transaction'):
        hook = root / '.git' / 'hooks' / name
        hook.write_text('#!/bin/sh\nprintf executed > ' + shlex.quote(str(marker)) + '\n')
        hook.chmod(0o700)
    await svc.initialize()
    op = await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    tasks = [{'task_id': 'a', 'accepted': True, 'code': True, 'operation_id': op['operation_id']}]
    assert (await svc.finalize(tasks, validate=passed))['state'] == 'published'
    assert not marker.exists()


@async_test
async def test_corrupt_goal_schema_refuses_git_mutations(tmp_path):
    root = repository(tmp_path / 'repo')
    svc = service(tmp_path, root)
    await svc.initialize()
    svc.record_path.write_text('[]')
    before = git(svc.worktree_root, 'rev-parse', 'HEAD')
    with pytest.raises(ToolError):
        await svc.finalize([{'task_id': 'research', 'accepted': True, 'code': False, 'evidence': '报告'}], validate=passed)
    assert git(svc.worktree_root, 'rev-parse', 'HEAD') == before


@async_test
async def test_plan_switch_during_validation_prevents_code_publication(tmp_path):
    root = repository(tmp_path / 'repo')
    mode = ['execute']
    base = git(root, 'rev-parse', 'HEAD')
    svc = IntegrationService(root, tmp_path / 'private', 'goal-one', base, 'main', current_mode=lambda: mode[0])
    a = member(root, tmp_path / 'a', 'a', base, {'a.txt': 'first\n'})
    await svc.initialize()

    async def switches(path, commit):
        mode[0] = 'plan'
        return {'ok': True}

    result = await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=switches)
    assert result['state'] == 'rolled_back'
    assert git(svc.worktree_root, 'rev-parse', 'HEAD') == base
    mode[0] = 'execute'
    op = await svc.integrate('a', tmp_path / 'a', 'a', a, accepted=True, validate=passed)
    tasks = [{'task_id': 'a', 'accepted': True, 'code': True, 'operation_id': op['operation_id']}]
    result = await svc.finalize(tasks, validate=switches)
    assert result['state'] == 'needs_review'
    assert git(root, 'rev-parse', 'main') == base
