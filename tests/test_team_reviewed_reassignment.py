"""新领取发布之前保留崩溃审核，失败不把旧领取恢复成可执行。"""

import pytest

from conftest import async_test
from mewcode.teams.tasks import TaskBoard
from test_team_crash_recovery import frozen_running_task, journal_writer, kill_writer
from test_team_lead_resume import reopen


async def reviewed_session(tmp_path, *, accepted=False):
    previous, runtime, member, task = await frozen_running_task(tmp_path, accepted=accepted)
    process = await journal_writer(previous, member)
    await kill_writer(process)
    session = reopen(previous)
    await session.teams.control({'action': 'resume', 'name': 'alpha'})
    return session, member, task


@pytest.mark.parametrize('failure', ['terminal', 'inactive_goal', 'task_save', 'concurrent_revision'])
@async_test
async def test_failed_reassignment_preserves_actual_review_and_claim(tmp_path, monkeypatch, failure):
    session, member, task = await reviewed_session(tmp_path, accepted=failure == 'terminal')
    store = session.teams.store
    board = session.teams.board()
    relative = f'members/{member.member_id}/recovery.json'
    try:
        if failure == 'inactive_goal':
            await session.teams.cancel_goal()
        if failure == 'concurrent_revision':
            get = board.get
            async def changed_get(actor, identity):
                saved = await get(actor, identity)
                await TaskBoard(store, 'alpha').update(actor, identity, saved.revision, title='并发真实更新')
                return saved
            monkeypatch.setattr(board, 'get', changed_get)
            monkeypatch.setattr(session.teams, 'board', lambda: board)
        original = store.write_json
        if failure == 'task_save':
            def failed_save(name, path, value):
                if path == 'tasks.json':
                    raise OSError('任务发布失败')
                return original(name, path, value)
            monkeypatch.setattr(store, 'write_json', failed_save)
        before_member = store.load('alpha').members[member.member_id].to_dict()
        before_marker = store.read_json('alpha', relative)
        before_task = (await TaskBoard(store, 'alpha').get(session.team_scope.member_id, task['task_id'])).to_dict()
        with pytest.raises((OSError, ValueError)):
            await session.teams.task({'action': 'reassign', 'task_id': task['task_id'], 'member_id': member.member_id})
        after = (await TaskBoard(store, 'alpha').get(session.team_scope.member_id, task['task_id'])).to_dict()
        if failure == 'concurrent_revision':
            assert after['title'] == '并发真实更新' and after['revision'] == before_task['revision'] + 1
            assert after['claim_id'] == before_task['claim_id'] and after['state'] == before_task['state']
        else:
            assert after == before_task
        assert store.load('alpha').members[member.member_id].to_dict() == before_member
        assert store.read_json('alpha', relative) == before_marker
        assert not session.provider.requests and not session.teams.runners
    finally:
        monkeypatch.undo()
        await session.aclose()


@async_test
async def test_member_bind_failure_keeps_review_after_actual_new_claim_publication(tmp_path, monkeypatch):
    session, member, task = await reviewed_session(tmp_path)
    store = session.teams.store
    relative = f'members/{member.member_id}/recovery.json'
    before_member = store.load('alpha').members[member.member_id].to_dict()
    marker = store.read_json('alpha', relative)
    original = store.write_json
    def failed_bind(name, path, value):
        if path == 'team.json' and value['members'][member.member_id]['claim_id'] != task['claim_id']:
            raise OSError('成员绑定失败')
        return original(name, path, value)
    monkeypatch.setattr(store, 'write_json', failed_bind)
    try:
        with pytest.raises(OSError):
            await session.teams.task({'action': 'reassign', 'task_id': task['task_id'], 'member_id': member.member_id})
        actual = await session.teams.board().get(session.team_scope.member_id, task['task_id'])
        assert actual.claim_id != task['claim_id'] and actual.state == 'claimed'
        assert store.load('alpha').members[member.member_id].to_dict() == before_member
        assert store.read_json('alpha', relative) == marker
        assert not session.provider.requests and not session.teams.runners
    finally:
        monkeypatch.undo()
        await session.aclose()


@async_test
async def test_successful_new_claim_alone_clears_member_review(tmp_path):
    session, member, task = await reviewed_session(tmp_path)
    try:
        result = await session.teams.task({'action': 'reassign', 'task_id': task['task_id'], 'member_id': member.member_id})
        saved = session.teams.store.load('alpha').members[member.member_id]
        assert result['claim_id'] != task['claim_id'] and result['state'] == 'claimed'
        assert saved.state == 'idle' and saved.claim_id == result['claim_id'] and saved.resume_allowed
        assert not session.provider.requests and not session.teams.runners
    finally:
        await session.aclose()
