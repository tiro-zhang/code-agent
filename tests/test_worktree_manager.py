"""真实 Git 工作树的恢复、保护与并发生命周期。"""

import asyncio
import importlib
import os
from pathlib import Path

import pytest

from conftest import async_test
from test_worktree_paths import repository, git
from mewcode.tools.base import ToolError
from mewcode.worktrees.paths import freeze_repository
from mewcode.worktrees.config import load_config


def manager(root, **kwargs):
    module=importlib.import_module('mewcode.worktrees')
    assert hasattr(module,'WorktreeManager'), '缺少完整 Worktree 生命周期'
    return module.WorktreeManager(freeze_repository(root),load_config(root),**kwargs)


@async_test
async def test_create_dirty_frozen_root_and_readonly_recovery(tmp_path,monkeypatch):
    root=repository(tmp_path/'repo')
    (root/'project/app.py').write_text('dirty\n')
    m=manager(root/'project')
    cwd=Path.cwd()
    tree=await m.create('general/agent_one',task_id='one')
    assert tree.workspace_root == tree.worktree_root/'project'
    assert (tree.workspace_root/'app.py').read_text() == 'frozen\n'
    assert tree.common_git_dir == root/'.git'
    assert git(tree.worktree_root,'branch','--show-current') == tree.branch
    assert tree.base_commit == git(root,'rev-parse','HEAD')
    assert git(root,'status','--porcelain') == 'M project/app.py'
    assert Path.cwd() == cwd
    before={p:p.stat().st_mtime_ns for p in m.state_root.iterdir()}
    async def forbidden(*args,**kwargs): raise AssertionError('恢复不能调用 Git')
    monkeypatch.setattr(m,'git',forbidden)
    recovered=await m.create('general/agent_one',task_id='one')
    assert recovered.worktree_root == tree.worktree_root
    assert recovered.recovered
    assert before == {p:p.stat().st_mtime_ns for p in m.state_root.iterdir()}


@async_test
async def test_unknown_directory_and_corrupt_backpointer_are_not_repaired(tmp_path):
    root=repository(tmp_path/'repo')
    m=manager(root)
    unknown=m.container/'unknown'
    unknown.mkdir(parents=True)
    (unknown/'keep').write_text('keep')
    with pytest.raises(ToolError): await m.create('unknown',task_id='unknown')
    assert (unknown/'keep').read_text() == 'keep'
    tree=await m.create('one',task_id='one')
    gitdir=Path((tree.worktree_root/'.git').read_text().strip().removeprefix('gitdir: '))
    (gitdir/'gitdir').write_text(str(root/'.git')+'\n')
    with pytest.raises(ToolError): await m.create('one',task_id='one')
    assert tree.worktree_root.exists()


@async_test
async def test_exclusive_lease_blocks_other_owner_and_deletion(tmp_path):
    root=repository(tmp_path/'repo')
    m=manager(root)
    tree=await m.create('one',task_id='one')
    lease=m.enter(tree)
    with pytest.raises(ToolError): m.enter(tree)
    result=await m.delete(tree)
    assert result['state']=='retained' and tree.worktree_root.exists()
    result=await m.exit(lease,evidence_safe=True)
    assert result['state']=='removed'
    assert not tree.worktree_root.exists()
    assert not git(root,'branch','--list',tree.branch)


@pytest.mark.parametrize('change',['unstaged','staged','untracked','ignored','commit','mode','link'])
@async_test
async def test_changed_worktree_is_retained(tmp_path,change):
    root=repository(tmp_path/'repo')
    (root/'.gitignore').write_text('.env\nresult.bin\nnode_modules\n')
    git(root,'add','.gitignore'); git(root,'commit','-m','忽略环境')
    (root/'.env').write_text('local-secret')
    (root/'node_modules').mkdir()
    m=manager(root)
    tree=await m.create('one',task_id='one')
    lease=m.enter(tree)
    path=tree.worktree_root/'project/app.py'
    if change in {'unstaged','staged','commit'}:
        path.write_text('changed\n')
        if change in {'staged','commit'}: git(tree.worktree_root,'add','project/app.py')
        if change=='commit': git(tree.worktree_root,'commit','-m','成果')
    elif change=='untracked': (tree.worktree_root/'new.txt').write_text('成果')
    elif change=='ignored': (tree.worktree_root/'result.bin').write_bytes('成果'.encode())
    elif change=='mode': (tree.worktree_root/'.env').chmod(0o700)
    elif change=='link':
        (tree.worktree_root/'node_modules').unlink()
        (tree.worktree_root/'node_modules').symlink_to(root/'project',target_is_directory=True)
    result=await m.exit(lease,evidence_safe=True)
    assert result['state']=='retained' and tree.worktree_root.exists()
    assert git(root,'branch','--list',tree.branch)


@async_test
async def test_plan_and_cancel_do_not_create_resources(tmp_path):
    root=repository(tmp_path/'repo')
    m=manager(root,current_mode=lambda:'plan')
    with pytest.raises(ToolError): await m.create('one',task_id='one')
    assert not m.container.exists()
    m=manager(root)
    cancel=asyncio.Event(); cancel.set()
    with pytest.raises(asyncio.CancelledError): await m.create('one',task_id='one',cancel_event=cancel)
    assert not m.container.exists()


@async_test
async def test_tracked_management_path_blocks_creation(tmp_path):
    root=repository(tmp_path/'repo')
    occupied=root/'.mewcode/worktrees/keep.txt'
    occupied.parent.mkdir(parents=True)
    occupied.write_text('keep')
    git(root,'add','.'); git(root,'commit','-m','已有路径')
    m=manager(root)
    with pytest.raises(ToolError): await m.create('one',task_id='one')
    assert occupied.read_text()=='keep'


@pytest.mark.parametrize('damage',['version','baseline-path','regenerable','time'])
@async_test
async def test_damaged_record_cannot_authorize_delete(tmp_path,damage):
    import json
    root=repository(tmp_path/'repo')
    m=manager(root)
    tree=await m.create('one',task_id='one')
    path=m.record_path('one')
    record=json.loads(path.read_text())
    if damage=='version': record['version']=True
    elif damage=='baseline-path': record['baseline']={'../outside':{'kind':'file','hash':'x','mode':384}}
    elif damage=='regenerable': record['regenerable']=['**']
    elif damage=='time': record['last_active']=float('nan')
    path.write_text(json.dumps(record))
    result=await m.delete(tree)
    assert result['state']=='retained' and tree.worktree_root.exists()


@async_test
async def test_replaced_group_directory_rejected_before_git(tmp_path,monkeypatch):
    root=repository(tmp_path/'repo')
    m=manager(root)
    m.container.mkdir(parents=True)
    group=m.container/'group'; group.mkdir()
    outside=tmp_path/'outside'; outside.mkdir()
    original=m.git
    async def replace_group(cwd,*args,**kwargs):
        result=await original(cwd,*args,**kwargs)
        if args[0]=='ls-files':
            group.rmdir(); group.symlink_to(outside,target_is_directory=True)
        return result
    monkeypatch.setattr(m,'git',replace_group)
    with pytest.raises(ToolError): await m.create('group/one',task_id='one')
    assert list(outside.iterdir())==[]


@async_test
async def test_unknown_file_appearing_at_delete_recheck_is_protected(tmp_path,monkeypatch):
    root=repository(tmp_path/'repo')
    m=manager(root)
    tree=await m.create('one',task_id='one')
    original=m._protected_reason
    checks=0
    async def introduce(*args):
        nonlocal checks
        result=await original(*args)
        checks+=1
        if checks==2: (tree.worktree_root/'late.txt').write_text('成果')
        return result
    monkeypatch.setattr(m,'_protected_reason',introduce)
    result=await m.delete(tree)
    assert result['state']=='retained'
    assert (tree.worktree_root/'late.txt').read_text()=='成果'


@async_test
async def test_created_storage_and_worktree_are_private(tmp_path):
    import stat
    root=repository(tmp_path/'repo')
    m=manager(root)
    tree=await m.create('one',task_id='one')
    for path in (tree.worktree_root,m.container,m.state_root):
        assert stat.S_IMODE(path.stat().st_mode)&0o077==0
    for path in m.state_root.iterdir(): assert stat.S_IMODE(path.stat().st_mode)&0o077==0


@async_test
async def test_lease_is_exclusive_across_processes(tmp_path):
    import subprocess,sys
    root=repository(tmp_path/'repo')
    m=manager(root); tree=await m.create('one',task_id='one')
    lease=m.enter(tree)
    program='''import sys
from pathlib import Path
from mewcode.worktrees import WorktreeManager
from mewcode.worktrees.paths import freeze_repository
from mewcode.tools.base import ToolError
m=WorktreeManager(freeze_repository(Path(sys.argv[1])))
t=m.recover("one",task_id="one")
try:
    m.enter(t)
except ToolError as error:
    print(error.code)
else:
    raise SystemExit(2)
'''
    try:
        result=subprocess.run([sys.executable,'-c',program,str(root)],capture_output=True,text=True)
        assert result.returncode==0 and result.stdout.strip()=='worktree_busy'
    finally: await m.exit(lease,evidence_safe=True)


@async_test
async def test_cancel_during_git_hook_drains_process_and_keeps_incomplete(tmp_path):
    import shlex
    root=repository(tmp_path/'repo')
    marker=tmp_path/'started'
    hook=root/'.git/hooks/post-checkout'
    hook.write_text('#!/bin/sh\necho $$ > '+shlex.quote(str(marker))+'\nsleep 30\n')
    hook.chmod(0o700)
    m=manager(root); cancel=asyncio.Event()
    task=asyncio.create_task(m.create('one',task_id='one',cancel_event=cancel))
    try:
        # 给真实 Git 检出及 Hook 启动独立的准备窗口。
        async with asyncio.timeout(10):
            while not marker.exists():
                if task.done():
                    await task
                    pytest.fail('创建已结束但阻塞 Hook 未启动')
                await asyncio.sleep(.01)
        cancel.set()
        with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task,3)
        with pytest.raises(ToolError): await m.create('one',task_id='one')
        assert (m.container/'one').exists()
    finally:
        cancel.set()
        if not task.done():
            task.cancel(); await asyncio.gather(task,return_exceptions=True)


@async_test
async def test_branch_delete_failure_reports_partial_completion(tmp_path,monkeypatch):
    root=repository(tmp_path/'repo');m=manager(root)
    tree=await m.create('one',task_id='one')
    original=m.git
    async def fail_branch(cwd,*args,**kwargs):
        if args[0]=='update-ref': raise ToolError('worktree_git_error','模拟分支删除失败')
        return await original(cwd,*args,**kwargs)
    monkeypatch.setattr(m,'git',fail_branch)
    result=await m.delete(tree)
    assert result['state']=='removed' and result['branch_state']=='retained'
    assert git(root,'branch','--list',tree.branch)
    assert not tree.worktree_root.exists()
