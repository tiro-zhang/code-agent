"""网页审批冻结完整快照，并回到既有权限复核与取消路径。"""
import asyncio
import json

import pytest
import yaml

from conftest import async_test
from mewcode.permissions.runtime import ApprovalRequest, PermissionManager
from mewcode.tools.base import ToolError
from mewcode.web.approval import ApprovalBroker
from mewcode.web.errors import WebError


def broker(root, **options):
    context = {'session_id': 'session', 'generation': 2, 'run_id': 'run'}
    instance = ApprovalBroker(root=root, server_instance_id='server', context=lambda: context,
                              notify=lambda event: None, redact=lambda value: value,
                              **options)
    return instance, context


def request(root, **options):
    return ApprovalRequest('request', options.get('tool', 'write_file'),
                           options.get('arguments', {'path': 'a.txt', 'content': '原始内容'}),
                           (str(root / 'a.txt'),), '需要确认', 'default')


async def wait_pending(instance):
    for _ in range(1000):
        values = instance.summaries()
        if values:
            return values[0]['id']
        await asyncio.sleep(0.001)
    raise AssertionError('审批未发布')


def decide(instance, identity, decision='once', **extra):
    return instance.decide(identity, decision, server_instance_id=extra.get('server_instance_id', 'server'),
                           generation=extra.get('generation', 2), run_id=extra.get('run_id', 'run'))


@pytest.mark.parametrize('decision', ['deny', 'once', 'session', 'permanent'])
@async_test
async def test_four_choices_return_to_original_responder_once(tmp_path, decision):
    instance, _ = broker(tmp_path)
    task = asyncio.create_task(instance.respond(request(tmp_path), asyncio.Event()))
    identity = await wait_pending(instance)
    result = decide(instance, identity, decision)
    assert result['decision'] == decision
    with pytest.raises(WebError) as caught:
        decide(instance, identity, decision)
    assert caught.value.code == 'approval_resolved'
    assert await task == decision
    assert instance.summaries() == []


@async_test
async def test_preview_freezes_arguments_and_unicode_byte_pages(tmp_path):
    instance, _ = broker(tmp_path)
    text = '完整汉字内容\n' * 10000
    original = request(tmp_path, arguments={'path': 'a.txt', 'content': text})
    task = asyncio.create_task(instance.respond(original, asyncio.Event()))
    identity = await wait_pending(instance)
    original.arguments['content'] = '后来变动'
    offset, parts = 0, []
    while True:
        page = instance.preview(identity, section='arguments', offset=offset, limit=101)
        assert len(page['content'].encode()) <= 101
        assert page['root'] == str(tmp_path.resolve()) and page['generation'] == 2
        parts.append(page['content'])
        offset = page['next_offset']
        if offset is None:
            break
    assert json.loads(''.join(parts)) == {'path': 'a.txt', 'content': text}
    with pytest.raises(WebError):
        decide(instance, identity)
    with pytest.raises(ToolError) as caught:
        await task
    assert caught.value.code == 'cancelled'


@async_test
async def test_edit_diff_and_scope_do_not_read_or_change_target(tmp_path):
    path = tmp_path / 'a.txt'
    path.write_text('当前磁盘内容不参与差异')
    instance, _ = broker(tmp_path)
    original = request(tmp_path, tool='edit_file', arguments={'path': 'a.txt', 'old_text': '旧行\n', 'new_text': '新行\n'})
    task = asyncio.create_task(instance.respond(original, asyncio.Event()))
    identity = await wait_pending(instance)
    page = instance.preview(identity, section='content')
    assert '-旧行' in page['content'] and '+新行' in page['content']
    assert '当前磁盘内容' not in page['content']
    assert str(path) in instance.preview(identity, section='targets')['content']
    scope = instance.preview(identity, section='scope')['content']
    assert '永久' in scope and '真实文件' in scope
    decide(instance, identity, 'deny')
    assert await task == 'deny'
    assert path.read_text() == '当前磁盘内容不参与差异'


@async_test
async def test_identity_mismatch_never_resolves_current_request(tmp_path):
    instance, context = broker(tmp_path)
    task = asyncio.create_task(instance.respond(request(tmp_path), asyncio.Event()))
    identity = await wait_pending(instance)
    for extra in [{'server_instance_id': 'old'}, {'generation': 1}, {'run_id': 'other'}]:
        with pytest.raises(WebError):
            decide(instance, identity, **extra)
        assert not task.done()
    context['generation'] = 3
    with pytest.raises(WebError):
        decide(instance, identity)
    with pytest.raises(ToolError) as caught:
        await task
    assert caught.value.code == 'cancelled'


@async_test
async def test_cancel_and_invalidate_unblock_original_waiter(tmp_path):
    for invalidate in (False, True):
        instance, _ = broker(tmp_path)
        cancel = asyncio.Event()
        task = asyncio.create_task(instance.respond(request(tmp_path), cancel))
        identity = await wait_pending(instance)
        if invalidate:
            instance.invalidate_all()
        else:
            cancel.set()
        with pytest.raises(ToolError) as caught:
            await asyncio.wait_for(task, 1)
        assert caught.value.code == 'cancelled'
        assert instance.summaries() == []
        with pytest.raises(WebError):
            decide(instance, identity)


@async_test
async def test_expiry_uses_original_deadline_and_does_not_extend_on_preview(tmp_path):
    now = [10.0]
    instance, _ = broker(tmp_path, clock=lambda: now[0])
    task = asyncio.create_task(instance.respond(request(tmp_path), asyncio.Event()))
    identity = await wait_pending(instance)
    deadline = instance.preview(identity)['expires_at']
    now[0] = 609.0
    assert instance.preview(identity)['expires_at'] == deadline
    now[0] = 610.0
    with pytest.raises(WebError) as caught:
        instance.preview(identity)
    assert caught.value.code == 'approval_expired'
    assert await task == 'deny'


@async_test
async def test_waiter_timeout_expires_without_browser_queries(tmp_path):
    instance, _ = broker(tmp_path, ttl=0.02)
    cancel = asyncio.Event()
    task = asyncio.create_task(instance.respond(request(tmp_path), cancel))
    await wait_pending(instance)
    assert await asyncio.wait_for(task, 1) == 'deny'
    assert not cancel.is_set()


@async_test
async def test_known_secret_is_hidden_in_all_preview_sections(tmp_path):
    secret = 'quote"and\\slash'
    instance = ApprovalBroker(root=tmp_path, server_instance_id='server',
        context=lambda: {'session_id': 'session', 'generation': 2, 'run_id': 'run'}, notify=lambda event: None,
        redact=lambda text: text.replace(secret, '[已隐藏]'))
    original = request(tmp_path, arguments={'path': 'a.txt', 'content': secret})
    task = asyncio.create_task(instance.respond(original, asyncio.Event()))
    identity = await wait_pending(instance)
    page = instance.preview(identity)
    arguments = json.loads(page['content'])
    assert arguments['content'] == '[已隐藏]'
    for section in page['sections']:
        assert secret not in instance.preview(identity, section=section)['content']
    assert original.arguments['content'] == secret
    decide(instance, identity)
    assert await task == 'once'


@async_test
async def test_real_permission_manager_rechecks_deny_after_web_approval(tmp_path):
    instance, _ = broker(tmp_path)
    permissions = PermissionManager(tmp_path, responder=instance.respond, user_path=tmp_path / 'user.yaml')
    task = asyncio.create_task(permissions.authorize('write_file', {'path': 'a.txt', 'content': 'x'}))
    identity = await wait_pending(instance)
    path = tmp_path / '.mewcode/permissions.yaml'
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump({'rules': [{'effect': 'deny', 'rule': 'write_file(a.txt)', 'match': 'exact'}]}))
    decide(instance, identity)
    with pytest.raises(ToolError) as caught:
        await task
    assert caught.value.code == 'permission_denied'
    assert not (tmp_path / 'a.txt').exists()


@async_test
async def test_expired_request_returns_core_permission_denied_instead_of_cancelled(tmp_path):
    instance, _ = broker(tmp_path, ttl=0.02)
    permissions = PermissionManager(tmp_path, responder=instance.respond, user_path=tmp_path / 'user.yaml')
    cancel = asyncio.Event()
    task = asyncio.create_task(permissions.authorize('write_file', {'path': 'a.txt', 'content': 'x'}, cancel_event=cancel))
    await wait_pending(instance)
    with pytest.raises(ToolError) as caught:
        await asyncio.wait_for(task, 1)
    assert caught.value.code == 'permission_denied'
    assert not cancel.is_set()


@async_test
async def test_large_page_request_stays_within_32_kib(tmp_path):
    instance, _ = broker(tmp_path)
    task = asyncio.create_task(instance.respond(request(tmp_path, arguments={'path': 'a.txt', 'content': '文' * 20000}), asyncio.Event()))
    identity = await wait_pending(instance)
    page = instance.preview(identity, section='content', limit=1000000)
    assert len(page['content'].encode()) <= 32768 and page['next_offset']
    decide(instance, identity, 'deny')
    assert await task == 'deny'


@async_test
async def test_target_change_requires_a_new_web_approval(tmp_path):
    (tmp_path / 'a.txt').write_text('a')
    (tmp_path / 'b.txt').write_text('b')
    alias = tmp_path / 'alias'
    alias.symlink_to(tmp_path / 'a.txt')
    instance, _ = broker(tmp_path)
    permissions = PermissionManager(tmp_path, mode='strict', responder=instance.respond, user_path=tmp_path / 'user.yaml')
    task = asyncio.create_task(permissions.authorize('read_file', {'path': 'alias'}))
    first = await wait_pending(instance)
    assert str(tmp_path / 'a.txt') in instance.preview(first, section='targets')['content']
    alias.unlink()
    alias.symlink_to(tmp_path / 'b.txt')
    decide(instance, first)
    second = await wait_pending(instance)
    assert second != first and not task.done()
    assert str(tmp_path / 'b.txt') in instance.preview(second, section='targets')['content']
    decide(instance, second, 'deny')
    with pytest.raises(ToolError) as caught:
        await task
    assert caught.value.code == 'permission_denied'


@async_test
async def test_permanent_save_failure_preserves_once_only_and_core_warning(tmp_path):
    instance, _ = broker(tmp_path)
    permissions = PermissionManager(tmp_path, responder=instance.respond, user_path=tmp_path / 'user.yaml')
    events = []
    arguments = {'path': 'a.txt', 'content': 'x'}
    task = asyncio.create_task(permissions.authorize('write_file', arguments, notify=events.append))
    identity = await wait_pending(instance)
    (tmp_path / '.mewcode/.permissions.lock').mkdir(parents=True)
    decide(instance, identity, 'permanent')
    authorized = await task
    assert authorized.arguments == arguments
    assert any(event.get('warning') and '永久授权保存失败' in event['warning'] for event in events)
    assert not (tmp_path / '.mewcode/permissions.local.yaml').exists()
    assert permissions.grants.session == ()
    following = asyncio.create_task(permissions.authorize('write_file', arguments))
    next_identity = await wait_pending(instance)
    assert next_identity != identity
    decide(instance, next_identity, 'deny')
    with pytest.raises(ToolError):
        await following


@async_test
async def test_external_connection_data_is_not_attached_and_target_json_is_redacted(tmp_path):
    secret = 'quoted"credential'
    instance = ApprovalBroker(root=tmp_path, server_instance_id='server',
        context=lambda: {'session_id': 'session', 'generation': 2, 'run_id': 'run'}, notify=lambda event: None,
        redact=lambda text: text.replace(secret, '[已隐藏]'))
    arguments = {'query': secret}
    target = json.dumps({'server': 'external', 'fingerprint': 'f' * 64, 'tool': 'query', 'arguments': arguments})
    original = ApprovalRequest('external-request', 'mcp_external_query', arguments, (target,), '需要确认', 'default',
        ('external', 'query'), 'headers: raw-connection-header env: raw-connection-env')
    task = asyncio.create_task(instance.respond(original, asyncio.Event()))
    identity = await wait_pending(instance)
    page = instance.preview(identity)
    for section in page['sections']:
        content = instance.preview(identity, section=section)['content']
        assert secret not in content and json.dumps(secret)[1:-1] not in content
        assert 'raw-connection-header' not in content and 'raw-connection-env' not in content
    decide(instance, identity, 'deny')
    assert await task == 'deny'


@async_test
async def test_cancel_before_responder_returns_wins_over_pending_decision(tmp_path):
    instance, _ = broker(tmp_path)
    cancel = asyncio.Event()
    task = asyncio.create_task(instance.respond(request(tmp_path), cancel))
    identity = await wait_pending(instance)
    decide(instance, identity, 'once')
    cancel.set()
    with pytest.raises(ToolError) as caught:
        await task
    assert caught.value.code == 'cancelled'
