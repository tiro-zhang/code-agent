"""通过真实 SDK 验证连接复用、隔离及生命周期。"""

import asyncio
import json
from pathlib import Path
import sys
import time
import os

import pytest
import yaml

from conftest import async_test
from mcp_fixture import http_peer
from mewcode.mcp.config import load_config
from mewcode.mcp.manager import MCPManager
from mewcode.tools import default_registry


FIXTURE = Path(__file__).with_name("mcp_fixture.py")


def config(tmp_path, servers):
    path = tmp_path / ".mewcode" / "mcp.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump({"mcpServers": servers}))
    return load_config(tmp_path, user_path=tmp_path / "absent", environment={})


def stdio(tmp_path, name="a", **kwargs):
    return {"transport": "stdio", "command": sys.executable,
            "args": [str(FIXTURE), "--log", str(tmp_path / (name + ".jsonl")), *kwargs.get("args", [])]}


@async_test
async def test_manager_stdio_reuses_and_closes_process(tmp_path):
    registry = default_registry()
    manager = MCPManager(config(tmp_path, {"a": stdio(tmp_path)}), registry)
    try:
        await manager.start()
        tool = next(t for t in manager.tools if t.original_name == "echo")
        for text in ("第一轮", "第二轮"):
            result = await manager.call(tool, {"text": text})
            assert result.ok and result.data["content"][0]["text"] == text
        assert manager.records["a"].state == "ready"
    finally:
        await manager.close()
        await manager.close()
    assert manager.records["a"].state == "closed"
    events = [json.loads(line) for line in (tmp_path / "a.jsonl").read_text().splitlines()]
    assert sum(e["method"] == "PROCESS_START" for e in events) == 1
    assert sum(e["method"] == "tools/call" for e in events) == 2
    assert not manager.records["a"].thread.is_alive()


@pytest.mark.parametrize("era", ["modern", "legacy"])
@pytest.mark.parametrize("cancel", [False, True])
@async_test
async def test_manager_http_recovery_cancel_timeout_and_reuse(tmp_path, era, cancel):
    with http_peer(era) as (peer, url):
        peer.recovery_empty = True
        manager = MCPManager(config(tmp_path, {"a": {"transport": "http", "url": url}}), default_registry(), call_timeout=.15)
        try:
            await manager.start()
            tool = next(t for t in manager.tools if t.original_name == "echo")
            event = asyncio.Event()
            operation = asyncio.create_task(manager.call(tool, {"recover": True, "timeout_seconds": 1000}, cancel_event=event))
            if cancel:
                await asyncio.sleep(.08)
                event.set()
            result = await operation
            assert result.error["code"] == ("cancelled" if cancel else "timeout")
            before = sum(e["method"] == "HTTP_GET" for e in peer.messages)
            await asyncio.sleep(.15)
            assert sum(e["method"] == "HTTP_GET" for e in peer.messages) == before
            assert (await manager.call(tool, {"text": "继续"})).ok
            assert manager.records["a"].state == "ready"
        finally:
            await manager.close()


@async_test
async def test_legacy_cancel_notification_cannot_stall_following_calls(tmp_path):
    with http_peer("legacy", cancel_delay=1) as (peer, url):
        manager = MCPManager(config(tmp_path, {"a": {"transport": "http", "url": url}}), default_registry(), call_timeout=.2)
        try:
            await manager.start()
            tool = next(t for t in manager.tools if t.original_name == "echo")
            assert (await manager.call(tool, {"delay": 2})).error["code"] == "timeout"
            started = time.monotonic()
            assert (await manager.call(tool, {"text": "仍可继续"})).ok
            assert time.monotonic() - started < .2
            assert sum(e["method"] == "tools/call" for e in peer.messages) == 2
        finally:
            await manager.close()


@async_test
async def test_startup_failure_isolation_no_partial_registration(tmp_path):
    snapshot = config(tmp_path, {"bad": {"transport": "stdio", "command": sys.executable, "args": ["-c", "import time; time.sleep(60)"]}, "good": stdio(tmp_path, "good")})
    manager = MCPManager(snapshot, default_registry(), start_timeout=.3)
    try:
        started = time.monotonic()
        await manager.start()
        assert time.monotonic() - started < 1
        assert {t.server_name for t in manager.tools} == {"good"}
        assert manager.records["bad"].state == "failed"
    finally:
        await manager.close()


@async_test
async def test_startup_cancel_exits_without_registering(tmp_path):
    manager = MCPManager(config(tmp_path, {"a": stdio(tmp_path)}), default_registry())
    cancel = asyncio.Event()
    cancel.set()
    await manager.start(cancel_event=cancel)
    assert not manager.tools
    await manager.close()


@pytest.mark.parametrize("behavior,expected", [({"repeat_cursor": True}, "failed"), ({"empty": True}, "ready"), ({"no_tools": True}, "ready")])
@async_test
async def test_complete_discovery_or_zero_tools(tmp_path, behavior, expected):
    entry = stdio(tmp_path, args=["--behavior", json.dumps(behavior)])
    manager = MCPManager(config(tmp_path, {"a": entry}), default_registry())
    try:
        await manager.start()
        assert manager.records["a"].state == expected
        assert not manager.tools
    finally:
        await manager.close()
    if behavior.get("no_tools"):
        assert '"method": "tools/list"' not in (tmp_path / "a.jsonl").read_text()


@async_test
async def test_dead_server_stays_registered_and_other_server_survives(tmp_path):
    registry = default_registry()
    manager = MCPManager(config(tmp_path, {"a": stdio(tmp_path), "b": stdio(tmp_path, "b")}), registry)
    try:
        await manager.start()
        a, b = [next(t for t in manager.tools if t.server_name == n and t.original_name == "echo") for n in ("a", "b")]
        names = registry.names()
        failed = await manager.call(a, {"exit": True})
        assert failed.error["code"] == "mcp_unavailable"
        started = time.monotonic()
        assert (await manager.call(a, {})).error["code"] == "mcp_unavailable"
        assert time.monotonic() - started < .1
        assert registry.names() == names
        assert (await manager.call(b, {})).ok
    finally:
        await manager.close()


@async_test
async def test_effective_environment_snapshot_and_stderr_secrets(tmp_path, monkeypatch, capsys):
    entry = stdio(tmp_path)
    entry["env"] = {"SECRET_TOKEN": "TEST_SECRET", "FIXED": "original"}
    snapshot = config(tmp_path, {"a": entry})
    monkeypatch.setenv("HOME", "/changed-after-snapshot")
    monkeypatch.setenv("FIXED", "changed")
    manager = MCPManager(snapshot, default_registry())
    try:
        await manager.start()
        tool = next(t for t in manager.tools if t.original_name == "echo")
        result = await manager.call(tool, {"environment": True, "stderr": True})
        assert result.ok
        actual = result.data["structuredContent"]
        # Python/macOS 在目标程序内部自行补入 locale/CoreFoundation 标记。
        assert {k: v for k, v in actual.items() if k not in {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}} == snapshot.servers[0].env
        assert "TEST_SECRET" not in repr(manager.diagnostics) + capsys.readouterr().err
        assert not (await manager.call(tool, {"business_error": True})).ok
        assert (await manager.call(tool, {})).ok
    finally:
        await manager.close()


@async_test
async def test_forced_stdio_close_is_bounded_and_reaped(tmp_path):
    manager = MCPManager(config(tmp_path, {"a": stdio(tmp_path, args=["--behavior", '{"hang_exit":true}'])}), default_registry())
    await manager.start()
    pid = json.loads((tmp_path / "a.jsonl").read_text().splitlines()[0])["pid"]
    started = time.monotonic()
    await manager.close()
    assert time.monotonic() - started < 5.2
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not manager.records["a"].thread.is_alive()


@async_test
async def test_unresponsive_owner_does_not_hold_other_cleanup_or_main_loop(tmp_path, monkeypatch):
    from concurrent.futures import Future
    from types import SimpleNamespace
    events = []
    manager = MCPManager(config(tmp_path, {}), default_registry(), notify=events.append, close_grace=.02, close_timeout=.06)
    completed = Future()
    completed.set_result(None)
    def record(name, future):
        return SimpleNamespace(config=SimpleNamespace(name=name), finished=future, cleanup_failed=False, state="ready",
                               request_stop=lambda **kwargs: None, signal_process=lambda sig: None, process_group_alive=lambda: False)
    manager.records = {"stuck": record("stuck", Future()), "good": record("good", completed)}
    started = time.monotonic()
    await manager.close()
    assert time.monotonic() - started < .2
    assert manager.records["good"].state == "closed"
    assert any(e.server == "stuck" and "未确认" in e.message for e in events)


@async_test
async def test_cross_server_collision_does_not_overwrite_existing_tools(tmp_path, monkeypatch):
    from mewcode.mcp.tools import tool_alias
    registry = default_registry()
    original = registry.names()
    monkeypatch.setattr("mewcode.mcp.tools.tool_alias", lambda server, tool: "mcp__same" if tool == "echo" else tool_alias(server, tool))
    manager = MCPManager(config(tmp_path, {"a": stdio(tmp_path), "b": stdio(tmp_path, "b")}), registry)
    try:
        await manager.start()
        assert all(t.original_name == "second" for t in manager.tools)
        assert len(manager.tools) == 2
        assert original.issubset(registry.names())
        assert "mcp__same" not in registry.names()
    finally:
        await manager.close()


@async_test
async def test_normal_server_exit_also_reaps_its_process_group(tmp_path):
    import signal
    manager = MCPManager(config(tmp_path, {"a": stdio(tmp_path, args=["--behavior", '{"child":true}'])}), default_registry())
    await manager.start()
    events = [json.loads(line) for line in (tmp_path / "a.jsonl").read_text().splitlines()]
    child = next(e for e in events if e["method"] == "CHILD_START")
    try:
        await manager.close()
        for _ in range(30):
            try:
                os.kill(child["pid"], 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(.02)
        else:
            pytest.fail("Server 正常退出后仍有同组子进程未回收")
    finally:
        try:
            os.killpg(child["pgid"], signal.SIGKILL)
        except ProcessLookupError:
            pass
