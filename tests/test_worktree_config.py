"""初始化配置的严格输入和冻结合同。"""

import importlib
from importlib.util import find_spec

import pytest

from mewcode.tools.base import ToolError


def api():
    assert find_spec('mewcode.worktrees') is not None, '缺少 Worktree 配置模块'
    return importlib.import_module('mewcode.worktrees.config')


def config_file(root, content):
    path=root/'.mewcode/worktrees.yaml'
    path.parent.mkdir(exist_ok=True)
    path.write_text(content)
    return path


def test_config_defaults_and_snapshot_survive_changes(tmp_path):
    cfg=api().load_config(tmp_path)
    assert [(x.path,x.required) for x in cfg.copy_files] == [('.env',False)]
    assert [x.path for x in cfg.link_directories] == ['.venv','node_modules']
    assert not cfg.copy_ignored_files
    assert (cfg.ttl_days,cfg.interval_seconds) == (30,1800)
    path=config_file(tmp_path, 'version: 1\ncopy-files: [{path: local.env, required: true}]\ncleanup: {ttl-days: 2, interval-seconds: 60}\n')
    frozen=api().load_config(tmp_path)
    path.write_text('invalid: true\n')
    assert [(x.path,x.required) for x in frozen.copy_files] == [('local.env',True)]
    assert (frozen.ttl_days,frozen.interval_seconds) == (2,60)
    with pytest.raises(AttributeError): frozen.ttl_days=5


@pytest.mark.parametrize('content', [
    'version: 2\n', 'version: true\n', 'version: 1\nversion: 1\n', 'unknown: 1\n',
    '!!python/object:object {}', 'cleanup: {ttl-days: true}\n',
    'cleanup: {interval-seconds: 0}\n', 'cleanup: {extra: 1}\n',
    'copy-files: [{path: ../secret}]\n', 'copy-files: [{path: /outside}]\n',
    'copy-files: [{path: a//b}]\n', 'copy-files: [{path: a, required: 1}]\n',
    'link-directories: [node_modules]\n', 'copy-ignored-files: [{pattern: ../*}]\n',
    'regenerable-paths: ["**"]\n', 'regenerable-paths: ["src/**"]\n',
    'hooks-path: ../hooks\n', 'copy-files: null\n',
    'copy-files: [{path: .mewcode/sessions/private.jsonl}]\n',
    'copy-files: [{path: .mewcode/permissions.local.yaml}]\n',
])
def test_invalid_config_fails_as_a_whole(tmp_path,content):
    config_file(tmp_path,content)
    with pytest.raises(ToolError) as error: api().load_config(tmp_path)
    assert error.value.code == 'worktree_config_error'


def test_config_rejects_ancestor_symlink(tmp_path):
    outside=tmp_path/'outside'
    outside.mkdir()
    (outside/'worktrees.yaml').write_text('version: 1\n')
    (tmp_path/'.mewcode').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ToolError): api().load_config(tmp_path)


@pytest.mark.parametrize('pattern',['**/*','**/*.py','*/**/*'])
def test_regenerable_patterns_cannot_classify_arbitrary_project_files(tmp_path,pattern):
    from mewcode.worktrees.config import load_config
    from mewcode.tools.base import ToolError
    (tmp_path/'.mewcode').mkdir()
    (tmp_path/'.mewcode/worktrees.yaml').write_text(f'version: 1\nregenerable-paths: ["{pattern}"]\n')
    with pytest.raises(ToolError): load_config(tmp_path)


def test_regenerable_default_rules_can_be_written_explicitly(tmp_path):
    from mewcode.worktrees.config import load_config,WorktreeConfig
    import yaml
    (tmp_path/'.mewcode').mkdir()
    (tmp_path/'.mewcode/worktrees.yaml').write_text(yaml.safe_dump({'version':1,'regenerable-paths':list(WorktreeConfig().regenerable_paths)}))
    assert load_config(tmp_path).regenerable_paths==WorktreeConfig().regenerable_paths


@pytest.mark.parametrize('pattern',['outputs/*','outputs/?*','outputs/[*?]*'])
def test_regenerable_patterns_need_specific_file_limit(tmp_path,pattern):
    from mewcode.worktrees.config import load_config
    from mewcode.tools.base import ToolError
    import yaml
    (tmp_path/'.mewcode').mkdir()
    (tmp_path/'.mewcode/worktrees.yaml').write_text(yaml.safe_dump({'regenerable-paths':[pattern]}))
    with pytest.raises(ToolError): load_config(tmp_path)
