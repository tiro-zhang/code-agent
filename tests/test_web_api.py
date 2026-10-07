"""HTTP 边界不能绕过本机认证、操作身份和运行门禁。"""

import asyncio

import httpx

from conftest import async_test, ScriptedProvider
from test_agent_loop import answer
from test_web_manager import manager, settled


async def client_for(web):
    from mewcode.web.api import create_app
    from mewcode.web.security import LocalSecurity
    security = LocalSecurity(8765, server_instance_id=web.server_instance_id)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(web, security)), base_url=security.origin)
    return client, security


async def auth(client, security):
    client.headers['Origin'] = security.origin
    assert (await client.post('/api/v1/auth', json={'token': security.token})).status_code == 200
    return (await client.post('/api/v1/clients', json={})).json()


@async_test
async def test_host_origin_auth_cookie_and_safe_errors(tmp_path):
    web = manager(tmp_path)
    client, security = await client_for(web)
    try:
        assert (await client.get('/api/v1/state')).status_code == 401
        assert (await client.post('/api/v1/auth', json={'token': security.token})).status_code == 403
        registration = await auth(client, security)
        state = await client.get('/api/v1/state')
        assert state.status_code == 200 and web.config.api_key not in state.text
        assert state.headers['cache-control'] == 'no-store'
        assert "frame-ancestors 'none'" in state.headers['content-security-policy']
        assert 'HttpOnly' in (await client.post('/api/v1/auth', json={'token': security.token})).headers['set-cookie']
        assert (await client.get('/api/v1/state', headers={'Host': 'evil.example'})).status_code == 403
        assert (await client.get('/api/v1/state', headers={'Origin': 'https://evil.example'})).status_code == 403
        assert (await client.post('/api/v1/sessions', json={'secret': web.config.api_key})).status_code == 422
        assert (await client.get('/api/v1/results/not-a-signed-result')).status_code == 400
        assert web.resources is None
    finally:
        await client.aclose()
        await web.aclose()


@async_test
async def test_api_retries_do_not_create_or_run_twice_and_invalid_generation_consumes_receipt(tmp_path):
    provider = ScriptedProvider([answer('完成')])
    web = manager(tmp_path, provider)
    client, security = await client_for(web)
    try:
        registration = await auth(client, security)
        def envelope(seq):
            return {'server_instance_id': web.server_instance_id, 'client_id': registration['client_id'],
                    'sequence': seq, 'generation': web.generation, 'state_version': web.state_version}
        create = envelope(1)
        first = await client.post('/api/v1/sessions', json=create)
        second = await client.post('/api/v1/sessions', json=create)
        assert first.json() == second.json()
        identity = first.json()['session_id']
        assert len((await client.get('/api/v1/sessions')).json()['items']) == 1
        activate = envelope(2)
        assert (await client.post(f'/api/v1/sessions/{identity}/activate', json=activate)).status_code == 202
        await settled(web)
        invalid = envelope(3) | {'text': '旧状态', 'generation': -1}
        response = await client.post(f'/api/v1/sessions/{identity}/inputs', json=invalid)
        assert response.status_code == 409 and response.json()['operation']['next_sequence'] == 4
        submit = envelope(4) | {'text': '执行'}
        accepted = await client.post(f'/api/v1/sessions/{identity}/inputs', json=submit)
        assert accepted.status_code == 202
        await settled(web)
        replay = await client.post(f'/api/v1/sessions/{identity}/inputs', json=submit)
        assert replay.json() == accepted.json() and len(provider.requests) == 1
        receipt = (await client.get(f'/api/v1/operations/{registration["client_id"]}/4')).json()
        assert receipt['status_code'] == 202 and receipt['body'] == accepted.json()
    finally:
        await client.aclose()
        await web.aclose()


@async_test
async def test_disconnect_after_ledger_accept_keeps_running(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed():
        entered.set()
        await release.wait()
        for event in answer('继续完成'):
            yield event
    provider = ScriptedProvider([delayed])
    web = manager(tmp_path, provider)
    client, security = await client_for(web)
    try:
        registration = await auth(client, security)
        identity = (await web.browser.create('openai', 'test-model'))['session_id']
        await web.activate(identity, web.generation, web.state_version)
        await settled(web)
        body = {'server_instance_id': web.server_instance_id, 'client_id': registration['client_id'],
                'sequence': 1, 'generation': web.generation, 'state_version': web.state_version, 'text': '任务'}
        assert (await client.post(f'/api/v1/sessions/{identity}/inputs', json=body)).status_code == 202
        await entered.wait()
        await client.aclose()
        release.set()
        await settled(web)
        assert len(provider.requests) == 1 and provider.closed_streams == 1
    finally:
        release.set()
        await web.aclose()
