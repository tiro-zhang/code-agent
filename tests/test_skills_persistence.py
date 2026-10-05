"""激活及重置遵守存档先于内存发布的事务边界。"""

from dataclasses import replace

import pytest

from conftest import ScriptedProvider, async_test, collect
from mewcode.config import ProviderConfig
from mewcode.types import Message
from test_skills_catalog import entry
from test_skills_session import session, response


CONFIG = ProviderConfig("test", "openai", "main", "http://localhost", "unused", False, context_window=128000)


@async_test
async def test_activation_restore_current_definition_and_reset_boundary(tmp_path):
    path = entry(tmp_path / ".mewcode/skills/a.md", "a", body="旧 {{args}}")
    chat = session(tmp_path, ScriptedProvider([response()]), persistent=True, config=CONFIG)
    identity = chat.session_id
    chat.skills.activate("a", "目标")
    await collect(chat.ask("工作"))
    chat.enter_plan()
    chat.close()
    entry(path, "a", "allowed-tools: []\n", "新 {{args}}")
    chat = session(tmp_path, ScriptedProvider([]), resume=identity, config=CONFIG)
    try:
        assert chat.skills.active[0].body == "新 目标"
        assert chat.history
        assert chat.warnings
        chat.context.failures = 3
        chat.agent.current_user = chat.history[0]
        chat.agent.task_budget = object()
        chat.reset()
        assert chat.session_id == identity
        assert chat.mode == "plan"
        assert chat.history == [] and chat.skills.active == ()
        assert not chat.context.circuit_open
        assert chat.agent.last_task is None
        assert chat.agent.current_user is None and chat.agent.task_budget is None
        assert chat.prompt_state.full_pending
    finally:
        chat.close()
    chat = session(tmp_path, ScriptedProvider([]), resume=identity, config=CONFIG)
    try:
        assert chat.history == [] and chat.skills.active == ()
        assert chat.mode == "plan"
    finally:
        chat.close()


def test_failed_reset_and_deactivation_do_not_publish(tmp_path, monkeypatch):
    entry(tmp_path / ".mewcode/skills/a.md", "a")
    chat = session(tmp_path, ScriptedProvider([]), persistent=True, config=CONFIG)
    chat.skills.activate("a")
    chat.history.append(Message("user", "旧历史"))
    def fail(*args, **kwargs):
        raise OSError("disk")
    monkeypatch.setattr(chat.journal, "append", fail)
    try:
        with pytest.raises(OSError):
            chat.reset()
        assert chat.history[0].content == "旧历史"
        assert len(chat.skills.active) == 1
        assert chat.agent.storage_blocked
        with pytest.raises(OSError):
            chat.skills.deactivate("a")
        assert len(chat.skills.active) == 1
    finally:
        chat.close()


def test_reset_preserves_registered_evidence_cache(tmp_path):
    from mewcode.tools.base import ToolResult
    chat = session(tmp_path, ScriptedProvider([]), persistent=True, config=CONFIG)
    evidence = Message("tool", tool_call_id="actual", tool_result=ToolResult.success("真实结果"))
    path = chat.context.cache.save(evidence, "execute_command")
    chat.reset()
    identity = chat.session_id
    chat.close()
    chat = session(tmp_path, ScriptedProvider([]), resume=identity, config=CONFIG)
    try:
        assert chat.context.cache.restore(path).data == "真实结果"
    finally:
        chat.close()
