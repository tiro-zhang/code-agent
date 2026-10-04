"""审批与旁路输出的真实时序，以及兼容终端选择。"""

import asyncio
from io import StringIO
import os
from types import SimpleNamespace

from conftest import async_test
from mewcode.app import _Renderer
from mewcode.permissions.terminal import InputReader
from mewcode.terminal.controller import TerminalController
from mewcode.tools.base import ToolResult
from mewcode.types import AgentEvent


def request(identity):
    return SimpleNamespace(id=identity, tool="read_file", arguments={"path": "a"},
                           targets=("/project/a",), reason="需要审批", mode="strict")


@async_test
async def test_other_result_waits_until_decision_and_before_next_review():
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd)
    output = StringIO()
    terminal = TerminalController(InputReader(source, interactive=True), output, secret="secret",
                                  root="/project", on_interrupt=lambda: None)
    renderer = _Renderer(terminal.stream, "secret")
    try:
        await terminal.start()
        first = asyncio.create_task(terminal.approve(request("one"), asyncio.Event()))
        await asyncio.sleep(.01)
        assert "授权>" in output.getvalue()
        terminal.show(renderer, AgentEvent("tool_result", run_id="run", tool_call_id="other",
            tool_name="other-tool", result=ToolResult.failure("timeout", "独立结果secret")))
        assert "独立结果" not in output.getvalue()
        second = asyncio.create_task(terminal.approve(request("two"), asyncio.Event()))
        os.write(write_fd, b"1\n")
        assert await asyncio.wait_for(first, 1) == "deny"
        await asyncio.sleep(.01)
        shown = output.getvalue()
        assert shown.index("独立结果") < shown.index("[two]")
        assert "secret" not in shown
        os.write(write_fd, b"2\n")
        assert await asyncio.wait_for(second, 1) == "once"
    finally:
        await terminal.close()
        source.close()
        os.close(write_fd)


@async_test
async def test_duplicate_request_callback_does_not_prompt_twice():
    output = StringIO()
    source = InputReader(StringIO("2\n"), interactive=True)
    terminal = TerminalController(source, output, secret="", root="/project", on_interrupt=lambda: None)
    try:
        assert await terminal.approve(request("one"), asyncio.Event()) == "once"
        written = output.getvalue()
        assert await terminal.approve(request("one"), asyncio.Event()) == "once"
        assert output.getvalue() == written
    finally:
        await terminal.close()


@async_test
async def test_redirected_output_uses_plain_and_preserves_input_protocol(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    reader = InputReader(StringIO("first\nsecond\n/exit\n"))
    output = StringIO()
    terminal = TerminalController(reader, output, secret="", root="/project", on_interrupt=lambda: None)
    await terminal.start()
    try:
        assert not terminal.enhanced
        assert await terminal.readline(asyncio.Event()) == "first"
        terminal.begin_task()
        terminal.discard_pending()
        assert await terminal.readline(asyncio.Event()) == "second"
        assert await terminal.readline(asyncio.Event()) == "/exit"
        assert "\x1b" not in output.getvalue()
    finally:
        await terminal.close()


@async_test
async def test_partial_answer_visible_live_and_committed_as_complete_line(monkeypatch):
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from test_terminal_input import screen_text
    with create_pipe_input() as pipe:
        output=StringIO()
        terminal=TerminalController(InputReader(StringIO()),output,secret='',root='/project',on_interrupt=lambda:None)
        monkeypatch.setattr(terminal,'_can_enhance',lambda:True)
        monkeypatch.setattr('mewcode.terminal.controller.create_input',lambda *a,**k:pipe)
        monkeypatch.setattr('mewcode.terminal.controller.create_output',lambda *a,**k:DummyOutput())
        await terminal.start()
        renderer=_Renderer(terminal.stream,'')
        try:
            terminal.show(renderer,AgentEvent('text_delta',run_id='r',text='流式第一段'))
            await asyncio.sleep(.08)
            assert '流式第一段' in screen_text(terminal.backend)
            terminal.show(renderer,AgentEvent('text_delta',run_id='r',text='第二段'))
            await asyncio.sleep(.08)
            assert '流式第一段第二段' in screen_text(terminal.backend)
            terminal.show(renderer,AgentEvent('finished',run_id='r',reason='model_done'))
            await terminal.drain()
            assert 'MewCode> 流式第一段第二段\n' in output.getvalue()
            assert output.getvalue().count('流式第一段第二段')==1
        finally:
            await terminal.close()


@async_test
async def test_initialization_failure_falls_back_without_cancelling_startup(monkeypatch):
    from prompt_toolkit.application import Application
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    cancel=asyncio.Event()
    with create_pipe_input() as pipe:
        output=StringIO()
        terminal=TerminalController(InputReader(StringIO('question\n')),output,secret='',root='/project',on_interrupt=cancel.set)
        monkeypatch.setattr(terminal,'_can_enhance',lambda:True)
        monkeypatch.setattr('mewcode.terminal.controller.create_input',lambda *a,**k:pipe)
        monkeypatch.setattr('mewcode.terminal.controller.create_output',lambda *a,**k:DummyOutput())
        async def broken_start(self, **kwargs):
            raise ValueError('无法建立布局')
        monkeypatch.setattr(Application,'run_async',broken_start)
        await terminal.start()
        try:
            assert not terminal.enhanced and not cancel.is_set()
            assert await terminal.readline(cancel)=='question'
            assert '纯文本模式' in output.getvalue()
        finally:
            await terminal.close()


@async_test
async def test_plain_interactive_discards_typeahead_but_accepts_fresh_prompt_input(monkeypatch):
    import pty
    monkeypatch.setenv("TERM", "dumb")
    master, slave = pty.openpty()
    source = os.fdopen(slave)
    terminal = TerminalController(InputReader(source), StringIO(), secret="", root="/project",
                                  on_interrupt=lambda: None)
    try:
        await terminal.start()
        first = asyncio.create_task(terminal.readline(asyncio.Event()))
        await asyncio.sleep(.02)
        os.write(master, b"first\n/permissions mode bypass\n")
        assert await asyncio.wait_for(first, 1) == "first"
        terminal.begin_task()
        os.write(master, b"queued while running\n")
        await asyncio.sleep(.02)
        second = asyncio.create_task(terminal.readline(asyncio.Event()))
        await asyncio.sleep(.02)
        assert not second.done()
        os.write(master, b"fresh question\n")
        assert await asyncio.wait_for(second, 1) == "fresh question"
    finally:
        await terminal.close()
        source.close()
        os.close(master)


@async_test
async def test_earlier_failure_stays_visible_when_later_tool_succeeds_during_review(monkeypatch):
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from test_terminal_input import screen_text
    with create_pipe_input() as pipe:
        output = StringIO()
        terminal = TerminalController(InputReader(StringIO()), output, secret="secret", root="/project",
                                      on_interrupt=lambda: None)
        monkeypatch.setattr(terminal, "_can_enhance", lambda: True)
        monkeypatch.setattr("mewcode.terminal.controller.create_input", lambda *a, **k: pipe)
        monkeypatch.setattr("mewcode.terminal.controller.create_output", lambda *a, **k: DummyOutput())
        await terminal.start()
        renderer = _Renderer(terminal.stream, "secret")
        pending = asyncio.create_task(terminal.approve(request("third"), asyncio.Event()))
        try:
            await asyncio.sleep(.08)
            terminal.show(renderer, AgentEvent("tool_result", run_id="run", tool_call_id="first",
                tool_name="read_file", result=ToolResult.failure("timeout", "UNIQUE-TIMEOUT-secret")))
            terminal.show(renderer, AgentEvent("tool_result", run_id="run", tool_call_id="second",
                tool_name="read_file", result=ToolResult.success({})))
            await asyncio.sleep(.08)
            assert "UNIQUE-TIMEOUT" in screen_text(terminal.backend)
            assert "secret" not in screen_text(terminal.backend)
            pipe.send_text("results\r")
            await asyncio.sleep(.08)
            assert "UNIQUE-TIMEOUT" in screen_text(terminal.backend)
            assert "成功" in screen_text(terminal.backend) and not pending.done()
            assert "UNIQUE-TIMEOUT" not in output.getvalue()
            pipe.send_text("1\r")
            assert await asyncio.wait_for(pending, 1) == "deny"
            assert output.getvalue().count("UNIQUE-TIMEOUT")==1
        finally:
            await terminal.close()
