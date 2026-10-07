"""借用真实 tmux 时仅同步当前主动开关，不改变借用者环境。"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest

from conftest import async_test
from mewcode.teams.backends import BackendLaunch, TmuxBackend, WorkerObservation


@pytest.fixture
def short_socket_root():
    # Darwin 的 Unix socket 路径容量小于默认 pytest 测试目录的完整路径。
    with tempfile.TemporaryDirectory(prefix='mewcode-borrow-', dir='/private/tmp') as directory:
        yield Path(directory)


@pytest.mark.parametrize('server_flag,current_flag', [('0', '1'), ('1', None), ('1', '0')])
@async_test
async def test_borrowed_server_uses_current_coordinator_flag_only_for_member_pane(tmp_path, short_socket_root, server_flag, current_flag):
    if shutil.which('tmux') is None:
        pytest.skip('本机没有 tmux')
    socket = short_socket_root / 'server.sock'
    server_env = dict(os.environ, MEWCODE_COORDINATOR=server_flag, MEWCODE_REVIEW_SENTINEL='旧server环境')
    server_env.pop('TMUX', None)
    server_env.pop('TMUX_PANE', None)
    created = subprocess.run(['tmux', '-S', str(socket), '-f', '/dev/null', 'new-session', '-d',
                              '-s', 'borrowed', 'sleep 30'], env=server_env, capture_output=True)
    if created.returncode or not socket.exists():
        pytest.skip('当前执行环境不能创建私有 tmux socket')
    try:
        subprocess.run(['tmux', '-S', str(socket), 'set-environment', '-t', 'borrowed',
                        'MEWCODE_COORDINATOR', server_flag], check=True, capture_output=True)
        pane = subprocess.check_output(['tmux', '-S', str(socket), 'display-message', '-p',
                                        '-t', 'borrowed', '#{pane_id}'], text=True).strip()
        current = dict(os.environ, TMUX=f'{socket},1,0', TMUX_PANE=pane,
                       MEWCODE_REVIEW_SENTINEL='当次Lead环境')
        current.pop('MEWCODE_COORDINATOR', None)
        if current_flag is not None:
            current['MEWCODE_COORDINATOR'] = current_flag
        report = tmp_path / 'actual.json'
        code = ('import json,os,pathlib,time;from types import SimpleNamespace;'
                'from mewcode.teams.capabilities import TeamScope;'
                'pathlib.Path(' + repr(str(report)) + ').write_text(json.dumps({'
                '"pid":os.getpid(),"present":"MEWCODE_COORDINATOR" in os.environ,'
                '"flag":os.environ.get("MEWCODE_COORDINATOR"),'
                '"sentinel":os.environ.get("MEWCODE_REVIEW_SENTINEL"),'
                '"member_coordinator":TeamScope("alpha","alice",False).coordinator('
                'SimpleNamespace(team_coordinator_enabled=True))}));time.sleep(30)')
        async def observe(launch):
            if not report.exists():
                return None
            data = json.loads(report.read_text())
            return WorkerObservation(launch.team_id, launch.member_id, launch.generation,
                                     launch.nonce, data['pid'], True, 'idle')
        backend = TmuxBackend(environment=current, observe=observe,
                              command=lambda launch: (sys.executable, '-c', code))
        assert (await backend.probe()).available and not backend.owns_server
        launch = BackendLaunch('team-review', 'member-review', 'generation-review', 'nonce-review',
                               tmp_path, tmp_path / 'config', tmp_path / 'startup')
        handle = await backend.start(launch)
        data = json.loads(report.read_text())
        assert data['flag'] == current_flag and data['present'] == (current_flag is not None)
        assert data['member_coordinator'] is False
        assert data['sentinel'] == '旧server环境'
        unchanged = subprocess.check_output(['tmux', '-S', str(socket), 'show-environment',
                                             '-t', 'borrowed', 'MEWCODE_COORDINATOR'], text=True)
        assert unchanged.strip() == f'MEWCODE_COORDINATOR={server_flag}'
        global_value = subprocess.check_output(['tmux', '-S', str(socket), 'show-environment',
                                                '-g', 'MEWCODE_COORDINATOR'], text=True)
        assert global_value.strip() == f'MEWCODE_COORDINATOR={server_flag}'
        await backend._tmux('kill-pane', '-t', handle.pane)
    finally:
        # 本测试拥有整个临时 server；生产 backend 不得结束借用者 server。
        subprocess.run(['tmux', '-S', str(socket), 'kill-server'], capture_output=True)
