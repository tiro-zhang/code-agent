"""实际成员运行链的并发、请求边界、门禁与持久失败回归。"""

import asyncio
from dataclasses import replace
import json
from pathlib import Path

import pytest

from conftest import ScriptedProvider, async_test
from test_agent_loop import answer, calls, tool
from test_team_service import make_session
from mewcode.agents.definitions import discover_roles
from mewcode.teams.mailbox import MessageError
from mewcode.types import Message, ProviderEvent, ToolCall


async def wait_for(predicate, runtime=None):
    async with asyncio.timeout(8):
        while not predicate():
            if runtime is not None and runtime.task.done():
                raise AssertionError((runtime.task.exception(), runtime.session.warnings))
            await asyncio.sleep(.01)


async def create_team(root, providers, *, maximum=4, queued=32, budget=20):
    session = make_session(root)
    session.config = replace(session.config, team_max_running=maximum, team_max_queued=queued)
    directory = root / '.mewcode' / 'agents'
    directory.mkdir(parents=True)
    (directory / 'boundary.md').write_text('---\nname: boundary\ndescription: 运行边界测试\n'
        f'max-iterations: {budget}\npermission-mode: bypass\n---\n核查真实工具和成员边界。\n')
    session.roles = discover_roles(root, user_root=session.user_root)
    pending = iter(providers)
    session.provider_factory = lambda config: next(pending)
    await session.teams.control({'action': 'create', 'name': 'alpha'})
    await session.teams.control({'action': 'goal', 'description': '核查运行边界'})
    return session


async def spawn(session, name, *, approval=False):
    member = await session.teams.member({'action': 'spawn', 'name': name, 'role': 'boundary',
        'backend': 'inprocess', 'require_plan_approval': approval})
    return session.teams.runners[member['member_id']]


async def send(session, runtime, body='开始核查'):
    return await session.teams.message({'action': 'send', 'recipient': runtime.member.member_id, 'body': body})


async def assign(session, runtime):
    task = await session.teams.task({'action': 'create', 'title': '实际工具任务'})
    return await session.teams.task({'action': 'reassign', 'task_id': task['task_id'],
                                     'member_id': runtime.member.member_id})


async def dispatch(session, runtime, task):
    team = session.teams.store.load('alpha')
    return await session.teams.inbox().send(team.lead_id, runtime.member.member_id, '开始当前任务',
        type='task_assignment', fields={key: task[key] for key in ('goal_id', 'task_id', 'claim_id')})


def gated_response(entered, release, response):
    async def stream():
        yield ProviderEvent('text_delta', '正在核查')
        entered.set()
        await release.wait()
        for event in response:
            yield event
    return stream


@async_test
async def test_two_members_stream_in_parallel_and_stopping_one_preserves_sibling(tmp_path):
    entered = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    providers = [ScriptedProvider([gated_response(entered[i], release[i], answer(f'成员{i}完成')),
                                  answer('兄弟仍可再次运行')]) for i in range(2)]
    session = await create_team(tmp_path, providers, maximum=2)
    try:
        alice, bob = await spawn(session, 'alice'), await spawn(session, 'bob')
        await send(session, alice)
        await send(session, bob)
        async with asyncio.timeout(5):
            await asyncio.gather(*(event.wait() for event in entered))
        assert alice.busy and bob.busy
        await session.teams.member({'action': 'stop', 'member': alice.member.member_id})
        assert providers[0].closed and providers[0].closed_streams == 1
        assert bob.busy and not providers[1].closed
        release[1].set()
        await wait_for(lambda: not bob.busy, bob)
        await send(session, bob, '再次核查')
        await wait_for(lambda: len(providers[1].requests) == 2 and not bob.busy, bob)
        assert session.teams.store.load('alpha').members[bob.member.member_id].state == 'idle'
    finally:
        for event in release:
            event.set()
        await session.aclose()


@async_test
async def test_model_write_is_denied_before_actual_task_start_then_succeeds(tmp_path):
    responses = []
    provider = ScriptedProvider(responses)
    session = await create_team(tmp_path, [provider])
    try:
        runtime = await spawn(session, 'alice')
        task = await assign(session, runtime)
        responses.extend([calls(tool('before-start', 'write_file', '{"path":"early.txt","content":"禁止"}')),
            calls(tool('start', 'team_task', json.dumps({'action': 'start', 'task_id': task['task_id'], 'claim_id': task['claim_id']}))),
            calls(tool('after-start', 'write_file', '{"path":"owned.txt","content":"已开工"}')), answer('完成')])
        await dispatch(session, runtime, task)
        await wait_for(lambda: len(provider.requests) == 4 and not runtime.busy, runtime)
        results = {message.tool_call_id: message.tool_result for message in runtime.session.history if message.role == 'tool'}
        assert not results['before-start'].ok and results['before-start'].error['code'] == 'tool_not_allowed'
        assert results['start'].ok and results['after-start'].ok
        root = Path(runtime.member.workspace_root)
        assert not (root / 'early.txt').exists() and (root / 'owned.txt').read_text() == '已开工'
        assert not (tmp_path / 'owned.txt').exists()
        started = await session.teams.board().get(session.team_scope.member_id, task['task_id'])
        assert started.state == 'running' and started.synced_commit
    finally:
        await session.aclose()


@async_test
async def test_streaming_mail_is_injected_once_only_at_next_model_request(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    provider = ScriptedProvider([gated_response(entered, release, calls(tool('read', 'read_file', '{"path":"README.md"}'))),
                                 answer('已接收后续邮件')])
    session = await create_team(tmp_path, [provider])
    try:
        runtime = await spawn(session, 'alice')
        (Path(runtime.member.workspace_root) / 'README.md').write_text('核查内容')
        await send(session, runtime)
        async with asyncio.timeout(5):
            await entered.wait()
        delivery = await send(session, runtime, '流式期间新增正文')
        assert '流式期间新增正文' not in repr(provider.requests[0][0])
        assert not any(message.id == 'mail-' + delivery['message_id'] for message in runtime.session.history)
        release.set()
        await wait_for(lambda: len(provider.requests) == 2 and not runtime.busy, runtime)
        history = provider.requests[1][0]
        assert len([message for message in history if message.id == 'mail-' + delivery['message_id']]) == 1
        assert '流式期间新增正文' in repr(history)
        assert len([message for message in history if message.role == 'tool' and message.tool_call_id == 'read']) == 1
    finally:
        release.set()
        await session.aclose()


@async_test
async def test_plan_wait_releases_slot_and_rejection_keeps_revision_read_only(tmp_path):
    bob_entered, bob_release = asyncio.Event(), asyncio.Event()
    responses = [calls(tool('plan1', 'team_message', '{"action":"send","recipient":"lead","type":"plan_request","body":"先读取再修改"}')),
        calls(tool('forbidden-revision', 'write_file', '{"path":"forbidden.txt","content":"禁止"}')),
        calls(tool('plan2', 'team_message', '{"action":"send","recipient":"lead","type":"plan_request","body":"修订：先核查测试"}'))]
    alice_provider = ScriptedProvider(responses)
    bob_provider = ScriptedProvider([gated_response(bob_entered, bob_release, answer('兄弟完成'))])
    session = await create_team(tmp_path, [alice_provider, bob_provider], maximum=1)
    try:
        alice, bob = await spawn(session, 'alice', approval=True), await spawn(session, 'bob')
        task = await assign(session, alice)
        await dispatch(session, alice, task)
        await wait_for(lambda: not alice.busy and alice.control.plan_version == 1, alice)
        await send(session, bob)
        async with asyncio.timeout(5):
            await bob_entered.wait()
        assert len(alice_provider.requests) == 1 and alice.control.awaiting_plan
        assert session.teams.store.load('alpha').members[alice.member.member_id].state == 'awaiting_approval'
        bob_release.set()
        await wait_for(lambda: not bob.busy, bob)
        fields = {key: task[key] for key in ('goal_id', 'task_id', 'claim_id')}
        first = {**fields, 'plan_id': alice.control.plan_id, 'plan_version': 1}
        await session.teams.message({'action': 'send', 'recipient': alice.member.member_id, 'type': 'plan_decision',
                                    'body': '仅修订计划', 'fields': {**first, 'approved': False}})
        await wait_for(lambda: alice.control.plan_version == 2 and not alice.busy, alice)
        assert len(alice_provider.requests) == 3 and alice.control.awaiting_plan
        assert not (Path(alice.member.workspace_root) / 'forbidden.txt').exists()
        revision = next(message.tool_result for message in alice.session.history if message.role == 'tool'
                        and message.tool_call_id == 'forbidden-revision')
        assert not revision.ok and revision.error['code'] == 'tool_not_allowed'
        assert all(tool.name != 'write_file' for tool in alice_provider.requests[1][1]['tools'])
        with pytest.raises(MessageError):
            await session.teams.message({'action': 'send', 'recipient': alice.member.member_id, 'type': 'plan_decision',
                                        'body': '过期批准', 'fields': {**first, 'approved': True}})
        assert alice.control.awaiting_plan and len(alice_provider.requests) == 3
        responses.extend([calls(tool('approved-start', 'team_task', json.dumps({'action': 'start',
            'task_id': task['task_id'], 'claim_id': task['claim_id']}))),
            calls(tool('approved-write', 'write_file', '{"path":"approved.txt","content":"批准后开工"}')), answer('批准任务完成')])
        await session.teams.message({'action': 'send', 'recipient': alice.member.member_id, 'type': 'plan_decision',
            'body': '批准当前修订', 'fields': {**first, 'plan_version': 2, 'approved': True}})
        await wait_for(lambda: len(alice_provider.requests) == 6 and not alice.busy, alice)
        assert not alice.control.awaiting_plan
        assert (Path(alice.member.workspace_root) / 'approved.txt').read_text() == '批准后开工'
    finally:
        bob_release.set()
        await session.aclose()


@async_test
async def test_running_and_queue_capacity_and_cancelled_queue_release_real_slots(tmp_path):
    entered = [asyncio.Event() for _ in range(4)]
    release = [asyncio.Event() for _ in range(4)]
    providers = [ScriptedProvider([gated_response(entered[i], release[i], answer(f'成员{i}完成'))]) for i in range(4)]
    session = await create_team(tmp_path, providers, maximum=1, queued=1)
    try:
        alice, bob, carol = [await spawn(session, name) for name in ('alice', 'bob', 'carol')]
        await send(session, alice)
        async with asyncio.timeout(5):
            await entered[0].wait()
        await send(session, bob)
        await wait_for(lambda: bob.busy, bob)
        assert not providers[1].requests
        await send(session, carol)
        async with asyncio.timeout(5):
            await carol.task
        assert not providers[2].requests and carol.session.warnings
        assert alice.busy and bob.busy
        await session.teams.member({'action': 'stop', 'member': bob.member.member_id})
        assert providers[1].closed and not providers[1].requests
        dave = await spawn(session, 'dave')
        await send(session, dave)
        await wait_for(lambda: dave.busy, dave)
        assert not providers[3].requests
        release[0].set()
        async with asyncio.timeout(5):
            await entered[3].wait()
        release[3].set()
        await wait_for(lambda: not dave.busy, dave)
        assert len(providers[3].requests) == 1
    finally:
        for event in release:
            event.set()
        await session.aclose()


@pytest.mark.parametrize('failure', ['checkpoint', 'control'])
@async_test
async def test_save_failure_after_model_answer_does_not_publish_idle(tmp_path, monkeypatch, failure):
    complete = asyncio.Event()
    async def response():
        complete.set()
        for event in answer('回答已产生但保存失败'):
            yield event
    provider = ScriptedProvider([response])
    session = await create_team(tmp_path, [provider])
    try:
        runtime = await spawn(session, 'alice')
        if failure == 'checkpoint':
            original = runtime.session._checkpoint
            def fail(*args, **kwargs):
                if complete.is_set():
                    raise OSError('注入的磁盘写入失败')
                return original(*args, **kwargs)
            monkeypatch.setattr(runtime.session, '_checkpoint', fail)
        else:
            original = runtime.save_control
            def fail(*args, **kwargs):
                if complete.is_set():
                    raise OSError('注入的控制记录写入失败')
                return original(*args, **kwargs)
            monkeypatch.setattr(runtime, 'save_control', fail)
        await send(session, runtime)
        async with asyncio.timeout(5):
            await runtime.task
        assert len(provider.requests) == 1
        messages = await session.teams.inbox().read(session.team_scope.member_id)
        assert not any(message.type == 'idle' and message.sender_id == runtime.member.member_id for message in messages)
        assert runtime.session.warnings and not runtime.busy
    finally:
        await session.aclose()


@async_test
async def test_member_budget_is_not_refreshed_by_mail_or_disk_restore(tmp_path):
    provider = ScriptedProvider([answer('第一次'), answer('第二次')])
    session = await create_team(tmp_path, [provider], budget=2)
    fresh = None
    try:
        runtime = await spawn(session, 'alice')
        for index in range(2):
            await send(session, runtime, f'第{index + 1}次请求')
            await wait_for(lambda: len(provider.requests) == index + 1 and not runtime.busy, runtime)
        assert runtime.budget.used == 2 and runtime.budget.remaining == 0
        await send(session, runtime, '第三封消息不能刷新预算')
        await wait_for(lambda: session.teams.store.load('alpha').members[runtime.member.member_id].state == 'blocked', runtime)
        assert len(provider.requests) == 2
        await session.aclose()
        fresh = make_session(tmp_path)
        restored_provider = ScriptedProvider([answer('不能调用')])
        fresh.provider_factory = lambda config: restored_provider
        await fresh.teams.control({'action': 'resume', 'name': 'alpha'})
        assert not fresh.teams.runners and not restored_provider.requests
        await fresh.teams.parent_for_input('明确继续原目标', asyncio.Event())
        delivered = await fresh.teams.message({'action': 'send', 'recipient': 'alice', 'body': '显式恢复也不刷新预算'})
        assert delivered['notified'], (delivered, fresh.teams.store.load('alpha').members[runtime.member.member_id].to_dict())
        await wait_for(lambda: runtime.member.member_id in fresh.teams.runners)
        restored = fresh.teams.runners[runtime.member.member_id]
        await wait_for(lambda: fresh.teams.store.load('alpha').members[runtime.member.member_id].state == 'blocked', restored)
        assert restored.budget.used == 2 and restored.budget.remaining == 0 and not restored_provider.requests
    finally:
        if fresh is not None:
            await fresh.aclose()
        await session.aclose()
