"""真实仓库身份与路径遍历的边界。"""

import importlib
from importlib.util import find_spec
import subprocess

import pytest

from mewcode.tools.base import ToolError


def api():
    assert find_spec('mewcode.worktrees') is not None, '缺少 Worktree 管理模块'
    return importlib.import_module('mewcode.worktrees.paths')


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def repository(root):
    root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.email', 'test@example.invalid')
    git(root, 'config', 'user.name', '测试')
    (root/'project').mkdir()
    (root/'project/app.py').write_text('frozen\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', '基线')
    return root


def test_frozen_identity_uses_original_project_and_committed_head(tmp_path):
    root = repository(tmp_path/'repo')
    (root/'project/app.py').write_text('dirty\n')
    snapshot = api().freeze_repository(root/'project')
    assert snapshot.origin_root == root/'project'
    assert snapshot.checkout_root == root
    assert snapshot.common_git_dir == root/'.git'
    assert snapshot.project_relative.as_posix() == 'project'
    expected = git(root, 'rev-parse', 'HEAD')
    assert snapshot.base_commit == expected
    git(root, 'add', '.')
    git(root, 'commit', '-m', '后续提交')
    assert snapshot.base_commit == expected != git(root, 'rev-parse', 'HEAD')


@pytest.mark.parametrize('kind', ['plain', 'unborn', 'uncommitted-project', 'bare'])
def test_unsupported_identity_fails_before_creation(tmp_path, kind):
    root=tmp_path/'repo'
    if kind == 'uncommitted-project':
        repository(root)
        root=root/'new-project'
        root.mkdir()
    else:
        root.mkdir()
        if kind == 'unborn': git(root, 'init')
        elif kind == 'bare': git(root, 'init', '--bare')
    with pytest.raises(ToolError) as caught:
        api().freeze_repository(root)
    assert caught.value.code == 'worktree_repository_error'
    assert not (root/'.mewcode/worktrees').exists()


@pytest.mark.parametrize('name', ['general/agent_123', 'a'*64, 'a/b/c/d', 'a'*63+'/'+ 'b'*64])
def test_safe_nested_names(name):
    assert api().validate_name(name) == tuple(name.split('/'))


@pytest.mark.parametrize('name', ['', '.', '..', '../a', 'a/../b', 'a//b', '/a', 'a/',
                                  'a\\b', 'a\n', '中文', 'a'*65, 'a/b/c/d/e',
                                  'a'*64+'/'+ 'b'*64, 'a/./b'])
def test_name_rejected_before_normalization(name):
    with pytest.raises(ToolError) as caught:
        api().validate_name(name)
    assert caught.value.code == 'invalid_worktree_name'


def test_managed_target_rejects_links_and_containing_worktrees(tmp_path):
    container=tmp_path/'.mewcode/worktrees'
    container.mkdir(parents=True)
    outside=tmp_path/'outside'
    outside.mkdir()
    (container/'alias').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ToolError): api().managed_target(container, 'alias/child')
    existing=container/'role/one'
    existing.mkdir(parents=True)
    (existing/'.git').write_text('gitdir: fake\n')
    with pytest.raises(ToolError): api().managed_target(container, 'role/one/nested')
    with pytest.raises(ToolError): api().managed_target(container, 'role')
    assert api().managed_target(container, 'role/two') == container/'role/two'


def test_missing_git_does_not_fall_back(tmp_path,monkeypatch):
    root=repository(tmp_path/'repo')
    monkeypatch.setenv('PATH','')
    with pytest.raises(ToolError): api().freeze_repository(root)
    assert not (root/'.mewcode').exists()
