"""用真实终端解析字节验证草稿和审批边界。"""

import asyncio
from io import StringIO
from types import SimpleNamespace

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from conftest import async_test


def req(identity="review-1"):
    return SimpleNamespace(id=identity, tool="write_file", arguments={"path": "a", "content": "hello"},
                           targets=("/project/a",), reason="需要批准", mode="default")


async def tick():
    await asyncio.sleep(.04)


@async_test
async def test_bracketed_paste_never_submits_and_preserves_whitespace():
    from mewcode.terminal.input import EnhancedTerminal
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None)
        await terminal.start()
        try:
            pending = asyncio.create_task(terminal.readline())
            await tick()
            pipe.send_text("\x1b[200~  第一行\r\n\r\n    /exit\r\n\x1b[201~")
            await tick()
            assert not pending.done()
            pipe.send_text("\r")
            assert await asyncio.wait_for(pending, 1) == "  第一行\n\n    /exit\n"
            assert terminal.phase == "running"
        finally:
            await terminal.close()


@async_test
async def test_manual_newline_history_and_completion_edit_without_submit():
    from mewcode.terminal.input import EnhancedTerminal
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None)
        await terminal.start()
        try:
            one = asyncio.create_task(terminal.readline())
            await tick()
            pipe.send_text("首行\x1b\r尾行")
            await tick()
            assert not one.done() and terminal.chat.text == "首行\n尾行"
            pipe.send_text("\r")
            assert await asyncio.wait_for(one, 1) == "首行\n尾行"
            terminal.remember("首行\n尾行")
            two = asyncio.create_task(terminal.readline())
            await tick()
            pipe.send_text("\x1b[A")
            await tick()
            assert terminal.chat.text == "首行\n尾行" and not two.done()
            pipe.send_text("\x15/per\t")
            await tick()
            assert not two.done()
            pipe.send_text("\x03")
            two.cancel()
            await asyncio.gather(two, return_exceptions=True)
        finally:
            await terminal.close()


@async_test
async def test_paste_and_typeahead_cannot_approve_following_request():
    from mewcode.terminal.input import EnhancedTerminal
    from mewcode.terminal.approval import ApprovalView
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None)
        await terminal.start()
        try:
            first = asyncio.create_task(terminal.approve(ApprovalView(req(), root="/project"), asyncio.Event()))
            await tick()
            pipe.send_text("\x1b[200~2\n3\n\x1b[201~")
            await tick()
            assert not first.done()
            pipe.send_text("\r")
            await tick()
            assert not first.done()
            pipe.send_text("2\r3\r")
            assert await asyncio.wait_for(first, 1) == "once"
            second = asyncio.create_task(terminal.approve(ApprovalView(req("review-2"), root="/project"), asyncio.Event()))
            await tick()
            assert not second.done()
            pipe.send_text("1\r")
            assert await asyncio.wait_for(second, 1) == "deny"
        finally:
            await terminal.close()


@async_test
async def test_paste_started_while_running_is_discarded_after_approval_transition():
    from mewcode.terminal.input import EnhancedTerminal
    from mewcode.terminal.approval import ApprovalView
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None)
        await terminal.start()
        try:
            terminal.set_phase("running")
            pipe.send_text("\x1b[200~2")
            await tick()
            pending = asyncio.create_task(terminal.approve(ApprovalView(req(), root="/project"), asyncio.Event()))
            await tick()
            pipe.send_text("\x1b[201~")
            await tick()
            assert not pending.done() and terminal.answer.text == ""
            pipe.send_text("1\r")
            assert await asyncio.wait_for(pending, 1) == "deny"
        finally:
            await terminal.close()


@async_test
async def test_approval_cancel_and_eof_preserve_distinct_exit_meaning():
    from mewcode.terminal.input import EnhancedTerminal
    from mewcode.terminal.approval import ApprovalView
    cancel = asyncio.Event()
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=cancel.set)
        await terminal.start()
        try:
            pending = asyncio.create_task(terminal.approve(ApprovalView(req(), root="/project"), cancel))
            await tick()
            pipe.send_text("\x03")
            assert await asyncio.wait_for(pending, 1) == "deny"
            assert cancel.is_set() and not terminal.eof
            cancel.clear()
            pending = asyncio.create_task(terminal.approve(ApprovalView(req("next"), root="/project"), cancel))
            await tick()
            pipe.send_text("\x04")
            assert await asyncio.wait_for(pending, 1) == "deny"
            assert cancel.is_set() and terminal.eof
        finally:
            await terminal.close()


@async_test
async def test_tab_really_completes_and_history_navigation_keeps_multiline_cursor():
    from mewcode.terminal.input import EnhancedTerminal
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None)
        await terminal.start()
        try:
            pending = asyncio.create_task(terminal.readline())
            await tick()
            pipe.send_text('/permissions\t')
            await tick()
            assert terminal.chat.text == '/permissions' and not pending.done()
            pipe.send_text('\r')
            assert await asyncio.wait_for(pending, 1) == '/permissions'
            # 只有应用确认的普通消息写入历史，命令从未 remember。
            terminal.remember('早先消息')
            terminal.remember('首行\n尾行')
            pending = asyncio.create_task(terminal.readline())
            await tick()
            pipe.send_text('\x1b[A')
            await tick()
            assert terminal.chat.text == '首行\n尾行' and terminal.chat.document.on_last_line
            pipe.send_text('\x1b[A')
            await tick()
            assert terminal.chat.document.cursor_position_row == 0
            pipe.send_text('\x1b[B')
            await tick()
            assert terminal.chat.text == '首行\n尾行' and terminal.chat.document.on_last_line
            pipe.send_text('\x1b[A')
            await tick()
            assert terminal.chat.text == '首行\n尾行' and terminal.chat.document.cursor_position_row == 0
            pipe.send_text('\x1b[A')
            await tick()
            assert terminal.chat.text == '早先消息'
            pipe.send_text('修改\r')
            assert await asyncio.wait_for(pending, 1) == '早先消息修改'
        finally:
            await terminal.close()


def screen_text(terminal):
    screen = terminal.application.renderer._last_screen
    return '\n'.join(''.join(cell.char for _, cell in sorted(row.items()))
                     for _, row in sorted(screen.data_buffer.items())) if screen else ''


@async_test
async def test_actual_input_eof_during_running_cancels_without_waiting_for_readline():
    from mewcode.terminal.input import EnhancedTerminal
    cancel = asyncio.Event()
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=cancel.set)
        await terminal.start()
        terminal.set_phase('running')
        pipe.close()
        await tick()
        try:
            assert terminal.eof and cancel.is_set()
        finally:
            await terminal.close()


@async_test
async def test_small_review_pages_do_not_skip_lines_hidden_by_other_regions():
    from prompt_toolkit.data_structures import Size
    from mewcode.terminal.input import EnhancedTerminal
    class SmallOutput(DummyOutput):
        def get_size(self): return Size(rows=12, columns=80)
    class NumberedView:
        request_id = 'review'
        decisions = {'1': 'deny'}
        def page(self, section, index, *, width, height):
            total = (20 + height - 1) // height
            index = min(max(0,index),total-1)
            return '\n'.join(f'REVIEW-LINE-{n:02}' for n in range(index*height,min(20,(index+1)*height))), index, total
    with create_pipe_input() as pipe:
        terminal=EnhancedTerminal(pipe, SmallOutput(), on_interrupt=lambda:None,
                                  status=lambda:'status1\nstatus2\nstatus3', active=lambda:['tool']*4)
        await terminal.start()
        try:
            pending=asyncio.create_task(terminal.approve(NumberedView(),asyncio.Event()))
            seen=set()
            for _ in range(22):
                await tick()
                import re
                seen.update(re.findall(r'REVIEW-LINE-\d\d',screen_text(terminal)))
                pipe.send_text('next\r')
            assert seen=={f'REVIEW-LINE-{n:02}' for n in range(20)}
            pipe.send_text('1\r')
            assert await asyncio.wait_for(pending,1)=='deny'
        finally:
            await terminal.close()


@async_test
async def test_paste_opener_split_across_phase_does_not_turn_payload_into_decisions():
    from mewcode.terminal.input import EnhancedTerminal
    from mewcode.terminal.approval import ApprovalView
    with create_pipe_input() as pipe:
        terminal=EnhancedTerminal(pipe,DummyOutput(),on_interrupt=lambda:None)
        await terminal.start()
        try:
            terminal.set_phase('running')
            pipe.send_text('\x1b[20')
            await tick()
            pending=asyncio.create_task(terminal.approve(ApprovalView(req(),root='/project'),asyncio.Event()))
            await tick()
            pipe.send_text('0~2\n3\n\x1b[201~')
            await tick()
            assert not pending.done() and terminal.answer.text==''
            pipe.send_text('1\r')
            assert await asyncio.wait_for(pending,1)=='deny'
        finally:
            await terminal.close()


@pytest.mark.parametrize('prefix', ['\x1b[', '\x1b[2', '\x1b[20', '\x1b[200'])
@async_test
async def test_partial_paste_opener_survives_input_flush_timeout(prefix):
    from mewcode.terminal.input import EnhancedTerminal
    from mewcode.terminal.approval import ApprovalView
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None)
        terminal.application.ttimeoutlen = .01
        await terminal.start()
        try:
            terminal.set_phase('running')
            pipe.send_text(prefix)
            await tick()
            pending = asyncio.create_task(terminal.approve(ApprovalView(req(), root='/project'), asyncio.Event()))
            await tick()
            pipe.send_text('\x1b[200~'[len(prefix):] + '2\n3\n\x1b[201~')
            await tick()
            assert not pending.done() and terminal.answer.text == ''
            pipe.send_text('1\r')
            assert await asyncio.wait_for(pending, 1) == 'deny'
        finally:
            await terminal.close()


@async_test
async def test_stale_paste_and_fresh_paste_in_same_read_keep_separate_generations():
    from mewcode.terminal.input import EnhancedTerminal
    from mewcode.terminal.approval import ApprovalView
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None)
        await terminal.start()
        try:
            terminal.set_phase('running')
            pipe.send_text('\x1b')
            await tick()
            pending = asyncio.create_task(terminal.approve(ApprovalView(req(), root='/project'), asyncio.Event()))
            await tick()
            # 阶段切换后，即使超时要求 flush，也不能把旧开头拆成普通按键。
            assert pipe.flush_keys() == []
            pipe.send_text('[200~2\n4\n\x1b[201~\x1b[200~3\x1b[201~')
            await tick()
            assert not pending.done() and terminal.answer.text == '3'
            pipe.send_text('\r')
            assert await asyncio.wait_for(pending, 1) == 'session'
        finally:
            await terminal.close()


@async_test
async def test_paste_guard_preserves_escape_timeout_and_restores_parser_on_close():
    from mewcode.terminal.input import EnhancedTerminal
    with create_pipe_input() as pipe:
        parser = pipe.vt100_parser
        original = (parser.feed, parser.flush, parser.feed_key_callback)
        terminal = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None)
        terminal.application.ttimeoutlen = .01
        await terminal.start()
        try:
            pending = asyncio.create_task(terminal.readline())
            await tick()
            pipe.send_text('\x1b')
            await tick()
            pipe.send_text('hello\r')
            assert await asyncio.wait_for(pending, 1) == 'hello'
        finally:
            await terminal.close()
        assert (parser.feed, parser.flush, parser.feed_key_callback) == original


@pytest.mark.parametrize('rows', [12, 24])
@async_test
async def test_review_keeps_side_result_visible_after_long_active_tool(rows):
    from prompt_toolkit.data_structures import Size
    from mewcode.terminal.input import EnhancedTerminal
    from mewcode.terminal.approval import ApprovalView
    class ReviewOutput(DummyOutput):
        def get_size(self): return Size(rows=rows, columns=80)
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, ReviewOutput(), on_interrupt=lambda: None,
                                    active=lambda: ['当前等待授权' * 40, '旁路结果 UNIQUE-TIMEOUT'])
        await terminal.start()
        try:
            pending = asyncio.create_task(terminal.approve(ApprovalView(req(), root='/project'), asyncio.Event()))
            await tick()
            assert 'UNIQUE-TIMEOUT' in screen_text(terminal)
            assert not pending.done()
            pipe.send_text('1\r')
            assert await asyncio.wait_for(pending, 1) == 'deny'
        finally:
            await terminal.close()


@async_test
async def test_tiny_review_keeps_priority_side_result_and_all_review_lines_visible():
    from prompt_toolkit.data_structures import Size
    from mewcode.terminal.input import EnhancedTerminal
    class TinyOutput(DummyOutput):
        def get_size(self): return Size(rows=6, columns=40)
    class NumberedView:
        request_id = 'review'
        decisions = {'1': 'deny'}
        def page(self, section, index, *, width, height):
            total = (20 + height - 1) // height
            index = min(max(0, index), total - 1)
            return '\n'.join(f'REVIEW-LINE-{n:02}' for n in range(index * height, min(20, (index + 1) * height))), index, total
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, TinyOutput(), on_interrupt=lambda: None,
                                    active=lambda: ['旁路结果 UNIQUE-TIMEOUT', '当前等待授权'])
        await terminal.start()
        try:
            pending = asyncio.create_task(terminal.approve(NumberedView(), asyncio.Event()))
            seen = set()
            for page in range(5):
                # 等待目标页真正绘制，固定 40ms 会在高负载下跨过一页。
                expected = f'REVIEW-LINE-{page * 4:02}'
                deadline = asyncio.get_running_loop().time() + 2
                while expected not in screen_text(terminal):
                    assert asyncio.get_running_loop().time() < deadline, screen_text(terminal)
                    await asyncio.sleep(.01)
                import re
                rendered = screen_text(terminal)
                assert 'UNIQUE-TIMEOUT' in rendered
                seen.update(re.findall(r'REVIEW-LINE-\d\d', rendered))
                pipe.send_text('next\r')
            assert seen == {f'REVIEW-LINE-{n:02}' for n in range(20)}
            pipe.send_text('1\r')
            assert await asyncio.wait_for(pending, 1) == 'deny'
        finally:
            await terminal.close()


@async_test
async def test_narrow_review_header_shows_page_before_long_request_id():
    from prompt_toolkit.data_structures import Size
    from mewcode.terminal.input import EnhancedTerminal
    from mewcode.terminal.approval import ApprovalView
    class NarrowOutput(DummyOutput):
        def get_size(self): return Size(rows=15, columns=45)
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, NarrowOutput(), on_interrupt=lambda: None)
        await terminal.start()
        try:
            pending = asyncio.create_task(terminal.approve(
                ApprovalView(req('request-' + 'a' * 64), root='/project'), asyncio.Event()))
            await tick()
            pipe.send_text('content\r')
            deadline = asyncio.get_running_loop().time() + 2
            while '审阅 1/1 页 · content' not in screen_text(terminal):
                assert asyncio.get_running_loop().time() < deadline, screen_text(terminal)
                await asyncio.sleep(.01)
            assert '审阅 1/1 页 · content' in screen_text(terminal)
            assert not pending.done()
            pipe.send_text('1\r')
            assert await asyncio.wait_for(pending, 1) == 'deny'
        finally:
            await terminal.close()


@async_test
async def test_completion_menu_enter_confirms_then_submits_and_escape_dismisses():
    from mewcode.terminal.input import EnhancedTerminal
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None)
        await terminal.start()
        try:
            pending = asyncio.create_task(terminal.readline())
            await asyncio.sleep(.03)
            pipe.send_text('/per\t')
            await asyncio.sleep(.1)
            assert len(terminal.chat.complete_state.completions) == 2
            pipe.send_text('\r')
            await asyncio.sleep(.05)
            assert not pending.done() and terminal.chat.complete_state is None
            assert terminal.chat.text == '/permission'
            pipe.send_text('\r')
            assert await asyncio.wait_for(pending, 1) == '/permission'
            pending = asyncio.create_task(terminal.readline())
            await asyncio.sleep(.03)
            pipe.send_text('/per\t')
            await asyncio.sleep(.1)
            pipe.send_text('\x1b')
            await asyncio.sleep(.6)
            assert terminal.chat.complete_state is None and not pending.done()
            assert terminal.chat.text == '/per'
            pipe.send_text('\x15/HE\t')
            await asyncio.sleep(.1)
            assert terminal.chat.text == '/help' and terminal.chat.complete_state is None
            pipe.send_text('\r')
            assert await asyncio.wait_for(pending, 1) == '/help'
        finally:
            await terminal.close()
