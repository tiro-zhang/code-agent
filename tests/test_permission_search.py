"""搜索授权边界使用真实 rg 验证，不读取未获准文件后再过滤。"""

import asyncio
from contextlib import contextmanager
import os
import sys

import pytest

from conftest import async_test
from mewcode.tools import search
from mewcode.tools.base import ToolContext, ToolError


@async_test
async def test_candidate_enumeration_only_uses_metadata_and_preserves_visibility(tmp_path, monkeypatch):
    (tmp_path / "public.py").write_text("visible")
    (tmp_path / "binary.py").write_bytes(b"a\0b")
    (tmp_path / ".hidden.py").write_text("hidden")
    (tmp_path / "ignored.py").write_text("ignored")
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / "alias.py").symlink_to("public.py")
    observed = []
    original = asyncio.create_subprocess_exec
    async def observe(*argv, **kwargs):
        observed.append(argv)
        return await original(*argv, **kwargs)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", observe)
    candidates = await search.enumerate_candidates(search.SearchCode(), {"pattern": "marker", "glob": "*.py"},
                                                   ToolContext(tmp_path), asyncio.Event())
    assert candidates == (str(tmp_path / "binary.py"), str(tmp_path / "public.py"))
    assert observed and all("--files" in argv and "--json" not in argv for argv in observed)


def test_authorized_glob_never_returns_denied_names(tmp_path):
    (tmp_path / "public.txt").write_text("public")
    (tmp_path / "private.txt").write_text("secret")
    context = ToolContext(tmp_path, authorized_paths=(str(tmp_path / "public.txt"),), permission_skipped_files=1)
    result = search.GlobFiles().execute({"pattern": "*.txt"}, context)
    assert result.data == {"paths": ["public.txt"], "permission_limited": True, "skipped_files": 1}
    assert not result.truncated


def test_authorized_search_only_opens_explicit_files(tmp_path, monkeypatch):
    (tmp_path / "public.txt").write_text("marker visible")
    (tmp_path / "private.txt").write_text("marker secret")
    observed = []
    original = search.child_process
    @contextmanager
    def observe(argv, cwd):
        observed.append(argv)
        with original(argv, cwd) as process:
            yield process
    monkeypatch.setattr(search, "child_process", observe)
    result = search.SearchCode().execute({"pattern": "marker"},
        ToolContext(tmp_path, authorized_paths=(str(tmp_path / "public.txt"),), permission_skipped_files=1))
    assert [item["path"] for item in result.data["matches"]] == ["public.txt"]
    assert result.data["permission_limited"] and result.data["skipped_files"] == 1
    assert "private.txt" not in result.to_json() and "secret" not in result.to_json()
    assert observed
    assert all(argv[argv.index("--") + 1:] == [str(tmp_path / "public.txt")] for argv in observed)


def test_empty_authorized_search_still_validates_regex_without_scanning(tmp_path, monkeypatch):
    (tmp_path / "private").write_text("marker secret")
    observed = []
    original = search.child_process
    @contextmanager
    def observe(argv, cwd):
        observed.append(argv)
        with original(argv, cwd) as process:
            yield process
    monkeypatch.setattr(search, "child_process", observe)
    context = ToolContext(tmp_path, authorized_paths=(), permission_skipped_files=1)
    result = search.SearchCode().execute({"pattern": "marker"}, context)
    assert result.ok and result.data == {"matches": [], "permission_limited": True, "skipped_files": 1}
    with pytest.raises(ToolError) as caught:
        search.SearchCode().execute({"pattern": "["}, context)
    assert caught.value.code == "invalid_pattern"
    assert all(argv[argv.index("--") + 1:] == ["-"] for argv in observed)


def test_low_level_search_enumerates_then_searches_only_explicit_candidates(tmp_path, monkeypatch):
    (tmp_path / "visible.py").write_text("marker")
    (tmp_path / "other.txt").write_text("marker")
    observed = []
    original = search.child_process
    @contextmanager
    def observe(argv, cwd):
        observed.append(argv)
        with original(argv, cwd) as process:
            yield process
    monkeypatch.setattr(search, "child_process", observe)
    result = search.SearchCode().execute({"pattern": "marker", "glob": "*.py"}, ToolContext(tmp_path))
    assert [item["path"] for item in result.data["matches"]] == ["visible.py"]
    assert result.data["permission_limited"] is False
    content_calls = [argv for argv in observed if "--json" in argv]
    assert content_calls and all(argv[argv.index("--") + 1:] == [str(tmp_path / "visible.py")] for argv in content_calls)


def test_explicit_content_search_batches_large_candidate_sets(tmp_path, monkeypatch):
    paths = []
    for index in range(270):
        path = tmp_path / f"file-{index:03d}.txt"
        path.write_text("marker\n")
        paths.append(str(path))
    observed = []
    original = search.child_process
    @contextmanager
    def observe(argv, cwd):
        observed.append(argv)
        with original(argv, cwd) as process:
            yield process
    monkeypatch.setattr(search, "child_process", observe)
    result = search.SearchCode().execute({"pattern": "marker", "max_results": 1000},
                                        ToolContext(tmp_path, authorized_paths=tuple(paths)))
    assert len(result.data["matches"]) == 270 and not result.truncated
    assert len(observed) > 1
    assert all("." not in argv[argv.index("--") + 1:] for argv in observed)


def test_authorized_symlink_replacement_cannot_expand_search_scope(tmp_path):
    path = tmp_path / "public"
    path.write_text("public")
    (tmp_path / "private").write_text("secret")
    context = ToolContext(tmp_path, authorized_paths=(str(path),))
    path.unlink()
    path.symlink_to("private")
    with pytest.raises(ToolError) as caught:
        search.SearchCode().execute({"pattern": "secret"}, context)
    assert caught.value.code == "permission_denied"
    assert caught.value.details["not_started"]


@async_test
async def test_cancelled_enumeration_reaps_metadata_process(tmp_path, monkeypatch):
    script = tmp_path / "metadata.py"
    marker = tmp_path / "started"
    script.write_text("import os, pathlib, time\n" +
                      f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n" +
                      "time.sleep(120)\n")
    monkeypatch.setattr(search, "rg_command", lambda: [sys.executable, str(script)])
    cancel = asyncio.Event()
    task = asyncio.create_task(search.enumerate_candidates(search.GlobFiles(), {"pattern": "**"},
                                                          ToolContext(tmp_path), cancel))
    for _ in range(100):
        if marker.exists():
            break
        await asyncio.sleep(0.01)
    assert marker.exists()
    pid = int(marker.read_text())
    cancel.set()
    with pytest.raises(ToolError) as caught:
        await asyncio.wait_for(task, 1)
    assert caught.value.code == "cancelled" and caught.value.details["not_started"]
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@async_test
async def test_task_cancellation_during_subprocess_creation_still_reaps_process(tmp_path, monkeypatch):
    script = tmp_path / "metadata.py"
    script.write_text("import time\ntime.sleep(120)\n")
    monkeypatch.setattr(search, "rg_command", lambda: [sys.executable, str(script)])
    launched, release = asyncio.Event(), asyncio.Event()
    original = asyncio.create_subprocess_exec
    processes = []
    async def delayed_return(*argv, **kwargs):
        process = await original(*argv, **kwargs)
        processes.append(process)
        launched.set()
        await release.wait()
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_return)
    task = asyncio.create_task(search.enumerate_candidates(search.GlobFiles(), {"pattern": "**"},
                                                          ToolContext(tmp_path), asyncio.Event()))
    await launched.wait()
    task.cancel()
    release.set()
    try:
        with pytest.raises(ToolError) as caught:
            await asyncio.wait_for(task, 1)
        assert caught.value.code == "cancelled"
        with pytest.raises(ProcessLookupError):
            os.kill(processes[0].pid, 0)
    finally:
        # 红灯阶段也回收真实测试进程，避免复现本身遗留后台进程。
        for process in processes:
            if process.returncode is None:
                os.killpg(process.pid, 9)
                await process.wait()


@async_test
async def test_enumeration_launch_failure_is_a_structured_unstarted_error(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "rg_command", lambda: [str(tmp_path / "missing-rg")])
    with pytest.raises(ToolError) as caught:
        await search.enumerate_candidates(search.GlobFiles(), {"pattern": "**"},
                                          ToolContext(tmp_path), asyncio.Event())
    assert caught.value.code == "permission_check_failed"
    assert caught.value.details["not_started"]
