"""真实固定 flock 等待中的取消，区分消息提交前后消费边界。"""
import asyncio

import pytest

from conftest import ScriptedProvider, async_test
from test_team_security import active_team
from test_team_service import make_session
from mewcode.teams.mailbox import Mailbox
from mewcode.teams.runtime import MemberRuntime
from mewcode.teams.store import TeamLease


async def consumer(tmp_path, kind):
    if kind == 'lead':
        session = make_session(tmp_path)
        await session.teams.control({'action': 'create', 'name': 'alpha'})
        await session.teams.control({'action': 'goal', 'description': '验证消息取消边界'})
        parent = session.teams.goal_parent
        return session, session.teams.consume_lead, parent.cancel, session.team_scope.member_id, None
    session, previous = await active_team(tmp_path)
    identity = previous.member.member_id
    await session.teams.stop_member(identity, resumable=True)
    await session.teams.store.update_team('alpha', lambda team: setattr(team.members[identity], 'state', 'idle'))
    member = session.teams.store.load('alpha').members[identity]
    runtime = MemberRuntime(session.teams, member, previous.definition, previous.config,
        ScriptedProvider([]), previous.permissions, registry=session.executor.registry, mcp=session.executor.mcp)
    # 真实恢复、真实 Journal 与真实邮箱；不启动第二个后台接收者争测试消息。
    await runtime.setup()
    session.teams.runners[identity] = runtime
    return session, runtime.receive, runtime.cancel, identity, runtime


def context_for(session, runtime):
    return runtime.session if runtime else session


def consumed_for(session, runtime):
    return runtime.control.consumed_ids if runtime else session.teams.consumed_ids


async def send_unread(session, identity):
    mailbox = session.teams.inbox()
    mailbox.notify = None
    return await mailbox.send(session.team_scope.member_id, identity, '取消边界的新信件')


@pytest.mark.parametrize('kind', ['lead', 'member'])
@async_test
async def test_cancel_during_actual_inbox_lock_wait_does_not_commit_or_ack(tmp_path, kind):
    session, receive, cancel, identity, runtime = await consumer(tmp_path, kind)
    delivery = await send_unread(session, identity)
    path = session.teams.store.team_path('alpha') / f'locks/inbox-{identity}.lock'
    lease = TeamLease(path)
    operation = asyncio.create_task(receive())
    try:
        await asyncio.sleep(.03)
        assert not operation.done()
        cancel.set()
        lease.close()
        await asyncio.wait_for(operation, 2)
        assert delivery.message_id not in consumed_for(session, runtime)
        assert not any(m.id == 'mail-' + delivery.message_id for m in context_for(session, runtime).history)
        assert delivery.message_id in {m.message_id for m in await session.teams.inbox().read(identity)}
        assert not session.provider.requests
        if runtime:
            assert not runtime.provider.requests
    finally:
        lease.close()
        if not operation.done():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
        await session.aclose()


@async_test
async def test_member_stop_during_actual_inbox_lock_wait_does_not_consume(tmp_path):
    session, receive, cancel, identity, runtime = await consumer(tmp_path, 'member')
    delivery = await send_unread(session, identity)
    lease = TeamLease(session.teams.store.team_path('alpha') / f'locks/inbox-{identity}.lock')
    operation = asyncio.create_task(receive())
    try:
        await asyncio.sleep(.03)
        assert not operation.done()
        runtime.stop_requested = True
        lease.close()
        await asyncio.wait_for(operation, 2)
        assert not cancel.is_set()
        assert delivery.message_id not in runtime.control.consumed_ids
        assert not any(m.id == 'mail-' + delivery.message_id for m in runtime.session.history)
        assert delivery.message_id in {m.message_id for m in await session.teams.inbox().read(identity)}
    finally:
        lease.close()
        if not operation.done():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
        await session.aclose()


@pytest.mark.parametrize('kind', ['lead', 'member'])
@async_test
async def test_cancel_after_commit_while_actual_ack_lock_waits_keeps_consumption(tmp_path, monkeypatch, kind):
    session, receive, cancel, identity, runtime = await consumer(tmp_path, kind)
    delivery = await send_unread(session, identity)
    original = Mailbox.ack
    blocked = asyncio.Event()
    leases = []
    async def ack_wait(mailbox, actor, ids):
        ids = set(ids)
        if actor == identity and delivery.message_id in ids and not leases:
            leases.append(TeamLease(session.teams.store.team_path('alpha') / f'locks/inbox-{identity}.lock'))
            blocked.set()
        return await original(mailbox, actor, ids)
    monkeypatch.setattr(Mailbox, 'ack', ack_wait)
    operation = asyncio.create_task(receive())
    try:
        await asyncio.wait_for(blocked.wait(), 2)
        await asyncio.sleep(.03)
        assert not operation.done()
        assert delivery.message_id in consumed_for(session, runtime)
        assert any(m.id == 'mail-' + delivery.message_id for m in context_for(session, runtime).history)
        cancel.set()
        leases[0].close()
        await asyncio.wait_for(operation, 2)
        await receive()
        assert not await session.teams.inbox().read(identity)
        assert sum(m.id == 'mail-' + delivery.message_id for m in context_for(session, runtime).history) == 1
        assert not session.provider.requests
        if runtime:
            assert not runtime.provider.requests
    finally:
        for lease in leases:
            lease.close()
        if not operation.done():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
        await session.aclose()


@pytest.mark.parametrize('kind', ['lead', 'member'])
@async_test
async def test_cancel_after_ack_failure_reconciles_committed_ids_without_replay(tmp_path, monkeypatch, kind):
    session, receive, cancel, identity, runtime = await consumer(tmp_path, kind)
    delivery = await send_unread(session, identity)
    original = Mailbox.ack
    async def fail_new_ack(mailbox, actor, ids):
        if delivery.message_id in set(ids):
            raise OSError('模拟消费提交后确认失败')
        return await original(mailbox, actor, ids)
    monkeypatch.setattr(Mailbox, 'ack', fail_new_ack)
    try:
        with pytest.raises(OSError):
            await receive()
        assert delivery.message_id in consumed_for(session, runtime)
        cancel.set()
        monkeypatch.setattr(Mailbox, 'ack', original)
        await receive()
        assert not await session.teams.inbox().read(identity)
        assert sum(m.id == 'mail-' + delivery.message_id for m in context_for(session, runtime).history) == 1
    finally:
        await session.aclose()
