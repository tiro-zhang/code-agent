"""只处理准确受管、过期、无活动引用和无成果的工作树。"""
import asyncio
import time

from conftest import async_test,ScriptedProvider
from test_worktree_paths import repository,git
from test_worktree_manager import manager
from test_skills_session import session,response
from mewcode.worktrees.records import load_record


def expire(m,tree,age=31*86400):
    record=load_record(m.record_path(tree.name))
    record.update(created=time.time()-age,last_active=time.time()-age,evidence_protected=False)
    m.save(tree.name,record)


@async_test
async def test_scan_filters_live_refs_changes_age_and_foreign_directories(tmp_path):
    root=repository(tmp_path/'repo');m=manager(root)
    trees={name:await m.create(name,task_id=name) for name in ('clean','live','proof','dirty','fresh')}
    for tree in trees.values(): expire(m,tree)
    lease=m.enter(trees['live'])
    expire(m,trees['live'])
    record=load_record(m.record_path('proof'));record['evidence_protected']=True;m.save('proof',record)
    (trees['dirty'].workspace_root/'unknown').write_text('成果')
    record=load_record(m.record_path('fresh'));record['last_active']=time.time();m.save('fresh',record)
    foreign=m.container/'manual';foreign.mkdir();(foreign/'keep').write_text('人工目录')
    try:
        reports=await m.scan()
        assert not trees['clean'].worktree_root.exists()
        assert all(tree.worktree_root.exists() for name,tree in trees.items() if name!='clean')
        assert (foreign/'keep').read_text()=='人工目录'
        assert any(r['name']=='clean' and r['state']=='removed' for r in reports)
    finally:
        lease.close()


@async_test
async def test_scan_old_base_after_parent_new_commit_and_strict_ttl(tmp_path):
    root=repository(tmp_path/'repo');m=manager(root)
    tree=await m.create('old',task_id='old');expire(m,tree)
    (root/'new').write_text('B');git(root,'add','.');git(root,'commit','-m','B')
    current=manager(root)
    record=load_record(m.record_path('old'));now=record['last_active']+30*86400
    assert await current.scan(now=now)==[] and tree.worktree_root.exists()
    reports=await current.scan(now=now+1)
    assert reports[0]['state']=='removed' and not tree.worktree_root.exists()


@async_test
async def test_scan_rechecks_activity_inside_delete_lock(tmp_path,monkeypatch):
    root=repository(tmp_path/'repo');m=manager(root)
    tree=await m.create('old',task_id='old');expire(m,tree)
    from mewcode.worktrees.manager import WorktreeManager
    original=WorktreeManager.delete
    async def refreshed(self,tree,**kwargs):
        record=load_record(self.record_path(tree.name));record['last_active']=time.time();self.save(tree.name,record)
        return await original(self,tree,**kwargs)
    monkeypatch.setattr(WorktreeManager,'delete',refreshed)
    reports=await m.scan()
    assert reports[0]['state']=='retained' and tree.worktree_root.exists()


@async_test
async def test_session_cleanup_start_mode_and_close_are_owned(tmp_path,monkeypatch):
    from mewcode.worktrees.cleanup import WorktreeCleaner
    root=repository(tmp_path/'repo');m=manager(root)
    tree=await m.create('old',task_id='old');expire(m,tree)
    chat=session(root,ScriptedProvider([response('可继续')]))
    try:
        chat.enter_plan();await chat.start()
        await asyncio.sleep(.05)
        assert tree.worktree_root.exists()
        await chat.set_mode('execute')
        await chat.worktree_cleaner.scan_once()
        assert not tree.worktree_root.exists()
        await chat.aclose()
        assert chat.worktree_cleaner.closed and chat.worktree_cleaner.task.done()
    finally:
        await chat.aclose()


@async_test
async def test_corrupt_record_is_skipped_with_bounded_diagnostic(tmp_path):
    root=repository(tmp_path/'repo');m=manager(root)
    tree=await m.create('bad',task_id='bad')
    m.record_path('bad').write_text('{broken')
    reports=await m.scan()
    assert reports[0]['state']=='skipped' and 'reason' in reports[0]
    assert tree.worktree_root.exists()


@async_test
async def test_scan_failure_is_recoverable_and_close_waits_local_cleanup(tmp_path,monkeypatch):
    from mewcode.worktrees.cleanup import WorktreeCleaner
    root=repository(tmp_path/'repo')
    warnings=[];cleaner=WorktreeCleaner(root,lambda:'execute',warnings)
    await cleaner.ready()
    calls=0
    async def flaky():
        nonlocal calls
        calls+=1
        if calls==1: raise OSError('本轮失败')
        return []
    monkeypatch.setattr(cleaner.manager,'scan',flaky)
    assert await cleaner.scan_once()==[]
    assert await cleaner.scan_once()==[] and calls==2 and warnings
    entered,drained=asyncio.Event(),asyncio.Event()
    async def blocked():
        entered.set()
        try: await asyncio.Event().wait()
        finally:
            await asyncio.sleep(.05)
            drained.set()
    monkeypatch.setattr(cleaner.manager,'scan',blocked)
    cleaner.start();await entered.wait()
    await cleaner.close()
    assert drained.is_set() and cleaner.task.done() and cleaner._scan is None
