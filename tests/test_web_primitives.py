"""Web 基础组件的幂等、连接恢复、限额和本机访问边界。"""

import asyncio

import pytest

from conftest import async_test
from mewcode.web.errors import WebError


def envelope(ledger, client, sequence=1):
    return {'server_instance_id': ledger.server_instance_id, 'client_id': client['client_id'],
            'sequence': sequence, 'generation': 0, 'state_version': 0}


@async_test
async def test_retransmission_after_cancelled_request_reuses_owned_operation_once():
    from mewcode.web.operations import OperationLedger
    ledger = OperationLedger('server')
    client = ledger.register()
    entered, release = asyncio.Event(), asyncio.Event()
    effects = []
    async def operation():
        entered.set()
        await release.wait()
        effects.append('accepted')
        return 202, {'run_id': 'run-1'}
    request = asyncio.create_task(ledger.run(envelope(ledger, client), {'route': 'input', 'text': '目标'}, operation))
    await entered.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    status, pending = ledger.lookup(client['client_id'], 1)
    assert status == 202 and pending['status'] == 'pending'
    release.set()
    result = await ledger.run(envelope(ledger, client), {'route': 'input', 'text': '目标'}, operation)
    assert result == (202, {'run_id': 'run-1', 'operation': {'client_id': client['client_id'], 'sequence': 1, 'next_sequence': 2}})
    assert ledger.lookup(client['client_id'], 1) == result
    assert effects == ['accepted']


@async_test
async def test_conflicts_expiry_and_business_failure_never_reexecute():
    from mewcode.web.operations import OperationLedger
    ledger = OperationLedger('server', max_receipts=2)
    client = ledger.register()
    count = 0
    async def fail():
        nonlocal count
        count += 1
        raise WebError('project_busy', '项目忙碌')
    first = await ledger.run(envelope(ledger, client), {'text': '任务'}, fail)
    assert first[0] == 409 and first[1]['error']['code'] == 'project_busy'
    assert await ledger.run(envelope(ledger, client), {'text': '任务'}, fail) == first
    with pytest.raises(WebError) as conflict:
        await ledger.run(envelope(ledger, client), {'text': '另一任务'}, fail)
    assert conflict.value.code == 'operation_conflict'
    for sequence in (2, 3):
        await ledger.run(envelope(ledger, client, sequence), {'text': '任务'}, fail)
    with pytest.raises(WebError) as expired:
        ledger.lookup(client['client_id'], 1)
    assert expired.value.status == 410
    with pytest.raises(WebError) as expired:
        await ledger.run(envelope(ledger, client), {'text': '任务'}, fail)
    assert expired.value.status == 410 and count == 3
    with pytest.raises(WebError) as unknown:
        ledger.lookup(client['client_id'], 4)
    assert unknown.value.status == 404


@async_test
async def test_wrong_server_sequence_and_client_limits_have_no_effect():
    from mewcode.web.operations import OperationLedger
    ledger = OperationLedger('server', max_clients=1)
    client = ledger.register()
    with pytest.raises(WebError) as capacity:
        ledger.register()
    assert capacity.value.status == 429
    async def forbidden():
        pytest.fail('无效身份不得进入操作')
    for request in (dict(envelope(ledger, client), server_instance_id='old'),
                    envelope(ledger, client, 2), envelope(ledger, client, True),
                    dict(envelope(ledger, client), client_id='unknown')):
        with pytest.raises(WebError):
            await ledger.run(request, {}, forbidden)


@async_test
async def test_state_version_is_part_of_idempotency_identity_and_error_is_redacted():
    from mewcode.web.operations import OperationLedger
    ledger = OperationLedger('server')
    client = ledger.register()
    async def failure():
        raise ValueError('secret-provider-key')
    result = await ledger.run(envelope(ledger, client), {}, failure)
    assert result[0] == 500 and 'secret-provider-key' not in str(result)
    with pytest.raises(WebError) as conflict:
        await ledger.run(dict(envelope(ledger, client), state_version=1), {}, failure)
    assert conflict.value.code == 'operation_conflict'


@async_test
async def test_snapshot_high_water_and_replay_have_no_gap_or_duplicate():
    from mewcode.web.events import EventHub
    state = {'text': ''}
    hub = EventHub('server', snapshot=lambda: dict(state))
    state['text'] = '甲'
    hub.publish('agent', {'text': '甲'}, session_id='session', runtime_generation=2, run_id='run')
    subscriber = hub.subscribe()
    state['text'] = '甲乙'
    second = hub.publish('agent', {'text': '乙'}, session_id='session', runtime_generation=2, run_id='run')
    snapshot = await anext(subscriber)
    assert snapshot['kind'] == 'snapshot_replace' and snapshot['seq'] == 1
    assert snapshot['payload']['text'] == '甲' and snapshot['payload']['seq'] == 1
    assert await anext(subscriber) == second and second['seq'] == 2
    hub.unsubscribe(subscriber)
    replay = hub.subscribe(after=1)
    assert await anext(replay) == second
    hub.close()
    with pytest.raises(StopAsyncIteration):
        await anext(replay)


@async_test
async def test_old_event_cursor_replaces_snapshot_and_slow_subscriber_does_not_block():
    from mewcode.web.events import EventHub
    hub = EventHub('server', snapshot=lambda: {'ready': True}, max_events=2,
                   queue_events=2, max_subscribers=2)
    for i in range(3):
        hub.publish('notice', {'text': str(i)})
    old = hub.subscribe(after=0)
    event = await anext(old)
    assert event['kind'] == 'snapshot_replace' and event['seq'] == 3
    slow = hub.subscribe(after=3)
    with pytest.raises(WebError) as full:
        hub.subscribe(after=3)
    assert full.value.status == 429
    for i in range(3):
        hub.publish('notice', {'text': str(i)})
    assert slow.closed and slow.reason == 'slow_consumer'
    assert hub.seq == 6
    hub.close()


@async_test
async def test_byte_limits_evict_ring_and_disconnect_oversized_queue():
    from mewcode.web.events import EventHub
    hub = EventHub('server', snapshot=lambda: {}, max_bytes=600, queue_bytes=350)
    subscriber = hub.subscribe(after=0)
    hub.publish('notice', {'text': '中' * 200})
    assert subscriber.closed
    replacement = hub.subscribe(after=0)
    assert (await anext(replacement))['kind'] == 'snapshot_replace'
    assert hub.retained_bytes <= 600
    hub.close()


def test_local_credentials_are_instance_scoped_and_host_origin_are_exact():
    from mewcode.web.security import LocalSecurity
    first = LocalSecurity(8765, server_instance_id='first')
    second = LocalSecurity(8765, server_instance_id='second')
    assert first.cookie_name != second.cookie_name and first.token != second.token
    value = first.exchange(first.token)
    assert first.authenticated(value) and not second.authenticated(value)
    assert '#token=' in first.url and first.origin == 'http://127.0.0.1:8765'
    first.check_host('127.0.0.1:8765')
    first.check_origin(first.origin)
    for host in ('localhost:8765', '127.0.0.1:8765.evil', 'evil.test', '127.0.0.1'):
        with pytest.raises(WebError):
            first.check_host(host)
    for origin in ('http://evil.test', 'null', None, 'http://127.0.0.1:8765/'):
        with pytest.raises(WebError):
            first.check_origin(origin)
    with pytest.raises(WebError) as denied:
        first.exchange(second.token)
    assert denied.value.status == 401


def test_redaction_and_public_fields_exclude_credentials_and_internal_content():
    from mewcode.web.security import Redactor, public_fields
    redactor = Redactor('key-with-秘密', 'longer-token')
    source = {'text': '输出 key-with-秘密', 'provider_content': {'api_key': '未登记秘密'},
              'headers': {'Authorization': 'Bearer longer-token'}, 'env': {'PRIVATE': '未登记秘密'},
              'data': {'message': 'longer-token', 'api_key': '未登记秘密'}}
    safe = public_fields(source, ('text', 'data'), redactor=redactor)
    assert safe == {'text': '输出 [已隐藏]', 'data': {'message': '[已隐藏]'}}
    assert 'headers' not in safe and 'provider_content' not in safe and 'env' not in safe


@async_test
async def test_receipt_capacity_does_not_discard_running_operation():
    from mewcode.web.operations import OperationLedger
    ledger = OperationLedger('server', max_receipts=1)
    client = ledger.register()
    started, release = asyncio.Event(), asyncio.Event()
    effects = []
    async def operation():
        started.set()
        await release.wait()
        effects.append(1)
        return 200, {'ok': True}
    first = asyncio.create_task(ledger.run(envelope(ledger, client), {}, operation))
    await started.wait()
    with pytest.raises(WebError) as full:
        await ledger.run(envelope(ledger, client, 2), {}, operation)
    assert full.value.status == 429
    assert ledger.lookup(client['client_id'], 1)[1]['status'] == 'pending'
    release.set()
    await first
    await ledger.run(envelope(ledger, client, 2), {}, operation)
    assert effects == [1, 1]


@async_test
async def test_ledger_close_before_owned_operation_starts_keeps_a_terminal_receipt():
    from mewcode.web.operations import OperationLedger
    ledger = OperationLedger('server')
    client = ledger.register()
    async def operation():
        pytest.fail('关闭前未开始的操作不能运行')
    request = asyncio.create_task(ledger.run(envelope(ledger, client), {}, operation))
    # run 先登记并创建子 task，当前测试在子 task 获得时间片之前关闭账本。
    await asyncio.sleep(0)
    assert await ledger.aclose()
    await asyncio.gather(request, return_exceptions=True)
    status, body = ledger.lookup(client['client_id'], 1)
    assert status == 409 and body['error']['code'] == 'operation_cancelled'


@async_test
async def test_failed_snapshot_does_not_leak_subscription_slot():
    from mewcode.web.events import EventHub
    attempts = 0
    def snapshot():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError('状态构造失败')
        return {}
    hub = EventHub('server', snapshot=snapshot, max_subscribers=1)
    with pytest.raises(ValueError):
        hub.subscribe()
    subscriber = hub.subscribe()
    assert (await anext(subscriber))['kind'] == 'snapshot_replace'
    hub.close()


@async_test
async def test_initial_snapshot_does_not_use_bounded_live_queue():
    from mewcode.web.events import EventHub
    hub = EventHub('server', snapshot=lambda: {'text': '大' * 1000}, queue_bytes=400)
    subscriber = hub.subscribe()
    assert not subscriber.closed
    published = hub.publish('notice', {'text': '新增', 'parent_run_id': 'parent'})
    first = await anext(subscriber)
    assert first['kind'] == 'snapshot_replace' and first['seq'] == 0
    assert first['payload']['text'] == '大' * 1000
    assert await anext(subscriber) == published
    assert published['schema_version'] == 1 and published['parent_run_id'] == 'parent'
    hub.close()


def test_malformed_unicode_token_is_denied_as_authentication_error():
    from mewcode.web.security import LocalSecurity
    security = LocalSecurity(8765)
    with pytest.raises(WebError) as denied:
        security.exchange('\ud800')
    assert denied.value.status == 401
    assert not security.authenticated('\ud800')
