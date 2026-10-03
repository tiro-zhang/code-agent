"""精确存续授权、最新永久记录和失败降级测试。"""

import importlib
from pathlib import Path

import pytest


def make_store(root):
    config_module = importlib.import_module("mewcode.permissions.config")
    grant_module = importlib.import_module("mewcode.permissions.grants")
    config = config_module.PermissionConfig(root, user_path=root / "user.yaml")
    return config, grant_module.GrantStore(config)


def test_session_grants_are_exact_and_do_not_cross_tools(tmp_path):
    config, store = make_store(tmp_path)
    assert store.remember("execute_command", "command", ["git status"], "session") is None
    snapshot = config.load()
    assert store.has("execute_command", "command", "git status", snapshot)
    assert not store.has("execute_command", "command", "git status && git push", snapshot)
    assert not store.has("read_file", "path", str(tmp_path / "a"), snapshot)
    _, other = make_store(tmp_path)
    assert not other.has("execute_command", "command", "git status", snapshot)


def test_search_paths_are_reusable_without_granting_new_candidates(tmp_path):
    config, store = make_store(tmp_path)
    a, b = str(tmp_path / "a.py"), str(tmp_path / "b.py")
    store.remember("search_code", "path", [a], "session")
    assert store.has("search_code", "path", a, config.load())
    assert not store.has("search_code", "path", b, config.load())
    assert not store.has("glob_files", "path", a, config.load())


def test_changed_link_target_does_not_reuse_original_path(tmp_path):
    config, store = make_store(tmp_path)
    a, b, link = tmp_path / "a", tmp_path / "b", tmp_path / "alias"
    a.touch()
    b.touch()
    link.symlink_to(a)
    store.remember("read_file", "path", [str(link.resolve())], "session")
    link.unlink()
    link.symlink_to(b)
    assert not store.has("read_file", "path", str(link.resolve()), config.load())


def test_permanent_grants_reload_and_revoke_without_creating_allow_rules(tmp_path):
    config, store = make_store(tmp_path)
    assert store.remember("execute_command", "command", ["git status"], "permanent") is None
    fresh_config, fresh_store = make_store(tmp_path)
    assert fresh_store.has("execute_command", "command", "git status", fresh_config.load())
    assert fresh_config.load().rules == ()
    store.revoke("permanent")
    assert not fresh_store.has("execute_command", "command", "git status", fresh_config.load())


def test_new_ask_does_not_revoke_approval_but_rule_engine_keeps_deny(tmp_path):
    config, store = make_store(tmp_path)
    store.remember("execute_command", "command", ["git status"], "permanent")
    local = config.paths[1]
    local.parent.mkdir(exist_ok=True)
    local.write_text('rules:\n- {effect: ask, rule: "Bash(git status)", match: exact}\n')
    assert store.has("execute_command", "command", "git status", config.load())
    local.write_text('rules:\n- {effect: deny, rule: "Bash(git status)", match: exact}\n')
    rules = importlib.import_module("mewcode.permissions.rules")
    assert rules.merge_rules(config.load().rules, "execute_command", ("git status",)).effect == "deny"


def test_failed_permanent_save_does_not_create_session_or_permanent_grant(tmp_path, monkeypatch):
    config, store = make_store(tmp_path)
    config_module = importlib.import_module("mewcode.permissions.config")
    def unavailable(_):
        raise config_module.PermissionConfigError("存储失败")
    monkeypatch.setattr(config, "save_approvals", unavailable)
    message = store.remember("execute_command", "command", ["git status"], "permanent")
    assert message and "失败" in message
    assert not store.has("execute_command", "command", "git status", config.load())
    assert len(store.session) == 0


def test_session_revoke_keeps_permanent_records(tmp_path):
    config, store = make_store(tmp_path)
    store.remember("execute_command", "command", ["git status"], "session")
    store.remember("execute_command", "command", ["git diff"], "permanent")
    store.revoke("session")
    assert not store.has("execute_command", "command", "git status", config.load())
    assert store.has("execute_command", "command", "git diff", config.load())


def test_grants_do_not_cross_project_roots(tmp_path):
    config, store = make_store(tmp_path)
    store.remember("execute_command", "command", ["git status"], "permanent")
    _, other = make_store(tmp_path / "other")
    assert not other.has("execute_command", "command", "git status", config.load())


@pytest.mark.parametrize("tool,kind,value", [("execute_command", "path", "/a"), ("read_file", "command", "git status"), ("read_file", "path", "/outside"), ("unknown", "command", "x")])
def test_invalid_remember_does_not_create_grant(tmp_path, tool, kind, value):
    _, store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.remember(tool, kind, [value], "session")
    assert len(store.session) == 0
