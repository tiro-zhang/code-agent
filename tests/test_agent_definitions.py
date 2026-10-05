"""角色入口、覆盖及冻结快照的实际行为。"""

import importlib
from importlib.util import find_spec
import os

import pytest

from mewcode.tools.base import ToolError


def api():
    assert find_spec("mewcode.agents") is not None, "缺少独立角色加载模块"
    return importlib.import_module("mewcode.agents.definitions")


def entry(path, name="sample", extra="", body="固定角色正文"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: 一句说明\n{extra}---\n{body}", encoding="utf-8")
    return path


def test_role_is_frozen_and_defaults_do_not_expand_tools(tmp_path):
    module = api()
    path = entry(tmp_path / "sample.md")
    role = module.parse_role(path, layer="project")
    path.write_text("已改写")
    assert role.body == "固定角色正文"
    assert role.model == role.permission_mode == "inherit"
    assert role.max_iterations == 20
    assert role.tools({"read_file", "load_skill"}) == frozenset({"read_file", "load_skill"})
    with pytest.raises(AttributeError):
        role.body = "改写快照"


@pytest.mark.parametrize("extra", [
    "unknown: x\n", "name: duplicate\n", "max-iterations: true\n",
    "max-iterations: 0\n", "max-iterations: 1.5\n", "permission-mode: admin\n",
    "model: arbitrary\n", "allowed-tools: read_file\n", "disallowed-tools: [true]\n",
    "allowed-tools: [' read_file']\n", "description: [bad]\n",
    "unsafe: !!python/object:object {}\n",
])
def test_bad_metadata_never_loads_partially(tmp_path, extra):
    module = api()
    with pytest.raises(ToolError) as error:
        module.parse_role(entry(tmp_path / "bad.md", extra=extra), layer="project")
    assert error.value.code == "invalid_agent_definition"


def test_empty_allowlist_and_deny_override_system_tools(tmp_path):
    module = api()
    empty = module.parse_role(entry(tmp_path / "empty.md", extra="allowed-tools: []\n"), layer="project")
    deny = module.parse_role(entry(tmp_path / "deny.md", extra="allowed-tools: [read_file, load_skill]\ndisallowed-tools: [load_skill]\n"), layer="project")
    assert not empty.tools({"read_file", "load_skill"})
    assert deny.tools({"read_file", "load_skill", "write_file"}) == frozenset({"read_file"})


@pytest.mark.parametrize("body", ["", " \n", "x" * 65537, "\x00"], ids=["empty", "blank", "oversize", "nul"])
def test_bounded_nonempty_utf8_body(tmp_path, body):
    module = api()
    with pytest.raises(ToolError):
        module.parse_role(entry(tmp_path / "bad.md", body=body), layer="user")


def test_four_layers_whole_override_and_invalid_fallback(tmp_path):
    module = api()
    project, user, builtin, plugin = [tmp_path / x for x in ("p", "u", "b", "plugin")]
    entry(plugin / "r.md", body="插件")
    entry(builtin / "r.md", extra="allowed-tools: []\n", body="内置")
    entry(user / "agents" / "r.md", body="用户")
    broken = entry(project / ".mewcode" / "agents" / "r.md", extra="bad: yes\n", body="项目")
    entry(project / ".mewcode" / "agents" / "nested" / "hidden.md", name="hidden")
    catalog = module.discover_roles(project, user_root=user, builtin_root=builtin, plugin_dirs=(plugin,))
    assert len(catalog.roles) == 1
    role = catalog.get("sample")
    assert (role.body, role.layer, role.allowed_tools) == ("用户", "user", None)
    assert any(str(broken) in warning for warning in catalog.warnings)
    entry(broken, body="项目")
    newer = module.discover_roles(project, user_root=user, builtin_root=builtin, plugin_dirs=(plugin,))
    assert newer.get("sample").body == "项目"
    assert catalog.get("sample").body == "用户"
    assert newer.index_text() == "- sample: 一句说明"


def test_duplicate_and_unknown_tools_reject_candidate(tmp_path):
    module = api()
    directory = tmp_path / ".mewcode" / "agents"
    entry(directory / "a.md", extra="disallowed-tools: [read_flie]\n")
    options = dict(user_root=tmp_path / "u", builtin_root=tmp_path / "b")
    catalog = module.discover_roles(tmp_path, **options)
    with pytest.raises(ValueError, match="read_flie"):
        catalog.validate_tools({"read_file"})
    entry(directory / "b.md")
    with pytest.raises(ValueError, match="sample"):
        module.discover_roles(tmp_path, **options)


def test_symlinks_and_special_files_are_skipped(tmp_path):
    module = api()
    root = tmp_path / ".mewcode" / "agents"
    root.mkdir(parents=True)
    target = entry(tmp_path / "outside.md")
    (root / "link.md").symlink_to(target)
    os.mkfifo(root / "pipe.md")
    catalog = module.discover_roles(tmp_path, user_root=tmp_path / "u", builtin_root=tmp_path / "b")
    assert not catalog.roles
    assert len(catalog.warnings) == 2


def test_builtin_roles_are_discoverable_without_project_files(tmp_path):
    module = api()
    catalog = module.discover_roles(tmp_path, user_root=tmp_path / "u")
    assert {role.name for role in catalog.roles} == {"explore", "general"}
    assert catalog.get("explore").tools({"read_file", "glob_files", "search_code", "write_file"}) == frozenset({"read_file", "glob_files", "search_code"})
    assert catalog.get("general").allowed_tools is None


def test_refresh_failure_keeps_previous_complete_catalog(tmp_path):
    module = api()
    path = entry(tmp_path / ".mewcode" / "agents" / "r.md", body="旧定义")
    store = module.RoleStore(tmp_path, {"read_file"}, user_root=tmp_path / "u", builtin_root=tmp_path / "b")
    frozen = store.catalog.get("sample")
    entry(path, extra="allowed-tools: [missing]\n", body="坏新定义")
    with pytest.raises(ValueError, match="missing"):
        store.refresh()
    assert store.catalog.get("sample").body == "旧定义"
    entry(path, body="合法新定义")
    store.refresh()
    assert store.catalog.get("sample").body == "合法新定义"
    assert frozen.body == "旧定义"


def test_role_worktree_isolation_is_frozen(tmp_path):
    module=api()
    path=entry(tmp_path/'isolated.md', extra='isolation: worktree\n')
    role=module.parse_role(path,layer='project')
    assert role.isolation == 'worktree'
    entry(path)
    assert module.parse_role(path,layer='project').isolation is None
    assert role.isolation == 'worktree'


@pytest.mark.parametrize('value',['null','false','true','shared','[]','{}','1','""'])
def test_invalid_isolation_does_not_fall_back(tmp_path,value):
    with pytest.raises(ToolError) as caught:
        api().parse_role(entry(tmp_path/'bad.md',extra=f'isolation: {value}\n'),layer='project')
    assert caught.value.code == 'invalid_agent_definition'
