"""直接邮箱、保存与通知分离及消费检查点对账。"""
import asyncio
import pytest
from mewcode.teams.mailbox import Mailbox, MessageError
from test_team_tasks import setup


def test_send_dedupe_notify_and_read_checkpoint(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        async def notify(member_id):
            raise OSError('pane disappeared')
        mailbox = Mailbox(store, 'example', notify=notify)
        a, b = members
        delivery = await mailbox.send(a.member_id, b.name, '请检查接口', call_id='invoke-1')
        assert delivery.saved and not delivery.notified and delivery.error
        repeated = await mailbox.send(a.member_id, b.name, '请检查接口', call_id='invoke-1')
        assert repeated.message_id == delivery.message_id
        messages = await mailbox.read(b.member_id)
        assert len(messages) == 1 and messages[0].summary == '请检查接口' and not messages[0].read
        await mailbox.reconcile(b.member_id, [messages[0].message_id])
        assert await mailbox.read(b.member_id) == []
        await store.pause('example')
    asyncio.run(scenario())


def test_protocol_permissions_and_stale_plan(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        mailbox = Mailbox(store, 'example')
        task = await board.create(team.lead_id, goal.goal_id, 'A')
        task = await board.claim(members[0].member_id, task.task_id)
        fields = {'goal_id': goal.goal_id, 'task_id': task.task_id, 'claim_id': task.claim_id,
                  'plan_id': 'p1', 'plan_version': 1, 'approved': True}
        with pytest.raises(MessageError):
            await mailbox.send(members[1].member_id, members[0].name, '批准', type='plan_decision', fields=fields)
        with pytest.raises(MessageError):
            await mailbox.send(team.lead_id, members[0].name, '批准', type='plan_decision', fields=fields)
        with pytest.raises(MessageError):
            await mailbox.send(team.lead_id, members[0].name, '普通', fields={'read': True})
        await store.pause('example')
    asyncio.run(scenario())


def test_broadcast_partial_failure_and_resume_idempotence(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        store.lock_timeout = .02
        mailbox = Mailbox(store, 'example')
        async with store.lock('example', f'locks/inbox-{members[1].member_id}.lock'):
            results = await mailbox.broadcast(team.lead_id, '同步', broadcast_id='broadcast-1')
        assert [r.saved for r in results].count(True) == 1
        results = await mailbox.broadcast(team.lead_id, '同步', broadcast_id='broadcast-1')
        assert all(r.saved for r in results)
        assert len(await mailbox.read(members[0].member_id)) == 1
        await store.pause('example')
    asyncio.run(scenario())


def test_commit_cancel_does_not_ack_and_paused_send_does_not_wake(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        notifications = []
        mailbox = Mailbox(store, 'example', notify=lambda member_id: notifications.append(member_id))
        await store.pause('example')
        await mailbox.send(members[0].member_id, members[1].name, 'echo harmless data')
        assert notifications == []
        async def commit(messages):
            raise asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await mailbox.consume(members[1].member_id, commit, consumed_ids=[])
        assert len(await mailbox.read(members[1].member_id)) == 1
    asyncio.run(scenario())


def test_repeated_decision_after_state_transition_and_broadcast_snapshot(tmp_path):
    async def scenario():
        from mewcode.teams.models import Member, new_id
        store, team, goal, members, board = await setup(tmp_path)
        mailbox = Mailbox(store, 'example')
        task = await board.create(team.lead_id, goal.goal_id, 'A')
        task = await board.claim(members[0].member_id, task.task_id)
        def planning(team):
            member = team.members[members[0].member_id]
            member.task_id, member.claim_id = task.task_id, task.claim_id
            member.plan_id, member.plan_version, member.state = 'p1', 1, 'awaiting_approval'
        await store.update_team('example', planning)
        fields = {'goal_id': goal.goal_id, 'task_id': task.task_id, 'claim_id': task.claim_id,
                  'plan_id': 'p1', 'plan_version': 1, 'approved': True}
        first = await mailbox.send(team.lead_id, members[0].name, 'approve', type='plan_decision', fields=fields, call_id='decision1')
        await store.update_team('example', lambda team: setattr(team.members[members[0].member_id], 'state', 'idle'))
        repeated = await mailbox.send(team.lead_id, members[0].name, 'approve', type='plan_decision', fields=fields, call_id='decision1')
        assert repeated.message_id == first.message_id
        original = await mailbox.broadcast(team.lead_id, '大家好', broadcast_id='b1')
        third = Member(new_id('member'), 'charlie', 'developer', team.workspace_root)
        await store.register_member('example', third)
        repeated = await mailbox.broadcast(team.lead_id, '大家好', broadcast_id='b1')
        assert {r.recipient_id for r in repeated} == {r.recipient_id for r in original}
        assert await mailbox.read(third.member_id) == []
        await store.pause('example')
    asyncio.run(scenario())


def test_saved_history_repairs_failed_ack_without_double_context(tmp_path, monkeypatch):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        mailbox = Mailbox(store, 'example')
        await mailbox.send(members[0].member_id, members[1].name, 'old')
        committed = []
        async def commit(messages):
            committed.extend(message.message_id for message in messages)
            await mailbox.send(members[0].member_id, members[1].name, 'arrived while streaming')
        original = mailbox.ack
        async def fail(*args):
            if committed:
                raise OSError('ack unavailable')
            await original(*args)
        monkeypatch.setattr(mailbox, 'ack', fail)
        with pytest.raises(OSError):
            await mailbox.consume(members[1].member_id, commit, consumed_ids=[])
        assert len(committed) == 1
        monkeypatch.setattr(mailbox, 'ack', original)
        await mailbox.reconcile(members[1].member_id, committed)
        unread = await mailbox.read(members[1].member_id)
        assert len(unread) == 1 and unread[0].body == 'arrived while streaming'
        await store.pause('example')
    asyncio.run(scenario())


@pytest.mark.parametrize('body,summary', [('x' * 65537, None), ('text', 's' * 257)])
def test_input_limits_never_save_message(tmp_path, body, summary):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        mailbox = Mailbox(store, 'example')
        with pytest.raises(MessageError):
            await mailbox.send(members[0].member_id, members[1].name, body, summary=summary)
        assert await mailbox.read(members[1].member_id) == []
        await store.pause('example')
    asyncio.run(scenario())


def test_send_retry_keeps_identity_after_target_rename(tmp_path):
    async def scenario():
        from mewcode.teams.models import Member, new_id
        store, team, goal, members, board = await setup(tmp_path)
        mailbox = Mailbox(store, 'example')
        delivery = await mailbox.send(members[0].member_id, 'bob', 'original', call_id='stable-call')
        await store.update_team('example', lambda team: setattr(team.members[members[1].member_id], 'name', 'renamed'))
        new_bob = Member(new_id('member'), 'bob', 'developer', team.workspace_root)
        await store.register_member('example', new_bob)
        retry = await mailbox.send(members[0].member_id, 'bob', 'original', call_id='stable-call')
        assert retry.recipient_id == delivery.recipient_id and retry.message_id == delivery.message_id
        assert await mailbox.read(new_bob.member_id) == []
        await store.pause('example')
    asyncio.run(scenario())


def test_send_retry_after_rename_without_reused_name(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        mailbox = Mailbox(store, 'example')
        first = await mailbox.send(members[0].member_id, 'bob', 'original', call_id='stable-call')
        await store.update_team('example', lambda team: setattr(team.members[members[1].member_id], 'name', 'renamed'))
        retry = await mailbox.send(members[0].member_id, 'bob', 'original', call_id='stable-call')
        assert retry.message_id == first.message_id
        await store.pause('example')
    asyncio.run(scenario())


def test_broadcast_retry_does_not_notify_already_successful_targets(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        counts = []
        mailbox = Mailbox(store, 'example', notify=lambda member_id: counts.append(member_id))
        await mailbox.broadcast(team.lead_id, 'broadcast', broadcast_id='notification-once')
        await mailbox.broadcast(team.lead_id, 'broadcast', broadcast_id='notification-once')
        assert sorted(counts) == sorted(member.member_id for member in members)
        await store.pause('example')
    asyncio.run(scenario())


def test_mailbox_persistence_failure_never_notifies(tmp_path, monkeypatch):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        notified = []
        mailbox = Mailbox(store, 'example', notify=lambda identity: notified.append(identity))
        original = store.write_json
        def fail(name, relative, data):
            if relative.startswith('inboxes/'):
                raise MessageError('disk unavailable')
            return original(name, relative, data)
        monkeypatch.setattr(store, 'write_json', fail)
        with pytest.raises(MessageError):
            await mailbox.send(members[0].member_id, members[1].name, 'will not save', call_id='failed-save')
        assert notified == [] and await mailbox.read(members[1].member_id) == []
        await store.pause('example')
    asyncio.run(scenario())


def test_corrupt_mailbox_not_treated_as_empty(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        mailbox = Mailbox(store, 'example')
        path = store.team_path('example') / 'inboxes' / (members[1].member_id + '.json')
        path.write_text('{}', encoding='utf-8')
        with pytest.raises(MessageError):
            await mailbox.send(members[0].member_id, members[1].name, 'never overwrite')
        assert path.read_text() == '{}'
        await store.pause('example')
    asyncio.run(scenario())


def test_shutdown_ack_requires_actual_stopped_source_and_current_generation(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        mailbox = Mailbox(store, 'example')
        await store.update_team('example', lambda team: setattr(team.members[members[0].member_id], 'generation', 'gen1'))
        with pytest.raises(MessageError):
            await mailbox.send(members[0].member_id, team.lead_id, 'stopped', type='shutdown_ack',
                               fields={'generation': 'gen1', 'stopped': True})
        await store.update_team('example', lambda team: setattr(team.members[members[0].member_id], 'state', 'stopped'))
        delivery = await mailbox.send(members[0].member_id, team.lead_id, 'stopped', type='shutdown_ack',
                                      fields={'generation': 'gen1', 'stopped': True})
        assert delivery.saved
        with pytest.raises(MessageError):
            await mailbox.send(members[0].member_id, team.lead_id, 'old', type='shutdown_ack',
                               fields={'generation': 'gen0', 'stopped': True})
        await store.pause('example')
    asyncio.run(scenario())


def test_capacity_is_explicit_and_unread_messages_are_retained(tmp_path):
    async def scenario():
        from mewcode.teams.models import Message, new_id
        from mewcode.teams.mailbox import MAX_MESSAGES
        store, team, goal, members, board = await setup(tmp_path)
        mailbox = Mailbox(store, 'example')
        messages = [Message(new_id('message'), team.team_id, members[0].member_id, members[1].member_id, 'pending', 'pending') for _ in range(MAX_MESSAGES)]
        store.write_json('example', f'inboxes/{members[1].member_id}.json', {'version': 1, 'team_id': team.team_id,
                         'member_id': members[1].member_id, 'messages': [item.to_dict() for item in messages]})
        with pytest.raises(MessageError, match='容量'):
            await mailbox.send(members[0].member_id, members[1].name, 'over capacity')
        assert len(await mailbox.read(members[1].member_id)) == MAX_MESSAGES
        await store.pause('example')
    asyncio.run(scenario())


def test_registry_lock_contention_does_not_send_from_stale_name_map(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        store.lock_timeout = .02
        mailbox = Mailbox(store, 'example')
        async with store.lock('example', 'state.lock'):
            with pytest.raises(OSError, match='busy'):
                await mailbox.send(members[0].member_id, members[1].name, 'wait for registry')
        assert await mailbox.read(members[1].member_id) == []
        await store.pause('example')
    asyncio.run(scenario())
