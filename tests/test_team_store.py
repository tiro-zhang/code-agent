"""私有快照、固定锁及同仓库接续。"""
import asyncio
import os
from pathlib import Path
import subprocess
import pytest
from mewcode.teams.store import TeamStore, TeamStoreError
from mewcode.teams.models import Member, new_id


def repository(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    subprocess.run(['git', 'init', '-b', 'main', str(root)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.test',
                    'commit', '--allow-empty', '-m', 'base'], check=True, capture_output=True)
    return root


def test_create_pause_resume_and_ownership(tmp_path):
    async def scenario():
        root = repository(tmp_path)
        store = TeamStore(tmp_path / 'teams', lock_timeout=.05)
        team = await store.create('example', root)
        assert (store.team_path('example').stat().st_mode & 0o777) == 0o700
        assert (store.team_path('example') / 'team.json').stat().st_mode & 0o777 == 0o600
        with pytest.raises(TeamStoreError):
            await store.create('example', root)
        second = TeamStore(tmp_path / 'teams', lock_timeout=.05)
        with pytest.raises(TeamStoreError):
            await second.resume('example', root)
        await store.pause('example')
        restored = await second.resume('example', root)
        assert restored.team_id == team.team_id
        await second.pause('example')
    asyncio.run(scenario())


def test_special_and_symlink_records_refused(tmp_path):
    async def scenario():
        store = TeamStore(tmp_path / 'teams')
        await store.create('example', repository(tmp_path))
        target = store.team_path('example') / 'runtime.json'
        target.symlink_to(tmp_path / 'external')
        with pytest.raises(TeamStoreError):
            store.write_json('example', 'runtime.json', {})
        target.unlink()
        os.mkfifo(target)
        with pytest.raises(TeamStoreError):
            store.read_json('example', 'runtime.json')
        await store.pause('example')
    asyncio.run(scenario())


def test_lock_wait_is_cancelable_and_inode_preserved(tmp_path):
    async def scenario():
        store = TeamStore(tmp_path / 'teams', lock_timeout=.05)
        await store.create('example', repository(tmp_path))
        async with store.lock('example', 'tasks.lock'):
            inode = (store.team_path('example') / 'tasks.lock').stat().st_ino
            with pytest.raises(TeamStoreError, match='busy'):
                async with store.lock('example', 'tasks.lock'):
                    pass
            async def wait():
                async with store.lock('example', 'tasks.lock'):
                    pass
            task = asyncio.create_task(wait())
            await asyncio.sleep(.005)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert (store.team_path('example') / 'tasks.lock').stat().st_ino == inode
        await store.pause('example')
    asyncio.run(scenario())


def test_wrong_repository_and_unknown_runtime_recovery(tmp_path):
    async def scenario():
        store = TeamStore(tmp_path / 'teams')
        root = repository(tmp_path)
        team = await store.create('example', root)
        member = Member(new_id('member'), 'alice', 'developer', str(root), state='running')
        await store.register_member('example', member)
        await store.pause('example', unknown_members=[member.member_id])
        with pytest.raises(TeamStoreError, match='核查'):
            await store.resume('example', root)
        assert store.load('example').members[member.member_id].state == 'needs_review'
    asyncio.run(scenario())


def test_goal_freezes_current_target_and_pause_refuses_live_member(tmp_path):
    async def scenario():
        store = TeamStore(tmp_path / 'teams')
        root = repository(tmp_path)
        team = await store.create('example', root)
        subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.test',
                        'commit', '--allow-empty', '-m', 'second'], check=True, capture_output=True)
        goal = await store.start_goal('example', '第二个基线')
        assert goal.baseline_commit != team.baseline_commit
        member = Member(new_id('member'), 'alice', 'developer', str(root), state='running')
        await store.register_member('example', member)
        with pytest.raises(TeamStoreError):
            await store.pause('example')
        assert store.load('example').status == 'active'
        await store.pause('example', unknown_members=[member.member_id])
    asyncio.run(scenario())


def _hold_lease(path, connection):
    from mewcode.teams.store import TeamLease
    lease = TeamLease(Path(path))
    connection.send('ready')
    connection.recv()
    lease.close()


def test_real_process_lock_ownership_and_exit_release(tmp_path):
    import multiprocessing
    async def scenario():
        store = TeamStore(tmp_path / 'teams', lock_timeout=.03)
        await store.create('example', repository(tmp_path))
        path = store.team_path('example') / 'tasks.lock'
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe()
        process = context.Process(target=_hold_lease, args=(str(path), child))
        process.start()
        assert parent.poll(5) and parent.recv() == 'ready'
        inode = path.stat().st_ino
        os.utime(path, (1, 1))
        try:
            with pytest.raises(TeamStoreError, match='busy'):
                async with store.lock('example', 'tasks.lock'):
                    pass
            assert path.stat().st_ino == inode
        finally:
            parent.send('stop')
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join()
        async with store.lock('example', 'tasks.lock'):
            assert path.stat().st_ino == inode
        await store.pause('example')
    asyncio.run(scenario())


def test_resume_refuses_actual_member_lease_even_if_state_idle(tmp_path):
    async def scenario():
        store = TeamStore(tmp_path / 'teams')
        root = repository(tmp_path)
        team = await store.create('example', root)
        member = Member(new_id('member'), 'alice', 'developer', str(root), state='idle')
        await store.register_member('example', member)
        lease = store.member_lease('example', member.member_id)
        await store.pause('example')
        try:
            with pytest.raises(TeamStoreError):
                await TeamStore(tmp_path / 'teams').resume('example', root)
        finally:
            lease.close()
    asyncio.run(scenario())


def test_atomic_replace_failure_preserves_prior_snapshot(tmp_path, monkeypatch):
    async def scenario():
        store = TeamStore(tmp_path / 'teams')
        team = await store.create('example', repository(tmp_path))
        original = store.load('example').to_dict()
        def fail(*args, **kwargs):
            raise OSError('disk unavailable')
        with monkeypatch.context() as patch:
            patch.setattr(os, 'replace', fail)
            with pytest.raises(TeamStoreError):
                await store.update_team('example', lambda team: setattr(team, 'status', 'paused'))
        assert store.load('example').to_dict() == original
        await store.pause('example')
    asyncio.run(scenario())


@pytest.mark.parametrize('name', ['../evil', '/tmp/team', 'Mixed', 'a\n', 'a' * 65])
def test_invalid_team_name_never_creates_storage(tmp_path, name):
    from mewcode.teams.models import TeamValidationError
    store = TeamStore(tmp_path / 'teams')
    with pytest.raises(TeamValidationError):
        store.team_path(name)
    assert not (tmp_path / 'teams').exists()


def test_corrupt_record_is_not_overwritten(tmp_path):
    async def scenario():
        store = TeamStore(tmp_path / 'teams')
        await store.create('example', repository(tmp_path))
        record = store.team_path('example') / 'team.json'
        record.write_text('{invalid', encoding='utf-8')
        with pytest.raises(TeamStoreError):
            await store.update_team('example', lambda team: setattr(team, 'status', 'paused'))
        assert record.read_text() == '{invalid'
        store.release_lead('example')
    asyncio.run(scenario())


def test_resume_allows_same_repository_worktree_and_rejects_other_repo(tmp_path):
    async def scenario():
        store = TeamStore(tmp_path / 'teams')
        root = repository(tmp_path)
        team = await store.create('example', root)
        await store.pause('example')
        linked = tmp_path / 'linked'
        subprocess.run(['git', '-C', str(root), 'worktree', 'add', '-b', 'linked', str(linked)], check=True, capture_output=True)
        other_container = tmp_path / 'other'
        other_container.mkdir()
        other = repository(other_container)
        with pytest.raises(TeamStoreError):
            await store.resume('example', other)
        restored = await store.resume('example', linked)
        assert restored.team_id == team.team_id
        await store.pause('example')
    asyncio.run(scenario())


def test_member_directory_symlink_refuses_resume(tmp_path):
    async def scenario():
        store = TeamStore(tmp_path / 'teams')
        root = repository(tmp_path)
        team = await store.create('example', root)
        await store.pause('example')
        member_dir = store.member_path('example', team.lead_id)
        (member_dir / 'cache').rmdir()
        member_dir.rmdir()
        member_dir.symlink_to(root, target_is_directory=True)
        with pytest.raises((TeamStoreError, OSError)):
            await store.resume('example', root)
        assert store.load('example').status == 'paused'
    asyncio.run(scenario())


def test_resume_only_revives_members_allowed_by_lifecycle(tmp_path):
    async def scenario():
        store = TeamStore(tmp_path / 'teams')
        root = repository(tmp_path)
        team = await store.create('example', root)
        resumable = Member(new_id('member'), 'alice', 'developer', str(root), state='stopped', resume_allowed=True)
        stopped = Member(new_id('member'), 'bob', 'developer', str(root), state='stopped', resume_allowed=False)
        await store.register_member('example', resumable)
        await store.register_member('example', stopped)
        await store.pause('example')
        resumed = await store.resume('example', root)
        assert resumed.members[resumable.member_id].state == 'idle'
        assert resumed.members[stopped.member_id].state == 'stopped'
        assert resumed.members[resumable.member_id].member_id == resumable.member_id
        await store.pause('example')
    asyncio.run(scenario())
