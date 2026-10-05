"""Hook 授权归属、控制指令及独立通知的终端合同。"""

import asyncio
from io import StringIO
import signal
from types import SimpleNamespace

import pytest

from conftest import async_test, ScriptedProvider
from mewcode.app import _Renderer
from mewcode.commands import dispatch, parse_command
from mewcode.commands.adapter import SessionCommandContext
from mewcode.commands.builtins import build_registry
from mewcode.permissions.terminal import InputReader
from mewcode.terminal.approval import ApprovalView
from mewcode.terminal.controller import TerminalController
from test_terminal_controller import request
from test_hook_lifecycle import configure, observe
from test_hook_actions import script
from test_skills_session import session
from test_app import config_file


@pytest.mark.parametrize('phase', ['starting', 'idle', 'summary', 'running', 'cancelling', 'closing'])
@async_test
async def test_hook_approval_restores_owning_phase_without_task(phase):
    terminal = TerminalController(InputReader(StringIO('2\n'), interactive=True), StringIO(),
                                  root='/project', secret='', on_interrupt=lambda: None)
    terminal.set_phase(phase)
    details = terminal.state.details
    item = request('hook')
    item.origin = ('/project/.mewcode/hooks.yaml#hooks[2]', 'session.start')
    try:
        assert await terminal.approve(item, asyncio.Event()) == 'once'
        assert terminal.phase == terminal.state.phase == phase
        assert terminal.state.details is details
        assert not terminal.state._tools
    finally:
        await terminal.close()


def test_hook_approval_source_is_safe_immutable_and_visible_on_every_page():
    item = request('hook')
    item.origin = ['hooks.yaml#hooks[2]\x1b[31mprivate', 'session.start']
    view = ApprovalView(item, root='/project', secret='private')
    item.origin[0] = 'changed'
    for text in (view.summary(), view.identity()):
        assert 'Hook' in text and 'hooks[2]' in text and 'session.start' in text
        assert 'changed' not in text and 'private' not in text and '\x1b' not in text


@async_test
async def test_mode_command_awaits_cleanup_and_hooks_with_control_cancel(tmp_path):
    configure(tmp_path, [{'event': 'mode.changed', 'action': {'type': 'prompt', 'text': 'mode-marker'}}])
    chat = session(tmp_path, ScriptedProvider([]))
    events = observe(chat)
    terminal = TerminalController(InputReader(StringIO()), StringIO(), root=tmp_path, secret='', on_interrupt=lambda: None)
    context = SessionCommandContext(build_registry(), chat, terminal, _Renderer(terminal.stream, ''), SimpleNamespace(api_key=''))
    context.cancel_event = asyncio.Event()
    await dispatch(parse_command('/plan'), context.registry, context)
    assert chat.mode == 'plan' and len(chat.hooks.prompts.snapshot()) == 1
    context.cancel_event.set()
    await dispatch(parse_command('/do'), context.registry, context)
    assert chat.mode == 'plan'
    assert len([item for item in events if item['event'] == 'mode.changed']) == 1
    assert not chat.provider.requests
    await chat.aclose()
    await terminal.close()


def test_app_starts_hooks_before_input_and_closes_in_closing_phase(tmp_path, monkeypatch):
    from conftest import run_work_app
    from mewcode.hooks.runtime import HookRuntime
    monkeypatch.chdir(tmp_path)
    configure(tmp_path, [{'event': 'session.start', 'action': {'type': 'command', 'command': script(tmp_path, "import pathlib;pathlib.Path('startup').touch()")}}])
    seen = []
    original_dispatch = HookRuntime.dispatch
    original_phase = TerminalController.set_phase
    phase = ['starting']
    def record_phase(self, value):
        phase[0] = value
        return original_phase(self, value)
    async def record(self, event, **options):
        seen.append((event.event, phase[0]))
        return await original_dispatch(self, event, **options)
    monkeypatch.setattr(HookRuntime, 'dispatch', record)
    monkeypatch.setattr(TerminalController, 'set_phase', record_phase)
    provider = ScriptedProvider([])
    assert run_work_app(config_file(tmp_path), stdin=StringIO('/exit\n'), stdout=StringIO(),
        permission_mode='bypass', provider_factory=lambda c: provider) == 0
    assert (tmp_path / 'startup').exists()
    assert seen == [('session.start', 'starting'), ('session.end', 'closing')]
    assert provider.closed and not provider.requests


def test_control_hook_sigint_cancels_control_not_next_input(tmp_path, monkeypatch):
    from conftest import run_work_app
    monkeypatch.chdir(tmp_path)
    configure(tmp_path, [{'event': 'mode.changed', 'action': {'type': 'command', 'command': script(tmp_path, "import pathlib;pathlib.Path('should-not-run').touch()")}}])
    provider = ScriptedProvider([])
    approvals = []
    async def approve(item, cancel):
        approvals.append(item.origin)
        signal.raise_signal(signal.SIGINT)
        await asyncio.sleep(0)
        assert cancel.is_set()
        return 'once'
    output = StringIO()
    assert run_work_app(config_file(tmp_path), stdin=StringIO('/plan\n/do\n/status\n/exit\n'),
        stdout=output, provider_factory=lambda c: provider, approval_responder=approve) == 0
    assert len(approvals) == 1 and approvals[0][1] == 'mode.changed'
    assert '项目' in output.getvalue() and '正在停止' in output.getvalue()
    assert not provider.requests and not (tmp_path / 'should-not-run').exists()


@async_test
async def test_enhanced_control_hook_keeps_input_closed_after_approval_until_cleanup(tmp_path, monkeypatch):
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from mewcode.app import _run
    from mewcode.config import load_config
    monkeypatch.chdir(tmp_path)
    configure(tmp_path, [{'event': 'mode.changed', 'action': {'type': 'command', 'command': script(tmp_path,
        "import pathlib,time;pathlib.Path('running').touch();time.sleep(2);pathlib.Path('leaked').touch()")}}])
    provider = ScriptedProvider([])
    owner = []
    start = TerminalController.start
    async def capture(self):
        owner.append(self)
        await start(self)
    monkeypatch.setattr(TerminalController, 'start', capture)
    monkeypatch.setattr(TerminalController, '_can_enhance', lambda self: True)
    async def wait_for(check):
        async def poll():
            while not check():
                await asyncio.sleep(.01)
        await asyncio.wait_for(poll(), 3)
    with create_pipe_input() as pipe:
        monkeypatch.setattr('mewcode.terminal.controller.create_input', lambda *a, **k: pipe)
        monkeypatch.setattr('mewcode.terminal.controller.create_output', lambda *a, **k: DummyOutput())
        task = asyncio.create_task(_run(load_config(config_file(tmp_path)), StringIO(), StringIO(), StringIO(),
            lambda c: provider, persistent=False, memory_enabled=False, user_root=tmp_path / 'user'))
        try:
            await wait_for(lambda: owner and owner[0].backend and owner[0].backend.phase == 'idle')
            terminal = owner[0]
            details = terminal.state.details
            pipe.send_text('/plan\r')
            await wait_for(lambda: terminal.state.mode == 'plan' and terminal.backend.phase == 'idle')
            pipe.send_text('/do\r')
            await wait_for(lambda: terminal.approval_active)
            pipe.send_text('2\r')
            await wait_for(lambda: (tmp_path / 'running').exists())
            assert terminal.backend.phase != 'idle' and terminal.phase == terminal.backend.phase
            pipe.send_text('discarded-draft\x03')
            await wait_for(lambda: terminal.backend.phase == 'idle')
            assert terminal.state.details is details and terminal.backend.chat.text == ''
            assert not (tmp_path / 'leaked').exists() and not provider.requests
            pipe.send_text('/exit\r')
            assert await asyncio.wait_for(task, 3) == 0
        finally:
            if not task.done():
                pipe.send_text('\x03')
                await asyncio.sleep(.3)
                pipe.send_text('/exit\r')
                await asyncio.wait_for(task, 5)
