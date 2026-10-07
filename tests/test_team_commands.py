"""本地团队命令、空闲恢复和独立维护展示使用真实服务。"""

import asyncio
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from conftest import ScriptedProvider, async_test
from mewcode.app import run, _Renderer
from mewcode.commands import dispatch, parse_command
from mewcode.commands.adapter import SessionCommandContext
from mewcode.commands.builtins import build_registry
from mewcode.permissions.terminal import InputReader
from mewcode.terminal.controller import TerminalController
from mewcode.types import AgentEvent, TokenUsage
from test_app import config_file
from test_team_service import make_session
from test_team_integration import repository


def context_for(session):
    output = StringIO()
    registry = build_registry(session.skills.catalog)
    terminal = TerminalController(InputReader(StringIO()), output, secret=session.config.api_key,
        root=session.executor.context.root, on_interrupt=lambda: None, registry=registry)
    return SessionCommandContext(registry, session, terminal, _Renderer(output, session.config.api_key), session.config), output


@async_test
async def test_team_commands_create_inspect_pause_resume_use_real_service_without_model_or_member(tmp_path):
    session = make_session(tmp_path)
    context, output = context_for(session)
    try:
        before = tuple(session.history)
        for text in ['/team list', '/team status', '/team create alpha', '/team list', '/team status alpha']:
            assert (await dispatch(parse_command(text), context.registry, context)).kind == 'handled'
        assert session.team_scope is not None and session.team_scope.team_name == 'alpha'
        assert session.teams.store.load('alpha').status == 'active'
        assert not session.teams.runners and not session.teams.handles
        assert '未知命令' not in output.getvalue()
        assert 'alpha' in output.getvalue() and 'inprocess' in output.getvalue()
        await dispatch(parse_command('/team pause'), context.registry, context)
        assert session.team_scope is None
        assert session.teams.store.load('alpha').status == 'paused'
        await dispatch(parse_command('/team resume alpha'), context.registry, context)
        assert session.team_scope is not None
        assert tuple(session.history) == before and session.provider.requests == []
    finally:
        await session.aclose()


@async_test
async def test_team_local_plan_and_lead_gates_are_not_bypassed(tmp_path):
    session = make_session(tmp_path)
    context, output = context_for(session)
    try:
        session.enter_plan()
        await dispatch(parse_command('/team create alpha'), context.registry, context)
        assert not (session.user_root / 'teams' / 'alpha').exists()
        assert '禁止' in output.getvalue() or '不允许' in output.getvalue()
        session.enter_execute()
        await dispatch(parse_command('/team stop member-unknown'), context.registry, context)
        assert not session.provider.requests and session.team_scope is None
    finally:
        await session.aclose()


@async_test
async def test_team_stop_targets_actual_registered_member_and_keeps_result(tmp_path):
    from mewcode.teams.models import Member, new_id

    session = make_session(tmp_path)
    context, output = context_for(session)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    member = Member(new_id('member'), 'worker', '开发', str(tmp_path), state='idle')
    await session.teams.store.register_member('alpha', member)
    try:
        await dispatch(parse_command('/team stop ' + member.member_id), context.registry, context)
        actual = session.teams.store.load('alpha').members[member.member_id]
        assert actual.state == 'stopped'
        assert member.member_id in output.getvalue()
        assert not session.provider.requests
    finally:
        await session.aclose()


@async_test
@pytest.mark.parametrize('text', ['/team list alpha', '/team create', '/team resume', '/team pause alpha', '/team stop', '/team status alpha extra', '/team unknown'])
async def test_team_invalid_usage_never_mutates_registry_or_calls_model(tmp_path, text):
    session = make_session(tmp_path)
    context, output = context_for(session)
    try:
        await dispatch(parse_command(text), context.registry, context)
        assert '用法：/team' in output.getvalue()
        assert session.teams.store.list() == [] and not session.provider.requests
    finally:
        await session.aclose()


def test_team_spec_is_shared_by_help_and_completion():
    from mewcode.skills.catalog import SkillCatalog
    registry = build_registry(SkillCatalog(()))
    assert registry.find('team') is not None
    assert registry.completions('/te') == ['/team']
    assert set(registry.completions('/team ')) == {
        '/team list', '/team status', '/team create', '/team resume', '/team pause', '/team stop'}
    assert '/team list|status [name]|create <name>|resume <name>|pause|stop <member_id>' in registry.help_text(enhanced=True)
    assert '--team' in registry.help_text(enhanced=False)


def test_app_explicit_team_resume_idle_and_passes_absolute_config_before_start(tmp_path, monkeypatch):
    from mewcode.teams.store import TeamStore

    root = repository(tmp_path / 'repo')
    user = tmp_path / 'user'
    store = TeamStore(user / 'teams')

    async def prepare():
        await store.create('alpha', root)
        await store.pause('alpha')

    asyncio.run(prepare())
    monkeypatch.chdir(root)
    config = config_file(root)
    provider = ScriptedProvider([])
    observed = []
    from mewcode.session import ChatSession
    original = ChatSession.start

    async def start(session, **kwargs):
        observed.append((session.team_scope.team_name, session.config_path, bool(session.teams.runners), bool(session.teams.handles)))
        return await original(session, **kwargs)

    monkeypatch.setattr(ChatSession, 'start', start)
    output, errors = StringIO(), StringIO()
    code = run(config.name, stdin=StringIO('/team status\n/exit\n'), stdout=output, stderr=errors,
               provider_factory=lambda _: provider, persistent=False, memory_enabled=False, user_root=user, team='alpha')
    assert code == 0, errors.getvalue()
    assert observed == [('alpha', config.resolve(), False, False)]
    assert provider.requests == [] and provider.closed
    assert 'alpha' in output.getvalue() and '空闲' in output.getvalue()
    assert TeamStore(user / 'teams').load('alpha').status == 'paused'


def test_app_team_resume_failure_occurs_before_mcp_start(tmp_path, monkeypatch):
    root = repository(tmp_path / 'repo')
    monkeypatch.chdir(root)
    provider = ScriptedProvider([])
    errors = StringIO()

    async def forbidden(*args, **kwargs):
        raise AssertionError('团队恢复失败后不应启动 MCP')

    monkeypatch.setattr('mewcode.app.MCPManager.start', forbidden)
    code = run(config_file(root), stdin=StringIO('/exit\n'), stdout=StringIO(), stderr=errors,
        provider_factory=lambda _: provider, persistent=False, memory_enabled=False, user_root=tmp_path / 'user', team='unknown')
    assert code == 2 and '启动失败' in errors.getvalue()
    assert provider.closed and provider.requests == []


def test_app_programmatic_resume_and_team_are_exclusive_before_provider_creation(tmp_path):
    def forbidden(config):
        raise AssertionError('互斥参数失败时不应构造 Provider')

    errors = StringIO()
    code = run(config_file(tmp_path), stdin=StringIO(), stdout=StringIO(), stderr=errors,
               provider_factory=forbidden, resume='latest', team='alpha')
    assert code == 2 and '同时' in errors.getvalue()


@async_test
async def test_normal_team_notifications_visible_without_touching_draft_usage_or_authorization():
    from mewcode.terminal.input import EnhancedTerminal

    output = StringIO()
    terminal = TerminalController(InputReader(StringIO()), output, secret='private-key',
        root=Path.cwd(), on_interrupt=lambda: None, registry=build_registry())
    renderer = _Renderer(output, 'private-key')
    terminal.state.update(AgentEvent('progress', run_id='lead-run', phase='model', iteration=1))
    terminal.state.update(AgentEvent('usage', run_id='lead-run', usage=TokenUsage(3, 4)))
    before = (terminal.state.run_id, terminal.state._total_usage, terminal.state.iteration)
    with create_pipe_input() as pipe:
        backend = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None, registry=build_registry())
        terminal.backend = backend
        await backend.start()
        try:
            from prompt_toolkit.document import Document
            terminal.set_phase('approval')
            terminal.approval_active = True
            backend.chat.set_document(Document('保留未提交草稿'), bypass_readonly=True)
            for text in ['worker · backend inprocess · idle', 'worker · awaiting_approval · private-key\x1b[31m']:
                terminal.show(renderer, AgentEvent('team_update', text=text, purpose='team', phase='idle'), maintenance=True)
            shown = output.getvalue()
            assert '团队>' in shown and 'inprocess' in shown and 'idle' in shown and 'awaiting_approval' in shown
            assert 'private-key' not in shown and '\x1b' not in shown
            assert backend.chat.text == '保留未提交草稿' and terminal.phase == 'approval'
            assert terminal.approval_active and terminal.state.phase == 'approval'
            assert (terminal.state.run_id, terminal.state._total_usage, terminal.state.iteration) == before
        finally:
            await terminal.close()


@async_test
async def test_team_notice_is_deferred_beside_real_approval_and_never_answers_it():
    from mewcode.terminal.input import EnhancedTerminal
    from test_terminal_input import req

    output = StringIO()
    terminal = TerminalController(InputReader(StringIO()), output, secret='private-key',
        root=Path('/project'), on_interrupt=lambda: None, registry=build_registry())
    renderer = _Renderer(terminal.stream, 'private-key')
    with create_pipe_input() as pipe:
        backend = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None, active=terminal._active_lines)
        terminal.backend = backend
        await backend.start()
        try:
            terminal.set_phase('running')
            pending = asyncio.create_task(terminal.approve(req(), asyncio.Event()))
            await asyncio.sleep(.05)
            pipe.send_text('2')
            await asyncio.sleep(.05)
            terminal.show(renderer, AgentEvent('team_update', purpose='team',
                text='worker · backend tmux · idle · private-key', phase='idle'), maintenance=True)
            await asyncio.sleep(.03)
            assert not pending.done() and backend.answer.text == '2'
            assert terminal.approval_active and terminal.phase == 'approval'
            sidebar = '\n'.join(terminal._active_lines())
            assert '团队>' in sidebar and 'tmux' in sidebar and 'private-key' not in sidebar
            pipe.send_text('\r')
            assert await asyncio.wait_for(pending, 1) == 'once'
        finally:
            if 'pending' in locals() and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await terminal.close()


@async_test
async def test_team_status_shows_repository_plan_task_mailbox_and_integration_without_waking(tmp_path):
    from mewcode.teams.models import Member, new_id
    from mewcode.teams.mailbox import Mailbox
    from mewcode.teams.tasks import TaskBoard

    session = make_session(tmp_path)
    context, output = context_for(session)
    team = await session.teams.control({'action': 'create', 'name': 'alpha'})
    goal = await session.teams.control({'action': 'goal', 'description': '显示真实状态'})
    member = Member(new_id('member'), 'worker', '开发', str(tmp_path / 'member-root'),
                    state='awaiting_approval', require_plan_approval=True)
    await session.teams.store.register_member('alpha', member)
    board = TaskBoard(session.teams.store, 'alpha')
    first = await board.create(team['lead_id'], goal['goal_id'], '前置待接纳', code=False)
    first = await board.claim(member.member_id, first.task_id)
    downstream = await board.create(team['lead_id'], goal['goal_id'], '后置任务', depends_on=[first.task_id])
    def planning(team):
        target = team.members[member.member_id]
        target.task_id, target.claim_id = first.task_id, first.claim_id
        target.plan_id, target.plan_version = 'plan-visible', 3
    await session.teams.store.update_team('alpha', planning)
    message = await Mailbox(session.teams.store, 'alpha').send(team['lead_id'], member.name,
        '先审查接口，等待权限 key\x1b[31m', type='plan_decision', fields={'goal_id': goal['goal_id'],
        'task_id': first.task_id, 'claim_id': first.claim_id, 'plan_id': 'plan-visible',
        'plan_version': 3, 'approved': False})
    try:
        await dispatch(parse_command('/team status alpha'), context.registry, context)
        shown = output.getvalue()
        for text in ['仓库>', str(tmp_path), 'Lead>', team['lead_id'], '工作根>', member.workspace_root,
                     '计划>', 'plan-visible', '版本 3', '先审查接口，等待权限', '任务>',
                     downstream.task_id, '阻塞', '邮箱>', message.message_id, '已保存',
                     '唤醒未确认', '整合>', 'not_initialized']:
            assert text in shown, (text, shown)
        assert 'key' not in shown and '\x1b' not in shown
        assert not session.provider.requests and not session.teams.runners and not session.teams.handles
    finally:
        await session.teams.store.update_team('alpha', lambda team: setattr(team.members[member.member_id], 'state', 'idle'))
        await session.aclose()


@async_test
@pytest.mark.parametrize('code,state', [(False, 'completed'), (True, 'integrated'), (True, 'completed')])
async def test_team_status_does_not_report_satisfied_dependency_as_blocked(tmp_path, monkeypatch, code, state):
    session = make_session(tmp_path)
    context, _ = context_for(session)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    original = session.teams.control
    async def snapshot(arguments):
        result = await original(arguments)
        # 只控制展示输入，三种状态均来自任务板允许解除依赖的合同。
        result['tasks'] = [
            {'task_id': 'predecessor', 'title': '前置', 'state': state, 'code': code,
             'integrated_commit': 'a' * 40 if code else None, 'depends_on': []},
            {'task_id': 'downstream', 'title': '后置', 'state': 'pending', 'depends_on': ['predecessor']},
        ]
        return result
    monkeypatch.setattr(session.teams, 'control', snapshot)
    try:
        shown = await context.team_text('status', 'alpha')
        downstream = next(line for line in shown.splitlines() if line.startswith('任务> downstream'))
        assert '阻塞／理由 无' in downstream
        assert '前置成果未接纳' not in downstream
    finally:
        await session.aclose()
