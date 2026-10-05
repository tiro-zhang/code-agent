"""动作使用真实进程和本地 HTTP 服务，验证取消与许可边界。"""

import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import shlex
import sys
import threading
import time

import pytest

from conftest import async_test, permission_bypass


def rule(kind="command", event="tool.before", **fields):
    from mewcode.hooks.models import HookAction, HookRule
    return HookRule(event, HookAction(kind, **fields), source="hooks.yaml", index=2)


def snapshot(event="tool.before", **fields):
    from mewcode.hooks.events import HookEvent
    return HookEvent.create(event, session_id="s", **fields)


def runner(tmp_path, **options):
    from mewcode.hooks.actions import ActionRunner
    return ActionRunner(tmp_path, permissions=options.pop("permissions", permission_bypass(tmp_path)), **options)


def script(tmp_path, text):
    path = tmp_path / "action.py"
    path.write_text(text)
    return f"{shlex.quote(sys.executable)} action.py"


@async_test
async def test_command_stdin_is_data_and_fixed_root(tmp_path):
    command = script(tmp_path, "import json,sys,pathlib\ne=json.load(sys.stdin)\npathlib.Path('event.json').write_text(json.dumps(e))\nprint(json.dumps({'decision':'deny','reason':'protected'}))\n")
    action = runner(tmp_path)
    event = snapshot(tool={"arguments": {"path": "$(touch injected);\n'\""}})
    result = await action.run(rule(command=command), event)
    assert result.decision == "deny" and result.reason == "protected"
    assert json.loads((tmp_path / "event.json").read_text())["tool"]["arguments"]["path"] == "$(touch injected);\n'\""
    assert not (tmp_path / "injected").exists()
    await action.close()


@pytest.mark.parametrize("text,want", [
    ("print('not-json')", "failed"),
    ("print('{\"decision\":\"deny\"}')", "failed"),
    ("print('{\"decision\":\"unknown\"}')", "failed"),
    ("print('{\"decision\":\"allow\"}')", "ok"),
    ("print('{}')", "ok"),
    ("import sys;print('{\"decision\":\"deny\",\"reason\":\"x\"}');sys.exit(1)", "failed"),
])
@async_test
async def test_only_successful_valid_decisions_intercept(tmp_path, text, want):
    action = runner(tmp_path)
    result = await action.run(rule(command=script(tmp_path, text)), snapshot())
    assert result.status == want and result.decision != "deny"
    await action.close()


@async_test
async def test_after_output_does_not_intercept(tmp_path):
    action = runner(tmp_path)
    result = await action.run(rule(event="tool.after", command=script(tmp_path, "print('{\"decision\":\"deny\",\"reason\":\"x\"}')")), snapshot("tool.after"))
    assert result.status == "ok" and not result.decision
    await action.close()


@pytest.mark.parametrize("cancelled", [False, True])
@async_test
async def test_command_timeout_and_cancel_reap_group(tmp_path, cancelled):
    command = script(tmp_path, "import pathlib,subprocess,time\np=subprocess.Popen(['sleep','30'])\npathlib.Path('child.pid').write_text(str(p.pid))\ntime.sleep(30)\n")
    action = runner(tmp_path)
    cancel = asyncio.Event()
    running = asyncio.create_task(action.run(rule(command=command, timeout_seconds=1), snapshot(), cancel_event=cancel))
    for _ in range(100):
        if (tmp_path / "child.pid").exists():
            break
        await asyncio.sleep(.01)
    assert (tmp_path / "child.pid").exists()
    pid = int((tmp_path / "child.pid").read_text())
    if cancelled:
        cancel.set()
    result = await running
    assert result.status == ("cancelled" if cancelled else "failed")
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(.01)
    else:
        pytest.fail("受控子进程仍存在")
    await action.close()


@async_test
async def test_foreground_origin_and_approval_wait_outside_timeout(tmp_path):
    from mewcode.permissions.runtime import PermissionManager
    requests = []
    async def approve(request, cancel):
        requests.append(request)
        await asyncio.sleep(1.05)
        return "once"
    permissions = PermissionManager(tmp_path, responder=approve, user_path=tmp_path / "empty.yaml")
    action = runner(tmp_path, permissions=permissions)
    result = await action.run(rule(command=script(tmp_path, "print('{}')"), timeout_seconds=1), snapshot())
    assert result.status == "ok"
    assert requests[0].origin == ("hooks.yaml#hooks[2]", "tool.before")
    await action.close()


@async_test
async def test_background_does_not_ask_and_plan_does_not_start(tmp_path):
    from mewcode.permissions.runtime import PermissionManager
    async def forbidden(*args):
        pytest.fail("后台不能请求授权")
    permissions = PermissionManager(tmp_path, responder=forbidden, user_path=tmp_path / "empty.yaml")
    action = runner(tmp_path, permissions=permissions)
    selected = rule(command=script(tmp_path, "import pathlib;pathlib.Path('ran').touch()"))
    assert not action.can_submit(selected, snapshot(), background=True)
    result = await action.run(selected, snapshot(mode="plan"))
    assert result.status == "skipped" and not (tmp_path / "ran").exists()
    await action.close()


@async_test
async def test_prompt_and_stub_have_no_process_or_model(tmp_path):
    action = runner(tmp_path)
    assert (await action.run(rule("prompt", event="turn.end", text="remember"), snapshot("turn.end"))).status == "ok"
    assert [item.text for item in action.prompts.snapshot()] == ["remember"]
    assert (await action.run(rule("subagent", event="turn.end", agent="reviewer", prompt="review"), snapshot("turn.end"))).status == "unimplemented"
    assert [item.text for item in action.prompts.snapshot()] == ["remember"]
    await action.close()


@contextmanager
def http_service():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            calls.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            if self.path == '/disconnect':
                self.connection.close()
                return
            if self.path == "/slow":
                time.sleep(.3)
            body = b'{"decision":"deny","reason":"remote"}' if self.path != "/large" else b"x" * 70000
            self.send_response(302 if self.path == "/redirect" else 500 if self.path == "/error" else 200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Location", "/ok")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("path,want", [("/ok", "ok"), ("/redirect", "failed"), ("/error", "failed"), ("/large", "failed"), ("/slow", "failed"), ('/disconnect', 'failed')])
@async_test
async def test_http_decisions_limits_and_no_retry(tmp_path, path, want):
    with http_service() as (url, calls):
        action = runner(tmp_path, http_timeout=.05 if path == "/slow" else 30)
        result = await action.run(rule("http", url=url + path), snapshot())
        assert result.status == want
        assert len(calls) == 1 and calls[0][1]["session_id"] == "s"
        assert result.decision == ("deny" if path == "/ok" else "")
        await action.close()


@async_test
async def test_http_cancel_ends_local_wait_without_retry(tmp_path):
    with http_service() as (url, calls):
        action = runner(tmp_path)
        cancel = asyncio.Event()
        task = asyncio.create_task(action.run(rule('http', url=url + '/slow'), snapshot(), cancel_event=cancel))
        for _ in range(100):
            if calls:
                break
            await asyncio.sleep(.01)
        assert len(calls) == 1
        cancel.set()
        assert (await asyncio.wait_for(task, 1)).status == 'cancelled'
        assert len(calls) == 1
        await action.close()


@async_test
async def test_blacklist_and_explicit_deny_survive_bypass_no_process(tmp_path, monkeypatch):
    from mewcode.hooks import actions
    action = runner(tmp_path)
    async def forbidden(*args, **options):
        pytest.fail('拒绝命令不得启动')
    monkeypatch.setattr(actions.asyncio, 'create_subprocess_exec', forbidden)
    assert (await action.run(rule(command='rm -rf /'), snapshot())).status == 'failed'
    (tmp_path / '.mewcode').mkdir(exist_ok=True)
    (tmp_path / '.mewcode/permissions.yaml').write_text('version: 1\nrules:\n  - effect: deny\n    rule: "Bash(echo forbidden)"\n    match: exact\n')
    assert (await action.run(rule(command='echo forbidden'), snapshot())).status == 'failed'
    await action.close()


@async_test
async def test_command_output_budget_and_diagnostics_do_not_leak(tmp_path, caplog):
    command = script(tmp_path, "import sys;print('private-output'*5000);print('private-error',file=sys.stderr)")
    action = runner(tmp_path)
    assert (await action.run(rule(command=command), snapshot(message={'text': 'private-input'}))).status == 'failed'
    assert 'private-output' not in caplog.text and 'private-error' not in caplog.text and 'private-input' not in caplog.text
    assert 'hooks[2]' in caplog.text and 'tool.before' in caplog.text
    await action.close()


@async_test
async def test_cancel_during_process_creation_cleans_before_command_continues(tmp_path, monkeypatch):
    from mewcode.hooks import actions
    spawning = asyncio.Event()
    spawn = actions.asyncio.create_subprocess_exec
    async def delayed_spawn(*args, **options):
        spawning.set()
        await asyncio.sleep(.05)
        return await spawn(*args, **options)
    monkeypatch.setattr(actions.asyncio, 'create_subprocess_exec', delayed_spawn)
    action = runner(tmp_path)
    command = script(tmp_path, "import pathlib,time;time.sleep(.8);pathlib.Path('leaked').touch()")
    task = asyncio.create_task(action.run(rule(command=command), snapshot()))
    await spawning.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (tmp_path / 'leaked').exists()
    await action.close()


@async_test
async def test_own_timeout_during_spawn_does_not_set_parent_cancel(tmp_path, monkeypatch):
    from mewcode.hooks import actions
    spawn = actions.asyncio.create_subprocess_exec
    async def delayed_spawn(*args, **options):
        await asyncio.sleep(1.1)
        return await spawn(*args, **options)
    monkeypatch.setattr(actions.asyncio, 'create_subprocess_exec', delayed_spawn)
    action = runner(tmp_path)
    cancel = asyncio.Event()
    command = script(tmp_path, "import pathlib,time;time.sleep(.5);pathlib.Path('leaked').touch()")
    result = await action.run(rule(command=command, timeout_seconds=1), snapshot(), cancel_event=cancel)
    assert result.status == 'failed' and not cancel.is_set()
    assert not (tmp_path / 'leaked').exists()
    await action.close()
