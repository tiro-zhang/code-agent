"""委派许可绑定完整参数，不能授予子文件许可。"""

import json

import pytest

from conftest import async_test
from mewcode.permissions.config import PermissionConfigError
from mewcode.tools.base import ToolError
from test_permission_runtime import manager, policy, rule


@async_test
async def test_agent_exact_rule_normalizes_complete_json(tmp_path):
    args = {"type":"defined", "role":"general", "prompt":"检查 src/a.py", "background":True}
    policy(tmp_path, [rule("allow", "agent(" + json.dumps(args, ensure_ascii=False) + ")", "exact")])
    runtime = manager(tmp_path)
    result = await runtime.authorize("agent", dict(reversed(tuple(args.items()))))
    assert result.arguments == args
    with pytest.raises(ToolError) as error:
        await runtime.authorize("agent", {**args, "prompt":"另一任务"})
    assert error.value.code == "permission_denied"


@async_test
async def test_agent_glob_is_whole_string_and_handles_slashes(tmp_path):
    policy(tmp_path, [rule("allow", "agent(*)")])
    runtime = manager(tmp_path)
    result = await runtime.authorize("agent", {"type":"fork", "prompt":"检查 src/a.py"})
    assert result.arguments["prompt"] == "检查 src/a.py"


@async_test
async def test_agent_session_and_permanent_grants_bind_canonical_parameters(tmp_path):
    decisions = iter(["session", "permanent"])
    async def respond(request, cancel):
        return next(decisions)
    runtime = manager(tmp_path, responder=respond)
    first = {"type":"defined", "role":"general", "prompt":"检查"}
    await runtime.authorize("agent", first)
    await runtime.authorize("agent", dict(reversed(tuple(first.items()))))
    second = {"type":"fork", "prompt":"检查"}
    await runtime.authorize("agent", second)
    fresh = manager(tmp_path)
    await fresh.authorize("agent", second)
    with pytest.raises(ToolError):
        await fresh.authorize("agent", {**second, "prompt":"改变"})
    assert len(runtime.grants.session) == 1
    assert runtime.grants.session[0].value == '{"prompt":"检查","role":"general","type":"defined"}'
    child = runtime.fork()
    with pytest.raises(ToolError) as error:
        await child.authorize("write_file", {"path":"new", "content":"未获文件批准"})
    assert error.value.code == "approval_required"
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("pattern", ['[]', 'null', '"value"', '{"value":NaN}', '{broken'])
def test_agent_exact_rule_requires_complete_finite_json_object(tmp_path, pattern):
    policy(tmp_path, [rule("allow", f"agent({pattern})", "exact")])
    with pytest.raises(PermissionConfigError):
        manager(tmp_path).config.load()
