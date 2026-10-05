"""声明式初始化的真实文件、Git hooks 与失败残留。"""

from pathlib import Path

import pytest

from conftest import async_test
from test_worktree_paths import repository,git
from test_worktree_manager import manager
from test_worktree_config import config_file
from mewcode.tools.base import ToolError


@async_test
async def test_optional_nested_file_and_dependency_missing_are_skipped(tmp_path):
    root=repository(tmp_path/'repo')
    config_file(root,'version: 1\ncopy-files: [{path: missing/local.env}]\n')
    tree=await manager(root).create('one',task_id='one')
    assert not (tree.worktree_root/'missing/local.env').exists()


@async_test
async def test_initialization_copies_only_declared_ignored_files(tmp_path):
    root=repository(tmp_path/'repo')
    (root/'.gitignore').write_text('.env\nrun/*.json\n')
    git(root,'add','.gitignore'); git(root,'commit','-m','环境')
    (root/'.env').write_text('private-key')
    (root/'run').mkdir()
    (root/'run/local.json').write_text('{"runtime":true}')
    (root/'new-source.py').write_text('parent only')
    config_file(root,'version: 1\ncopy-ignored-files: [{pattern: "run/*.json", required: true}]\n')
    m=manager(root)
    tree=await m.create('one',task_id='one')
    assert (tree.worktree_root/'.env').read_text()=='private-key'
    assert (tree.worktree_root/'run/local.json').read_text()=='{"runtime":true}'
    assert not (tree.worktree_root/'new-source.py').exists()
    for record in m.state_root.glob('*.json'): assert 'private-key' not in record.read_text()
    (root/'.env').write_text('new key')
    assert (tree.worktree_root/'.env').read_text()=='private-key'


@pytest.mark.parametrize('kind',['required','oversize','tracked','escape','special','target','permission-record'])
@async_test
async def test_initialization_failure_never_publishes_ready(tmp_path,kind):
    import os
    root=repository(tmp_path/'repo')
    cfg='version: 1\ncopy-files: [{path: local.env, required: true}]\n'
    if kind=='oversize': (root/'local.env').write_bytes(b'x'*(1024*1024+1))
    elif kind=='tracked':
        (root/'local.env').write_text('frozen')
        git(root,'add','.'); git(root,'commit','-m','跟踪')
        (root/'local.env').write_text('dirty')
    elif kind=='escape':
        outside=tmp_path/'outside'; outside.write_text('private')
        (root/'local.env').symlink_to(outside)
    elif kind=='special': os.mkfifo(root/'local.env')
    elif kind=='target':
        (root/'local.env').write_text('tracked')
        git(root,'add','.'); git(root,'commit','-m','已存在')
    elif kind=='permission-record':
        (root/'.mewcode').mkdir(exist_ok=True)
        (root/'.gitignore').write_text('.mewcode/permissions.local.yaml\n')
        (root/'.mewcode/permissions.local.yaml').write_text('grants: []')
        cfg='version: 1\ncopy-files: []\ncopy-ignored-files: [{pattern: ".mewcode/*.yaml"}]\n'
    config_file(root,cfg)
    m=manager(root)
    with pytest.raises(ToolError): await m.create('one',task_id='one')
    with pytest.raises(ToolError): await m.create('one',task_id='one')
    assert (m.container/'one').exists()


@async_test
async def test_hooks_configuration_is_worktree_specific(tmp_path):
    root=repository(tmp_path/'repo')
    (root/'hooks').mkdir(); (root/'hooks/keep').write_text('hook')
    git(root,'add','.'); git(root,'commit','-m','hooks')
    shared=tmp_path/'shared-hooks'; shared.mkdir()
    git(root,'config','core.hooksPath',str(shared))
    config_file(root,'version: 1\nhooks-path: hooks\n')
    m=manager(root)
    tree=await m.create('one',task_id='one')
    assert git(root,'config','core.hooksPath')==str(shared)
    assert git(tree.worktree_root,'config','core.hooksPath')==str(tree.worktree_root/'hooks')
    assert git(root,'config','--local','core.hooksPath')==str(shared)


@async_test
async def test_total_copy_budget_is_checked_before_ready(tmp_path):
    root=repository(tmp_path/'repo')
    paths=[]
    for i in range(17):
        name=f'local-{i}.env'; paths.append('{path: '+name+'}')
        (root/name).write_bytes(b'x'*(1024*1024))
    config_file(root,'version: 1\ncopy-files: ['+', '.join(paths)+']\n')
    m=manager(root)
    with pytest.raises(ToolError) as error: await m.create('one',task_id='one')
    assert '预算' in error.value.message
    with pytest.raises(ToolError): m.recover('one')


@async_test
async def test_ignored_enumeration_limit_is_not_bypassed(tmp_path):
    root=repository(tmp_path/'repo')
    (root/'.gitignore').write_text('runtime/\n')
    git(root,'add','.gitignore'); git(root,'commit','-m','忽略')
    (root/'runtime').mkdir()
    for i in range(513): (root/f'runtime/{i}.json').write_text('x')
    config_file(root,'version: 1\ncopy-files: []\ncopy-ignored-files: [{pattern: "runtime/*.json"}]\n')
    m=manager(root)
    with pytest.raises(ToolError): await m.create('one',task_id='one')
    with pytest.raises(ToolError): m.recover('one')


@async_test
async def test_incompatible_hook_config_never_changes_parent_policy(tmp_path):
    root=repository(tmp_path/'repo')
    (root/'hooks').mkdir(); (root/'hooks/x').write_text('hook')
    git(root,'add','.');git(root,'commit','-m','hooks')
    git(root,'config','core.worktree',str(root))
    config_file(root,'version: 1\nhooks-path: hooks\n')
    m=manager(root)
    with pytest.raises(ToolError): await m.create('one',task_id='one')
    assert git(root,'config','core.worktree')==str(root)
    import subprocess
    result=subprocess.run(['git','-C',str(root),'config','--get','extensions.worktreeConfig'],capture_output=True)
    assert result.returncode==1
    with pytest.raises(ToolError): m.recover('one')
