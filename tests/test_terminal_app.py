"""本地命令、兼容输入和统一审批输出的应用回归。"""

import asyncio
from io import StringIO
import os

from conftest import ScriptedProvider, async_test
from mewcode.app import run
from mewcode.types import Message
from test_agent_loop import answer
from test_app import config_file


def test_help_status_and_invalid_commands_never_call_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider([answer("答复")])
    output = StringIO()
    text = "/help\n/status\n/hepl\n/do extra\n/exit extra\n  问题  \n/status\n/exit\n"
    assert run(config_file(tmp_path), stdin=StringIO(text), stdout=output,
               provider_factory=lambda config: provider) == 0
    assert len(provider.requests) == 1
    assert [m.content for m in provider.requests[0][0] if m.role == "user"] == ["  问题  "]
    shown = output.getvalue()
    assert "/permissions revoke permanent" in shown and "暂无任务记录" in shown
    assert "未知命令" in shown and "用法：/do" in shown and "请求 1" in shown
    assert "\x1b" not in shown


def test_pipe_preloaded_questions_survive_task_cancel(tmp_path, monkeypatch):
    import signal
    monkeypatch.chdir(tmp_path)
    async def cancel_first():
        signal.raise_signal(signal.SIGINT)
        await asyncio.Event().wait()
        yield
    provider = ScriptedProvider([cancel_first, answer("第二答")])
    read_fd, write_fd = os.pipe()
    os.write(write_fd, "第一问\n第二问\n/exit\n".encode())
    os.close(write_fd)
    with os.fdopen(read_fd) as source:
        assert run(config_file(tmp_path), stdin=source, stdout=StringIO(),
                   provider_factory=lambda config: provider) == 0
    assert len(provider.requests) == 2
    assert Message("user", "第二问") in provider.requests[1][0]


def test_escaped_slash_is_a_message_and_status_keeps_pending_plan(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider([answer("计划"), answer("完成"), answer("路径解释")])
    output = StringIO()
    text = "/plan 任务\n/status\n/do extra\n/do\n//tmp/example\n/exit\n"
    assert run(config_file(tmp_path), stdin=StringIO(text), stdout=output,
               provider_factory=lambda config: provider) == 0
    assert len(provider.requests) == 3
    assert "待执行计划 可用" in output.getvalue()
    assert Message("user", "/tmp/example") in provider.requests[-1][0]


def test_startup_interrupt_is_not_reset_before_mcp_start(tmp_path, monkeypatch):
    from mewcode.terminal.controller import TerminalController
    monkeypatch.chdir(tmp_path)
    provider=ScriptedProvider([])
    async def interrupt_during_ui_start(self):
        self.on_interrupt()
        await asyncio.sleep(0)
    monkeypatch.setattr(TerminalController,'start',interrupt_during_ui_start)
    output=StringIO()
    assert run(config_file(tmp_path),stdin=StringIO('/exit\n'),stdout=output,
               provider_factory=lambda config:provider)==0
    assert '你>' not in output.getvalue() and provider.closed and not provider.requests
