"""动态命令和输入边界原子更新。"""

import io

from conftest import ScriptedProvider, async_test, collect, run_work_app
from mewcode.commands.builtins import build_registry
from mewcode.skills.catalog import discover_skills
from test_config import write_config
from test_skills_catalog import entry
from test_skills_session import session, response
from test_skills_isolated import isolated


@async_test
async def test_direct_shared_and_isolated_submit_once(tmp_path):
    entry(tmp_path / ".mewcode/skills/shared.md", "shared", body="共享 SOP {{args}}")
    isolated(tmp_path / ".mewcode/skills/child.md")
    provider = ScriptedProvider([response("共享结果"), response("子摘要")])
    chat = session(tmp_path, provider)
    try:
        await collect(chat.run_skill("shared", 'a  "b" /reset'))
        assert len(provider.requests) == 1
        assert '共享 SOP a  "b" /reset' in provider.requests[0][0][-1].content
        await collect(chat.run_skill("child", "测试目标"))
        assert len(provider.requests) == 2
        assert len([m for m in chat.history if m.role == "user"]) == 2
        assert "子摘要" in chat.history[-1].content
        assert [a.skill.name for a in chat.skills.active] == ["shared"]
    finally:
        chat.close()


def test_hot_reload_publishes_whole_generation_and_preserves_bad_candidate(tmp_path):
    path = entry(tmp_path / ".mewcode/skills/shared.md", "shared", body="旧 {{args}}")
    chat = session(tmp_path, ScriptedProvider([]))
    chat.skills.activate("shared", "目标")
    registry = build_registry(chat.skills.catalog)
    try:
        entry(path, "shared", body="新 {{args}}")
        entry(tmp_path / ".mewcode/skills/new.md", "new")
        updated, warnings = chat.refresh_skills()
        assert updated.find("new") is not None
        assert "/new" in updated.completions("/ne")
        assert chat.skills.active[0].body == "新 目标"
        snapshot = chat.skills.catalog
        entry(path, "shared", "allowed-tools: [missing]\n", "非法更新")
        rejected, warnings = chat.refresh_skills()
        assert rejected is None and warnings
        assert chat.skills.catalog is snapshot
        assert chat.skills.active[0].body == "新 目标"
        path.unlink()
        updated, warnings = chat.refresh_skills()
        assert not chat.skills.active
        assert warnings
    finally:
        chat.close()


def test_reserved_name_fails_before_provider(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    entry(tmp_path / ".mewcode/skills/bad.md", "reset")
    calls = []
    def factory(config):
        calls.append(config)
        return ScriptedProvider([])
    output, error = io.StringIO(), io.StringIO()
    code = run_work_app(write_config(tmp_path / "config"), stdin=io.StringIO('/exit\n'),
                        stdout=output, stderr=error, provider_factory=factory)
    assert code == 2 and calls == []
    assert 'reset' in error.getvalue()
    assert not (tmp_path / ".mewcode/sessions").exists()


def test_unknown_tool_fails_before_input_or_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    entry(tmp_path / ".mewcode/skills/bad.md", "bad", "allowed-tools: [read_flie]\n")
    provider = ScriptedProvider([])
    error = io.StringIO()
    code = run_work_app(write_config(tmp_path / "config"), stdin=io.StringIO('执行\n'),
                        stdout=io.StringIO(), stderr=error, provider_factory=lambda _: provider)
    assert code == 2 and provider.requests == []
    assert 'read_flie' in error.getvalue()
