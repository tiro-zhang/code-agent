"""规则优先级、完整匹配与权限模式矩阵测试。"""

import importlib
import itertools

import pytest


def modules():
    return importlib.import_module("mewcode.permissions.models"), importlib.import_module("mewcode.permissions.rules")


@pytest.mark.parametrize("order", list(itertools.permutations(("allow", "ask", "deny"))))
def test_priority_ignores_rule_order_and_sources(order):
    models, rules = modules()
    entries = tuple(models.Rule(e, "execute_command", "git *", "glob", str(i)) for i, e in enumerate(order))
    result = rules.merge_rules(entries, "execute_command", ("git status",))
    assert result.effect == "deny"
    assert len(result.rules) == 3


def test_exact_allow_cannot_override_subcommand_ask_or_deny():
    models, rules = modules()
    entries = (models.Rule("allow", "execute_command", "git status && git push", "exact"), models.Rule("ask", "execute_command", "git push", "exact"))
    assert rules.merge_rules(entries, "execute_command", ("git status && git push", "git status", "git push")).effect == "ask"
    deny = models.Rule("deny", "execute_command", "git push", "exact")
    assert rules.merge_rules(entries + (deny,), "execute_command", ("git status && git push", "git push")).effect == "deny"


def test_subcommand_allow_and_disabled_glob_do_not_allow_complex_command():
    models, rules = modules()
    entries = (models.Rule("allow", "execute_command", "git *", "glob"), models.Rule("allow", "execute_command", "git status", "exact"))
    assert rules.merge_rules(entries, "execute_command", ("git status && printf done", "git status"), glob_allow=False).effect is None
    assert rules.merge_rules(entries, "execute_command", ("git status",), glob_allow=True).effect == "allow"


def test_allow_uses_original_command_not_static_subject():
    models, rules = modules()
    entry = models.Rule("allow", "execute_command", "git status", "exact")
    assert rules.merge_rules((entry,), "execute_command", ("git status",), allow_subject="git 'status'").effect is None


@pytest.mark.parametrize("pattern,subject,want", [
    ("src/*.py", "src/a.py", "deny"),
    ("src/*.py", "src/pkg/a.py", None),
    ("src/**/*.py", "src/a.py", "deny"),
    ("src/**/*.py", "src/pkg/a.py", "deny"),
    ("secrets/**", "secrets/a/b", "deny"),
    ("**/a.py", "a.py", "deny"),
    ("src/?.[pc]y", "src/a.py", "deny"),
    ("src/*", "other/src/a.py", None),
    ("src/*.py", "src/A.PY", None),
])
def test_path_glob_obeys_segments_and_case(pattern, subject, want):
    models, rules = modules()
    entry = models.Rule("deny", "search_code", pattern, "glob")
    assert rules.merge_rules((entry,), "search_code", (subject,)).effect == want


def test_exact_star_is_literal_and_tool_scope_does_not_expand():
    models, rules = modules()
    entry = models.Rule("deny", "read_file", "src/*", "exact")
    assert rules.merge_rules((entry,), "read_file", ("src/a",)).effect is None
    assert rules.merge_rules((entry,), "read_file", ("src/*",)).effect == "deny"
    assert rules.merge_rules((entry,), "search_code", ("src/*",)).effect is None


@pytest.mark.parametrize("mode,effect,want", [
    ("strict", "deny", "deny"), ("strict", "ask", "ask"), ("strict", "allow", "ask"), ("strict", None, "ask"),
    ("default", "deny", "deny"), ("default", "ask", "ask"), ("default", "allow", "allow"), ("default", None, "ask"),
    ("bypass", "deny", "deny"), ("bypass", "ask", "allow"), ("bypass", "allow", "allow"), ("bypass", None, "allow"),
])
def test_permission_mode_matrix(mode, effect, want):
    _, rules = modules()
    assert rules.apply_mode(effect, mode) == want


def test_invalid_mode_is_not_silently_bypass():
    _, rules = modules()
    with pytest.raises(ValueError):
        rules.apply_mode(None, "unknown")
