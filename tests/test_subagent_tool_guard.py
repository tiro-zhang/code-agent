"""系统入口例外不能越过运行硬上限或授权后的收紧。"""

import json

from conftest import async_test, permission_bypass
from mewcode.tools.base import ToolContext, ToolError, object_schema
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import ToolRegistry
from test_permission_runtime import manager


class SystemEntry:
    name = "entry"
    description = "主进程入口"
    system = True
    read_only = False
    input_schema = object_schema({"isolated":{"type":"boolean"}}, [])


def executor_for(tmp_path, permissions=None):
    executor = ToolExecutor(ToolRegistry([SystemEntry()]), ToolContext(tmp_path), permissions=permissions or permission_bypass(tmp_path))
    assert hasattr(executor, "guard"), "执行器缺少系统分流前的运行硬过滤"
    return executor


@async_test
async def test_system_tool_is_blocked_before_permission_or_handler(tmp_path):
    permissions = manager(tmp_path)
    executor = executor_for(tmp_path, permissions)
    def guard(name, arguments):
        raise ToolError("tool_not_allowed", "运行禁止此入口", not_started=True)
    executor.guard = guard
    async def handler(arguments, **options):
        (tmp_path / "unexpected").touch()
    executor.system_handlers["entry"] = handler
    result = await executor.execute("entry", "{}", allowed_tools=frozenset())
    assert result.error["code"] == "tool_not_allowed"
    assert result.error["details"]["not_started"]
    assert not (tmp_path / "unexpected").exists()
    unknown = await executor.execute("missing", "{}")
    assert unknown.error["code"] == "unknown_tool"


@async_test
async def test_argument_guard_blocks_isolated_branch_before_system_dispatch(tmp_path):
    executor = executor_for(tmp_path)
    def guard(name, arguments):
        if arguments and arguments.get("isolated"):
            raise ToolError("tool_not_allowed", "禁止下一层运行", not_started=True)
    executor.guard = guard
    async def handler(arguments, **options):
        (tmp_path / "unexpected").touch()
    executor.system_handlers["entry"] = handler
    result = await executor.execute("entry", '{"isolated":true}')
    assert result.error["code"] == "tool_not_allowed"
    assert not (tmp_path / "unexpected").exists()


@async_test
async def test_hard_scope_rechecked_after_approval_wait(tmp_path):
    allowed = {"entry"}
    async def respond(request, cancel):
        allowed.clear()
        return "once"
    executor = executor_for(tmp_path, manager(tmp_path, responder=respond))
    def guard(name, arguments):
        if name not in allowed:
            raise ToolError("tool_not_allowed", "等待期间范围收紧", not_started=True)
    executor.guard = guard
    async def handler(arguments, **options):
        (tmp_path / "unexpected").touch()
    executor.system_handlers["entry"] = handler
    result = await executor.execute("entry", "{}")
    assert result.error["code"] == "tool_not_allowed"
    assert not (tmp_path / "unexpected").exists()
