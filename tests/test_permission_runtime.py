"""在真实配置与批准存储上验证授权边界和取消。"""

import asyncio
import json

import pytest

from conftest import async_test
from mewcode.tools.base import ToolError


def manager(root, **kwargs):
    from mewcode.permissions.runtime import PermissionManager
    return PermissionManager(root, user_path=root / "user-policy.yaml", **kwargs)


def policy(root, rules):
    import yaml
    target = root / ".mewcode" / "permissions.yaml"
    target.parent.mkdir(exist_ok=True)
    target.write_text(yaml.safe_dump({"rules": rules}))


def rule(effect, expression, match="glob"):
    return {"effect": effect, "rule": expression, "match": match}


@async_test
async def test_default_without_human_denies_single_target(tmp_path):
    runtime = manager(tmp_path)
    with pytest.raises(ToolError) as caught:
        await runtime.authorize("write_file", {"path": "new", "content": "text"})
    assert caught.value.code == "permission_denied"
    assert caught.value.details["not_started"]
    assert not (tmp_path / "new").exists()


@async_test
async def test_once_reprompts_and_session_grant_is_scoped(tmp_path):
    calls = []
    choices = iter(["once", "session", "deny"])
    async def respond(request, cancel):
        calls.append(request)
        return next(choices)
    runtime = manager(tmp_path, responder=respond)
    for _ in range(3):
        await runtime.authorize("read_file", {"path": "a"})
    assert len(calls) == 2
    with pytest.raises(ToolError):
        await runtime.authorize("edit_file", {"path": "a", "old_text": "a", "new_text": "b"})
    assert len(calls) == 3


@async_test
async def test_new_deny_after_approval_prevents_execution(tmp_path):
    async def respond(request, cancel):
        policy(tmp_path, [rule("deny", "write_file(a)", "exact")])
        return "permanent"
    runtime = manager(tmp_path, responder=respond)
    with pytest.raises(ToolError) as caught:
        await runtime.authorize("write_file", {"path": "a", "content": "blocked"})
    assert caught.value.code == "permission_denied"
    assert not (tmp_path / "a").exists()


@async_test
async def test_bypass_preserves_deny_blacklist_and_protected_config(tmp_path):
    policy(tmp_path, [rule("deny", "read_file(secret)", "exact")])
    runtime = manager(tmp_path, mode="bypass")
    for tool, arguments in [
        ("read_file", {"path": "secret"}),
        ("execute_command", {"command": "rm -rf /"}),
        ("write_file", {"path": ".mewcode/permissions.local.yaml", "content": "rules: []"}),
    ]:
        with pytest.raises(ToolError) as caught:
            await runtime.authorize(tool, arguments)
        assert caught.value.code == "permission_denied"


@async_test
async def test_search_filters_denied_and_unanswered_targets(tmp_path):
    policy(tmp_path, [rule("allow", "search_code(public)", "exact"),
                      rule("deny", "search_code(secret)", "exact")])
    paths = tuple(str(tmp_path / name) for name in ("public", "secret", "unanswered"))
    runtime = manager(tmp_path)
    granted = await runtime.authorize("search_code", {"pattern": "word"}, targets=paths)
    assert granted.targets == (str(tmp_path / "public"),)
    assert granted.skipped_files == 2


@async_test
async def test_prompt_queue_serial_and_cancel_does_not_leave_waiters(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def respond(request, cancel):
        calls.append(request)
        entered.set()
        await release.wait()
        return "once"
    runtime = manager(tmp_path, responder=respond)
    cancel = asyncio.Event()
    first = asyncio.create_task(runtime.authorize("read_file", {"path": "a"}, cancel_event=cancel))
    await entered.wait()
    second = asyncio.create_task(runtime.authorize("read_file", {"path": "b"}, cancel_event=cancel))
    await asyncio.sleep(0)
    assert len(calls) == 1
    cancel.set()
    results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 1)
    assert all(isinstance(result, ToolError) and result.code == "cancelled" for result in results)
    release.set()


@async_test
async def test_symlink_target_changes_require_fresh_approval(tmp_path):
    (tmp_path / "a").write_text("a")
    (tmp_path / "b").write_text("b")
    link = tmp_path / "link"
    link.symlink_to("a")
    calls = []
    async def respond(request, cancel):
        calls.append(request.targets)
        if len(calls) == 1:
            link.unlink()
            link.symlink_to("b")
            return "once"
        return "deny"
    with pytest.raises(ToolError):
        await manager(tmp_path, responder=respond).authorize("read_file", {"path": "link"})
    assert calls == [(str(tmp_path / "a"),), (str(tmp_path / "b"),)]


@async_test
async def test_simple_allow_does_not_authorize_compound_or_its_denied_child(tmp_path):
    policy(tmp_path, [rule("allow", "Bash(git *)"), rule("deny", "Bash(git push*)")])
    runtime = manager(tmp_path)
    await runtime.authorize("execute_command", {"command": "git status"})
    for command in ("git status && printf done", "printf started; git 'push'"):
        with pytest.raises(ToolError) as caught:
            await runtime.authorize("execute_command", {"command": command})
        assert caught.value.code == "permission_denied"


@async_test
async def test_runtime_bad_config_fails_closed_then_recovers(tmp_path):
    runtime = manager(tmp_path, mode="bypass")
    policy(tmp_path, [])
    target = tmp_path / ".mewcode" / "permissions.yaml"
    target.write_text("rules: [")
    with pytest.raises(ToolError) as caught:
        await runtime.authorize("read_file", {"path": "a"})
    assert caught.value.code == "permission_config_error"
    policy(tmp_path, [])
    result = await runtime.authorize("read_file", {"path": "a"})
    assert result.arguments == {"path": "a"}
