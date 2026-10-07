"""受控内部成员入口的登记、安全路径和独立服务生命周期。"""

import asyncio
from dataclasses import asdict, replace
import io
import os
from pathlib import Path
import json
import shutil
import signal
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest

from conftest import async_test
from test_team_service import make_session
from mewcode.teams.backends import BackendLaunch
from mewcode.teams.models import Member, new_id
from mewcode.worktrees.manager import WorktreeManager
from mewcode.worktrees.paths import freeze_repository


async def publish_definition(store, team, member, definition):
    from mewcode.teams.runtime import definition_fingerprint
    member.definition_fingerprint = definition_fingerprint(definition)
    store.write_json(team.name, f'members/{member.member_id}/definition.json', definition)
    await store.update_team(team.name, lambda value: setattr(value.members[member.member_id], 'definition_fingerprint', member.definition_fingerprint))


async def registration(root):
    session = make_session(root)
    store = session.teams.store
    team = await store.create('alpha', root)
    await store.start_goal(team.name, '读取 README')
    identity = new_id('member')
    snapshot = freeze_repository(root)
    manager = WorktreeManager(snapshot)
    tree = await manager.create(f'team/{team.team_id}/{identity}', task_id=identity)
    role = session.roles.get('general')
    member = Member(identity, 'alice', role.name, str(tree.workspace_root), role_body=role.body,
                    role_fingerprint=role.fingerprint, branch=tree.branch, backend='tmux',
                    generation=uuid4().hex, state='starting')
    await store.register_member(team.name, member)
    config = root / '.env.worker'
    config.write_text('name=test\nprotocol=openai\nmodel=test\nbase_url=https://example.test/v1\napi_key=private-test-key\nthinking=false\ncontext_window=32768\n')
    startup = store.member_path(team.name, identity) / 'startup.json'
    launch = BackendLaunch(team.team_id, identity, member.generation, uuid4().hex, tree.workspace_root, config, startup)
    raw = {key: str(value) for key, value in asdict(launch).items()}
    definition = {'version': 1, 'role_body': role.body, 'role_fingerprint': role.fingerprint,
                  'model': 'inherit', 'max_iterations': 5, 'permission_mode': 'strict',
                  'parent_permission_mode': 'default', 'tools': ['read_file', 'team_task', 'team_message'],
                  'user_permission_path': str(session.permissions.config.paths[0]),
                  'worktree': tree.metadata(), 'repository': {key: str(value) for key, value in asdict(snapshot).items()}}
    await publish_definition(store, team, member, definition)
    policy = {'permission_mode': session.permissions.mode,
              'user_permission_path': str(session.permissions.config.paths[0]), 'tools': definition['tools']}
    store.write_json(team.name, f'members/{identity}/registration.json', {'version': 1, **raw, 'policy': policy})
    store.write_json(team.name, f'members/{identity}/startup.json', {'version': 1, 'team_name': team.name,
                     'store_root': str(store.root), 'launch': raw, 'handle': None, 'activated': False})
    return session, store, team, member, launch


def worker_api():
    from mewcode.teams import worker
    return worker


@async_test
async def test_worker_accepts_only_matching_registered_identity(tmp_path):
    session, store, team, member, launch = await registration(tmp_path)
    try:
        record = worker_api().load_startup(launch.startup_path, launch.config_path)
        assert record.member.member_id == member.member_id and record.launch.nonce == launch.nonce
        assert record.definition['role_body'] == member.role_body
    finally:
        store.release_lead(team.name)
        await session.aclose()


@pytest.mark.parametrize('change', ['nonce', 'generation', 'root', 'config', 'role', 'lead', 'backend', 'permission'])
@async_test
async def test_worker_rejects_modified_startup_or_role(tmp_path, change):
    session, store, team, member, launch = await registration(tmp_path)
    relative = f'members/{member.member_id}/startup.json'
    data = store.read_json(team.name, relative)
    if change in {'nonce', 'generation'}:
        data['launch'][change] = uuid4().hex
    elif change == 'root':
        data['launch']['workspace_root'] = str(tmp_path)
    elif change == 'config':
        data['launch']['config_path'] = str(tmp_path / 'other.env')
    elif change == 'role':
        definition = store.read_json(team.name, f'members/{member.member_id}/definition.json')
        definition['role_body'] = '修改后的职责'
        store.write_json(team.name, f'members/{member.member_id}/definition.json', definition)
    elif change == 'lead':
        store.release_lead(team.name)
    elif change == 'backend':
        await store.update_team(team.name, lambda value: setattr(value.members[member.member_id], 'backend', 'inprocess'))
    else:
        launch.startup_path.chmod(0o644)
    store.write_json(team.name, relative, data) if change != 'permission' else None
    try:
        with pytest.raises((ValueError, OSError)):
            worker_api().load_startup(launch.startup_path, launch.config_path)
    finally:
        store.release_lead(team.name)
        await session.aclose()


@async_test
async def test_worker_rejects_copied_startup_and_links(tmp_path):
    session, store, team, member, launch = await registration(tmp_path)
    copied = tmp_path / 'startup.json'
    copied.write_bytes(launch.startup_path.read_bytes())
    copied.chmod(0o600)
    linked = tmp_path / 'linked.json'
    linked.symlink_to(launch.startup_path)
    try:
        for path in (copied, linked):
            with pytest.raises((ValueError, OSError)):
                worker_api().load_startup(path, launch.config_path)
    finally:
        store.release_lead(team.name)
        await session.aclose()


@async_test
async def test_worker_rebuilds_parent_user_deny_without_widening_mode(tmp_path):
    from mewcode.tools.base import ToolError
    session, store, team, member, launch = await registration(tmp_path)
    try:
        record = worker_api().load_startup(launch.startup_path, launch.config_path)
        record.definition['permission_mode'] = 'bypass'
        record.definition['parent_permission_mode'] = 'strict'
        parent, permissions = worker_api().build_permissions(record)
        assert permissions.mode == 'strict' and permissions.noninteractive
        assert permissions.ceiling is parent
        assert permissions.config.paths[0] == session.permissions.config.paths[0]
        (tmp_path / 'README.md').write_text('父源码')
        (launch.workspace_root / 'README.md').write_text('成员源码')
        session.permissions.config.paths[0].write_text('rules:\n  - {effect: deny, rule: "read_file(README.md)", match: exact}\n')
        with pytest.raises(ToolError) as error:
            permissions.authorize_noninteractive('read_file', {'path': 'README.md'})
        assert error.value.code == 'permission_denied'
    finally:
        store.release_lead(team.name)
        await session.aclose()


@pytest.mark.parametrize('change', ['missing', 'mode', 'relative_path', 'expanded_tools', 'boolean_version'])
@async_test
async def test_worker_rejects_invalid_current_launch_policy(tmp_path, change):
    session, store, team, member, launch = await registration(tmp_path)
    try:
        relative = f'members/{member.member_id}/registration.json'
        value = store.read_json(team.name, relative)
        if change == 'missing':
            value.pop('policy')
        elif change == 'mode':
            value['policy']['permission_mode'] = 'inherit'
        elif change == 'relative_path':
            value['policy']['user_permission_path'] = 'relative.yaml'
        elif change == 'expanded_tools':
            value['policy']['tools'] = ['execute_command']
        else:
            value['version'] = True
        store.write_json(team.name, relative, value)
        with pytest.raises((ValueError, OSError)):
            worker_api().load_startup(launch.startup_path, launch.config_path)
    finally:
        store.release_lead(team.name)
        await session.aclose()


@async_test
async def test_worker_current_strict_policy_overrides_historical_bypass(tmp_path):
    from mewcode.tools.base import ToolError
    session, store, team, member, launch = await registration(tmp_path)
    try:
        definition = store.read_json(team.name, f'members/{member.member_id}/definition.json')
        definition['permission_mode'] = definition['parent_permission_mode'] = 'bypass'
        await publish_definition(store, team, member, definition)
        current = tmp_path / 'current-user.yaml'
        current.write_text('rules:\n  - {effect: deny, rule: "read_file(README.md)", match: exact}\n')
        relative = f'members/{member.member_id}/registration.json'
        value = store.read_json(team.name, relative)
        value['policy'] = {'permission_mode': 'strict', 'user_permission_path': str(current), 'tools': ['read_file']}
        store.write_json(team.name, relative, value)
        record = worker_api().load_startup(launch.startup_path, launch.config_path)
        parent, permissions = worker_api().build_permissions(record)
        assert parent.mode == permissions.mode == 'strict'
        assert permissions.config.paths[0] == current
        (tmp_path / 'README.md').write_text('父源码')
        (launch.workspace_root / 'README.md').write_text('成员源码')
        with pytest.raises(ToolError) as error:
            permissions.authorize_noninteractive('read_file', {'path': 'README.md'})
        assert error.value.code == 'permission_denied'
    finally:
        store.release_lead(team.name)
        await session.aclose()


def test_cli_team_resume_are_mutually_exclusive():
    from mewcode.cli import main
    with pytest.raises(SystemExit) as error:
        main(['--config', 'cfg', '--team', 'alpha', '--resume', 'latest'])
    assert error.value.code == 2


def test_cli_team_is_passed_to_app(monkeypatch):
    from mewcode.cli import main
    captured = {}
    def run(path, **options):
        captured.update(path=path, **options)
        return 0
    monkeypatch.setattr('mewcode.app.run', run)
    assert main(['--config', 'cfg', '--team', 'alpha']) == 0
    assert captured == {'path': 'cfg', 'permission_mode': 'default', 'team': 'alpha'}


def test_cli_private_worker_uses_its_async_entry(monkeypatch, tmp_path):
    module = worker_api()
    from mewcode.cli import main
    captured = []
    async def worker(path, config, **kwargs):
        captured.append((path, config))
        return 7
    monkeypatch.setattr(module, 'worker', worker)
    startup, config = tmp_path / 'startup.json', tmp_path / '.env'
    assert main(['--config', str(config), '--team-worker', str(startup)]) == 7
    assert captured == [(startup, config)]


@pytest.mark.parametrize('option', ['--resume', '--team', '--list-sessions'])
def test_cli_private_worker_cannot_mix_normal_session_flags(option):
    from mewcode.cli import main
    values = [option] if option == '--list-sessions' else [option, 'alpha']
    with pytest.raises(SystemExit) as error:
        main(['--config', '/cfg', '--team-worker', '/startup', *values])
    assert error.value.code == 2


@async_test
async def test_worker_invalid_bootstrap_returns_failure_without_secret(tmp_path):
    source = tmp_path / 'startup.json'
    source.write_text('{"api_key":"never-print-this-secret"}')
    output = io.StringIO()
    assert await worker_api().worker(source, tmp_path / '.env', stderr=output) == 2
    assert 'never-print-this-secret' not in output.getvalue()


@async_test
async def test_old_worker_failure_does_not_mark_new_generation(tmp_path, monkeypatch):
    from mewcode.teams.backends import BackendHandle
    module = worker_api()
    session, store, team, member, launch = await registration(tmp_path)
    try:
        original = module.load_startup(launch.startup_path, launch.config_path)
        handle = BackendHandle(launch, '/tmp/not-a-tmux-socket', '$1', '%1', os.getpid() + 100000, '', '')
        original = replace(original, handle=handle)
        monkeypatch.setattr(module, 'load_startup', lambda *args, **kwargs: original)
        generation = uuid4().hex
        def advance(value):
            current = value.members[member.member_id]
            current.generation, current.state = generation, 'idle'
        await store.update_team(team.name, advance)
        assert await module.worker(launch.startup_path, launch.config_path, stderr=io.StringIO()) == 2
        current = store.load(team.name).members[member.member_id]
        assert current.generation == generation and current.state == 'idle'
    finally:
        store.release_lead(team.name)
        await session.aclose()


@async_test
async def test_lead_peer_notifier_checks_independent_registration_before_address(tmp_path, monkeypatch):
    import hashlib
    from mewcode.teams import backends
    from mewcode.teams.capabilities import TeamScope
    session, store, team, member, launch = await registration(tmp_path)
    called = False
    async def notify(*args, **kwargs):
        nonlocal called
        called = True
    monkeypatch.setattr(backends, 'notify_peer', notify)
    session.team_scope = TeamScope(team.name, team.lead_id, True)
    try:
        data = store.read_json(team.name, f'members/{member.member_id}/startup.json')
        data['launch']['nonce'] = uuid4().hex
        owner = hashlib.sha256('\0'.join((launch.team_id, member.member_id, member.generation,
                                          data['launch']['nonce'])).encode()).hexdigest()
        data['handle'] = {'socket': '/tmp/untrusted-socket', 'session': '$1', 'pane': '%1', 'pid': os.getpid(),
                          'owner': owner, 'channel': 'mewcode-team-' + uuid4().hex}
        store.write_json(team.name, f'members/{member.member_id}/startup.json', data)
        with pytest.raises(ValueError, match='登记'):
            await session.teams.notify_member(member.member_id)
        assert not called
    finally:
        session.team_scope = None
        store.release_lead(team.name)
        await session.aclose()


@pytest.mark.parametrize('stop_kind', ['protocol', 'signal', 'lead_exit', 'pre_activation'])
@async_test
async def test_real_tmux_full_worker_activation_tool_history_and_owned_mcp_close(tmp_path, stop_kind):
    module = worker_api()
    assert callable(getattr(module, 'worker', None))
    from mewcode.teams.backends import TmuxBackend, WorkerObservation, run_command
    from mewcode.teams.mailbox import Mailbox
    from test_mcp_manager import config as mcp_config, stdio
    if shutil.which('tmux') is None:
        pytest.skip('本机没有 tmux')
    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    environment = dict(os.environ, TMUX='', TMUX_PANE='', HOME=str(home))
    probe_backend = TmuxBackend(environment=environment)
    if not (await probe_backend.probe()).available:
        pytest.skip('当前执行环境不能创建私有 tmux socket')
    await probe_backend.close()
    session, store, team, member, launch = await registration(tmp_path)
    root = launch.workspace_root
    (root / 'README.md').write_text('启动：uv sync，uv run mewcode --config .env。')
    definition = store.read_json(team.name, f'members/{member.member_id}/definition.json')
    definition['permission_mode'] = definition['parent_permission_mode'] = 'bypass'
    await publish_definition(store, team, member, definition)
    registered = store.read_json(team.name, f'members/{member.member_id}/registration.json')
    registered['policy']['tools'] = ['read_file']
    store.write_json(team.name, f'members/{member.member_id}/registration.json', registered)
    mcp_config(root, {'owned': stdio(root, 'owned')})
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            if len(requests) == 1:
                delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'read-start', 'type': 'function',
                        'function': {'name': 'read_file', 'arguments': '{"path":"README.md"}'}}]}
                finish = 'tool_calls'
            else:
                delta, finish = {'role': 'assistant', 'content': '已读取并概括启动步骤。'}, 'stop'
            chunk = {'id': 'test', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'test',
                     'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
            payload = ('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\ndata: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    launch.config_path.write_text(f'name=test\nprotocol=openai\nmodel=test\nbase_url=http://127.0.0.1:{server.server_port}/v1\napi_key=private-test-key\ncontext_window=32768\n')
    async def observe(spec):
        try:
            return WorkerObservation(**store.read_json(team.name, f'members/{spec.member_id}/runtime.json'))
        except OSError:
            return None
    backend = TmuxBackend(environment=environment, observe=observe, startup_timeout=20,
                          stop_timeout=2 if stop_kind == 'pre_activation' else 10)
    handle = None
    try:
        async def on_started(value):
            metadata = store.read_json(team.name, f'members/{member.member_id}/startup.json')
            metadata['handle'] = value.metadata()
            metadata['handle'].pop('generation')
            store.write_json(team.name, f'members/{member.member_id}/startup.json', metadata)
        handle = await backend.start(launch, on_started=on_started)
        assert handle.pid != os.getpid()
        command = subprocess.run(['ps', '-o', 'args=', '-p', str(handle.pid)], capture_output=True, text=True, check=True).stdout
        assert 'private-test-key' not in command and launch.nonce not in command
        assert '--team-worker' in command
        inbox = Mailbox(store, team.name)
        await inbox.send(team.lead_id, member.member_id, '读取 README.md 并概括启动步骤')
        await asyncio.sleep(.3)
        assert requests == []
        if stop_kind == 'pre_activation':
            async def early_stop(spec):
                await inbox.send(team.lead_id, member.member_id, '停止尚未激活的成员', type='shutdown_request',
                                 fields={'generation': member.generation})
            stopped = await backend.stop(handle, request_stop=early_stop)
            assert stopped.confirmed, stopped.reason
            events = [json.loads(line) for line in (root / 'owned.jsonl').read_text().splitlines()]
            pid = next(event['pid'] for event in events if event['method'] == 'PROCESS_START')
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
            assert requests == []
            assert any(message.type == 'shutdown_ack' for message in await inbox.read(team.lead_id))
            return
        metadata = store.read_json(team.name, f'members/{member.member_id}/startup.json')
        metadata['activated'] = True
        store.write_json(team.name, f'members/{member.member_id}/startup.json', metadata)
        await backend.notify(handle)
        async with asyncio.timeout(15):
            while len(requests) < 2 or store.load(team.name).members[member.member_id].state != 'idle':
                await asyncio.sleep(.02)
        result = next(message for message in requests[1]['messages'] if message['role'] == 'tool')
        assert result['tool_call_id'] == 'read-start' and 'uv sync' in result['content']
        assert json.loads(result['content'])['ok'] is True
        assert all(tool['function']['name'] not in {'agent', 'team', 'team_member'} for request in requests for tool in request.get('tools', []))
        assert {tool['function']['name'] for tool in requests[0]['tools']} == {'read_file'}
        await inbox.send(team.lead_id, member.member_id, '继续概括上一轮读取的启动步骤')
        await backend.notify(handle)
        async with asyncio.timeout(10):
            while len(requests) < 3 or store.load(team.name).members[member.member_id].state != 'idle':
                await asyncio.sleep(.02)
        assert any(message['role'] == 'assistant' and '已读取并概括启动步骤。' in message.get('content', '')
                   for message in requests[2]['messages'])
        events = [json.loads(line) for line in (root / 'owned.jsonl').read_text().splitlines()]
        pid = next(event['pid'] for event in events if event['method'] == 'PROCESS_START')
        async def request_stop(spec):
            await inbox.send(team.lead_id, member.member_id, '停止', type='shutdown_request', fields={'generation': member.generation})
        if stop_kind == 'signal':
            os.kill(handle.pid, signal.SIGTERM)
            stopped = await backend.stop(handle)
        elif stop_kind == 'lead_exit':
            store.release_lead(team.name)
            stopped = await backend.stop(handle)
        else:
            stopped = await backend.stop(handle, request_stop=request_stop)
        assert stopped.confirmed, stopped.reason
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        assert any(message.type == 'shutdown_ack' for message in await inbox.read(team.lead_id))
    finally:
        if backend.socket:
            await run_command(['tmux', '-S', backend.socket, 'kill-server'])
        await backend.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        store.release_lead(team.name)
        await session.aclose()
