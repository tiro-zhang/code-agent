"""定义、覆盖快照与包资源的实际边界。"""

from dataclasses import FrozenInstanceError
from pathlib import Path
import os

import pytest

from mewcode.skills.catalog import discover_skills
from mewcode.skills.loader import parse_skill, read_resource
from mewcode.tools.base import ToolError


def entry(path, name="sample", extra="", body="目标：{{args}}；目录：{{skill_dir}}"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: 一句说明\nmode: shared\n{extra}---\n{body}", encoding="utf-8")
    return path


def test_literal_arguments_and_snapshot(tmp_path):
    path = entry(tmp_path / "sample.md")
    skill = parse_skill(path, layer="project")
    path.write_text("中途改写")
    assert skill.render('a  "b" {{skill_dir}}') == f'目标：a  "b" {{{{skill_dir}}}}；目录：{tmp_path}'
    assert skill.allowed_tools is None
    with pytest.raises(FrozenInstanceError):
        skill.name = "changed"


@pytest.mark.parametrize("extra", [
    "unknown: x\n", "name: duplicate\n", "history: true\n", "history: 0\n",
    "model: other\n", "allowed-tools: read_file\n", "allowed-tools: [true]\n",
    "description: [bad]\n", "unsafe: !!python/object:object {}\n",
])
def test_bad_metadata_rejected(tmp_path, extra):
    with pytest.raises(ToolError):
        parse_skill(entry(tmp_path / "bad.md", extra=extra), layer="project")


def test_isolated_history_and_bounds(tmp_path):
    path = entry(tmp_path / "sample.md", extra="allowed-tools: []\n")
    path.write_text(path.read_text().replace("mode: shared", "mode: isolated\nhistory: all\nmodel: small"))
    skill = parse_skill(path, layer="user")
    assert (skill.mode, skill.history, skill.model, skill.allowed_tools) == ("isolated", "all", "small", frozenset())
    with pytest.raises(ToolError, match="64") as error:
        skill.render("中" * 65536)
    assert error.value.code == "skill_content_too_large"
    path.write_text("x" * 65537)
    with pytest.raises(ToolError):
        parse_skill(path, layer="user")


def test_cover_fallback_and_nonrecursive_discovery(tmp_path):
    project, user, builtin = [tmp_path / x for x in ("p", "u", "b")]
    entry(builtin / "r.md", "review", body="builtin")
    entry(user / "skills" / "r.md", "review", body="user")
    broken = entry(project / ".mewcode" / "skills" / "r.md", "review", extra="bad: yes\n")
    entry(project / ".mewcode" / "skills" / "pkg" / "SKILL.md", "package")
    entry(project / ".mewcode" / "skills" / "pkg" / "references" / "r.md", "hidden")
    catalog = discover_skills(project, user_root=user, builtin_root=builtin)
    assert {s.name for s in catalog.skills} == {"review", "package"}
    assert catalog.get("review").render() == "user"
    assert any(str(broken) in warning for warning in catalog.warnings)
    entry(broken, "review", body="project")
    newer = discover_skills(project, user_root=user, builtin_root=builtin)
    assert newer.get("review").render() == "project"
    assert catalog.get("review").render() == "user"
    assert "project" not in newer.index_text()


def test_duplicate_reserved_and_unknown_are_global_errors(tmp_path):
    root = tmp_path / ".mewcode" / "skills"
    entry(root / "a.md", extra="allowed-tools: [missing]\n")
    catalog = discover_skills(tmp_path, user_root=tmp_path / "u", builtin_root=tmp_path / "b")
    with pytest.raises(ValueError, match="missing"):
        catalog.validate_tools({"read_file"})
    with pytest.raises(ValueError, match="sample"):
        catalog.validate_names({"sample"})
    entry(root / "other" / "SKILL.md")
    with pytest.raises(ValueError, match="sample"):
        discover_skills(tmp_path, user_root=tmp_path / "u", builtin_root=tmp_path / "b")


def test_resources_are_bounded_package_reads(tmp_path):
    package = tmp_path / "outside" / "pkg"
    skill = parse_skill(entry(package / "SKILL.md"), layer="user", package=True)
    (package / "references").mkdir()
    (package / "references" / "guide.md").write_text("参考正文")
    assert read_resource(skill, "references/guide.md") == "参考正文"
    secret = tmp_path / "secret"
    secret.write_text("秘密")
    (package / "link").symlink_to(secret)
    (package / "dirlink").symlink_to(package / "references", target_is_directory=True)
    (package / "binary").write_bytes(b"\x00hello")
    (package / "big").write_text("x" * 65537)
    os.mkfifo(package / "pipe")
    for name in ("../../secret", str(secret), "link", "dirlink/guide.md", "binary", "big", "pipe"):
        with pytest.raises(ToolError):
            read_resource(skill, name)
    single = parse_skill(entry(tmp_path / "single.md"), layer="project")
    with pytest.raises(ToolError):
        read_resource(single, "secret")


def test_symlink_entry_and_directory_are_not_discovered(tmp_path):
    root = tmp_path / ".mewcode" / "skills"
    target = entry(tmp_path / "outside" / "SKILL.md")
    root.mkdir(parents=True)
    (root / "link.md").symlink_to(target)
    (root / "directory").symlink_to(target.parent, target_is_directory=True)
    catalog = discover_skills(tmp_path, user_root=tmp_path / "u", builtin_root=tmp_path / "b")
    assert not catalog.skills
    assert len(catalog.warnings) == 2
