"""MCP 配置合并、秘密边界与连接身份。"""

import sys
from pathlib import Path

import pytest
import yaml

from mewcode.mcp.config import load_config


def load(tmp_path, user=None, project=None, environment=None):
    user_path = tmp_path / "user.yaml"
    project_path = tmp_path / ".mewcode" / "mcp.yaml"
    project_path.parent.mkdir(exist_ok=True)
    for path, value in ((user_path, user), (project_path, project)):
        if value is not None:
            path.write_text(value if isinstance(value, str) else yaml.safe_dump(value))
    return load_config(tmp_path, user_path=user_path, environment=environment or {})


def doc(**servers):
    return {"mcpServers": servers}


def test_missing_empty_and_real_root(tmp_path):
    assert load(tmp_path).servers == ()
    assert load(tmp_path, "", "").diagnostics == ()
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    assert load_config(alias, user_path=tmp_path / "absent").root == tmp_path.resolve()


@pytest.mark.parametrize("bad", ["version: 2", "version: true", "extra: 1", "mcpServers: []", "!!python/object:evil {}", "mcpServers:\n  a: {}\n  a: {}", "mcpServers:\n  a:\n    env: {A: 1, A: 2}", "null"])
def test_document_error_disables_all_mcp_without_raising(tmp_path, bad):
    config = load(tmp_path, bad, doc(good={"transport": "http", "url": "http://localhost/mcp"}))
    assert config.servers == ()
    assert config.diagnostics


def test_whole_server_override_before_validation_and_expansion(tmp_path):
    config = load(tmp_path,
                  doc(a={"transport": "stdio", "command": "bad", "env": {"TOKEN": "${MISSING}"}}, b={"transport": "http", "url": "https://b.test"}),
                  doc(a={"transport": "http", "url": "https://a.test"}))
    assert [s.name for s in config.servers] == ["a", "b"]
    assert config.servers[0].headers == {}
    assert not config.diagnostics


def test_bad_override_never_falls_back_and_other_server_survives(tmp_path):
    config = load(tmp_path, doc(a={"transport": "http", "url": "https://old.test"}),
                  doc(a={"transport": "http", "command": "bad"}, b={"transport": "http", "url": "https://b.test"}))
    assert [s.name for s in config.servers] == ["b"]
    assert config.diagnostics[0].server == "a"


def test_environment_is_single_pass_only_expands_values_and_snapshots(tmp_path):
    env = {"TOKEN": "${OTHER}", "EMPTY": "", "PATH": str(Path(sys.executable).parent)}
    config = load(tmp_path, project=doc(a={"transport": "stdio", "command": sys.executable,
                  "args": ["${TOKEN}"], "env": {"A": "x${TOKEN}${EMPTY}", "B": "字"}}), environment=env)
    server = config.servers[0]
    assert server.env["A"] == "x${OTHER}"
    assert server.args == ("${TOKEN}",)
    assert server.command == str(Path(sys.executable).absolute())
    assert server.cwd == str(tmp_path.resolve())
    env["TOKEN"] = "changed"
    assert server.env["A"] == "x${OTHER}"


@pytest.mark.parametrize("bad", [
    {"transport": "http", "url": "https://user:secret@host/mcp"},
    {"transport": "http", "url": "file:///tmp/a"},
    {"transport": "http", "url": "https://host", "headers": {"A": "${ABSENT}"}},
    {"transport": "http", "url": "https://host", "headers": {"A": "${A:-fallback}"}},
    {"transport": "http", "url": "https://host", "headers": {"A": 3}},
    {"transport": "stdio", "command": sys.executable, "args": "-m a"},
    {"transport": "stdio", "command": "no-such-mewcode-program"},
    {"url": "https://host"},
    {"transport": "stdio", "command": sys.executable, "url": "https://host"},
])
def test_invalid_entries_are_isolated_and_secrets_not_reported(tmp_path, bad):
    config = load(tmp_path, project=doc(bad=bad, good={"transport": "http", "url": "https://good.test"}))
    assert [s.name for s in config.servers] == ["good"]
    assert config.diagnostics
    assert "secret" not in repr(config.diagnostics)


def test_fingerprints_defaults_map_order_and_changes(tmp_path):
    base = {"transport": "http", "url": "https://host", "headers": {"A": "a", "B": "b"}}
    first = load(tmp_path, project=doc(a=base)).servers[0]
    same = load(tmp_path, project=doc(a={**base, "headers": {"B": "b", "A": "a"}})).servers[0]
    changed = load(tmp_path, project=doc(a={**base, "url": "https://new"})).servers[0]
    assert first.fingerprint == same.fingerprint != changed.fingerprint
    empty = load(tmp_path, project=doc(a={"transport": "http", "url": "https://host"})).servers[0]
    explicit = load(tmp_path, project=doc(a={"transport": "http", "url": "https://host", "headers": {}})).servers[0]
    assert empty.fingerprint == explicit.fingerprint
    program = {"transport": "stdio", "command": sys.executable}
    one = load(tmp_path, project=doc(a=program), environment={"HOME": "/first"}).servers[0]
    two = load(tmp_path, project=doc(a=program), environment={"HOME": "/second"}).servers[0]
    assert one.fingerprint != two.fingerprint


def test_fingerprint_pins_resolved_program_args_and_headers(tmp_path):
    for name in ("one", "two"):
        directory = tmp_path / name
        directory.mkdir()
        program = directory / "tool"
        program.write_text("#!/bin/sh\nexit 0\n")
        program.chmod(0o755)
    entry = {"transport": "stdio", "command": "tool"}
    one = load(tmp_path, project=doc(a=entry), environment={"PATH": str(tmp_path / "one")}).servers[0]
    two = load(tmp_path, project=doc(a=entry), environment={"PATH": str(tmp_path / "two")}).servers[0]
    args = load(tmp_path, project=doc(a={**entry, "args": ["changed"]}), environment={"PATH": str(tmp_path / "one")}).servers[0]
    assert one.command == str(tmp_path / "one" / "tool")
    assert len({one.fingerprint, two.fingerprint, args.fingerprint}) == 3
    http = {"transport": "http", "url": "https://host", "headers": {"Authorization": "secret-a"}}
    before = load(tmp_path, project=doc(a=http)).servers[0]
    after = load(tmp_path, project=doc(a={**http, "headers": {"Authorization": "secret-b"}})).servers[0]
    assert before.fingerprint != after.fingerprint
    assert "secret-a" not in repr(before)


def test_python_virtualenv_entry_keeps_import_semantics(tmp_path):
    import subprocess
    # venv 的 python 通常是符号链接；入口目录决定 sys.prefix 与包搜索路径。
    snapshot = load(tmp_path, project=doc(a={"transport": "stdio", "command": sys.executable}))
    server = snapshot.servers[0]
    result = subprocess.run([server.command, "-c", "import sys, mcp; print(sys.prefix)"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == sys.prefix


def test_symlink_target_change_also_changes_fingerprint(tmp_path):
    for name in ("one", "two"):
        program = tmp_path / name
        program.write_text("#!/bin/sh\nexit 0\n")
        program.chmod(0o755)
    link = tmp_path / "entry"
    link.symlink_to(tmp_path / "one")
    entry = {"transport": "stdio", "command": str(link)}
    before = load(tmp_path, project=doc(a=entry)).servers[0]
    link.unlink()
    link.symlink_to(tmp_path / "two")
    after = load(tmp_path, project=doc(a=entry)).servers[0]
    assert before.command == after.command == str(link)
    assert before.fingerprint != after.fingerprint
