"""实际子循环：固定角色、冻结 Fork、预算、缓存与权限隔离。"""

import importlib
from importlib.util import find_spec
import json

from conftest import ScriptedProvider, async_test, collect
from mewcode.agents.definitions import parse_role
from mewcode.agents.request import RequestSnapshot
from mewcode.tasks.manager import TaskManager
from mewcode.types import Message, ToolCall
from test_agent_definitions import entry as role_entry
from test_skills_catalog import entry as skill_entry
from test_skills_session import session, response
from test_permission_runtime import manager as permission_manager


def api():
    assert find_spec("mewcode.agents.runtime") is not None, "缺少独立 Agent 执行器"
    return importlib.import_module("mewcode.agents.runtime")


def submit(chat, arguments, role=None):
    module = api()
    tasks = getattr(chat, "tasks", None) or TaskManager()
    chat.tasks = tasks
    parent = tasks.new_parent("父目标", limit=chat.agent.max_iterations, mode=chat.mode)
    snapshot = module.freeze_child(chat, arguments, role=role)
    record = tasks.submit(parent.task_id, lambda record: module.ChildRuntime(chat, record).run(),
                          type=arguments["type"], background=arguments.get("background", False),
                          snapshot=snapshot, role=role.name if role else "", source=str(role.path) if role else "")
    return tasks, parent, record


@async_test
async def test_defined_has_empty_history_fixed_role_and_independent_parent_scope(tmp_path):
    module = api()
    (tmp_path / "MEWCODE.md").write_text("项目规则标记")
    skill_entry(tmp_path / ".mewcode/skills/parent.md", "parent", body="父激活 SOP 独有标记")
    role = parse_role(role_entry(tmp_path / "role.md", extra="allowed-tools: [read_file, write_file]\n", body="固定角色独有标记"), layer="project")
    provider = ScriptedProvider([response(calls=(ToolCall("write", "write_file", '{"path":"blocked","content":"x"}'),)), response("子最终答复")])
    chat = session(tmp_path, provider)
    chat.skills.activate("parent")
    chat.history[:] = [Message("user", "父历史独有标记"), Message("assistant", "父旧答复")]
    chat.prompt_state.memory = "父记忆独有标记"
    # 注册上限与角色许可取交集，角色不能扩大父有效范围。
    chat.effective_tools = lambda: frozenset({"read_file", "load_skill", "agent"})
    tasks, parent, record = submit(chat, {"type":"defined", "role":role.name, "prompt":"子目标"}, role)
    try:
        await tasks.wait_terminal(record.task_id)
        assert record.state == "completed"
        assert record.outcome.text == "子最终答复"
        assert parent.budget.used == 0
        assert not (tmp_path / "blocked").exists()
        for messages, options in provider.requests:
            text = "\n".join(message.content for message in messages)
            assert "项目规则标记" in text
            assert "父历史独有标记" not in text and "父激活 SOP 独有标记" not in text and "父记忆独有标记" not in text
            assert "固定角色独有标记" in options["system_prompt"]
            assert {tool.name for tool in options["tools"]} == {"read_file"}
        assert record.outcome.evidence[0]["result"]["error"]["code"] == "tool_not_allowed"
        assert chat.history[0].content == "父历史独有标记"
        assert [activation.skill.name for activation in chat.skills.active] == ["parent"]
        assert not provider.closed
    finally:
        await tasks.aclose()
        await chat.aclose()


@async_test
async def test_fork_keeps_sent_prefix_and_control_snapshot_after_parent_changes(tmp_path):
    module = api()
    skill_entry(tmp_path / ".mewcode/skills/later.md", "later", body="父后来激活标记")
    provider = ScriptedProvider([response("父完成"), response("Fork 结果")])
    chat = session(tmp_path, provider)
    try:
        await collect(chat.ask("已发送父目标"))
        sent = chat.agent.last_request.copy()
        chat.skills.activate("later")
        chat.history[0] = Message("user", "父后续改写")
        tasks, parent, record = submit(chat, {"type":"fork", "prompt":"Fork 子目标"})
        chat.prompt_state.custom_instructions = "父后来规则"
        await tasks.wait_terminal(record.task_id)
        messages, options = provider.requests[-1]
        assert tuple(messages[:len(sent.messages)]) == sent.messages
        assert options["system_prompt"] == sent.system_prompt
        assert options["tools"] == sent.tools
        assert "父完成" not in "\n".join(message.content for message in messages)
        assert "父后来激活标记" not in messages[-1].content
        assert "父后来规则" not in messages[-1].content
        assert record.display_mode == "background"
        assert record.outcome.text == "Fork 结果"
        assert chat.history[0].content == "父后续改写"
        assert parent.budget.used == 0
        await tasks.aclose()
    finally:
        await chat.aclose()


@async_test
async def test_first_fork_overflow_does_not_spill_or_send_model(tmp_path):
    module = api()
    provider = ScriptedProvider([])
    chat = session(tmp_path, provider)
    original = Message("user", "文" * 200000)
    chat.agent.last_request = RequestSnapshot.capture([original], chat.executor.registry.definitions(), "父固定系统", chat.config)
    tasks, parent, record = submit(chat, {"type":"fork", "prompt":"子目标"})
    try:
        await tasks.wait_terminal(record.task_id)
        assert record.state == "failed"
        assert record.outcome.reason == "context_blocked"
        assert provider.requests == []
        assert chat.agent.last_request.messages == (original,)
        assert parent.budget.used == 0
    finally:
        await tasks.aclose()
        await chat.aclose()


@async_test
async def test_child_own_budget_and_noninteractive_denial_are_real_results(tmp_path):
    module = api()
    role = parse_role(role_entry(tmp_path / "role.md", extra="max-iterations: 2\n"), layer="project")
    provider = ScriptedProvider([response(calls=(ToolCall("a", "write_file", '{"path":"new","content":"x"}'),)), response(calls=(ToolCall("b", "glob_files", '{"pattern":"*"}'),))])
    chat = session(tmp_path, provider, max_iterations=1)
    chat.permissions = chat.executor.permissions = permission_manager(tmp_path)
    tasks, parent, record = submit(chat, {"type":"defined", "role":role.name, "prompt":"子目标"}, role)
    try:
        await tasks.wait_terminal(record.task_id)
        assert record.state == "failed" and record.outcome.reason == "max_iterations"
        assert len(provider.requests) == 2 and parent.budget.used == 0
        assert record.outcome.evidence[0]["result"]["error"]["code"] == "approval_required"
        assert record.outcome.evidence[1]["result"]["ok"]
        assert not (tmp_path / "new").exists()
        assert chat.permissions.grants.session == ()
    finally:
        await tasks.aclose()
        await chat.aclose()


@async_test
async def test_child_spill_cache_is_private_and_survives_child_end(tmp_path):
    module = api()
    (tmp_path / "large").write_text("文" * 14000)
    role = parse_role(role_entry(tmp_path / "role.md"), layer="project")
    provider = ScriptedProvider([response(calls=(ToolCall("read", "read_file", '{"path":"large"}'),)), response("摘要")])
    chat = session(tmp_path, provider)
    tasks, parent, record = submit(chat, {"type":"defined", "role":role.name, "prompt":"读取大文件"}, role)
    try:
        await tasks.wait_terminal(record.task_id)
        path = record.outcome.evidence[0]["cache_path"]
        assert path and record.task_id in path
        assert (tmp_path / path).exists()
        assert chat.context.cache.paths == set()
        assert chat.history == []
        assert not provider.closed
    finally:
        await tasks.aclose()
        assert not (tmp_path / path).exists()
        await chat.aclose()


@async_test
async def test_fork_declares_agent_but_hard_guard_rejects_both_nesting_paths(tmp_path):
    module = api()
    skill_entry(tmp_path / ".mewcode/skills/isolated.md", "isolated")
    path = tmp_path / ".mewcode/skills/isolated.md"
    path.write_text(path.read_text().replace("mode: shared", "mode: isolated"))
    provider = ScriptedProvider([response("父完成"), response(calls=(
        ToolCall("nested", "agent", '{"type":"fork","prompt":"嵌套"}'),
        ToolCall("skill", "load_skill", '{"name":"isolated"}'))), response("受限结果")])
    chat = session(tmp_path, provider)
    try:
        await collect(chat.ask("父目标"))
        tasks, parent, record = submit(chat, {"type":"fork", "prompt":"子目标"})
        await tasks.wait_terminal(record.task_id)
        assert "agent" in {tool.name for tool in provider.requests[1][1]["tools"]}
        assert [item["result"]["error"]["code"] for item in record.outcome.evidence] == ["tool_not_allowed", "tool_not_allowed"]
        assert len(provider.requests) == 3
        assert len(tasks.records) == 1
        await tasks.aclose()
    finally:
        await chat.aclose()


@async_test
async def test_fork_declaration_is_frozen_but_registration_scope_can_only_tighten(tmp_path):
    skill_entry(tmp_path / '.mewcode/skills/limited.md', 'limited', extra='allowed-tools: [read_file]\n', body='后来激活正文')
    provider = ScriptedProvider([response('父完成'), response(calls=(ToolCall('write', 'write_file', '{"path":"blocked","content":"x"}'),)), response('子已受限')])
    chat = session(tmp_path, provider)
    try:
        await collect(chat.ask('父目标'))
        sent = chat.agent.last_request.copy()
        chat.skills.activate('limited')
        tasks, parent, record = submit(chat, {'type':'fork', 'prompt':'子目标'})
        await tasks.wait_terminal(record.task_id)
        assert provider.requests[1][1]['tools'] == sent.tools
        assert '后来激活正文' not in provider.requests[1][0][-1].content
        assert record.outcome.evidence[0]['result']['error']['code'] == 'tool_not_allowed'
        assert not (tmp_path / 'blocked').exists()
    finally:
        await chat.aclose()


@async_test
async def test_child_hook_effect_is_reported_without_ordinary_tool_calls(tmp_path):
    from test_hook_runtime import make_runtime, rule
    role = parse_role(role_entry(tmp_path / 'role.md'), layer='project')
    chat = session(tmp_path, ScriptedProvider([response('完成')]))
    await chat.hooks.close()
    chat.hooks = make_runtime(tmp_path, [rule(0, 'command', command='printf written > hook-output')])
    try:
        tasks, parent, record = submit(chat, {'type':'defined', 'role':role.name, 'prompt':'子目标'}, role)
        await tasks.wait_terminal(record.task_id)
        assert (tmp_path / 'hook-output').read_text() == 'written'
        assert record.outcome.evidence == ()
        assert record.outcome.side_effects == 'possible'
    finally:
        await chat.aclose()
