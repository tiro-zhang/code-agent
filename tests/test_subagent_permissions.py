"""新子运行的批准副本、非交互及实时拒绝边界。"""

import asyncio

import pytest

from conftest import async_test
from mewcode.tools.base import ToolError
from test_permission_runtime import manager, policy, rule


def child_of(parent, mode="inherit"):
    assert hasattr(parent, "fork"), "缺少独立的子权限快照"
    return parent.fork(mode)


@pytest.mark.parametrize("parent_mode,role_mode,want", [
    ("bypass", "bypass", "bypass"), ("bypass", "default", "default"), ("bypass", "strict", "strict"),
    ("default", "bypass", "default"), ("default", "default", "default"), ("default", "strict", "strict"),
    ("strict", "bypass", "strict"), ("strict", "default", "strict"), ("strict", "strict", "strict"),
    ("strict", "inherit", "strict"),
])
def test_role_mode_only_tightens_parent(tmp_path, parent_mode, role_mode, want):
    parent = manager(tmp_path, mode=parent_mode)
    child = child_of(parent, role_mode)
    assert child.mode == want
    assert parent.mode == parent_mode


@async_test
async def test_session_grants_are_independent_and_once_is_not_inherited(tmp_path):
    decisions = iter(["session", "once"])
    async def respond(request, cancel):
        return next(decisions)
    parent = manager(tmp_path, responder=respond)
    await parent.authorize("read_file", {"path":"granted"})
    await parent.authorize("read_file", {"path":"once"})
    child = child_of(parent)
    parent.grants.revoke("session")
    parent.grants.remember("read_file", "path", [str(tmp_path / "later")], "session")
    granted = await child.authorize("read_file", {"path":"granted"})
    assert granted.targets == (str(tmp_path / "granted"),)
    for path in ("once", "later"):
        with pytest.raises(ToolError) as error:
            await child.authorize("read_file", {"path":path})
        assert error.value.code == "approval_required"
        assert error.value.details["not_started"]
    assert [approval.value for approval in parent.grants.session] == [str(tmp_path / "later")]
    assert [approval.value for approval in child.grants.session] == [str(tmp_path / "granted")]


@async_test
async def test_new_deny_and_permanent_revocation_are_checked_fresh(tmp_path):
    parent = manager(tmp_path)
    parent.grants.remember("read_file", "path", [str(tmp_path / "a")], "permanent")
    child = child_of(parent)
    await child.authorize("read_file", {"path":"a"})
    parent.grants.revoke("permanent")
    with pytest.raises(ToolError) as error:
        await child.authorize("read_file", {"path":"a"})
    assert error.value.code == "approval_required"
    parent.grants.remember("read_file", "path", [str(tmp_path / "a")], "session")
    second = child_of(parent)
    policy(tmp_path, [rule("deny", "read_file(a)", "exact")])
    with pytest.raises(ToolError) as error:
        await second.authorize("read_file", {"path":"a"})
    assert error.value.code == "permission_denied"


@async_test
async def test_search_excludes_unapproved_candidates_without_input(tmp_path):
    policy(tmp_path, [rule("allow", "search_code(public)", "exact"), rule("deny", "search_code(secret)", "exact")])
    child = child_of(manager(tmp_path))
    targets = tuple(str(tmp_path / name) for name in ("public", "secret", "unanswered"))
    result = await child.authorize("search_code", {"pattern":"word"}, targets=targets)
    assert result.targets == (str(tmp_path / "public"),)
    assert result.skipped_files == 2
    restricted = await child.authorize("glob_files", {"pattern":"*"}, targets=targets)
    assert restricted.targets == ()
    assert restricted.skipped_files == 3
    assert child.grants.session == ()


@async_test
async def test_noninteractive_hard_checks_and_cancel_are_not_approval(tmp_path):
    child = child_of(manager(tmp_path, mode="bypass"))
    for tool, arguments in (("execute_command", {"command":"rm -rf /"}), ("read_file", {"path":"../outside"})):
        with pytest.raises(ToolError) as error:
            await child.authorize(tool, arguments)
        assert error.value.code != "approval_required"
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(ToolError) as error:
        await child.authorize("read_file", {"path":"a"}, cancel_event=cancel)
    assert error.value.code == "cancelled"
