"""团队后端的选择、进程边界和真实 tmux 合同。"""

import asyncio
from dataclasses import replace
import os
from pathlib import Path
import shlex
import shutil
import sys

import pytest

from conftest import async_test


def api():
    from mewcode.teams import backends
    return backends


@async_test
async def test_auto_reports_reason_before_using_inprocess():
    module = api()
    class Backend:
        def __init__(self, name, available):
            self.name, self.available = name, available
        async def probe(self):
            return module.BackendProbe(self.name, self.available, '没有 tmux server 权限')
    reports = []
    fallback = Backend('inprocess', True)
    result = await module.select_backend('auto', Backend('tmux', False), fallback, reports.append)
    assert result.backend is fallback
    assert result.fallback_reason == '没有 tmux server 权限'
    assert '同进程' in reports[0] and '没有 tmux server 权限' in reports[0]


@async_test
async def test_explicit_unavailable_backend_fails_without_fallback():
    module = api()
    class Backend:
        name = 'tmux'
        async def probe(self):
            return module.BackendProbe(self.name, False, '命令不存在')
    with pytest.raises(module.BackendError, match='命令不存在'):
        await module.select_backend('tmux', Backend(), None)


@async_test
async def test_safe_command_does_not_interpret_shell_body(tmp_path):
    module = api()
    injected = '$(touch bad);`touch bad2`\n中文'
    result = await module.run_command([sys.executable, '-c', 'import sys;print(sys.argv[1])', injected], cwd=tmp_path)
    assert result.stdout.strip() == injected
    assert not (tmp_path / 'bad').exists() and not (tmp_path / 'bad2').exists()


@async_test
async def test_safe_command_timeout_waits_for_process_group(tmp_path):
    module = api()
    marker = tmp_path / 'escaped'
    command = [sys.executable, '-c', 'import subprocess,time;subprocess.Popen(["'+sys.executable+'","-c", "import time,pathlib;time.sleep(.5);pathlib.Path('+repr(str(marker))+').touch()"]);time.sleep(20)']
    with pytest.raises(module.BackendError, match='超时'):
        await module.run_command(command, cwd=tmp_path, timeout=.05)
    await asyncio.sleep(.6)
    assert not marker.exists()


@async_test
async def test_safe_command_output_is_bounded(tmp_path):
    module = api()
    with pytest.raises(module.BackendError, match='输出超限'):
        await module.run_command([sys.executable, '-c', 'print("x"*10000)'], cwd=tmp_path, output_limit=100)


@async_test
async def test_safe_command_cancellation_stops_owned_process(tmp_path):
    module = api()
    pid_file = tmp_path / 'pid'
    code = 'import os,pathlib,time;pathlib.Path('+repr(str(pid_file))+').write_text(str(os.getpid()));time.sleep(20)'
    task = asyncio.create_task(module.run_command([sys.executable, '-c', code], cwd=tmp_path))
    async with asyncio.timeout(3):
        while not pid_file.exists():
            await asyncio.sleep(.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


@async_test
async def test_safe_command_cancel_during_spawn_preserves_cancel(tmp_path, monkeypatch):
    module = api()
    original = asyncio.create_subprocess_exec
    entered = asyncio.Event()
    release = asyncio.Event()
    processes = []
    async def delayed(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        entered.set()
        await release.wait()
        return process
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', delayed)
    task = asyncio.create_task(module.run_command([sys.executable, '-c', 'import time;time.sleep(20)'], cwd=tmp_path, timeout=.02))
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None


def launch(tmp_path):
    module = api()
    config = tmp_path / '.env'
    config.write_text('API_KEY=secret-never-argv\n')
    startup = tmp_path / 'startup.json'
    startup.write_text('{}')
    return module.BackendLaunch('team-a', 'member-a', 'generation-a', 'nonce-a', tmp_path, config, startup)


def test_default_worker_command_has_only_trusted_paths(tmp_path):
    module = api()
    spec = launch(tmp_path)
    command = module.worker_command(spec)
    assert command == (sys.executable, '-m', 'mewcode', '--config', str(spec.config_path), '--team-worker', str(spec.startup_path))
    assert 'nonce-a' not in command and 'secret-never-argv' not in command
    assert shlex.split(module.shell_command(command)) == ['exec', *command]


@async_test
async def test_probe_missing_tmux_is_explicit(tmp_path):
    module = api()
    backend = module.TmuxBackend(executable='/not/a/tmux', environment={})
    result = await backend.probe()
    assert not result.available and 'tmux' in result.reason


@async_test
async def test_probe_rejects_unverified_tmux_environment(tmp_path):
    module = api()
    backend = module.TmuxBackend(environment={'TMUX': '/tmp/forged,1,0'})
    result = await backend.probe()
    assert not result.available and '验证' in result.reason


@async_test
async def test_cancelled_probe_closes_only_its_owned_server(tmp_path):
    module = api()
    commands = []
    async def runner(argv, **kwargs):
        commands.append(tuple(argv))
        if 'start-server' in argv:
            raise asyncio.CancelledError
        return module.CommandResult(0, '', '')
    backend = module.TmuxBackend(runner=runner, environment={})
    with pytest.raises(asyncio.CancelledError):
        await backend.probe()
    assert any('kill-server' in command for command in commands)


class FakeTmux:
    """仅替代系统 tmux 边界，成员身份仍由实际后端核验。"""
    def __init__(self):
        self.calls = []
        self.owner = ''
        self.alive = True
        self.pid = 321
        self.fail_start = False
    async def __call__(self, argv, **kwargs):
        module = api()
        self.calls.append(tuple(argv))
        args = list(argv)
        if 'new-session' in args or 'new-window' in args:
            if self.fail_start:
                raise module.BackendError('窗格创建失败')
            return module.CommandResult(0, '%4\t$2\n', '')
        if 'set-option' in args and '@mewcode-team-owner' in args:
            self.owner = args[-1]
        if 'list-panes' in args:
            if not self.alive:
                return module.CommandResult(1, '', 'no pane')
            return module.CommandResult(0, f'%4\t$2\t{self.pid}\t0\t{self.owner}\n', '')
        if 'kill-pane' in args:
            self.alive = False
        return module.CommandResult(0, '', '')


def fake_backend(tmp_path, *, observation=None):
    module = api()
    runner = FakeTmux()
    spec = launch(tmp_path)
    current = {'value': observation}
    async def observe(value):
        return current['value']
    backend = module.TmuxBackend(runner=runner, environment={}, observe=observe, startup_timeout=.04, stop_timeout=.04)
    backend.socket = '/tmp/trusted-socket'
    backend.owns_server = True
    backend.probed = True
    current['value'] = observation or module.WorkerObservation(spec.team_id, spec.member_id, spec.generation, spec.nonce, 321, True, 'idle')
    return backend, runner, spec, current


@async_test
async def test_start_requires_matching_nonce_generation_and_lease(tmp_path):
    module = api()
    backend, runner, spec, current = fake_backend(tmp_path)
    current['value'] = replace(current['value'], nonce='wrong')
    with pytest.raises(module.BackendError, match='就绪') as error:
        await backend.start(spec)
    assert not runner.alive and error.value.residual is None


@async_test
async def test_start_failure_keeps_residual_when_pane_gone_but_process_alive(tmp_path):
    module = api()
    backend, runner, spec, current = fake_backend(tmp_path)
    runner.pid = os.getpid()
    current['value'] = replace(current['value'], pid=os.getpid(), nonce='wrong')
    with pytest.raises(module.BackendError) as error:
        await backend.start(spec)
    assert error.value.residual is not None
    assert not (await backend.close()).confirmed


@async_test
async def test_start_failure_does_not_run_alternate_backend(tmp_path):
    module = api()
    backend, runner, spec, _ = fake_backend(tmp_path)
    runner.fail_start = True
    with pytest.raises(module.BackendError, match='窗格创建失败'):
        await backend.start(spec)
    assert not any('send-keys' in call for call in runner.calls)


@async_test
async def test_creation_without_pane_identity_reports_unknown_residual(tmp_path):
    module = api()
    backend, runner, spec, _ = fake_backend(tmp_path)
    async def malformed(argv, **kwargs):
        result = await runner(argv, **kwargs)
        return module.CommandResult(0, 'unverified-pane\n', '') if 'new-session' in argv else result
    backend.runner = malformed
    with pytest.raises(module.BackendError) as error:
        await backend.start(spec)
    assert error.value.residual is not None
    assert not (await backend.close()).confirmed


@async_test
async def test_notify_uses_opaque_wait_for_and_checks_pane_owner(tmp_path):
    module = api()
    backend, runner, spec, _ = fake_backend(tmp_path)
    handle = await backend.start(spec)
    await backend.notify(handle)
    signals = [call for call in runner.calls if 'wait-for' in call]
    assert signals[-1][-3:] == ('wait-for', '-S', handle.channel)
    assert 'member-a' not in handle.channel and 'nonce-a' not in handle.channel
    runner.owner = 'some-other-process'
    with pytest.raises(module.BackendError, match='归属'):
        await backend.notify(handle)


@async_test
async def test_stop_requires_shutdown_confirmation_and_process_exit(tmp_path):
    backend, runner, spec, current = fake_backend(tmp_path)
    handle = await backend.start(spec)
    async def request_stop(value):
        current['value'] = replace(current['value'], state='stopped', lease_owned=False, shutdown_confirmed=True)
        runner.alive = False
    result = await backend.stop(handle, request_stop=request_stop)
    assert result.confirmed and result.state == 'stopped'


@async_test
async def test_unconfirmed_stop_retains_pane_and_returns_needs_review(tmp_path):
    backend, runner, spec, _ = fake_backend(tmp_path)
    handle = await backend.start(spec)
    result = await backend.stop(handle)
    assert not result.confirmed and result.state == 'needs_review' and runner.alive


@async_test
async def test_stop_does_not_trust_stopped_record_while_pid_is_alive(tmp_path):
    backend, runner, spec, current = fake_backend(tmp_path)
    runner.pid = os.getpid()
    current['value'] = replace(current['value'], pid=os.getpid())
    handle = await backend.start(spec)
    runner.alive = False
    current['value'] = replace(current['value'], state='stopped', lease_owned=False, shutdown_confirmed=True)
    result = await backend.stop(handle)
    assert not result.confirmed and result.state == 'needs_review'


@async_test
async def test_start_cleanup_retains_unknown_pane_and_blocks_server_close(tmp_path):
    module = api()
    backend, runner, spec, current = fake_backend(tmp_path)
    async def overwrite_owner(handle):
        runner.owner = 'external-owner'
    with pytest.raises(module.BackendError) as error:
        await backend.start(spec, on_started=overwrite_owner)
    assert error.value.residual is not None and runner.alive
    assert not (await backend.close()).confirmed


@async_test
async def test_cancel_during_pane_creation_still_removes_owned_pane(tmp_path):
    backend, runner, spec, _ = fake_backend(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()
    async def delayed(argv, **kwargs):
        result = await runner(argv, **kwargs)
        if 'new-session' in argv:
            entered.set()
            await release.wait()
        return result
    backend.runner = delayed
    task = asyncio.create_task(backend.start(spec))
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not runner.alive
    assert (await backend.close()).confirmed


@async_test
async def test_inspect_rejects_pid_reuse_in_owned_pane(tmp_path):
    backend, runner, spec, _ = fake_backend(tmp_path)
    handle = await backend.start(spec)
    runner.pid = 999
    result = await backend.inspect(handle)
    assert result.state == 'needs_review' and not result.owned


@async_test
async def test_missing_target_pane_is_not_replaced_by_current_pane(tmp_path):
    backend, runner, spec, current = fake_backend(tmp_path)
    handle = await backend.start(spec)
    runner.alive = False
    current['value'] = replace(current['value'], state='stopped', lease_owned=False, shutdown_confirmed=True)
    state = await backend.inspect(handle)
    assert not state.alive and state.state == 'stopped'


@async_test
async def test_notification_wait_rejects_missing_pane(tmp_path):
    backend, runner, spec, _ = fake_backend(tmp_path)
    handle = await backend.start(spec)
    runner.alive = False
    assert not await backend.wait_notification(handle)


@async_test
async def test_peer_notifier_rejects_unverified_socket(tmp_path):
    module = api()
    backend, runner, spec, _ = fake_backend(tmp_path)
    handle = await backend.start(spec)
    with pytest.raises(module.BackendError, match='socket'):
        await module.notify_peer(handle, observe=backend.observe)


@async_test
async def test_real_tmux_workers_have_independent_cwd_notification_and_clean_exit(tmp_path):
    module = api()
    if shutil.which('tmux') is None:
        pytest.skip('本机没有 tmux')
    import json
    script = tmp_path / 'worker.py'
    script.write_text('''import fcntl,json,os,pathlib,subprocess,time
root=pathlib.Path.cwd()
lease=open(root/'lease','w')
fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
while not (root/'backend.json').exists(): time.sleep(.01)
metadata=json.loads((root/'backend.json').read_text())
identity=json.loads((root/'identity.json').read_text())
def save(state,owned=True,confirmed=False):
    value=dict(identity,pid=os.getpid(),lease_owned=owned,state=state,shutdown_confirmed=confirmed,cwd=str(root))
    temporary=root/'observation.tmp'
    temporary.write_text(json.dumps(value))
    temporary.replace(root/'observation.json')
save('idle')
while not (root/'shutdown').exists():
    try:
        subprocess.run(['tmux','-S',metadata['socket'],'wait-for',metadata['channel']],timeout=.2,check=True)
        (root/'notified').touch()
    except subprocess.TimeoutExpired: pass
lease.close()
save('stopped',False,True)
''')
    async def observe(spec):
        path = spec.workspace_root / 'observation.json'
        if not path.exists():
            return None
        value = json.loads(path.read_text())
        value.pop('cwd')
        return module.WorkerObservation(**value)
    backend = module.TmuxBackend(environment=dict(os.environ, TMUX='', TMUX_PANE=''), observe=observe,
                                 command=lambda spec: [sys.executable, str(script)], stop_timeout=3)
    probe = await backend.probe()
    if not probe.available:
        pytest.skip(probe.reason)
    handles = []
    try:
        for index in range(2):
            root = tmp_path / str(index)
            root.mkdir()
            spec = replace(launch(root), member_id=f'member-{index}', generation=f'gen-{index}', nonce=f'nonce-{index}')
            identity = {name: getattr(spec, name) for name in ('team_id', 'member_id', 'generation', 'nonce')}
            (root / 'identity.json').write_text(json.dumps(identity))
            async def registered(handle):
                (handle.launch.workspace_root / 'backend.json').write_text(json.dumps(handle.metadata()))
            handles.append(await backend.start(spec, on_started=registered))
        assert len({handle.pid for handle in handles}) == 2
        assert all(handle.pid != os.getpid() for handle in handles)
        for handle in handles:
            assert json.loads((handle.launch.workspace_root / 'observation.json').read_text())['cwd'] == str(handle.launch.workspace_root)
            await module.notify_peer(handle, observe=observe)
        async with asyncio.timeout(3):
            while not all((handle.launch.workspace_root / 'notified').exists() for handle in handles):
                await asyncio.sleep(.02)
        for handle in handles:
            async def shutdown(spec):
                (spec.workspace_root / 'shutdown').touch()
            result = await backend.stop(handle, request_stop=shutdown)
            assert result.confirmed, result.reason
            assert await backend._pane(handle) is None
        assert (await backend.close()).confirmed
    finally:
        # 测试异常时仅清理本测试私有 socket，不涉及用户 server。
        await module.run_command(['tmux', '-S', backend.socket, 'kill-server'])
        await backend.close()
