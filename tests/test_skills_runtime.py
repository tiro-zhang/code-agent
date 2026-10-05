"""激活事务和不可扩大的工具交集。"""

import pytest

from mewcode.skills.catalog import discover_skills
from mewcode.skills.runtime import SkillRuntime
from mewcode.tools.base import ToolError
from test_skills_catalog import entry


def runtime(tmp_path, *, commit=None, parent_tools=None):
    root = tmp_path / ".mewcode" / "skills"
    entry(root / "a.md", "a", "allowed-tools: [read_file, edit_file]\n")
    entry(root / "b.md", "b", "allowed-tools: [read_file]\n")
    entry(root / "open.md", "open")
    entry(root / "empty.md", "empty", "allowed-tools: []\n")
    catalog = discover_skills(tmp_path, user_root=tmp_path / "u", builtin_root=tmp_path / "builtin")
    return SkillRuntime(catalog, commit=commit, parent_tools=parent_tools)


def test_intersection_reactivation_and_parent_cap(tmp_path):
    state = runtime(tmp_path, parent_tools={"read_file", "edit_file"})
    registered = {"read_file", "edit_file", "execute_command", "load_skill"}
    state.activate("a", "first")
    state.activate("b", "second")
    state.activate("a", "updated")
    assert [a.skill.name for a in state.active] == ["a", "b"]
    assert "updated" in state.active[0].body
    assert state.allowed_tools(registered) == {"read_file", "load_skill"}
    state.deactivate("b")
    state.activate("open")
    assert state.allowed_tools(registered) == {"read_file", "edit_file", "load_skill"}
    assert state.allowed_tools(registered, mode="plan") == {"read_file", "load_skill"}
    state.activate("empty")
    assert state.allowed_tools(registered) == {"load_skill"}


def test_commit_before_publish_and_oversize_unchanged(tmp_path):
    records = []
    state = runtime(tmp_path, commit=records.append)
    state.activate("a", "original")
    with pytest.raises(ToolError):
        state.activate("a", "x" * 65536)
    assert len(records) == 1
    assert state.active[0].args == "original"
    def fail(_):
        raise OSError("存档失败")
    state.commit = fail
    with pytest.raises(OSError):
        state.deactivate("a")
    assert len(state.active) == 1


def test_refresh_and_restore_use_current_definition(tmp_path):
    state = runtime(tmp_path)
    state.activate("a", "用户参数")
    state.activate("b")
    descriptors = state.descriptors()
    entry(tmp_path / ".mewcode" / "skills" / "a.md", "a", "allowed-tools: []\n", "新定义 {{args}}")
    (tmp_path / ".mewcode" / "skills" / "b.md").unlink()
    catalog = discover_skills(tmp_path, user_root=tmp_path / "u", builtin_root=tmp_path / "builtin")
    warnings = state.replace_catalog(catalog)
    assert state.active[0].body == "新定义 用户参数"
    assert len(state.active) == 1
    assert warnings
    restored = SkillRuntime(catalog)
    assert restored.restore(descriptors)
    assert restored.active[0].body == "新定义 用户参数"
    assert restored.allowed_tools({"read_file", "load_skill"}) == {"load_skill"}


def test_refresh_is_atomic_on_render_or_storage_failure(tmp_path):
    state = runtime(tmp_path)
    state.activate("a", "x" * 10000)
    previous = state.catalog
    entry(tmp_path / ".mewcode" / "skills" / "a.md", "a", body="{{args}}" * 10)
    candidate = discover_skills(tmp_path, user_root=tmp_path / "u", builtin_root=tmp_path / "builtin")
    with pytest.raises(ToolError):
        state.replace_catalog(candidate)
    assert state.catalog is previous
    assert state.active[0].skill is previous.get("a")
