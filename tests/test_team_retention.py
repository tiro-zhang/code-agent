"""真实团队成员目录和用户域存档不随普通 TTL 清理。"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time

import pytest

from conftest import ScriptedProvider, async_test
from test_agent_loop import answer
from test_team_runtime import wait_idle
from test_team_service import make_session
from test_team_integration import git
from mewcode.context.spill import ResultCache
from mewcode.sessions.store import Journal, cleanup_expired
from mewcode.tools.base import ToolResult
from mewcode.types import Message
from mewcode.worktrees.records import load_record


async def member_session(tmp_path):
    session = make_session(tmp_path)
    session.provider_factory = lambda config: ScriptedProvider([answer('保存上下文')])
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    await session.teams.control({'action': 'goal', 'description': '长期引用'})
    member = await session.teams.member({'action': 'spawn', 'name': 'worker', 'role': 'general', 'backend': 'inprocess'})
    await session.teams.message({'action': 'send', 'recipient': 'worker', 'body': '读取后保持空闲'})
    runtime = await wait_idle(session, member['member_id'])
    return session, member, runtime, runtime.tree_lease.tree


def make_expired(manager, tree, **changes):
    record = load_record(manager.record_path(tree.name))
    old = time.time() - 61 * 86400
    record.update(created=old, last_active=old, **changes)
    manager.save(tree.name, record)
    return record


@async_test
async def test_real_idle_and_paused_member_worktree_survives_expired_scan(tmp_path):
    session, member, runtime, tree = await member_session(tmp_path)
    manager = runtime.manager
    try:
        assert session.teams.store.load('alpha').members[member['member_id']].state == 'idle'
        make_expired(manager, tree, evidence_protected=False)
        await manager.scan()
        assert tree.worktree_root.exists()
        await session.teams.control({'action': 'pause'})
        assert session.teams.store.load('alpha').status == 'paused'
        # 清除其它保护并确认无活动工作树租约，让此断言单独验证 team_pin。
        record = make_expired(manager, tree, evidence_protected=False)
        assert record['team_pin']['member_id'] == member['member_id']
        reports = await manager.scan()
        assert reports == []
        report = await manager.delete(tree)
        assert report['state'] == 'retained' and '团队持久引用' in report['reason']
        assert tree.worktree_root.exists() and git(tree.worktree_root, 'branch', '--show-current') == tree.branch
    finally:
        await session.aclose()


@async_test
@pytest.mark.parametrize('protection', ['commit', 'dirty', 'evidence', 'clean'])
async def test_releasing_team_pin_preserves_actual_result_or_evidence(tmp_path, protection):
    session, member, runtime, tree = await member_session(tmp_path)
    manager = runtime.manager
    try:
        await session.teams.control({'action': 'pause'})
        record = make_expired(manager, tree, evidence_protected=protection == 'evidence')
        record.pop('team_pin')
        manager.save(tree.name, record)
        artifact = tree.workspace_root / 'result.txt'
        commit = None
        if protection in {'commit', 'dirty'}:
            artifact.write_text('成员真实成果\n')
        if protection == 'commit':
            git(tree.worktree_root, 'add', 'result.txt')
            git(tree.worktree_root, '-c', 'user.name=测试', '-c', 'user.email=test@example.invalid', 'commit', '-m', '成果')
            commit = git(tree.worktree_root, 'rev-parse', 'HEAD')
        reports = await manager.scan()
        if protection == 'evidence':
            assert reports == []
            report = await manager.delete(tree)
        else:
            report = next(item for item in reports if item['name'] == tree.name)
        if protection == 'clean':
            assert report['state'] == 'removed' and not tree.worktree_root.exists()
            assert not git(manager.repository.checkout_root, 'branch', '--list', tree.branch)
        else:
            assert report['state'] == 'retained' and tree.worktree_root.exists()
            assert git(tree.worktree_root, 'branch', '--show-current') == tree.branch
            if protection in {'commit', 'dirty'}:
                assert artifact.read_text() == '成员真实成果\n'
            if commit:
                assert git(tree.worktree_root, 'rev-parse', 'HEAD') == commit
            if protection == 'evidence':
                assert '证据' in report['reason']
    finally:
        await session.aclose()


@async_test
async def test_project_ttl_does_not_scan_external_member_journal_and_cache(tmp_path):
    session, member, runtime, tree = await member_session(tmp_path)
    root = session.executor.context.root
    storage = session.teams.store.member_path('alpha', member['member_id'])
    journal_path = runtime.session.journal.path
    identity = runtime.session.journal.id
    cached = runtime.session.context.cache.save(Message('tool', tool_call_id='proof',
        tool_result=ToolResult.success({'content': '长期成果证据'})), 'read_file')
    cache_file = storage / cached
    assert journal_path.is_relative_to(session.user_root) and cache_file.is_relative_to(session.user_root)
    assert not journal_path.is_relative_to(tree.workspace_root)
    # 普通存档作为实际 TTL 删除的对照，防止“清理没有运行”的假阳性。
    ordinary = Journal.create(root, 'openai', 'test')
    ordinary_path = ordinary.path
    ordinary.close()
    try:
        await session.teams.control({'action': 'pause'})
        before = (journal_path.read_bytes(), cache_file.read_bytes())
        future = datetime.now(timezone.utc) + timedelta(days=61)
        cleanup_expired(root, now=future)
        cleanup_expired(tree.workspace_root, now=future)
        assert not ordinary_path.exists()
        assert (journal_path.read_bytes(), cache_file.read_bytes()) == before
        reopened = Journal.resume(tree.workspace_root, identity, 'openai', 'test', storage_root=storage)
        cache = ResultCache(tree.workspace_root, session_id=identity, persistent=True, storage_root=storage)
        try:
            cache.reopen({cached})
            assert cache.restore(cached).data == {'content': '长期成果证据'}
            assert any('保存上下文' in str(message.content) for message in reopened.projection.history)
        finally:
            cache.close()
            reopened.close()
    finally:
        await session.aclose()
