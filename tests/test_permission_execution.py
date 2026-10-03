"""用真实执行器验证拒绝先于进程与搜索内容访问。"""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json

from conftest import async_test
from mewcode.tools import default_registry
from mewcode.tools.base import ToolContext
from mewcode.tools.executor import ToolExecutor
from test_permission_runtime import manager, policy, rule


def executor(root, **kwargs):
    return ToolExecutor(default_registry(), ToolContext(root), permissions=manager(root, **kwargs))


@async_test
async def test_denied_compound_command_never_executes_prefix(tmp_path):
    policy(tmp_path, [rule("deny", "Bash(touch blocked*)")])
    ex = executor(tmp_path, mode="bypass")
    result = await ex.execute("execute_command", json.dumps({"command": "touch before; touch blocked"}))
    assert result.error["code"] == "permission_denied"
    assert result.error["details"]["not_started"]
    assert not (tmp_path / "before").exists()
    assert not (tmp_path / "blocked").exists()


@async_test
async def test_waiting_approval_has_no_start_event_or_side_effect(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    async def respond(request, cancel):
        entered.set()
        await release.wait()
        return "once"
    ex = executor(tmp_path, responder=respond)
    events = []
    operation = asyncio.create_task(ex.execute("write_file", '{"path":"a","content":"approved"}',
                                               on_event=events.append))
    await entered.wait()
    assert not (tmp_path / "a").exists()
    assert not any(event["kind"] == "tool_started" for event in events)
    release.set()
    result = await operation
    assert result.ok and (tmp_path / "a").read_text() == "approved"
    assert sum(event["kind"] == "tool_started" for event in events) == 1


@async_test
async def test_cancel_approval_returns_unstarted_cancelled(tmp_path):
    entered = asyncio.Event()
    async def respond(request, cancel):
        entered.set()
        await asyncio.Event().wait()
    ex = executor(tmp_path, responder=respond)
    cancel = asyncio.Event()
    operation = asyncio.create_task(ex.execute("write_file", '{"path":"a","content":"x"}', cancel_event=cancel))
    await entered.wait()
    cancel.set()
    result = await asyncio.wait_for(operation, 1)
    assert result.error["code"] == "cancelled" and result.error["details"]["not_started"]
    assert not (tmp_path / "a").exists()


@async_test
async def test_search_partial_and_all_denied_report_limited_success(tmp_path):
    (tmp_path / "public.txt").write_text("visible marker")
    (tmp_path / "private.txt").write_text("secret marker")
    policy(tmp_path, [rule("deny", "search_code(private.txt)", "exact")])
    ex = executor(tmp_path, mode="bypass")
    partial = await ex.execute("search_code", '{"pattern":"marker","glob":"*.txt"}')
    assert partial.ok
    assert [item["path"] for item in partial.data["matches"]] == ["public.txt"]
    assert partial.data["permission_limited"] and partial.data["skipped_files"] == 1
    assert "private.txt" not in partial.to_json() and "secret" not in partial.to_json()
    policy(tmp_path, [rule("deny", "search_code(**)")])
    empty = await ex.execute("search_code", '{"pattern":"marker","glob":"*.txt"}')
    assert empty.ok and empty.data["matches"] == []
    assert empty.data["permission_limited"] and empty.data["skipped_files"] == 2


def test_search_only_passes_authorized_files_to_real_rg(tmp_path, monkeypatch):
    from mewcode.tools import search
    (tmp_path / "public").write_text("marker visible")
    (tmp_path / "private").write_text("marker secret")
    original = search.child_process
    invocations = []
    @contextmanager
    def observe(argv, cwd):
        invocations.append(argv)
        with original(argv, cwd) as process:
            yield process
    monkeypatch.setattr(search, "child_process", observe)
    context = ToolContext(tmp_path, authorized_paths=(str(tmp_path / "public"),), permission_skipped_files=1)
    result = search.SearchCode().execute({"pattern": "marker"}, context)
    assert [item["path"] for item in result.data["matches"]] == ["public"]
    searches = [argv for argv in invocations if "--json" in argv]
    assert searches
    assert all("." not in argv[argv.index("--") + 1:] for argv in searches)
    assert all(str(tmp_path / "private") not in argv[argv.index("--") + 1:] for argv in searches)
