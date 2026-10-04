"""权限评审发现的绕过回归；危险文本始终只解析，绝不执行。"""

import asyncio
from io import StringIO
import json
import os
import pty
import tty
from types import SimpleNamespace

import pytest
import yaml

from conftest import async_test
from mewcode.permissions.runtime import PermissionManager
from mewcode.permissions.shell import analyze_command
from mewcode.tools.base import ToolError


def write_rules(root, entries):
    path = root / ".mewcode" / "permissions.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump({"rules": entries}))


@pytest.mark.parametrize("command", [
    "git 'push' \"$BRANCH\"", "g'it' 'push' $BRANCH", "git 'push' *.txt", "git 'push' $(printf branch)",
])
@async_test
async def test_dynamic_argument_does_not_hide_static_deny_command(tmp_path, command):
    write_rules(tmp_path, [{"effect": "deny", "rule": "Bash(git push*)", "match": "glob"}])
    manager = PermissionManager(tmp_path, mode="bypass", user_path=tmp_path / "user.yaml")
    with pytest.raises(ToolError) as caught:
        await manager.authorize("execute_command", {"command": command})
    assert caught.value.code == "permission_denied"
    assert caught.value.details["source"] == "rule"
    assert caught.value.details["not_started"] is True


@async_test
async def test_dynamic_argument_does_not_hide_static_ask_from_exact_allow(tmp_path):
    command = "git 'push' \"$BRANCH\""
    write_rules(tmp_path, [
        {"effect": "allow", "rule": f"Bash({command})", "match": "exact"},
        {"effect": "ask", "rule": "Bash(git push*)", "match": "glob"},
    ])
    requests = []
    async def reject(request, cancel):
        requests.append(request)
        return "deny"
    manager = PermissionManager(tmp_path, responder=reject, user_path=tmp_path / "user.yaml")
    with pytest.raises(ToolError):
        await manager.authorize("execute_command", {"command": command})
    assert len(requests) == 1


def test_dynamic_extra_argument_does_not_hide_known_blacklist():
    with pytest.raises(ToolError) as caught:
        analyze_command("r'm' '-rf' '/' \"$EXTRA\"")
    assert caught.value.code == "permission_denied"
    assert caught.value.details["source"] == "blacklist"


@async_test
async def test_normalized_static_prefix_still_cannot_glob_allow_dynamic_command(tmp_path):
    write_rules(tmp_path, [{"effect": "allow", "rule": "Bash(git *)", "match": "glob"}])
    manager = PermissionManager(tmp_path, user_path=tmp_path / "user.yaml")
    with pytest.raises(ToolError) as caught:
        await manager.authorize("execute_command", {"command": "git 'status' \"$EXTRA\""})
    assert caught.value.code == "permission_denied"
    assert analyze_command("git 'status' \"$EXTRA\"").simple is False


@pytest.mark.parametrize("command", [
    "printf data > /d'e'v/sda", "printf data 2> /dev/'disk2'", "printf data >| /dev/sda", "printf data >& /dev/sda",
])
def test_static_redirection_target_and_write_operator_cannot_hide_raw_disk_write(command):
    with pytest.raises(ToolError) as caught:
        analyze_command(command)
    assert caught.value.code == "permission_denied"
    assert caught.value.details["source"] == "blacklist"
    assert caught.value.details["rule_id"] == "raw_disk_write"


def test_static_device_input_redirection_is_not_mislabeled_as_write():
    assert analyze_command("cat < /d'e'v/sda").simple is False


@pytest.mark.parametrize("old_input", ["tty_queue", "reader_buffer"])
@async_test
async def test_new_approval_does_not_accept_previous_task_typeahead(old_input):
    from mewcode.permissions.terminal import InputReader, TerminalApproval
    master, slave = pty.openpty()
    if old_input == "reader_buffer":
        # 非规范终端仍是真实 TTY，一次 fd 读取可以带回多行。
        tty.setraw(slave)
    stream = os.fdopen(slave, "r")
    reader, output = InputReader(stream), StringIO()
    request = SimpleNamespace(id="新审批", tool="write_file", arguments={"path": "a", "content": "新操作"},
                              targets=("/project/a",), reason="未匹配放行规则", mode="default")
    task = None
    try:
        if old_input == "reader_buffer":
            os.write(master, b"previous-question\n2\n")
            assert await asyncio.wait_for(reader.readline(), 1) == "previous-question\n"
            assert reader._buffer == "2\n"
        else:
            os.write(master, b"2\n")
        await asyncio.sleep(0.02)
        task = asyncio.create_task(TerminalApproval(reader, output)(request, asyncio.Event()))
        await asyncio.sleep(0.02)
        assert "新审批" in output.getvalue()
        assert not task.done(), "新审批不得消费显示操作之前已经排队的数字作为批准"
        os.write(master, b"2\n")
        assert await asyncio.wait_for(task, 1) == "once"
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        stream.close()
        os.close(master)


@async_test
async def test_current_approval_keeps_typed_decision_after_page_and_invalid_choice():
    from mewcode.permissions.terminal import InputReader, TerminalApproval
    master, slave = pty.openpty()
    tty.setraw(slave)
    stream = os.fdopen(slave, "r")
    reader, output = InputReader(stream), StringIO()
    request = SimpleNamespace(id="同一审批", tool="write_file",
                              arguments={"path": "a", "content": "\n".join(["line"] * 30)},
                              targets=("/project/a",), reason="需授权", mode="default")
    task = asyncio.create_task(TerminalApproval(reader, output, page_lines=5)(request, asyncio.Event()))
    try:
        await asyncio.sleep(0.02)
        assert "同一审批" in output.getvalue()
        os.write(master, b"next\ninvalid\n2\n")
        assert await asyncio.wait_for(task, 1) == "once"
        assert "无效选项" in output.getvalue()
        assert "第 2/" in output.getvalue()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        stream.close()
        os.close(master)


@pytest.mark.parametrize("changed_object", ["caller", "display"])
@pytest.mark.parametrize("field,new_value", [("content", "未经批准的新内容"), ("path", "other.txt")])
@async_test
async def test_once_approval_rejects_changed_complete_arguments(tmp_path, changed_object, field, new_value):
    arguments = {"path": "a.txt", "content": "原操作"}
    async def approve(request, cancel):
        target = arguments if changed_object == "caller" else request.arguments
        target[field] = new_value
        return "once"
    manager = PermissionManager(tmp_path, responder=approve, user_path=tmp_path / "user.yaml")
    with pytest.raises(ToolError) as caught:
        await manager.authorize("write_file", arguments)
    serialized = json.loads(caught.value.result().to_json())
    assert serialized["ok"] is False
    assert serialized["error"]["code"] == "permission_check_failed"
    assert serialized["error"]["details"]["not_started"] is True
    assert manager.grants.session == ()
    assert not (tmp_path / "a.txt").exists() and not (tmp_path / "other.txt").exists()


@pytest.mark.parametrize("mode", ["default", "bypass"])
@pytest.mark.parametrize("tool,arguments", [
    ("write_file", {"path": "policy-alias.yaml", "content": "rules: []"}),
    ("edit_file", {"path": "policy-alias.yaml", "old_text": "deny", "new_text": "allow"}),
])
@async_test
async def test_file_tool_config_link_protection_overrides_allow_and_bypass(tmp_path, mode, tool, arguments):
    from mewcode.tools import default_registry
    from mewcode.tools.base import ToolContext
    from mewcode.tools.executor import ToolExecutor
    write_rules(tmp_path, [{"effect": "allow", "rule": f"{tool}(**)", "match": "glob"}])
    path = tmp_path / ".mewcode" / "permissions.yaml"
    before = path.read_bytes()
    (tmp_path / "policy-alias.yaml").symlink_to(path)
    manager = PermissionManager(tmp_path, mode=mode, user_path=tmp_path / "user.yaml")
    executor = ToolExecutor(default_registry(), ToolContext(tmp_path), permissions=manager)
    events = []
    result = await executor.execute(tool, json.dumps(arguments), on_event=events.append)
    serialized = json.loads(result.to_json())
    assert serialized["error"]["code"] == "permission_denied"
    assert serialized["error"]["details"] == {"source": "protected_config", "not_started": True}
    assert path.read_bytes() == before
    assert not any(event["kind"] == "tool_started" for event in events)


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
@async_test
async def test_permission_result_source_and_pairing_survive_both_protocols(tmp_path, protocol):
    from mewcode.providers.tool_messages import anthropic_messages, anthropic_tools, openai_messages, openai_tools
    from mewcode.tools import default_registry
    from mewcode.types import Message, ToolCall
    write_rules(tmp_path, [{"effect": "deny", "rule": "write_file(a)", "match": "exact"}])
    manager = PermissionManager(tmp_path, mode="bypass", user_path=tmp_path / "user.yaml")
    arguments = {"path": "a", "content": "不得写入"}
    with pytest.raises(ToolError) as caught:
        await manager.authorize("write_file", arguments)
    history = [Message("user", "创建文件"),
               Message("assistant", tool_calls=(ToolCall("call-bound", "write_file", json.dumps(arguments)),)),
               Message("tool", tool_call_id="call-bound", tool_result=caught.value.result())]
    definitions = default_registry().definitions()
    if protocol == "openai":
        wire = openai_messages(history)
        assert [item["role"] for item in wire] == ["user", "assistant", "tool"]
        assert wire[-1]["tool_call_id"] == wire[-2]["tool_calls"][0]["id"] == "call-bound"
        body = json.loads(wire[-1]["content"])
        tools = openai_tools(definitions)
        exported = [tool["function"] for tool in tools]
        assert all(set(tool) == {"name", "description", "parameters"} for tool in exported)
    else:
        wire = anthropic_messages(history)
        assert [item["role"] for item in wire] == ["user", "assistant", "user"]
        block = wire[-1]["content"][0]
        assert block["tool_use_id"] == wire[-2]["content"][0]["id"] == "call-bound"
        assert block["is_error"] is True
        body = json.loads(block["content"])
        exported = anthropic_tools(definitions)
        assert all(set(tool) == {"name", "description", "input_schema"} for tool in exported)
    assert {tool["name"] for tool in exported} == {"read_file", "write_file", "edit_file", "execute_command", "glob_files", "search_code"}
    assert body["error"]["code"] == "permission_denied"
    assert body["error"]["details"] == {"source": "rule", "not_started": True}


@async_test
async def test_session_grant_survives_real_history_trim(tmp_path):
    from conftest import ScriptedProvider, collect
    from mewcode.session import ChatSession
    from mewcode.tools import default_registry
    from mewcode.tools.base import ToolContext
    from mewcode.tools.executor import ToolExecutor
    from mewcode.types import ContextLimitError, ToolCall
    from test_agent_loop import answer, calls
    from test_context_summary import response
    from test_context_partition import history_for_task
    (tmp_path / "a").write_text("内容")
    approvals = []
    async def remember(request, cancel):
        approvals.append(request.id)
        return "session"
    manager = PermissionManager(tmp_path, mode="strict", responder=remember, user_path=tmp_path / "user.yaml")
    provider = ScriptedProvider([
        calls(ToolCall("first", "read_file", '{"path":"a"}')), answer("旧任务完成"),
        [ContextLimitError("超出窗口")], response(), calls(ToolCall("second", "read_file", '{"path":"a"}')), answer("新任务完成"),
    ])
    executor = ToolExecutor(default_registry(), ToolContext(tmp_path), permissions=manager)
    session = ChatSession(provider, executor=executor)
    await collect(session.ask("旧任务"))
    grants = manager.grants.session
    _, earlier = history_for_task()
    session.history[:0] = earlier
    events = await collect(session.ask("新任务"))
    assert any(event.kind == "context_compaction" and event.phase == "success" for event in events)
    assert [message.content for message in session.history if message.role == "user"][-1] == "新任务"
    assert len(approvals) == 1 and manager.grants.session == grants
    assert session.history[-1].content == "新任务完成"
