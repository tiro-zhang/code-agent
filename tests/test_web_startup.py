"""Web 启动参数、构建产物完整性与服务器退出边界。"""

import hashlib
import json
from pathlib import Path
import signal
import socket

import pytest

from mewcode.cli import main


def fixture_assets(tmp_path):
    static, frontend = tmp_path / 'static', tmp_path / 'frontend'
    static.mkdir()
    frontend.mkdir()
    (static / 'index.html').write_text('<div id="root"></div>')
    (static / 'app.js').write_text('document.body.dataset.ready="true"')
    (frontend / 'app.ts').write_text('export const ready = true')
    (frontend / 'package-lock.json').write_text('{"lockfileVersion":3}')
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    sources = {path.name: digest(path) for path in sorted(frontend.iterdir())}
    artifacts = {path.name: digest(path) for path in sorted(static.iterdir())}
    source_sha = hashlib.sha256(''.join(f'{path}\0{hash}\n' for path, hash in sources.items()).encode()).hexdigest()
    manifest = {'schema_version': 1, 'algorithm': 'sha256', 'source_sha256': source_sha,
                'lock_sha256': sources['package-lock.json'], 'sources': sources, 'artifacts': artifacts}
    (static / 'manifest.json').write_text(json.dumps(manifest))
    return static, frontend, manifest


def test_web_cli_routes_port_and_permission_without_terminal(tmp_path, monkeypatch):
    from mewcode.web import server
    seen = []
    monkeypatch.setattr(server, 'run', lambda path, **options: seen.append((path, options)) or 0)
    assert main(['--config', 'local.env', '--web']) == 0
    assert main(['--config', 'local.env', '--web', '--port', '9123', '--permission-mode', 'strict']) == 0
    assert seen == [('local.env', {'permission_mode': 'default', 'port': 8765}),
                    ('local.env', {'permission_mode': 'strict', 'port': 9123})]


@pytest.mark.parametrize('arguments', [
    ['--web'], ['--config', 'unused', '--port', '9123'],
    ['--config', 'unused', '--web', '--resume', 'latest'],
    ['--config', 'unused', '--web', '--team', 'alpha'],
    ['--config', 'unused', '--web', '--list-sessions'],
    ['--config', 'unused', '--web', '--team-worker', 'start'],
    ['--config', 'unused', '--web', '--port', '0'],
    ['--config', 'unused', '--web', '--port', '65536'],
    ['--config', 'unused', '--web', '--host', '0.0.0.0'],
])
def test_web_cli_rejects_invalid_or_conflicting_arguments_before_start(arguments):
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2


def test_assets_validate_installed_package_without_frontend_or_node(tmp_path):
    from mewcode.web.assets import validate_assets
    static, frontend, _ = fixture_assets(tmp_path)
    assert validate_assets(static, frontend_root=frontend) == static.resolve()
    assert validate_assets(static, frontend_root=tmp_path / 'absent') == static.resolve()


@pytest.mark.parametrize('change', ['edited', 'added', 'lock', 'missing', 'corrupt', 'extra', 'traversal', 'symlink'])
def test_assets_reject_stale_sources_and_corrupt_or_unlisted_artifacts(tmp_path, change):
    from mewcode.web.assets import validate_assets
    static, frontend, manifest = fixture_assets(tmp_path)
    if change == 'edited':
        (frontend / 'app.ts').write_text('export const ready = false')
    elif change == 'added':
        (frontend / 'new.ts').write_text('新源码')
    elif change == 'lock':
        (frontend / 'package-lock.json').write_text('{}')
    elif change == 'missing':
        (static / 'index.html').unlink()
    elif change == 'corrupt':
        (static / 'app.js').write_text('变更')
    elif change == 'extra':
        (static / 'unexpected.js').write_text('未登记')
    elif change == 'traversal':
        manifest['artifacts']['../outside'] = 'a' * 64
        (static / 'manifest.json').write_text(json.dumps(manifest))
    else:
        (static / 'app.js').unlink()
        (static / 'app.js').symlink_to(frontend / 'app.ts')
    with pytest.raises(ValueError, match='构建|静态|清单'):
        validate_assets(static, frontend_root=frontend)


def test_missing_assets_fail_before_manager_or_model_creation(tmp_path, monkeypatch, capsys):
    from mewcode.web import server
    from test_app import config_file
    monkeypatch.chdir(tmp_path)
    def missing():
        raise ValueError('缺少静态入口，请运行构建')
    monkeypatch.setattr(server, 'validate_assets', missing)
    def forbidden(*args, **options):
        pytest.fail('静态资源缺失时不能创建运行管理器')
    monkeypatch.setattr(server, 'WebManager', forbidden)
    assert server.run(config_file(tmp_path)) == 2
    assert '缺少静态入口' in capsys.readouterr().err


def test_port_conflict_reports_failure_without_manager_or_alternate_port(tmp_path, monkeypatch, capsys):
    from mewcode.web import server
    from test_app import config_file
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(server, 'validate_assets', lambda: tmp_path)
    def forbidden(*args, **options):
        pytest.fail('端口冲突时不能创建运行管理器')
    monkeypatch.setattr(server, 'WebManager', forbidden)
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        assert server.run(config_file(tmp_path), port=port) == 2
    assert '端口' in capsys.readouterr().err


def test_server_signal_closes_event_connections_before_uvicorn_drain():
    from mewcode.web.server import LocalServer
    import uvicorn
    from types import SimpleNamespace
    states = []
    manager = SimpleNamespace(begin_shutdown=lambda: states.append('closing'))
    server = LocalServer(uvicorn.Config('unused', access_log=False), manager)
    server.handle_exit(signal.SIGTERM, None)
    assert states == ['closing'] and server.should_exit
