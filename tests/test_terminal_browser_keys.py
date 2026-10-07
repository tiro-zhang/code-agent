"""实际按键穿过 F2 层级、搜索与审批边界时保持输入所有权。"""

import asyncio

from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from conftest import async_test
from mewcode.terminal.approval import ApprovalView
from mewcode.terminal.browser import DetailsBrowser
from mewcode.terminal.input import EnhancedTerminal
from mewcode.terminal.state import TerminalState
from test_terminal_history import begin, tool, finish
from test_terminal_input import req, tick, screen_text


@async_test
async def test_real_keys_layers_search_approval_and_draft_are_isolated():
    state = TerminalState()
    begin(state, 'old', '旧问题')
    tool(state, 'old', text='旧内容')
    finish(state, 'old')
    begin(state, 'current', '当前问题')
    tool(state, 'current', text='第一行\n第二行')
    browser = DetailsBrowser(state.history)
    with create_pipe_input() as pipe:
        ui = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None, browser=browser)
        await ui.start()
        try:
            ui.set_phase('running')
            pipe.send_text('下一条草稿\x1b[D')
            await tick()
            document = ui.chat.document
            pipe.send_text('\x1bOQ')
            await tick()
            assert browser.view == 'call_list' and ui.details_open
            pipe.send_text('\r')
            await tick()
            assert browser.view == 'detail'
            pipe.send_text('/')
            await tick()
            pipe.send_text('第二行\r')
            await tick()
            assert '第二行' in ui._details_text() and not browser.searching
            pipe.send_text('/')
            await tick()
            pipe.send_text('\x1b[200~2')
            await tick()
            approval = asyncio.create_task(ui.approve(ApprovalView(req(), root='/project'), asyncio.Event()))
            await tick()
            assert not ui.details_open
            pipe.send_text('\x1b[201~')
            await tick()
            assert not approval.done() and ui.answer.text == ''
            pipe.send_text('1\r2\r')
            assert await asyncio.wait_for(approval, 1) == 'deny'
            assert ui.chat.document == document
            pipe.send_text('\x1bOQ')
            await tick()
            assert browser.view == 'detail' and not browser.searching
            pipe.send_text('\x1b')
            await asyncio.sleep(.6)
            assert browser.view == 'call_list' and ui.details_open
            pipe.send_text('t\x1b[A\r')
            await tick()
            assert browser.turn_id == state.history.turns[0].id
            pipe.send_text('\x1bOQ')
            await tick()
            assert not ui.details_open and ui.chat.document == document
            assert state.run_id == 'current' and state.phase == 'model'
        finally:
            await ui.close()


@async_test
async def test_resize_keeps_browser_selection_and_reachable_close():
    class ResizableOutput(DummyOutput):
        size = Size(rows=34, columns=110)
        def get_size(self):
            return self.size
    state = TerminalState()
    begin(state, 'run')
    tool(state, 'run', text='长内容\n' * 200)
    output = ResizableOutput()
    with create_pipe_input() as pipe:
        ui = EnhancedTerminal(pipe, output, on_interrupt=lambda: None, browser=DetailsBrowser(state.history))
        await ui.start()
        try:
            ui.set_phase('running')
            pipe.send_text('\x1bOQ')
            await tick()
            pipe.send_text('\r\x1b[6~')
            await tick()
            chosen, anchor = ui.browser.call_id, ui.browser.anchor
            for rows, columns in ((15, 45), (6, 18), (34, 110)):
                output.size = Size(rows=rows, columns=columns)
                ui.application._on_resize()
                await tick()
                assert ui.browser.call_id == chosen and ui.browser.anchor == anchor
                assert 'F2' in screen_text(ui)
            pipe.send_text('\x1bOQ')
            await tick()
            assert not ui.details_open
        finally:
            await ui.close()
