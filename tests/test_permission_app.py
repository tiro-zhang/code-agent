"""权限本地控制及会话/UI 集成，供应商网络使用脚本化边界。"""

import asyncio
from io import StringIO
import os
import signal
from types import SimpleNamespace

import pytest

from conftest import ScriptedProvider, async_test, collect
from mewcode.app import _Renderer
from conftest import run_work_app as run
from mewcode.cli import main
from mewcode.session import ChatSession
from mewcode.types import ToolCall
from test_agent_loop import answer, calls
from test_app import config_file


def test_cli_rejects_invalid_permission_mode_before_launch(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--config", "unused", "--permission-mode", "unsafe"])
    assert error.value.code == 2 and "strict" in capsys.readouterr().err


def test_cli_passes_permission_mode_and_defaults_to_default(tmp_path, monkeypatch):
    # 应用启动是 CLI 的外部边界；验证真实 argparse 的选择及缺省。
    seen = []
    monkeypatch.setattr("mewcode.app.run", lambda path, **options: seen.append(options) or 0)
    assert main(["--config", "unused", "--permission-mode", "strict"]) == 0
    assert main(["--config", "unused"]) == 0
    assert seen == [{"permission_mode": "strict"}, {"permission_mode": "default"}]


def test_broken_idle_input_closes_provider_and_reports_failure(tmp_path):
    provider = ScriptedProvider([])
    stream = StringIO()
    stream.close()
    error = StringIO()
    assert run(config_file(tmp_path), stdin=stream, stdout=StringIO(), stderr=error,
        provider_factory=lambda config: provider) == 2
    assert provider.closed and "启动失败" in error.getvalue()


@pytest.mark.parametrize("term", ["dumb", "xterm-256color"])
def test_external_sigint_wakes_idle_child_event_loop_at_approval(tmp_path, term):
    """外部信号必须唤醒无定时器的子进程，不靠下一次键盘输入解锁。"""
    import pty
    import re
    import select
    import subprocess
    import sys
    import time

    (tmp_path / "a").write_text("不能提前读取")
    config = config_file(tmp_path)
    script = '''import sys
from mewcode.app import run
from mewcode.types import Message, ProviderEvent, ToolCall
class Provider:
    async def stream(self, messages, **options):
        yield ProviderEvent("completed", message=Message("assistant", tool_calls=(ToolCall("wait", "read_file", '{"path":"a"}'),)))
    async def aclose(self):
        pass
run(sys.argv[1], provider_factory=lambda config: Provider())
'''
    master, slave = pty.openpty()
    child = subprocess.Popen([sys.executable, "-c", script, str(config)], cwd=tmp_path,
        stdin=slave, stdout=slave, stderr=slave, env={**os.environ, "TERM": term})
    os.close(slave)
    transcript = bytearray()
    def read_until(marker, timeout=2):
        deadline = time.monotonic() + timeout
        # 增强后端重绘会省略行尾空格，按实际可见文字匹配。
        while marker.strip() not in re.sub(rb"\x1b\[[0-?]*[ -/]*[@-~]", b"", transcript):
            remaining = deadline - time.monotonic()
            assert remaining > 0, transcript.decode(errors="replace")
            readable, _, _ = select.select([master], [], [], remaining)
            assert readable, transcript.decode(errors="replace")
            transcript.extend(os.read(master, 65536))
    try:
        read_until("你> ".encode())
        os.write(master, "读取 a\r".encode())
        read_until("授权> 1".encode())
        # 仅父进程等待；子进程没有网络、定时器或额外输入可唤醒 selector。
        time.sleep(0.05)
        transcript.clear()
        os.kill(child.pid, signal.SIGINT)
        read_until("本轮未完成> cancelled".encode())
        read_until("你> ".encode())
        assert "开始" not in transcript.decode(errors="replace")
        os.write(master, b"/exit\r")
        assert child.wait(timeout=2) == 0
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=2)
        os.close(master)


def test_renderer_keeps_wait_start_rejection_partial_search_and_warning_distinct():
    output = StringIO()
    renderer = _Renderer(output, "secret")
    base = dict(tool_name="search_code", tool_call_id="call-1", permission_mode="strict")
    renderer.show(SimpleNamespace(kind="progress", phase="model", mode="plan", iteration=1,
        max_iterations=20, **base))
    renderer.show(SimpleNamespace(kind="permission_requested", permission_request=SimpleNamespace(id="req-1"), **base))
    assert "等待授权" in output.getvalue() and "开始" not in output.getvalue()
    from mewcode.tools.base import ToolResult
    renderer.show(SimpleNamespace(kind="permission_resolved", permission_decision="permanent",
        warning="保存失败-secret，仅本次有效", **base))
    renderer.show(SimpleNamespace(kind="tool_result", result=ToolResult.success(
        {"matches": [], "permission_limited": True, "skipped_files": 2}), **base))
    shown = output.getvalue()
    assert "规划" in shown and "strict" in shown and "范围受限" in shown
    assert "2" in shown and "仅本次" in shown and "secret" not in shown
    assert "永久批准" not in shown and "本次批准" in shown


def test_permission_commands_do_not_request_model_or_change_plan_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider([answer("计划"), answer("完成")])
    output = StringIO()
    text = "/plan 任务\n/permissions\n/permissions mode strict\n/permissions revoke session\n/permissions revoke permanent\n/permissions mode unknown\n/do\n执行任务\n/exit\n"
    code = run(config_file(tmp_path), stdin=StringIO(text), stdout=output,
        provider_factory=lambda config: provider)
    assert code == 0 and len(provider.requests) == 2
    shown = output.getvalue()
    assert "default" in shown and "strict" in shown and "已撤销" in shown and "用法" in shown
    assert '权限模式> 已切换为 strict' in shown and '进度>' not in shown
    assert not any("/permissions" in message.content for message in provider.requests[-1][0])
    assert len(provider.requests[0][1]["tools"]) == 6
    assert len(provider.requests[1][1]["tools"]) == 9
    assert "任务" in provider.requests[1][0][-2].content


def test_default_noninteractive_denies_tool_but_keeps_agent_loop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider([calls(ToolCall("w", "write_file", '{"path":"a","content":"x"}')), answer("改用解释")])
    output = StringIO()
    assert run(config_file(tmp_path), stdin=StringIO("任务\n/exit\n"), stdout=output,
        provider_factory=lambda config: provider) == 0
    assert not (tmp_path / "a").exists() and len(provider.requests) == 2
    assert "权限拒绝" in output.getvalue() and "开始" not in output.getvalue()


def test_injected_approval_responder_can_approve_without_stdin_channel(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider([calls(ToolCall("w", "write_file", '{"path":"a","content":"x"}')), answer("完成")])
    async def approve(request, cancel):
        return "once"
    assert run(config_file(tmp_path), stdin=StringIO("任务\n/exit\n"), stdout=StringIO(),
        provider_factory=lambda config: provider, approval_responder=approve) == 0
    assert (tmp_path / "a").read_text() == "x"


def test_invalid_permission_configuration_fails_startup_without_model_or_secret(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mewcode").mkdir()
    (tmp_path / ".mewcode/permissions.yaml").write_text("rules: [broken]\n")
    provider = ScriptedProvider([])
    error = StringIO()
    code = run(config_file(tmp_path), stdin=StringIO("任务\n"), stdout=StringIO(), stderr=error,
        provider_factory=lambda config: provider)
    assert code == 2 and provider.requests == [] and provider.closed
    assert "启动失败" in error.getvalue() and "dummy" not in error.getvalue()


def test_failed_permanent_revoke_is_not_reported_as_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    async def invalidate_after_answer():
        for item in answer("答复"):
            yield item
        (tmp_path / ".mewcode").mkdir(exist_ok=True)
        (tmp_path / ".mewcode/permissions.local.yaml").write_text("invalid: true\n")
    provider = ScriptedProvider([invalidate_after_answer])
    output = StringIO()
    assert run(config_file(tmp_path), stdin=StringIO("任务\n/permissions revoke permanent\n/exit\n"),
        stdout=output, provider_factory=lambda config: provider) == 0
    assert "已撤销" not in output.getvalue() and "提示>" in output.getvalue()
    assert len(provider.requests) == 1


def test_approval_eof_cancels_then_exits_without_next_model_request(tmp_path, monkeypatch):
    from mewcode.permissions.terminal import InputReader
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider([calls(ToolCall("w", "write_file", '{"path":"a","content":"x"}')), answer("不应请求")])
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "r")
    os.write(write_fd, "任务\n".encode())
    os.close(write_fd)
    output = StringIO()
    try:
        assert run(config_file(tmp_path), stdin=stream, stdout=output,
            input_reader=InputReader(stream, interactive=True), provider_factory=lambda config: provider) == 0
        assert len(provider.requests) == 1 and provider.closed and not (tmp_path / "a").exists()
        assert "cancelled" in output.getvalue() and "开始" not in output.getvalue()
    finally:
        stream.close()


@async_test
async def test_ctrl_c_at_approval_returns_to_prompt_and_accepts_next_question(tmp_path, monkeypatch):
    from mewcode.app import _run
    from mewcode.config import load_config
    from mewcode.permissions.terminal import InputReader
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider([calls(ToolCall("w", "write_file", '{"path":"a","content":"x"}')), answer("下一轮答复")])
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "r")
    output = StringIO()
    async def send_input():
        os.write(write_fd, "任务\n".encode())
        while "授权>" not in output.getvalue():
            await asyncio.sleep(0.005)
        signal.raise_signal(signal.SIGINT)
        while output.getvalue().count("你> ") < 2:
            await asyncio.sleep(0.005)
        os.write(write_fd, "下一问\n/exit\n".encode())
    sender = asyncio.create_task(send_input())
    try:
        result = await asyncio.wait_for(_run(load_config(config_file(tmp_path)), stream, output,
            StringIO(), lambda config: provider, input_reader=InputReader(stream, interactive=True), memory_enabled=False), 3)
        await sender
        assert result == 0 and len(provider.requests) == 2
        assert "cancelled" in output.getvalue() and "下一轮答复" in output.getvalue()
        assert not (tmp_path / "a").exists()
        assert [message.content for message in provider.requests[-1][0] if message.role == "user"][-1] == "下一问"
    finally:
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
        stream.close()
        os.close(write_fd)


@async_test
async def test_permission_state_survives_plan_and_do(tmp_path):
    from mewcode.permissions.runtime import PermissionManager
    from mewcode.tools import default_registry
    from mewcode.tools.base import ToolContext
    from mewcode.tools.executor import ToolExecutor
    (tmp_path / "a").write_text("旧文")
    provider = ScriptedProvider([calls(ToolCall("r", "read_file", '{"path":"a"}')), answer("计划"),
        calls(ToolCall("e", "edit_file", '{"path":"a","old_text":"旧文","new_text":"新文"}')), answer("完成")])
    manager = PermissionManager(tmp_path, mode="strict", user_path=tmp_path / "user.yaml")
    manager.grants.remember("read_file", "path", (str(tmp_path / "a"),), "session")
    grants = manager.grants.session
    executor = ToolExecutor(default_registry(), ToolContext(tmp_path), permissions=manager)
    session = ChatSession(provider, executor=executor, permission_mode="bypass")
    session.enter_plan()
    await collect(session.ask("任务"))
    session.enter_execute()
    await collect(session.ask("执行任务"))
    assert session.executor.permissions is manager and manager.mode == "strict"
    assert manager.grants.session == grants and (tmp_path / "a").read_text() == "旧文"
    results = [message.tool_result for message in session.history if message.role == "tool"]
    assert results[0].ok and results[1].error["code"] == "permission_denied"


@pytest.mark.parametrize("mode", ["strict", "default", "bypass"])
@async_test
async def test_plan_forbidden_tools_cannot_be_approved_in_any_permission_mode(tmp_path, mode):
    from mewcode.permissions.runtime import PermissionManager
    from mewcode.tools import default_registry
    from mewcode.tools.base import ToolContext
    from mewcode.tools.executor import ToolExecutor
    async def unexpected_approval(request, cancel):
        pytest.fail("规划禁用工具不应提供批准选项")
    manager = PermissionManager(tmp_path, mode=mode, responder=unexpected_approval,
        user_path=tmp_path / "user.yaml")
    provider = ScriptedProvider([calls(ToolCall("w", "write_file", '{"path":"a","content":"x"}'),
        ToolCall("s", "execute_command", '{"command":"printf ok"}')), answer("计划")])
    session = ChatSession(provider, executor=ToolExecutor(default_registry(), ToolContext(tmp_path), permissions=manager))
    session.enter_plan()
    events = await collect(session.ask("任务"))
    assert all(message.tool_result.error["code"] == "tool_not_allowed"
        for message in session.history if message.role == "tool")
    assert not (tmp_path / "a").exists() and not any(event.kind == "tool_started" for event in events)


@async_test
async def test_custom_executor_without_permission_manager_can_keep_session_history(tmp_path):
    from mewcode.tools import default_registry
    from mewcode.tools.base import ToolContext
    # 自定义执行适配器仍可承载纯对话；真实工具入口另外负责授权。
    executor = SimpleNamespace(registry=default_registry(), context=ToolContext(tmp_path))
    provider = ScriptedProvider([answer("答复")])
    session = ChatSession(provider, executor=executor)
    await collect(session.ask("问题"))
    assert session.history[-1].content == "答复" and session.permissions.mode == "default"
