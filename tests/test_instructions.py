"""手写指令的层级、引用和安全读取行为测试。"""

import importlib
import os
from pathlib import Path

import pytest


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load(root: Path, user_root: Path):
    return importlib.import_module("mewcode.instructions").load_instructions(root, user_root)


@pytest.fixture
def roots(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    return project, tmp_path / "user" / ".mewcode"


def test_missing_entries_are_quiet_and_do_not_create_templates(roots):
    project, user_root = roots
    result = load(project, user_root)
    assert result.text == ""
    assert result.warnings == ()
    assert tuple(project.iterdir()) == ()
    assert not user_root.exists()


def test_empty_entries_do_not_add_placeholder_sections(roots):
    project, user_root = roots
    write_text(project / "MEWCODE.md", " \n\t\n")
    write_text(project / ".mewcode" / "MEWCODE.md", "")
    result = load(project, user_root)
    assert result.text == ""
    assert result.warnings == ()


def test_three_layers_keep_priority_and_trusted_sources(roots):
    project, user_root = roots
    entries = (project / "MEWCODE.md", project / ".mewcode" / "MEWCODE.md", user_root / "MEWCODE.md")
    for path, content in zip(entries, ("项目采用四空格", "项目默认两空格", "用户默认制表符")):
        write_text(path, content)
    result = load(project, user_root)
    assert result.text.index("项目采用四空格") < result.text.index("项目默认两空格") < result.text.index("用户默认制表符")
    for path in entries:
        assert str(path.resolve()) in result.text
    assert "项目根" in result.text and "项目配置" in result.text and "用户" in result.text
    assert "优先" in result.text and "系统" in result.text and "权限" in result.text and "模式" in result.text
    assert result.warnings == ()


def test_default_user_directory_is_mewcode_under_home(roots, monkeypatch):
    project, user_root = roots
    write_text(user_root / "MEWCODE.md", "跨项目默认偏好")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_root.parent))
    module = importlib.import_module("mewcode.instructions")
    assert "跨项目默认偏好" in module.load_instructions(project).text


def test_relative_includes_expand_in_place_with_spaces_and_current_directory(roots):
    project, user_root = roots
    write_text(project / "MEWCODE.md", "最前规则\n@include guides/team rules.md\n最后规则\n")
    write_text(project / "guides" / "team rules.md", "团队规范\n@include ../shared.md\n")
    write_text(project / "shared.md", "共享规范")
    result = load(project, user_root)
    assert result.text.index("最前规则") < result.text.index("团队规范") < result.text.index("共享规范") < result.text.index("最后规则")
    assert str(project / "guides" / "team rules.md") in result.text
    assert str(project / "shared.md") in result.text
    assert result.warnings == ()


@pytest.mark.parametrize("opening,closing", [("```markdown", "```"), ("~~~~", "~~~~~"), ("````", "````")])
def test_fenced_include_examples_are_preserved(roots, opening, closing):
    project, user_root = roots
    example = f"{opening}\n@include missing-example.md\n{closing}\n"
    write_text(project / "MEWCODE.md", example + "@include actual.md\n")
    write_text(project / "actual.md", "有效引用内容")
    result = load(project, user_root)
    assert example in result.text
    assert "有效引用内容" in result.text
    assert result.warnings == ()


def test_shorter_or_other_fences_do_not_end_code_block(roots):
    project, user_root = roots
    content = "````\n```\n@include absent.md\n~~~\n@include also-absent.md\n````\n"
    write_text(project / "MEWCODE.md", content)
    result = load(project, user_root)
    assert content in result.text
    assert result.warnings == ()


def test_include_must_occupy_an_independent_line(roots):
    project, user_root = roots
    content = "文字 @include missing.md\n@include-not-a-directive missing.md\n"
    write_text(project / "MEWCODE.md", content)
    result = load(project, user_root)
    assert content in result.text
    assert result.warnings == ()


def test_include_uses_entire_remaining_line_as_path(roots):
    project, user_root = roots
    write_text(project / "MEWCODE.md", "@include team # rules.md\n")
    write_text(project / "team # rules.md", "路径含井号")
    result = load(project, user_root)
    assert "路径含井号" in result.text
    assert result.warnings == ()


def test_cycle_and_duplicates_share_realpath_across_layers(roots):
    project, user_root = roots
    write_text(project / "MEWCODE.md", "根规则\n@include shared.md\n@include shared-alias.md\n")
    write_text(project / "shared.md", "共享规则只加载一次\n@include MEWCODE.md\n")
    (project / "shared-alias.md").symlink_to(project / "shared.md")
    write_text(project / ".mewcode" / "MEWCODE.md", "隐藏目录规则\n@include ../shared.md\n")
    result = load(project, user_root)
    assert result.text.count("共享规则只加载一次") == 1
    assert result.text.count("根规则") == 1
    assert result.text.index("共享规则只加载一次") < result.text.index("隐藏目录规则")
    assert any("MEWCODE.md" in warning and "环路" in warning for warning in result.warnings)


def test_an_entry_already_included_by_higher_layer_is_not_loaded_again(roots):
    project, user_root = roots
    write_text(project / "MEWCODE.md", "@include .mewcode/MEWCODE.md\n")
    write_text(project / ".mewcode" / "MEWCODE.md", "只出现一次的项目规则")
    result = load(project, user_root)
    assert result.text.count("只出现一次的项目规则") == 1
    assert "项目根" in result.text


def test_depth_five_is_loaded_and_depth_six_is_skipped(roots):
    project, user_root = roots
    write_text(project / "MEWCODE.md", "@include depth-1.md\n")
    for depth in range(1, 7):
        write_text(project / f"depth-{depth}.md", f"深度{depth}的规则\n@include depth-{depth + 1}.md\n")
    result = load(project, user_root)
    assert "深度5的规则" in result.text
    assert "深度6的规则" not in result.text
    assert any("depth-6.md" in warning and "深度" in warning for warning in result.warnings)


@pytest.mark.parametrize("reference", ["../outside.md", "/absolute.md", "~/outside.md", "$HOME/outside.md", "${HOME}/outside.md"])
def test_forbidden_reference_is_diagnosed_and_valid_content_continues(roots, reference):
    project, user_root = roots
    write_text(project.parent / "outside.md", "不得读取的外部指令")
    write_text(project / "MEWCODE.md", f"@include {reference}\n@include valid.md\n")
    write_text(project / "valid.md", "合法指令仍加载")
    result = load(project, user_root)
    assert "不得读取的外部指令" not in result.text
    assert "合法指令仍加载" in result.text
    assert any("MEWCODE.md" in warning and reference in warning for warning in result.warnings)


@pytest.mark.parametrize("entry", [False, True])
def test_project_symlinks_cannot_escape_boundary(roots, entry):
    project, user_root = roots
    outside = project.parent / "outside.md"
    write_text(outside, "外部符号链接内容")
    if entry:
        (project / "MEWCODE.md").symlink_to(outside)
    else:
        write_text(project / "MEWCODE.md", "@include linked.md\n")
        (project / "linked.md").symlink_to(outside)
    write_text(project / ".mewcode" / "MEWCODE.md", "安全的项目补充")
    result = load(project, user_root)
    assert "外部符号链接内容" not in result.text
    assert "安全的项目补充" in result.text
    assert any("项目" in warning and "边界" in warning for warning in result.warnings)


def test_user_reference_cannot_escape_its_separate_root(roots):
    project, user_root = roots
    outside = user_root.parent / "outside.md"
    write_text(outside, "外部用户指令")
    write_text(user_root / "MEWCODE.md", "@include ../outside.md\n@include linked.md\n@include valid.md\n")
    (user_root / "linked.md").symlink_to(outside)
    write_text(user_root / "valid.md", "用户范围内的指令")
    result = load(project, user_root)
    assert "外部用户指令" not in result.text
    assert "用户范围内的指令" in result.text
    assert len(result.warnings) == 2
    assert all("用户" in warning and "边界" in warning for warning in result.warnings)


def test_project_root_alias_still_uses_real_working_root(roots):
    project, user_root = roots
    alias = project.parent / "project-alias"
    alias.symlink_to(project, target_is_directory=True)
    write_text(project / "MEWCODE.md", "真实项目根规则")
    result = load(alias, user_root)
    assert "真实项目根规则" in result.text
    assert str(project / "MEWCODE.md") in result.text
    assert result.warnings == ()


def test_missing_invalid_utf8_and_directory_includes_are_diagnosed(roots):
    project, user_root = roots
    write_text(project / "MEWCODE.md", "@include missing.md\n@include broken.md\n@include directory\n@include valid.md\n")
    (project / "broken.md").write_bytes(b"\xff\xfe")
    (project / "directory").mkdir()
    write_text(project / "valid.md", "错误之后继续加载")
    result = load(project, user_root)
    assert "错误之后继续加载" in result.text
    assert len(result.warnings) == 3
    assert any("missing.md" in warning and "不存在" in warning for warning in result.warnings)
    assert any("broken.md" in warning and "UTF-8" in warning for warning in result.warnings)
    assert any("directory" in warning and "普通文件" in warning for warning in result.warnings)


def test_fifo_is_rejected_without_blocking(roots):
    project, user_root = roots
    os.mkfifo(project / "pipe")
    write_text(project / "MEWCODE.md", "@include pipe\n普通内容\n")
    result = load(project, user_root)
    assert "普通内容" in result.text
    assert any("pipe" in warning and "普通文件" in warning for warning in result.warnings)


def test_include_of_boundary_directory_reports_nonregular_file(roots):
    project, user_root = roots
    write_text(project / "MEWCODE.md", "@include .\n继续加载的规则\n")
    result = load(project, user_root)
    assert "继续加载的规则" in result.text
    assert len(result.warnings) == 1
    assert "普通文件" in result.warnings[0]


def test_symlink_loop_is_diagnosed_without_stopping_other_content(roots):
    project, user_root = roots
    write_text(project / "MEWCODE.md", "@include loop.md\n其余规则\n")
    (project / "loop.md").symlink_to(project / "loop.md")
    result = load(project, user_root)
    assert "其余规则" in result.text
    assert any("loop.md" in warning and "环路" in warning for warning in result.warnings)


def test_user_entry_symlink_cannot_escape_its_root(roots):
    project, user_root = roots
    outside = user_root.parent / "outside.md"
    write_text(outside, "用户入口的越界内容")
    user_root.mkdir()
    (user_root / "MEWCODE.md").symlink_to(outside)
    result = load(project, user_root)
    assert result.text == ""
    assert any("用户" in warning and "边界" in warning for warning in result.warnings)


def test_null_byte_in_reference_is_reported_safely(roots):
    project, user_root = roots
    write_text(project / "MEWCODE.md", "@include bad\x00.md\n其余规则\n")
    result = load(project, user_root)
    assert "其余规则" in result.text
    assert len(result.warnings) == 1
    assert "\x00" not in result.warnings[0]


def test_read_error_is_reported_without_disclosing_exception_details(roots, monkeypatch):
    project, user_root = roots
    write_text(project / "MEWCODE.md", "@include unreadable.md\n")
    write_text(project / "unreadable.md", "拒绝读取")
    write_text(user_root / "MEWCODE.md", "其他层可读取")
    module = importlib.import_module("mewcode.instructions")
    original = module.os.open
    def open_with_failure(path, *args, **kwargs):
        if Path(path).name == "unreadable.md":
            raise PermissionError("不得展示的内部异常详情")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(module.os, "open", open_with_failure)
    result = module.load_instructions(project, user_root)
    assert "其他层可读取" in result.text
    assert any("unreadable.md" in warning and "读取" in warning for warning in result.warnings)
    assert not any("内部异常详情" in warning for warning in result.warnings)


def test_total_read_budget_is_shared_and_oversized_file_is_skipped_whole(roots):
    project, user_root = roots
    first = "甲" * 40000
    skipped = "禁止截断的整份内容" + "乙" * 46000
    write_text(project / "MEWCODE.md", first)
    write_text(project / ".mewcode" / "MEWCODE.md", skipped)
    write_text(user_root / "MEWCODE.md", "较小用户指令仍可装入")
    result = load(project, user_root)
    assert first in result.text
    assert "禁止截断的整份内容" not in result.text
    assert "乙" not in result.text
    assert "较小用户指令仍可装入" in result.text
    assert any("256000" in warning and str(project / ".mewcode" / "MEWCODE.md") in warning for warning in result.warnings)


def test_exact_read_budget_is_allowed_and_next_nonempty_file_is_skipped(roots):
    project, user_root = roots
    text = "x" * 256000
    write_text(project / "MEWCODE.md", text)
    write_text(project / ".mewcode" / "MEWCODE.md", "下一层不得加载")
    result = load(project, user_root)
    assert text in result.text
    assert "下一层不得加载" not in result.text
    assert len(result.warnings) == 1


def test_include_directive_bytes_count_toward_shared_budget(roots):
    project, user_root = roots
    entry = "@include included.md\n"
    included = "z" * (256000 - len(entry.encode("utf-8")) + 1)
    write_text(project / "MEWCODE.md", entry)
    write_text(project / "included.md", included)
    result = load(project, user_root)
    assert included not in result.text
    assert result.text == ""
    assert any("included.md" in warning and "256000" in warning for warning in result.warnings)


def test_diagnostics_escape_control_characters_in_reference_names(roots):
    project, user_root = roots
    write_text(project / "MEWCODE.md", "@include missing\x1b[31m.md\n")
    result = load(project, user_root)
    assert len(result.warnings) == 1
    assert "\x1b" not in result.warnings[0]
    assert "missing" in result.warnings[0]
