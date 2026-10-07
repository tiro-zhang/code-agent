"""用实际成员工具、存档与终端验证剩余规格边界。"""

import asyncio
import json
import os
from pathlib import Path
import shlex
import sys

from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from conftest import ScriptedProvider, async_test, collect
from test_agent_loop import answer, calls, tool
from test_session_persistence import age_records
from test_skills_catalog import entry
from test_team_commands import context_for
from test_team_lead_resume import reopen
from test_team_runtime_boundaries import create_team, dispatch, send, spawn, wait_for
from test_team_scenario_audit import noncode_task
from test_team_service import make_session
from mewcode.teams.backends import BackendProbe
from mewcode.types import AgentEvent
from mewcode.app import _idle_input
from mewcode.commands import dispatch as dispatch_command, parse_command
from mewcode.terminal.input import EnhancedTerminal


def results(runtime):
    return {message.tool_call_id: message.tool_result for message in runtime.session.history
            if message.role == 'tool'}


@async_test
async def test_simultaneous_real_member_commands_have_separate_journal_roots_and_call_ids(tmp_path):
    providers = [ScriptedProvider([]), ScriptedProvider([])]
    session = await create_team(tmp_path, providers, maximum=2)
    members = []
    try:
        for i, name in enumerate(('alice', 'bob')):
            runtime = await spawn(session, name)
            members.append(runtime)
            root = Path(runtime.member.workspace_root)
            # 各自真实命令进程报告启动；父测试观察双方存活后才释放，确保工具实际重叠。
            (root / 'probe.py').write_text('import os,pathlib,time\n'
                'pathlib.Path("started").write_text(str(os.getpid()))\n'
                'while not pathlib.Path("release").exists(): time.sleep(.01)\n'
                f'print("{name} 实际工具结果")\n')
            task = await noncode_task(session, runtime)
            providers[i].responses = iter([
                calls(tool(name + '-start', 'team_task', json.dumps({'action': 'start',
                    'task_id': task['task_id'], 'claim_id': task['claim_id']}))),
                calls(tool(name + '-command', 'execute_command', json.dumps({
                    'command': shlex.join([sys.executable, 'probe.py']), 'timeout_seconds': 10}))),
                answer(name + ' 已完成')])
            await dispatch(session, runtime, task)
        await wait_for(lambda: all((Path(runtime.member.workspace_root) / 'started').exists() for runtime in members))
        for runtime in members:
            os.kill(int((Path(runtime.member.workspace_root) / 'started').read_text()), 0)
            assert runtime.busy
        for runtime in members:
            (Path(runtime.member.workspace_root) / 'release').touch()
        await wait_for(lambda: all(len(provider.requests) == 3 for provider in providers)
                       and all(not runtime.busy for runtime in members))
        assert members[0].session.journal.path != members[1].session.journal.path
        for runtime, own, sibling in zip(members, ('alice', 'bob'), ('bob', 'alice')):
            result = results(runtime)[own + '-command']
            assert result.ok and result.data['exit_code'] == 0
            assert own + ' 实际工具结果' in result.data['stdout']
            records = [json.loads(line) for line in runtime.session.journal.path.read_text().splitlines()]
            assert records[0]['payload']['project_root'] == str(Path(runtime.member.workspace_root))
            assert any(record['kind'] == 'interaction_started' and own + '-command' in repr(record)
                       for record in records)
            persisted = [record['payload']['message'] for record in records if record['kind'] == 'tool_result'
                         and record['payload']['message']['tool_call_id'] == own + '-command']
            assert len(persisted) == 1 and own + ' 实际工具结果' in repr(persisted[0])
            assert sibling + '-command' not in repr(records)
    finally:
        for runtime in members:
            (Path(runtime.member.workspace_root) / 'release').touch()
        await session.aclose()


@async_test
async def test_member_actual_shared_skill_resource_and_nested_derivation_boundaries(tmp_path):
    provider = ScriptedProvider([])
    session = await create_team(tmp_path, [provider])
    try:
        runtime = await spawn(session, 'alice')
        package = Path(runtime.member.workspace_root) / '.mewcode' / 'skills' / 'shared'
        entry(package / 'SKILL.md', 'shared', body='共享成员 SOP：读取包内参考。')
        (package / 'reference.txt').write_text('成员共享 Skill 的实际包资源')
        isolated = entry(Path(runtime.member.workspace_root) / '.mewcode' / 'skills' / 'isolated.md', 'isolated')
        isolated.write_text(isolated.read_text().replace('mode: shared', 'mode: isolated'))
        provider.responses = iter([
            calls(tool('shared', 'load_skill', '{"name":"shared"}')),
            calls(tool('resource', 'load_skill', '{"name":"shared","resource":"reference.txt"}')),
            calls(tool('isolated', 'load_skill', '{"name":"isolated"}'),
                  tool('agent', 'agent', '{"type":"defined","role":"general","prompt":"不得派生"}'),
                  tool('member', 'team_member', '{"action":"spawn","name":"nested","role":"general"}')),
            answer('边界核查完成')])
        before = set(session.teams.store.load('alpha').members)
        await send(session, runtime)
        await wait_for(lambda: len(provider.requests) == 4 and not runtime.busy, runtime)
        actual = results(runtime)
        assert actual['shared'].ok and '共享成员 SOP' in repr(actual['shared'].data)
        assert actual['resource'].ok and '成员共享 Skill 的实际包资源' in repr(actual['resource'].data)
        for identity in ('isolated', 'agent', 'member'):
            assert not actual[identity].ok
            assert actual[identity].error['code'] == 'tool_not_allowed'
            assert actual[identity].error['details']['not_started'] is True
        assert '共享成员 SOP' in repr(provider.requests[1][0])
        assert set(session.teams.store.load('alpha').members) == before
        assert len(session.teams.runners) == 1 and not runtime.session.tasks.records
    finally:
        await session.aclose()


@async_test
async def test_long_paused_member_beyond_ttl_keeps_history_and_actual_snapshot_reminder(tmp_path):
    first = ScriptedProvider([answer('61 天前的成员结论')])
    session = await create_team(tmp_path, [first])
    runtime = await spawn(session, 'alice')
    await send(session, runtime)
    await wait_for(lambda: len(first.requests) == 1 and not runtime.busy, runtime)
    member = session.teams.store.load('alpha').members[runtime.member.member_id]
    path = runtime.session.journal.path
    await session.aclose()
    age_records(path, 61)
    fresh = reopen(session)
    next_provider = ScriptedProvider([answer('恢复后重新核查')])
    fresh.provider_factory = lambda config: next_provider
    try:
        await fresh.teams.control({'action': 'resume', 'name': 'alpha'})
        assert not fresh.teams.runners and not next_provider.requests
        await collect(fresh.ask('明确继续长期目标'))
        await fresh.teams.message({'action': 'send', 'recipient': 'alice', 'body': '恢复后重新核查当前文件'})
        continued = fresh.teams.runners[member.member_id]
        await wait_for(lambda: len(next_provider.requests) == 1 and not continued.busy, continued)
        submitted = repr(next_provider.requests[0][0])
        assert '61 天前的成员结论' in submitted
        assert '距今 61 天' in submitted
        assert '涉及当前文件时请重新读取，历史内容仅为当时快照' in submitted
        actual = fresh.teams.store.load('alpha').members[member.member_id]
        assert (actual.session_ref, actual.workspace_root, actual.branch) == (
            member.session_ref, member.workspace_root, member.branch)
    finally:
        await fresh.aclose()


def connect_terminal(session):
    context, output = context_for(session)
    notices = []
    def notify(notification):
        notices.append(notification)
        context.terminal.show(context.renderer, AgentEvent('team_update', purpose='team',
            text=notification['text'], phase=notification.get('status', '')), maintenance=True)
    session._notify = notify
    return context, output, notices


@async_test
async def test_exhausted_lead_budget_preserves_real_member_notice_in_status_and_terminal(tmp_path):
    session = make_session(tmp_path)
    session.agent.max_iterations = 1
    session.provider.responses = iter([answer('原目标预算耗尽')])
    member_provider = ScriptedProvider([])
    session.provider_factory = lambda config: member_provider
    context, output, notices = connect_terminal(session)
    try:
        await session.teams.control({'action': 'create', 'name': 'alpha'})
        await session.teams.control({'action': 'goal', 'description': '单轮目标'})
        await collect(session.ask('使用唯一一轮'))
        parent = session.teams.goal_parent
        assert parent.budget.remaining == 0 and session.next_parent() is None
        member = await session.teams.member({'action': 'spawn', 'name': 'alice', 'role': 'general', 'backend': 'inprocess'})
        runtime = session.teams.runners[member['member_id']]
        lead_id = session.teams.store.load('alpha').lead_id
        member_provider.responses = iter([calls(tool('lead-notice', 'team_message',
            json.dumps({'action': 'send', 'recipient': lead_id, 'body': '成员实际完成'}))),
            answer('成员实际完成')])
        await send(session, runtime)
        await wait_for(lambda: len(member_provider.requests) == 2 and not runtime.busy, runtime)
        await wait_for(lambda: bool(session.teams.pending))
        status = await session.teams.control({'action': 'status'})
        saved = [message for message in status['messages'] if message['sender_id'] == member['member_id']]
        assert saved and all(message['saved'] for message in saved)
        shown = await context.team_text('status')
        assert '成员实际完成' in shown and '已保存' in shown
        assert '"remaining": 0' in session.teams.lead_notice()
        assert any('idle' in notification['text'] for notification in notices)
        assert '团队>' in output.getvalue() and 'idle' in output.getvalue()
        await asyncio.sleep(.3)
        assert session.next_parent() is None and session.teams.goal_parent is parent
        assert len(session.provider.requests) == 1 and parent.budget.used == 1
    finally:
        await session.aclose()


@async_test
async def test_service_auto_fallback_notice_reaches_terminal_before_member_start(tmp_path):
    provider = ScriptedProvider([])
    session = await create_team(tmp_path, [provider])
    context, output, notices = connect_terminal(session)
    class UnavailableTmux:
        probed = False
        async def probe(self):
            return BackendProbe('tmux', False, '没有本地 tmux server 权限')
        async def close(self):
            return {'unknown': [], 'errors': []}
    session.teams.tmux = UnavailableTmux()
    before = (context.terminal.state.phase, context.terminal.state.iteration)
    try:
        member = await session.teams.member({'action': 'spawn', 'name': 'alice', 'role': 'boundary', 'backend': 'auto'})
        assert member['backend'] == 'inprocess' and not provider.requests
        fallback = [notice['text'] for notice in notices if 'tmux 不可用' in notice['text']]
        assert len(fallback) == 1
        assert '没有本地 tmux server 权限' in fallback[0]
        assert '同进程独立协程' in fallback[0] and '不提供文件系统沙箱' in fallback[0]
        assert '团队>' in output.getvalue() and fallback[0] in output.getvalue()
        assert (context.terminal.state.phase, context.terminal.state.iteration) == before
    finally:
        await session.aclose()


@async_test
async def test_submitted_team_control_wins_real_mail_wake_and_keeps_notice(tmp_path, monkeypatch):
    session = await create_team(tmp_path, [ScriptedProvider([])])
    runtime = await spawn(session, 'alice')
    session.provider.responses = iter([answer('等待成员消息')])
    await collect(session.ask('先激活原目标'))
    context, output, notices = connect_terminal(session)
    terminal = context.terminal
    # 令已提交的输入 Future 保持可观察，阻止外层 readline 抢先结束竞争。
    submitted, release = asyncio.Event(), asyncio.Event()
    original_wait = EnhancedTerminal._wait
    async def delayed_wait(backend, cancel):
        result = await original_wait(backend, cancel)
        submitted.set()
        await release.wait()
        return result
    monkeypatch.setattr(EnhancedTerminal, '_wait', delayed_wait)
    with create_pipe_input() as pipe:
        backend = EnhancedTerminal(pipe, DummyOutput(), on_interrupt=lambda: None,
                                   active=terminal._active_lines, registry=context.registry)
        terminal.backend = backend
        await backend.start()
        pending = asyncio.create_task(_idle_input(terminal, session, asyncio.Event()))
        try:
            await wait_for(lambda: backend._pending is not None)
            pipe.send_text('/team status\r')
            await asyncio.wait_for(submitted.wait(), 2)
            team = session.teams.store.load('alpha')
            mail = await runtime.session.teams.inbox().send(runtime.member.member_id, team.lead_id,
                '控制命令竞争时保留的通知')
            await wait_for(lambda: mail.message_id in session.teams.pending)
            assert session.next_parent() is not None and terminal.input_submitted()
            assert not pending.done()
            release.set()
            question, parent = await asyncio.wait_for(pending, 2)
            assert question == '/team status' and parent is None
            assert (await dispatch_command(parse_command(question), context.registry, context)).kind == 'handled'
            assert mail.message_id not in session.teams.consumed_ids
            assert mail.message_id in session.teams.pending
            assert '控制命令竞争时保留的通知' in output.getvalue()
            assert len(session.provider.requests) == 1 and not terminal.approval_active
        finally:
            release.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await terminal.close()
            await session.aclose()


@async_test
async def test_unconfirmed_independent_process_pause_ui_retains_identity_and_blocks_resume(tmp_path):
    from dataclasses import replace
    from test_team_backends import fake_backend

    provider = ScriptedProvider([])
    session = await create_team(tmp_path, [provider])
    context, output, notices = connect_terminal(session)
    runtime = await spawn(session, 'alice')
    identity = runtime.member.member_id
    await session.teams.stop_member(identity)
    saved_member = session.teams.store.load('alpha').members[identity]
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', 'import time;time.sleep(30)')
    backend, runner, launch, observation = fake_backend(tmp_path)
    runner.pid = process.pid
    observation['value'] = replace(observation['value'], pid=process.pid)
    handle = await backend.start(launch)
    session.teams.tmux = backend
    session.teams.handles[identity] = handle
    def independent(team):
        team.members[identity].backend = 'tmux'
        team.members[identity].state = 'idle'
    await session.teams.store.update_team('alpha', independent)
    try:
        assert (await dispatch_command(parse_command('/team pause'), context.registry, context)).kind == 'handled'
        paused = session.teams.store.load('alpha')
        assert paused.status == 'needs_review' and paused.members[identity].state == 'needs_review'
        assert process.returncode is None and identity in session.teams.handles
        assert paused.members[identity].session_ref == saved_member.session_ref
        assert identity in output.getvalue() and '待核查' in output.getvalue()
        assert saved_member.generation in output.getvalue() and handle.pane in output.getvalue()
        assert 'backend tmux' in output.getvalue() and '已暂停' not in output.getvalue()
        assert handle.launch.nonce not in output.getvalue()
        fresh = reopen(session)
        try:
            import pytest
            with pytest.raises((OSError, ValueError, RuntimeError), match='核查|恢复'):
                await fresh.teams.control({'action': 'resume', 'name': 'alpha'})
            assert not fresh.teams.runners and not fresh.provider.requests and not provider.requests
        finally:
            await fresh.aclose()
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
        await session.aclose()


@async_test
async def test_actual_ordinary_child_cannot_forge_collaboration_or_write_private_mailbox(tmp_path):
    from test_parent_tasks import RoutedProvider
    from mewcode.session import ChatSession

    seed = make_session(tmp_path)
    await seed.aclose()
    provider = RoutedProvider([], [])
    session = ChatSession(provider, executor=seed.executor, config=seed.config,
                          user_root=seed.user_root, memory_enabled=False)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    await session.teams.control({'action': 'goal', 'description': '核查普通子 Agent 团队边界'})
    team = session.teams.store.load('alpha')
    from mewcode.teams.models import Member, new_id
    private_member = Member(new_id('member'), 'private', '私有邮箱测试', str(tmp_path), state='idle')
    await session.teams.store.register_member('alpha', private_member)
    message = await session.teams.inbox().send(team.lead_id, private_member.member_id, '必须保留的私有协议记录')
    path = session.teams.store.team_path('alpha') / 'inboxes' / (private_member.member_id + '.json')
    before = path.read_bytes()
    provider.main.responses = iter([calls(tool('delegate', 'agent', json.dumps({
        'type': 'defined', 'role': 'general', 'prompt': '检查团队路径，禁止修改', 'background': False}))),
        answer('普通子 Agent 已完成边界核查')])
    provider.child.responses = iter([calls(
        tool('forged-mail', 'team_message', json.dumps({'action': 'send', 'recipient': str(path), 'body': '伪造协议'})),
        tool('private-write', 'write_file', json.dumps({'path': str(path), 'content': '[]'}))),
        answer('报告实际限制')])
    try:
        await collect(session.ask('请用普通子 Agent 核查团队边界'))
        record = next(iter(session.tasks.records.values()))
        evidence = {item['call_id']: item['result'] for item in record.outcome.evidence}
        assert evidence['forged-mail']['error']['code'] == 'tool_not_allowed'
        assert evidence['forged-mail']['error']['details']['not_started']
        assert not evidence['private-write']['ok']
        assert evidence['private-write']['error']['code'] == 'permission_denied'
        assert evidence['private-write']['error']['details']['not_started']
        assert path.read_bytes() == before
        assert any(item.message_id == message.message_id for item in await session.teams.inbox().read(private_member.member_id))
    finally:
        await session.aclose()
