"""自动发现、详情与取消共享同一个输入所有者。"""

import asyncio
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from conftest import async_test
from test_terminal_input import tick, req
from mewcode.terminal.input import EnhancedTerminal
from mewcode.terminal.approval import ApprovalView


@async_test
async def test_slash_discovers_description_and_enter_confirms_only_once():
    with create_pipe_input() as pipe:
        ui = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None)
        await ui.start()
        try:
            pending = asyncio.create_task(ui.readline())
            await tick()
            pipe.send_text('/sta')
            await tick()
            assert ui.chat.text == '/sta' and ui.chat.complete_state
            assert ui.chat.complete_state.completions[0].display_meta_text
            pipe.send_text('\r')
            await tick()
            assert ui.chat.text == '/status' and not pending.done()
            pipe.send_text('\r')
            assert await asyncio.wait_for(pending, 1) == '/status'
        finally:
            await ui.close()


@async_test
async def test_idle_ctrl_c_clears_even_whitespace_then_exits():
    with create_pipe_input() as pipe:
        interrupts = []
        ui = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: interrupts.append(True))
        await ui.start()
        try:
            pending = asyncio.create_task(ui.readline())
            await tick()
            pipe.send_text('   \x03')
            await tick()
            assert not pending.done() and ui.chat.text == '' and not interrupts
            pipe.send_text('\x03')
            await tick()
            assert pending.cancelled() and interrupts
        finally:
            await ui.close()


@async_test
async def test_details_preserves_draft_cursor_ignores_input_and_closes_on_approval():
    with create_pipe_input() as pipe:
        ui = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None,
                              details=lambda section: '调用详情' if section == 'tools' else '思考详情')
        await ui.start()
        try:
            pending = asyncio.create_task(ui.readline())
            await tick()
            pipe.send_text('草稿\x1b[D')
            await tick()
            cursor = ui.chat.cursor_position
            pipe.send_text('\x1bOQ')
            await tick()
            assert ui.details_open
            pipe.send_text('2\r\t')
            await tick()
            assert not pending.done() and ui.chat.text == '草稿'
            assert ui._details_section == 'thinking'
            pipe.send_text('\x1bOQ')
            await tick()
            assert not ui.details_open and ui.chat.cursor_position == cursor
            pipe.send_text('\r')
            assert await pending == '草稿'
            pipe.send_text('\x1bOQ')
            await tick()
            assert ui.details_open
            approval = asyncio.create_task(ui.approve(ApprovalView(req(), root='/project'), asyncio.Event()))
            await tick()
            assert not ui.details_open and not approval.done()
            pipe.send_text('1\r')
            assert await approval == 'deny'
        finally:
            await ui.close()


@async_test
async def test_paste_and_escaped_or_multiline_slash_do_not_discover():
    with create_pipe_input() as pipe:
        ui = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None)
        await ui.start()
        try:
            pending = asyncio.create_task(ui.readline())
            await tick()
            pipe.send_text('\x1b[200~/sta\x1b[201~')
            await tick()
            assert not ui.chat.complete_state
            pipe.send_text('t')
            await tick()
            assert ui.chat.complete_state
            pipe.send_text('\x03//')
            await tick()
            assert not ui.chat.complete_state
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        finally:
            await ui.close()


@async_test
async def test_narrow_approval_keeps_navigation_and_decisions_visible():
    from prompt_toolkit.data_structures import Size
    from test_terminal_input import screen_text
    class NarrowOutput(DummyOutput):
        def get_size(self):
            return Size(rows=15, columns=45)
    with create_pipe_input() as pipe:
        ui = EnhancedTerminal(pipe, NarrowOutput(), on_interrupt=lambda: None)
        await ui.start()
        try:
            pending = asyncio.create_task(ui.approve(ApprovalView(req(), root='/project'), asyncio.Event()))
            await tick()
            text = screen_text(ui)
            assert '翻页' in text and '拒绝' in text and '永久' in text
            assert 'hello' in text
            pipe.send_text('1\r')
            assert await pending == 'deny'
        finally:
            await ui.close()


def test_command_menu_description_uses_the_same_secret_boundary():
    from dataclasses import replace
    from mewcode.commands.builtins import build_registry
    from mewcode.commands.registry import CommandRegistry
    from mewcode.terminal.input import CommandCompleter
    from prompt_toolkit.document import Document
    from prompt_toolkit.completion import CompleteEvent
    spec = replace(build_registry().definitions[0], description='包含 private-key\x1b[2J 的说明')
    completer = CommandCompleter(CommandRegistry([spec]).freeze(), 'private-key')
    result = list(completer.get_completions(Document('/'), CompleteEvent()))
    assert result and '[已隐藏]' in result[0].display_meta_text
    assert 'private-key' not in result[0].display_meta_text and '\x1b' not in result[0].display_meta_text


@async_test
async def test_running_details_stays_open_when_task_returns_to_idle():
    with create_pipe_input() as pipe:
        ui = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None)
        await ui.start()
        try:
            ui.set_phase('running')
            pipe.send_text('\x1bOQ')
            await tick()
            assert ui.details_open
            pending = asyncio.create_task(ui.readline())
            await tick()
            assert ui.details_open and ui.phase == 'idle'
            pipe.send_text('2\r')
            await tick()
            assert not pending.done() and not ui.chat.text
            pipe.send_text('\x1bOQ')
            await tick()
            pipe.send_text('后续\r')
            assert await pending == '后续'
        finally:
            await ui.close()
