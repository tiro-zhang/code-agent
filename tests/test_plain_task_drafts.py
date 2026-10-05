"""退化终端暂存聊天草稿，审批始终使用独立的新输入。"""

import asyncio
import io
import os
import pytest
from conftest import async_test
from mewcode.permissions.terminal import InputReader
from mewcode.terminal.controller import TerminalController
from test_terminal_input import req, tick


@pytest.mark.parametrize('tty', [False, True])
@async_test
async def test_auto_resume_plain_draft_cannot_approve_and_returns_to_chat(tmp_path, tty):
    if tty:
        writer, reader_fd = os.openpty()
    else:
        reader_fd, writer = os.pipe()
    stream = os.fdopen(reader_fd, 'r')
    reader = InputReader(stream, interactive=True)
    terminal = TerminalController(reader, io.StringIO(), secret='', root=tmp_path,
                                  on_interrupt=lambda: None, allow_enhanced=False)
    try:
        initial = asyncio.create_task(terminal.readline(asyncio.Event()))
        await tick()
        os.write(writer, b'2')
        await tick()
        terminal.save_draft()
        initial.cancel()
        await asyncio.gather(initial, return_exceptions=True)
        terminal.set_phase('running')
        approval = asyncio.create_task(terminal.approve(req(), asyncio.Event()))
        await tick()
        os.write(writer, b'\n')
        assert await asyncio.wait_for(approval, 1) == 'deny'
        terminal.restore_draft()
        resumed = asyncio.create_task(terminal.readline(asyncio.Event()))
        await tick()
        os.write(writer, b' rest\n')
        assert await asyncio.wait_for(resumed, 1) == '2 rest'
    finally:
        await terminal.close()
        stream.close()
        os.close(writer)
