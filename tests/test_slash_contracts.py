"""跨会话、存储和终端的命令边界验收。"""
import asyncio
from dataclasses import replace
from io import StringIO
from types import SimpleNamespace

from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from conftest import ScriptedProvider, async_test, collect
from mewcode.commands import dispatch, parse_command
from mewcode.commands.adapter import SessionCommandContext
from mewcode.commands.builtins import build_registry
from mewcode.session import ChatSession
from mewcode.sessions import Journal
from mewcode.tools import default_registry
from mewcode.tools.base import ToolContext
from mewcode.tools.executor import ToolExecutor
from mewcode.permissions.runtime import PermissionManager
from mewcode.types import AgentEvent, TokenUsage, ToolCall
from test_agent_loop import answer, calls
from test_memory_store import note_value, write_note


def make_session(root, responses, *, resume=None):
    provider = ScriptedProvider(responses)
    config = SimpleNamespace(context_window=128000, max_output_tokens=8192, protocol='openai', model='test', api_key='dummy')
    executor = ToolExecutor(default_registry(), ToolContext(root),
        permissions=PermissionManager(root, mode='strict', user_path=root / 'user-permissions.yaml'))
    session = ChatSession(provider, executor=executor, config=config, persistent=True,
                          memory_enabled=False, user_root=root / 'user', resume=resume)
    return session, provider, config


def make_context(session, config):
    from mewcode.app import _Renderer
    from mewcode.terminal.controller import TerminalController
    from mewcode.permissions.terminal import InputReader
    registry = build_registry()
    terminal = TerminalController(InputReader(StringIO()), StringIO(), secret='', root=session.executor.context.root,
                                  on_interrupt=lambda: None, registry=registry)
    context = SessionCommandContext(registry, session, terminal, _Renderer(terminal.stream, ''), config)
    return context, terminal


@async_test
async def test_resumed_plan_do_keeps_history_and_does_not_restore_grants(tmp_path):
    path = tmp_path / 'target.txt'
    path.write_text('旧内容')
    first, _, config = make_session(tmp_path, [calls(ToolCall('read', 'read_file', '{"path":"target.txt"}')), answer('计划')])
    first.permissions.grants.remember('read_file', 'path', (str(path),), 'session')
    first.enter_plan()
    await collect(first.ask('计划'))
    identity, old = first.session_id, tuple(first.history)
    await first.aclose()
    second, provider, _ = make_session(tmp_path, [calls(ToolCall('edit', 'edit_file',
        '{"path":"target.txt","old_text":"旧内容","new_text":"新内容"}')), answer('未获授权')], resume=identity)
    try:
        context, terminal = make_context(second, config)
        await second.prepare_restore()
        await dispatch(parse_command('/do'), context.registry, context)
        assert second.mode == 'execute' and not provider.requests and tuple(second.history) == old
        assert not second.permissions.grants.session
        await collect(second.ask('执行修改'))
        assert path.read_text() == '旧内容'
        assert [m.tool_result for m in second.history if m.role == 'tool'][-1].error['code'] == 'permission_denied'
    finally:
        await second.aclose()


@async_test
async def test_session_queries_preserve_identity_and_surface_scan_diagnostics(tmp_path):
    old = Journal.create(tmp_path, 'openai', 'test')
    old.append('task_started', {'task_id': 'old-task', 'input': '旧任务标题', 'mode': 'execute'})
    old.close()
    with old.path.open('ab') as stream:
        stream.write(b'broken-tail')
    session, provider, config = make_session(tmp_path, [])
    context, terminal = make_context(session, config)
    try:
        identity, sequence = session.session_id, session.journal.seq
        session.memory_tasks['queued-task'] = 'queued'
        before = old.path.read_bytes()
        for command in ['/session', '/session list']:
            await dispatch(parse_command(command), context.registry, context)
        shown = terminal.output.getvalue()
        assert identity in shown and old.id in shown and '旧任务标题' in shown
        assert '活动中' in shown and '可恢复' in shown and '提示>' in shown
        assert session.session_id == identity and session.journal.seq == sequence
        assert session.memory_tasks == {'queued-task': 'queued'} and not provider.requests
        assert old.path.read_bytes() == before
    finally:
        await session.aclose()


@async_test
async def test_memory_two_scopes_same_identity_and_background_status_are_readonly(tmp_path):
    session, provider, config = make_session(tmp_path, [])
    context, terminal = make_context(session, config)
    try:
        identity = 'a' * 32
        write_note(tmp_path / '.mewcode' / 'memory', note_value(content='项目正文'))
        write_note(tmp_path / 'user' / 'memory', note_value(scope='user', category='user_preference',
            sources=[{'session_id': 's', 'task_id': 't', 'message_id': 'm', 'quote': '请始终使用中文'}], content='用户正文'))
        session.memory_tasks['task'] = 'running'
        for command in ['/memory', '/memory list', f'/memory show project {identity}', f'/memory show user {identity}']:
            await dispatch(parse_command(command), context.registry, context)
        shown = terminal.output.getvalue()
        assert '待处理 1' in shown and 'project/' + identity in shown and 'user/' + identity in shown
        assert '项目正文' in shown and '用户正文' in shown
        assert session.memory_tasks == {'task': 'running'} and not provider.requests
        assert not list(tmp_path.rglob('.lock'))
    finally:
        await session.aclose()


@async_test
async def test_clear_redraws_without_losing_session_state_or_input_history(tmp_path):
    from mewcode.terminal.input import EnhancedTerminal
    class Screen(DummyOutput):
        clears = 0
        def erase_screen(self): self.clears += 1
    screen = Screen()
    session, provider, config = make_session(tmp_path, [answer('记住的内容')])
    context, terminal = make_context(session, config)
    await collect(session.ask('记住标记'))
    session.enter_plan()
    session.permissions.grants.remember('read_file', 'path', (str(tmp_path / 'a'),), 'session')
    terminal.sync_session(session)
    terminal.state.update(AgentEvent('finished', run_id='recent', mode='plan', reason='model_done', usage=TokenUsage(3, 4)))
    with create_pipe_input() as pipe:
        backend = EnhancedTerminal(pipe, screen, on_interrupt=lambda: None, status=terminal.state.summary)
        terminal.backend = backend
        await backend.start()
        try:
            backend.remember('记住标记')
            identity, history, sequence = session.session_id, tuple(session.history), session.journal.seq
            grants = list(session.permissions.grants.session)
            pending = asyncio.create_task(backend.readline())
            await asyncio.sleep(.05)
            pipe.send_text('保留草稿')
            await asyncio.sleep(.05)
            cleared = screen.clears
            await context.clear_screen()
            assert screen.clears > cleared and backend.chat.text == '保留草稿'
            assert backend._history == ['记住标记'] and '[PLAN]' in backend._status_text()
            assert session.session_id == identity and tuple(session.history) == history and session.journal.seq == sequence
            assert list(session.permissions.grants.session) == grants and session.mode == 'plan'
            assert terminal.state.run_id == 'recent' and terminal.state._total_usage == TokenUsage(3, 4)
            assert len(provider.requests) == 1
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        finally:
            await terminal.close()
            await session.aclose()


@async_test
async def test_completion_menu_survives_resize_and_hides_hidden_aliases():
    from mewcode.commands import CommandRegistry
    from mewcode.terminal.input import EnhancedTerminal
    from prompt_toolkit.layout.menus import CompletionsMenuControl
    class Screen(DummyOutput):
        size = Size(rows=24, columns=80)
        def get_size(self): return self.size
    registry = build_registry()
    hidden = replace(registry.definitions[0], name='secret', aliases=('stealth',), hidden=True)
    registry = CommandRegistry((*registry.definitions, hidden)).freeze()
    screen = Screen()
    with create_pipe_input() as pipe:
        backend = EnhancedTerminal(pipe, screen, on_interrupt=lambda: None, registry=registry)
        await backend.start()
        try:
            pending = asyncio.create_task(backend.readline())
            await asyncio.sleep(.04)
            pipe.send_text('/s\t')
            await asyncio.sleep(.1)
            state = backend.chat.complete_state
            assert {item.text for item in state.completions} == {'/session', '/status'}
            for size in [Size(rows=8, columns=25), Size(rows=30, columns=110)]:
                screen.size = size
                backend.application.invalidate()
                await asyncio.sleep(.1)
                visible = backend.application.layout.visible_windows
                assert any(isinstance(window.content, CompletionsMenuControl) for window in visible)
                assert not pending.done()
            pipe.send_text('\r')
            await asyncio.sleep(.04)
            assert not pending.done()
            pipe.send_text('\r')
            assert await asyncio.wait_for(pending, 1) == '/session'
        finally:
            await backend.close()


@async_test
async def test_session_command_shows_new_and_restored_mode(tmp_path):
    session, _, config = make_session(tmp_path, [])
    identity = session.session_id
    context, _ = make_context(session, config)
    assert '[DEFAULT]' in context.session_text(listing=False)
    session.enter_plan()
    assert '[PLAN]' in context.session_text(listing=False)
    await session.aclose()
    restored, _, config = make_session(tmp_path, [], resume=identity)
    try:
        context, _ = make_context(restored, config)
        text = context.session_text(listing=False)
        assert '[PLAN]' in text and '显式恢复' in text
    finally:
        await restored.aclose()
