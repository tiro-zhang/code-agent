"""外部工具进入既有执行、会话与启动通道。"""

import asyncio
from io import StringIO
import json
import signal
import os
import threading

import pytest

from conftest import ScriptedProvider, async_test
from test_agent_loop import answer, calls
from test_mcp_manager import config, stdio
from test_app import config_file
from conftest import run_work_app as run
from mewcode.mcp.manager import MCPManager
from mewcode.mcp.tools import tool_alias
from mewcode.permissions.runtime import PermissionManager
from mewcode.tools import default_registry
from mewcode.tools.base import ToolContext
from mewcode.tools.executor import ToolExecutor
from mewcode.types import ToolCall


@async_test
async def test_executor_checks_before_sending_and_builtins_still_spawn(tmp_path):
    registry = default_registry()
    manager = MCPManager(config(tmp_path, {"a": stdio(tmp_path)}), registry)
    permissions = PermissionManager(tmp_path, user_path=tmp_path / "absent")
    executor = ToolExecutor(registry, ToolContext(tmp_path), permissions=permissions, mcp=manager)
    try:
        await manager.start()
        permissions.bind_mcp_tools(manager.tools)
        name = next(t.name for t in manager.tools if t.original_name == "echo")
        denied = await executor.execute(name, '{"text":"禁止"}')
        assert denied.error["code"] == "permission_denied"
        forbidden = await executor.execute(name, '{}', allowed_tools=registry.names(read_only=True))
        assert forbidden.error["code"] == "tool_not_allowed"
        permissions.mode = "bypass"
        events = []
        assert (await executor.execute(name, '{"text":"允许"}', on_event=events.append)).ok
        assert events == [{"kind": "tool_started"}]
        assert (await executor.execute("write_file", '{"path":"test.txt","content":"hello"}')).ok
        assert (await executor.execute("read_file", '{"path":"test.txt"}')).ok
        assert (await executor.execute("edit_file", '{"path":"test.txt","old_text":"hello","new_text":"world"}')).ok
        assert (await executor.execute("glob_files", '{"pattern":"*.txt"}')).ok
        assert (await executor.execute("search_code", '{"pattern":"world"}')).ok
        assert (await executor.execute("execute_command", '{"command":"pwd"}')).ok
        messages = [json.loads(line) for line in (tmp_path / "a.jsonl").read_text().splitlines()]
        assert sum(e["method"] == "tools/call" for e in messages) == 1
    finally:
        await manager.close()


def test_app_starts_discovers_calls_twice_and_closes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    config(tmp_path, {"a": stdio(tmp_path)})
    name = tool_alias("a", "echo")
    provider = ScriptedProvider([calls(ToolCall("one", name, '{"text":"第一轮"}')), answer("完成一"),
                                 calls(ToolCall("two", name, '{"text":"第二轮"}')), answer("完成二")])
    output = StringIO()
    code = run(config_file(tmp_path), stdin=StringIO("第一次\n第二次\n/exit\n"), stdout=output,
               provider_factory=lambda _: provider, permission_mode="bypass")
    assert code == 0
    assert "已注册 2 个工具" in output.getvalue()
    assert "完成二" in output.getvalue()
    assert all(len(options["tools"]) == 11 for _, options in provider.requests)
    messages = [json.loads(line) for line in (tmp_path / "a.jsonl").read_text().splitlines()]
    assert sum(e["method"] == "tools/call" for e in messages) == 2
    assert sum(e["method"] == "PROCESS_START" for e in messages) == 1
    assert provider.closed


def test_bad_permission_config_never_launches_mcp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    config(tmp_path, {"a": stdio(tmp_path)})
    (tmp_path / ".mewcode" / "permissions.yaml").write_text("version: 9")
    provider = ScriptedProvider([])
    assert run(config_file(tmp_path), stdin=StringIO("/exit\n"), stdout=StringIO(), stderr=StringIO(),
               provider_factory=lambda _: provider) == 2
    assert not (tmp_path / "a.jsonl").exists()
    assert provider.closed


def test_plan_excludes_mcp_and_do_restores_tools_without_approval(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    config(tmp_path, {"a": stdio(tmp_path)})
    name = tool_alias("a", "echo")
    provider = ScriptedProvider([calls(ToolCall("forged", name, '{}')), answer("目标：外部调用。步骤：调用 echo。验证：返回文本。"),
                                 calls(ToolCall("do", name, '{}')), answer("未获批准")])
    output = StringIO()
    assert run(config_file(tmp_path), stdin=StringIO("/plan 测试\n/do\n执行任务\n/exit\n"), stdout=output,
               provider_factory=lambda _: provider) == 0
    assert [len(options["tools"]) for _, options in provider.requests] == [6, 6, 11, 11]
    assert "tool_not_allowed" in output.getvalue() and "permission_denied" in output.getvalue()
    assert '"method": "tools/call"' not in (tmp_path / "a.jsonl").read_text()


@async_test
async def test_scheduler_read_mcp_read_and_cancel_pairing(tmp_path):
    from conftest import collect
    from mewcode.tools.scheduler import ToolScheduler
    registry = default_registry()
    manager = MCPManager(config(tmp_path, {"a": stdio(tmp_path)}), registry)
    permission = PermissionManager(tmp_path, mode="bypass", user_path=tmp_path / "absent")
    executor = ToolExecutor(registry, ToolContext(tmp_path), permissions=permission, mcp=manager)
    (tmp_path / "note").write_text("local")
    try:
        await manager.start()
        permission.bind_mcp_tools(manager.tools)
        name = next(t.name for t in manager.tools if t.original_name == "echo")
        scheduler = ToolScheduler(executor)
        batch = [ToolCall("r1", "read_file", '{"path":"note"}'), ToolCall("remote", name, '{"business_error":true}'),
                 ToolCall("r2", "read_file", '{"path":"note"}')]
        events = await collect(scheduler.run(batch, allowed_tools=None, run_id="test", iteration=1, mode="execute"))
        assert [e.tool_call_id for e in events if e.kind == "tool_started"] == ["r1", "remote", "r2"]
        assert scheduler.results[1].error["code"] == "mcp_tool_error" and scheduler.results[2].ok
        cancel = asyncio.Event()
        batch = [ToolCall("slow", name, '{"delay":5}'), ToolCall("later", name, '{}')]
        async def consume():
            async for event in scheduler.run(batch, allowed_tools=None, run_id="cancel", iteration=1, mode="execute", cancel_event=cancel):
                if event.kind == "tool_started":
                    cancel.set()
        await consume()
        assert len(scheduler.results) == 2
        assert scheduler.results[0].error["details"]["side_effects_may_have_occurred"]
        assert scheduler.results[1].error["details"]["not_started"]
    finally:
        await manager.close()


@async_test
async def test_scheduler_pairs_schema_errors_and_continues_other_tools(tmp_path):
    from conftest import collect
    from mewcode.mcp.tools import MCPTool
    from mewcode.tools.scheduler import ToolScheduler
    registry = default_registry()
    schema = {"type": "object", "$defs": {"cycle": {"$ref": "#/$defs/cycle"}}, "$ref": "#/$defs/cycle"}
    # 防御性测试：即使描述绕过发现过滤，校验异常也应形成结果而非卡住队列。
    registry.register(MCPTool("broken", "测试", schema, "s", "t", "a" * 64))
    (tmp_path / "note").write_text("继续")
    permissions = PermissionManager(tmp_path, mode="bypass", user_path=tmp_path / "absent")
    scheduler = ToolScheduler(ToolExecutor(registry, ToolContext(tmp_path), permissions=permissions))
    events = await collect(scheduler.run([ToolCall("bad", "broken", "{}"), ToolCall("good", "read_file", '{"path":"note"}')],
                                        allowed_tools=None, run_id="schema", iteration=1, mode="execute"))
    assert [e.tool_call_id for e in events if e.kind == "tool_result"] == ["bad", "good"]
    assert scheduler.results[0].error["code"] == "invalid_schema"
    assert scheduler.results[1].ok


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_mcp_provider_declarations_and_result_pairing(protocol):
    from mewcode.providers import tool_messages
    from mewcode.mcp.tools import adapt_tools, adapt_result
    from mewcode.types import Message
    registry = default_registry()
    tool = adapt_tools("s", "a" * 64, [{"name": "t", "inputSchema": {"type": "object", "properties": {"x": {"type": "integer"}}}}])[0][0]
    registry.register(tool)
    result = adapt_result({"content": [{"type": "text", "text": "ok"}], "structuredContent": {"x": 1}})
    definitions = getattr(tool_messages, protocol + "_tools")(registry.definitions())
    assert tool.name in json.dumps(definitions)
    history = [Message("assistant", tool_calls=(ToolCall("remote-id", tool.name, '{"x":1}'),)),
               Message("tool", tool_call_id="remote-id", tool_result=result)]
    messages = getattr(tool_messages, protocol + "_messages")(history)
    assert "remote-id" in json.dumps(messages)
    assert "structuredContent" in json.dumps(messages)


def test_startup_sigint_closes_mcp_and_provider(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    config(tmp_path, {"a": stdio(tmp_path, args=["--behavior", '{"list_delay":10}'])})
    provider = ScriptedProvider([])
    timer = threading.Timer(.25, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    try:
        assert run(config_file(tmp_path), stdin=StringIO("/exit\n"), stdout=StringIO(),
                   provider_factory=lambda _: provider) == 0
        assert provider.closed
    finally:
        timer.cancel()
        timer.join()
    if (tmp_path / "a.jsonl").exists():
        pid = json.loads((tmp_path / "a.jsonl").read_text().splitlines()[0])["pid"]
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
