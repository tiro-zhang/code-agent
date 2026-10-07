"""共享加载、完整上下文和任务快照的会话集成。"""

from conftest import ScriptedProvider, async_test, collect, permission_bypass
from mewcode.session import ChatSession
from mewcode.prompts import PromptState
from mewcode.tools import default_registry
from mewcode.tools.base import ToolContext
from mewcode.tools.executor import ToolExecutor
from mewcode.types import Message, ProviderEvent, ToolCall
from test_skills_catalog import entry


def response(text="完成", calls=()):
    return [ProviderEvent("completed", message=Message("assistant", text, tool_calls=calls))]


def session(tmp_path, provider, **kwargs):
    ex = ToolExecutor(default_registry(), ToolContext(tmp_path), permissions=permission_bypass(tmp_path))
    return ChatSession(provider, executor=ex, user_root=tmp_path / "user", memory_enabled=False, **kwargs)


def test_pinned_skills_on_short_reminder(tmp_path):
    prompt = PromptState(tmp_path, active_skills="完整 SOP", custom_instructions="项目规则")
    first = prompt.begin_request()
    prompt.commit(first)
    second = prompt.peek_request()
    assert second.content.index("完整 SOP") < second.content.index("当前模式")
    assert "环境信息" not in second.content
    assert "项目规则" not in second.content
    assert prompt.request_sequence == 1


@async_test
async def test_shared_load_same_batch_restricts_and_pins_next_request(tmp_path):
    entry(tmp_path / ".mewcode/skills/a.md", "a", "allowed-tools: [read_file]\n", "唯一 SOP {{args}}")
    provider = ScriptedProvider([
        response(calls=(ToolCall("load", "load_skill", '{"name":"a","args":"目标"}'),
                        ToolCall("write", "write_file", '{"path":"bad","content":"x"}'))),
        response(), response("下一任务"),
    ])
    chat = session(tmp_path, provider)
    try:
        events = await collect(chat.ask("执行 a"))
        assert events[-1].reason == "model_done"
        first, second = provider.requests[:2]
        assert "a: 一句说明" in first[0][-1].content
        assert "唯一 SOP" not in first[0][-1].content
        assert "唯一 SOP 目标" in second[0][-1].content
        assert {t.name for t in second[1]["tools"]} == {"read_file", "load_skill", "agent", "team"}
        assert "环境信息" not in second[0][-1].content
        assert not (tmp_path / "bad").exists()
        results = [e.result for e in events if e.kind == "tool_result"]
        assert results[1].error["code"] == "tool_not_allowed"
        await collect(chat.ask("继续"))
        assert "唯一 SOP 目标" in provider.requests[-1][0][-1].content
    finally:
        chat.close()


@async_test
async def test_midtask_first_load_uses_frozen_definition(tmp_path):
    path = entry(tmp_path / ".mewcode/skills/a.md", "a", body="原始正文")
    async def change_then_load():
        entry(path, "a", body="中途新正文")
        for event in response(calls=(ToolCall("load", "load_skill", '{"name":"a"}'),)):
            yield event
    provider = ScriptedProvider([change_then_load, response()])
    chat = session(tmp_path, provider)
    try:
        await collect(chat.ask("执行"))
        assert "原始正文" in provider.requests[1][0][-1].content
        assert "中途新正文" not in provider.requests[1][0][-1].content
    finally:
        chat.close()


@async_test
async def test_pinned_sop_too_large_blocks_without_truncation(tmp_path):
    from dataclasses import replace
    from test_skills_persistence import CONFIG
    body = '文' * 18000
    entry(tmp_path / '.mewcode/skills/a.md', 'a', body=body)
    provider = ScriptedProvider([])
    chat = session(tmp_path, provider, config=replace(CONFIG, context_window=20000, max_output_tokens=1000))
    chat.skills.activate('a')
    try:
        events = await collect(chat.ask('执行'))
        assert events[-1].reason == 'context_blocked'
        assert not provider.requests
        assert chat.skills.active[0].body == body
        assert chat.context.failures == 0
    finally:
        chat.close()
