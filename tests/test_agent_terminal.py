"""增强输入的后台切换和自动接续草稿保护。"""

import asyncio
from io import StringIO
from types import SimpleNamespace
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from conftest import async_test
from test_terminal_input import tick


@async_test
async def test_ctrl_b_dispatches_background_only_without_submitting_draft():
    from mewcode.terminal.input import EnhancedTerminal
    called = []
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None,
                                    on_background=lambda: called.append(terminal.phase))
        await terminal.start()
        try:
            terminal.set_phase("running")
            pipe.send_text("\x02")
            await tick()
            assert called == ["running"]
            terminal.set_phase("approval")
            pipe.send_text("\x02")
            await tick()
            assert called[-1] == "approval"
        finally:
            await terminal.close()


@async_test
async def test_auto_resume_suspends_reader_then_restores_complete_draft():
    from mewcode.terminal.input import EnhancedTerminal
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None)
        await terminal.start()
        try:
            read = asyncio.create_task(terminal.readline())
            await tick()
            pipe.send_text("\x1b[200~草稿\n第二行\x1b[201~")
            await tick()
            terminal.save_draft()
            read.cancel()
            await asyncio.gather(read, return_exceptions=True)
            terminal.set_phase("running")
            assert terminal.chat.text == ""
            resumed = asyncio.create_task(terminal.readline())
            await tick()
            assert terminal.chat.text == "草稿\n第二行"
            pipe.send_text("\r")
            assert await asyncio.wait_for(resumed, 1) == "草稿\n第二行"
        finally:
            await terminal.close()


@async_test
async def test_submitted_input_wins_when_backend_future_precedes_wrapper_task(tmp_path):
    from mewcode.app import _idle_input
    from mewcode.permissions.terminal import InputReader
    from mewcode.terminal.controller import TerminalController
    from mewcode.terminal.input import EnhancedTerminal
    with create_pipe_input() as pipe:
        backend = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None)
        terminal = TerminalController(InputReader(StringIO()), StringIO(), secret='', root=tmp_path,
                                      on_interrupt=lambda: None, allow_enhanced=False)
        terminal.backend = backend
        changed = asyncio.Event()
        ready = False
        session = SimpleNamespace(tasks=SimpleNamespace(changed=changed), next_parent=lambda: 'parent' if ready else None)
        await backend.start()
        work = asyncio.create_task(_idle_input(terminal, session, asyncio.Event()))
        try:
            await tick()
            backend._pending.set_result('/plan')
            ready = True
            changed.set()
            assert await asyncio.wait_for(work, 1) == ('/plan', None)
        finally:
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            await terminal.close()


def test_parent_final_status_is_visible_separately_from_segment():
    from mewcode.terminal.state import TerminalState
    from mewcode.terminal.projection import TerminalProjection
    from mewcode.types import AgentEvent, TokenUsage
    state = TerminalState('')
    state.begin_task(mode='execute', permission_mode='default', max_iterations=20)
    projection = TerminalProjection(state)
    projection.accept(AgentEvent('progress', run_id='segment', phase='model'))
    visible = projection.accept(AgentEvent('task_finished', run_id='parent', reason='model_done', usage=TokenUsage(30, 5, True)))
    assert len(visible) == 1 and '父任务结束' in visible[0].text
    assert 'parent' in visible[0].text and '30' in visible[0].text
    assert state.run_id == 'segment' and not state._finished
