"""集中校验停用整份文件，诊断不暴露配置正文。"""

import pytest


def load(tmp_path, text):
    from mewcode.hooks.config import load_config
    directory = tmp_path / ".mewcode"
    directory.mkdir(exist_ok=True)
    (directory / "hooks.yaml").write_text(text)
    return load_config(tmp_path)


def test_missing_configuration_is_empty(tmp_path):
    from mewcode.hooks.config import load_config
    snapshot = load_config(tmp_path)
    assert not snapshot.rules and not snapshot.diagnostics


def test_valid_snapshot_does_not_reload(tmp_path):
    snapshot = load(tmp_path, "version: 1\nhooks:\n  - event: session.start\n    once: true\n    action: {type: prompt, text: hello}\n")
    assert len(snapshot.rules) == 1 and not snapshot.diagnostics
    assert snapshot.rules[0].once and not snapshot.rules[0].background
    (tmp_path / ".mewcode/hooks.yaml").write_text("bad")
    assert snapshot.rules[0].action.text == "hello"


def test_errors_are_collected_and_disable_valid_rules(tmp_path):
    snapshot = load(tmp_path, """version: 1
hooks:
  - event: unknown
    action: {type: prompt, text: private-secret}
  - event: turn.end
    if: {all: [], any: []}
    action: {type: command, command: echo ok}
  - event: session.start
    action: {type: prompt, text: valid}
""")
    assert not snapshot.rules
    assert len(snapshot.diagnostics) >= 2
    assert "private-secret" not in str(snapshot.diagnostics)


@pytest.mark.parametrize("text", [
    "version: 1\nversion: 1\nhooks: []",
    "!!python/object:os.system {}", "version: true\nhooks: []", "version: 1\nother: 1\nhooks: []",
    "version: 1\nhooks:\n  - event: tool.before\n    async: true\n    action: {type: command, command: echo ok}",
    "version: 1\nhooks:\n  - event: turn.end\n    async: true\n    action: {type: prompt, text: hi}",
    "version: 1\nhooks:\n  - event: turn.end\n    action: {type: command, command: hi, timeout_seconds: true}",
    "version: 1\nhooks:\n  - event: turn.end\n    action: {type: http, url: 'https://user:secret@example.com/a'}",
    "version: 1\nhooks:\n  - event: turn.end\n    action: {type: http, url: 'https://example.com/#a'}",
    "version: 1\nhooks:\n  - event: turn.end\n    action: {type: http, url: 'https://example.com', headers: {X: 1}}",
    "version: 1\nhooks:\n  - event: turn.end\n    action: {type: subagent, agent: reviewer}",
])
def test_invalid_file_is_disabled(tmp_path, text):
    snapshot = load(tmp_path, text)
    assert not snapshot.rules and snapshot.diagnostics
