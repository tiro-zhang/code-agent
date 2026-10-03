"""终端审批与输入拥有者的真实边界测试。"""

import asyncio
from io import StringIO
import os
import pty
from types import SimpleNamespace

import pytest

from conftest import async_test


def request(tool="write_file", arguments=None):
    return SimpleNamespace(id="request-1", tool=tool,
        arguments=arguments or {"path": "a", "content": "正文"},
        targets=("/project/a",), reason="未命中允许规则", mode="default")


@pytest.mark.parametrize("answer,expected", [("\n", "deny"), ("1\n", "deny"),
    ("2\n", "once"), ("3\n", "session"), ("4\n", "permanent"),
    ("非法\n2\n", "once")])
@async_test
async def test_decisions_are_explicit_and_invalid_input_does_not_approve(answer, expected):
    from mewcode.permissions.terminal import InputReader, TerminalApproval
    output = StringIO()
    reader = InputReader(StringIO(answer), interactive=True)
    approve = TerminalApproval(reader, output, root="/project")
    assert await approve(request(), asyncio.Event()) == expected
    assert "request-1" in output.getvalue() and "/project/a" in output.getvalue()
    assert "完整参数" in output.getvalue() and "本次" in output.getvalue()
    assert "会话" in output.getvalue() and "永久" in output.getvalue()


@async_test
async def test_noninteractive_input_cannot_be_used_as_approval():
    from mewcode.permissions.terminal import InputReader, TerminalApproval
    reader = InputReader(StringIO("2\n下一问\n"))
    assert await TerminalApproval(reader, StringIO())(request(), asyncio.Event()) == "deny"
    assert await reader.readline() == "2\n"


@async_test
async def test_eof_cancels_task_and_marks_reader_for_application_exit():
    from mewcode.permissions.terminal import InputReader, TerminalApproval
    cancel = asyncio.Event()
    reader = InputReader(StringIO(""), interactive=True)
    assert await TerminalApproval(reader, StringIO())(request(), cancel) == "deny"
    assert reader.eof and cancel.is_set()


@async_test
async def test_unsubmitted_approval_at_fd_eof_is_not_accepted():
    from mewcode.permissions.terminal import InputReader, TerminalApproval
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "r")
    os.write(write_fd, b"2")
    os.close(write_fd)
    cancel = asyncio.Event()
    try:
        reader = InputReader(stream, interactive=True)
        assert await TerminalApproval(reader, StringIO())(request(), cancel) == "deny"
        assert cancel.is_set() and reader.eof
    finally:
        stream.close()


@async_test
async def test_injected_memory_channel_also_rejects_unsubmitted_eof_answer():
    from mewcode.permissions.terminal import InputReader, TerminalApproval
    cancel = asyncio.Event()
    reader = InputReader(StringIO("2"), interactive=True)
    assert await TerminalApproval(reader, StringIO())(request(), cancel) == "deny"
    assert reader.eof and cancel.is_set()


@async_test
async def test_long_edit_can_be_reviewed_completely_and_secret_controls_are_safe():
    from mewcode.permissions.terminal import InputReader, TerminalApproval
    output = StringIO()
    content = "\n".join(f"第{i}行" for i in range(100)) + "\n密钥-secret\x1b终点"
    req = request("edit_file", {"path": "a", "old_text": "旧内容", "new_text": content})
    approve = TerminalApproval(InputReader(StringIO("all\n2\n"), interactive=True), output,
        root="/project", secret="secret", page_lines=10)
    assert await approve(req, asyncio.Event()) == "once"
    shown = output.getvalue()
    assert "第99行" in shown and "终点" in shown and "旧内容" in shown
    assert "secret" not in shown and "\x1b" not in shown and "[已隐藏]" in shown
    assert "不同内容" in shown


@async_test
async def test_cancel_removes_fd_waiter_and_does_not_steal_next_question():
    from mewcode.permissions.terminal import InputReader
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "r")
    reader = InputReader(stream, interactive=True)
    cancel = asyncio.Event()
    try:
        pending = asyncio.create_task(reader.readline(cancel))
        await asyncio.sleep(0)
        cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 1)
        os.write(write_fd, "下一问\n".encode())
        assert await asyncio.wait_for(reader.readline(), 1) == "下一问\n"
    finally:
        stream.close()
        os.close(write_fd)


@async_test
async def test_pending_input_does_not_block_event_loop_and_preserves_buffered_lines():
    from mewcode.permissions.terminal import InputReader
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "r")
    reader = InputReader(stream)
    try:
        pending = asyncio.create_task(reader.readline())
        await asyncio.wait_for(asyncio.sleep(0.01), 1)
        assert not pending.done()
        os.write(write_fd, b"first\nsecond\n")
        assert await asyncio.wait_for(pending, 1) == "first\n"
        assert await reader.readline() == "second\n"
    finally:
        stream.close()
        os.close(write_fd)


@async_test
async def test_serial_prompts_keep_decisions_bound_to_request_ids():
    from mewcode.permissions.terminal import InputReader, TerminalApproval
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "r")
    output = StringIO()
    approval = TerminalApproval(InputReader(stream, interactive=True), output)
    first, second = request(), request()
    second.id = "request-2"
    one = asyncio.create_task(approval(first, asyncio.Event()))
    two = asyncio.create_task(approval(second, asyncio.Event()))
    try:
        await asyncio.sleep(0)
        assert "request-1" in output.getvalue() and "request-2" not in output.getvalue()
        os.write(write_fd, b"2\n")
        assert await asyncio.wait_for(one, 1) == "once"
        await asyncio.sleep(0)
        assert "request-2" in output.getvalue() and not two.done()
        os.write(write_fd, b"1\n")
        assert await asyncio.wait_for(two, 1) == "deny"
    finally:
        one.cancel()
        two.cancel()
        await asyncio.gather(one, two, return_exceptions=True)
        stream.close()
        os.close(write_fd)


@async_test
async def test_real_tty_cancel_flushes_unsubmitted_approval_before_next_prompt():
    from mewcode.permissions.terminal import InputReader
    master, slave = pty.openpty()
    stream = os.fdopen(slave, "r")
    reader = InputReader(stream)
    cancel = asyncio.Event()
    try:
        assert reader.interactive
        pending = asyncio.create_task(reader.readline(cancel))
        os.write(master, b"stale-approval")
        await asyncio.sleep(0.01)
        cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 1)
        os.write(master, b"next-question\n")
        assert await asyncio.wait_for(reader.readline(), 1) == "next-question\n"
    finally:
        stream.close()
        os.close(master)
