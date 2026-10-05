"""本地目录、任务操作与保留控制名。"""

import pytest
from conftest import ScriptedProvider, async_test
from mewcode.commands.builtins import build_registry
from test_skills_session import session
from test_skills_catalog import entry
from mewcode.skills.catalog import discover_skills


@async_test
async def test_local_agents_and_tasks_do_not_request_model(tmp_path):
    provider = ScriptedProvider([])
    chat = session(tmp_path, provider)
    try:
        assert "explore" in chat.agents_text()
        assert "代码探索 Agent" in chat.agents_text("show explore")
        assert "当前进程" in await chat.tasks_text()
        with pytest.raises(ValueError):
            chat.agents_text("show")
        with pytest.raises(ValueError):
            await chat.tasks_text("cancel")
        assert provider.requests == []
        registry = build_registry(chat.skills.catalog)
        assert registry.find("tasks") and registry.find("agents")
        assert "/tasks show" in registry.completions("/tasks sh")
    finally:
        await chat.aclose()


@pytest.mark.parametrize("name", ["tasks", "agents"])
def test_new_controls_are_reserved_for_skills(tmp_path, name):
    entry(tmp_path / ".mewcode/skills/a.md", name)
    with pytest.raises(ValueError):
        build_registry(discover_skills(tmp_path, user_root=tmp_path / "user"))
