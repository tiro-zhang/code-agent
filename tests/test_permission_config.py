"""权限 YAML 的严格读取与可信批准存储测试。"""

import importlib
import json
from pathlib import Path

import pytest


def config_for(root):
    module = importlib.import_module("mewcode.permissions.config")
    return module.PermissionConfig(root, user_path=root / "user.yaml")


def write_yaml(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def approval(root, value="git status", **overrides):
    model = importlib.import_module("mewcode.permissions.models")
    fields = dict(id="批准一", root=str(root.resolve()), tool="execute_command", kind="command", value=value)
    fields.update(overrides)
    return model.Approval(**fields)


def test_missing_and_empty_sources_are_empty_snapshot(tmp_path):
    config = config_for(tmp_path)
    initial = config.load()
    assert initial.rules == ()
    assert initial.approvals == ()
    write_yaml(config.paths[1], "# 无规则\n")
    empty = config.load()
    assert empty.rules == ()
    assert empty.approvals == ()


def test_sources_default_version_and_bash_alias(tmp_path):
    config = config_for(tmp_path)
    for path, effect in zip(config.paths, ("allow", "deny", "ask")):
        write_yaml(path, f'rules:\n  - effect: {effect}\n    rule: "Bash(git *)"\n    match: glob\n')
    snapshot = config.load()
    assert [r.effect for r in snapshot.rules] == ["allow", "deny", "ask"]
    assert all(r.tool == "execute_command" and r.pattern == "git *" for r in snapshot.rules)
    assert [r.source for r in snapshot.rules] == [str(p) for p in config.paths]
    write_yaml(config.paths[2], 'rules:\n  - effect: deny\n    rule: "Bash(git push)"\n    match: exact\n')
    assert config.load().signature != snapshot.signature


@pytest.mark.parametrize("text", [
    "version: 2", "version: true", "version: 1\nversion: 1", "mode: bypass", "rules: null", "rules: {}", "[]", "null", "~",
    'rules:\n- {effect: allow, rule: "Bash(git *)"}',
    'rules:\n- {effect: maybe, rule: "Bash(git *)", match: glob}',
    'rules:\n- {effect: allow, rule: "unknown(x)", match: exact}',
    'rules:\n- {effect: allow, rule: "Bash()", match: exact}',
    'rules:\n- {effect: allow, rule: "Bash(x", match: exact}',
    'rules:\n- {effect: allow, rule: "Bash(x)", match: regex}',
    'rules:\n- {effect: deny, effect: allow, rule: "Bash(x)", match: exact}',
    'rules:\n- {effect: deny, rule: "Bash(x)", match: exact, extra: 1}',
    "!!python/object/apply:os.system ['echo forbidden']",
])
def test_invalid_config_fails_closed_with_source(tmp_path, text):
    config = config_for(tmp_path)
    module = importlib.import_module("mewcode.permissions.config")
    write_yaml(config.paths[1], text)
    with pytest.raises(module.PermissionConfigError) as error:
        config.load()
    assert str(config.paths[1]) in str(error.value)


@pytest.mark.parametrize("source", [0, 1])
def test_shared_sources_reject_even_empty_approvals(tmp_path, source):
    config = config_for(tmp_path)
    write_yaml(config.paths[source], "approvals: []")
    module = importlib.import_module("mewcode.permissions.config")
    with pytest.raises(module.PermissionConfigError):
        config.load()


def test_rule_expression_preserves_inner_parentheses_and_literal_star(tmp_path):
    config = config_for(tmp_path)
    write_yaml(config.paths[0], 'rules:\n- {effect: ask, rule: "Bash(printf \'(x)*\')", match: exact}')
    rule = config.load().rules[0]
    assert rule.pattern == "printf '(x)*'"
    assert rule.match == "exact"


@pytest.mark.parametrize("mutation", [
    {"id": ""}, {"root": "relative"}, {"tool": "Bash"},
    {"scope": {"kind": "glob", "value": "*"}},
    {"scope": {"kind": "path", "value": "/outside"}},
    {"scope": {"kind": "command", "value": ""}},
    {"scope": {"kind": "command", "value": "git status", "extra": 1}},
    {"extra": 1},
])
def test_invalid_approval_is_not_imported(tmp_path, mutation):
    config = config_for(tmp_path)
    item = approval(tmp_path).to_dict()
    item.update(mutation)
    write_yaml(config.paths[2], json.dumps({"approvals": [item]}))
    module = importlib.import_module("mewcode.permissions.config")
    with pytest.raises(module.PermissionConfigError):
        config.load()


def test_nested_duplicate_scope_and_duplicate_ids_rejected(tmp_path):
    config = config_for(tmp_path)
    module = importlib.import_module("mewcode.permissions.config")
    write_yaml(config.paths[2], f'approvals:\n- id: a\n  root: {tmp_path}\n  tool: execute_command\n  scope: {{kind: command, value: first, value: second}}')
    with pytest.raises(module.PermissionConfigError):
        config.load()
    write_yaml(config.paths[2], json.dumps({"approvals": [approval(tmp_path).to_dict()] * 2}))
    with pytest.raises(module.PermissionConfigError):
        config.load()


def test_save_merges_latest_rules_and_deduplicates_scope(tmp_path):
    config = config_for(tmp_path)
    write_yaml(config.paths[2], 'rules:\n- {effect: deny, rule: "Bash(git push)", match: exact}\n')
    config.save_approvals([approval(tmp_path)])
    config.save_approvals([approval(tmp_path, id="重复范围"), approval(tmp_path, "git diff", id="批准二")])
    snapshot = config.load()
    assert len(snapshot.approvals) == 2
    assert {a.value for a in snapshot.approvals} == {"git status", "git diff"}
    assert snapshot.rules[0].effect == "deny"
    fresh = config_for(tmp_path).load()
    assert fresh.approvals == snapshot.approvals


def test_revoke_preserves_rules_and_other_project_approvals(tmp_path):
    config = config_for(tmp_path)
    foreign = tmp_path / "other"
    write_yaml(config.paths[2], json.dumps({"rules": [{"effect": "ask", "rule": "Bash(*)", "match": "glob"}], "approvals": [approval(tmp_path).to_dict(), approval(foreign, id="其他项目").to_dict()]}))
    config.revoke_permanent()
    snapshot = config.load()
    assert len(snapshot.rules) == 1
    assert [a.root for a in snapshot.approvals] == [str(foreign)]


def test_save_detects_concurrent_rule_change_without_overwrite(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    module = importlib.import_module("mewcode.permissions.config")
    original = module.os.fsync
    changed = False
    def competing_edit(fd):
        nonlocal changed
        original(fd)
        if not changed:
            changed = True
            write_yaml(config.paths[2], 'rules:\n- {effect: deny, rule: "Bash(*)", match: glob}\n')
    monkeypatch.setattr(module.os, "fsync", competing_edit)
    with pytest.raises(module.PermissionConfigError):
        config.save_approvals([approval(tmp_path)])
    snapshot = config.load()
    assert snapshot.rules[0].effect == "deny"
    assert snapshot.approvals == ()


def test_protected_paths_resolve_links_and_root_alias(tmp_path):
    real = tmp_path / "project"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    config = config_for(alias)
    external = tmp_path / "user-real.yaml"
    external.write_text("")
    config.paths[0].symlink_to(external)
    assert config.root == real
    assert config.protected_paths() == tuple(p.resolve() for p in config.paths)


def test_save_deduplicates_existing_scopes_without_expanding_them(tmp_path):
    config = config_for(tmp_path)
    write_yaml(config.paths[2], json.dumps({"approvals": [approval(tmp_path).to_dict(), approval(tmp_path, id="同范围二").to_dict()]}))
    config.save_approvals([])
    assert [a.value for a in config.load().approvals] == ["git status"]


def test_cleanup_error_keeps_failed_save_as_config_error(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    module = importlib.import_module("mewcode.permissions.config")
    def failed_replace(*_):
        raise OSError("文件系统不可写")
    def failed_cleanup(*_, **__):
        raise OSError("临时文件清理失败")
    monkeypatch.setattr(module.os, "replace", failed_replace)
    monkeypatch.setattr(Path, "unlink", failed_cleanup)
    with pytest.raises(module.PermissionConfigError):
        config.save_approvals([approval(tmp_path)])
    assert config.load().approvals == ()
