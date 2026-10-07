"""下一条草稿始终只有一个文档，运行期输入不会变成隐式提交。"""

import asyncio

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from conftest import async_test
from mewcode.terminal.approval import ApprovalView
from mewcode.terminal.input import EnhancedTerminal
from test_terminal_input import req, tick


@pytest.mark.parametrize('phase', ['running', 'summary'])
@async_test
async def test_running_draft_enter_requires_fresh_idle_enter(phase):
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None)
        await terminal.start()
        try:
            original = asyncio.create_task(terminal.readline())
            await tick()
            pipe.send_text('原任务\r')
            assert await asyncio.wait_for(original, 1) == '原任务'
            terminal.set_phase(phase)
            pipe.send_text('下一条\x1b\r第二行\r')
            await tick()
            assert terminal.chat.text == '下一条\n第二行'
            assert '不' in terminal._hint() and 'Enter' in terminal._hint()
            position = terminal.chat.cursor_position
            next_input = asyncio.create_task(terminal.readline())
            await tick()
            assert not next_input.done()
            assert terminal.chat.cursor_position == position
            assert terminal.chat.text == '下一条\n第二行'
            pipe.send_text('\r\r')
            assert await asyncio.wait_for(next_input, 1) == '下一条\n第二行'
            assert terminal.chat.text == ''
        finally:
            await terminal.close()


@async_test
async def test_approval_and_cancel_preserve_unique_live_draft():
    cancel = asyncio.Event()
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=cancel.set)
        await terminal.start()
        try:
            terminal.set_phase('running')
            pipe.send_text('接续前')
            await tick()
            terminal.save_draft()
            pipe.send_text('接续中新编辑')
            await tick()
            original = terminal.chat.document
            assert original.text == '接续前接续中新编辑'
            approval = asyncio.create_task(terminal.approve(ApprovalView(req(), root='/project'), cancel))
            await tick()
            pipe.send_text('2\r')
            assert await asyncio.wait_for(approval, 1) == 'once'
            assert terminal.chat.document == original
            pipe.send_text('\x03')
            await tick()
            assert cancel.is_set() and terminal.phase == 'cancelling'
            pipe.send_text('不应插入\r')
            await tick()
            assert terminal.chat.document == original
            cancel.clear()
            pending = asyncio.create_task(terminal.readline(cancel))
            await tick()
            assert terminal.chat.document == original and not pending.done()
            pipe.send_text('\r')
            assert await asyncio.wait_for(pending, 1) == original.text
        finally:
            await terminal.close()


@async_test
async def test_running_paste_is_editable_but_cross_phase_paste_is_discarded():
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None)
        await terminal.start()
        try:
            terminal.set_phase('running')
            pipe.send_text('\x1b[200~ /reset\n2\n\x1b[201~')
            await tick()
            assert terminal.chat.text == ' /reset\n2\n'
            pipe.send_text('\x1b[200~3')
            await tick()
            pending = asyncio.create_task(terminal.readline())
            await tick()
            pipe.send_text('\x1b[201~')
            await tick()
            assert not pending.done() and terminal.chat.text == ' /reset\n2\n'
            pipe.send_text('\r')
            assert await asyncio.wait_for(pending, 1) == ' /reset\n2\n'
        finally:
            await terminal.close()
