"""外部工具沿用权限矩阵，批准身份精确绑定参数及连接配置。"""

from dataclasses import replace
import json

import pytest
import yaml

from conftest import async_test
from mewcode.mcp.tools import MCPTool, tool_alias
from mewcode.permissions.config import PermissionConfig, PermissionConfigError
from mewcode.permissions.runtime import PermissionManager
from mewcode.tools.base import ToolError


TOOL = MCPTool(tool_alias("server", "tool"), "test", {"type": "object"}, "server", "tool", "a" * 64)


def manager(tmp_path, mode="default", responder=None, tool=TOOL):
    result = PermissionManager(tmp_path, mode=mode, responder=responder, user_path=tmp_path / "absent")
    result.bind_mcp_tools((tool,))
    return result


def rules(tmp_path, effect, pattern='{"value":1}', match="exact"):
    path = tmp_path / ".mewcode" / "permissions.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump({"rules": [{"effect": effect, "rule": f"{TOOL.name}({pattern})", "match": match}]}))


@pytest.mark.parametrize("mode", ["strict", "default", "bypass"])
@pytest.mark.parametrize("effect", [None, "deny", "ask", "allow"])
@async_test
async def test_mcp_matrix(tmp_path, mode, effect):
    if effect:
        rules(tmp_path, effect)
    asked = []
    async def approve(request, cancel):
        asked.append(request)
        return "once"
    permissions = manager(tmp_path, mode, approve)
    if effect == "deny":
        with pytest.raises(ToolError, match="禁止"):
            await permissions.authorize(TOOL.name, {"value": 1})
    else:
        await permissions.authorize(TOOL.name, {"value": 1})
        assert bool(asked) == (mode == "strict" or (mode == "default" and effect != "allow"))


@async_test
async def test_mcp_rules_canonical_json_offline_and_not_local_paths(tmp_path):
    rules(tmp_path, "allow", '{ "z": 1, "path": "/outside", "a": [1,2]}')
    config = PermissionConfig(tmp_path, user_path=tmp_path / "absent")
    assert config.load().rules
    permissions = manager(tmp_path)
    await permissions.authorize(TOOL.name, {"a": [1, 2], "path": "/outside", "z": 1})
    with pytest.raises(ToolError):
        await permissions.authorize(TOOL.name, {"a": [2, 1], "path": "/outside", "z": 1})
    rules(tmp_path, "allow", '*"path":"/remote/*"*', match="glob")
    await permissions.authorize(TOOL.name, {"path": "/remote/file"})


@pytest.mark.parametrize("duration", ["session", "permanent"])
@async_test
async def test_exact_grant_identity_reuse_restart_and_revoke(tmp_path, duration):
    asked = []
    async def approve(request, cancel):
        asked.append(request)
        return duration
    permissions = manager(tmp_path, responder=approve)
    await permissions.authorize(TOOL.name, {"a": 1, "b": 2})
    await permissions.authorize(TOOL.name, {"b": 2, "a": 1})
    assert len(asked) == 1
    permissions.bind_mcp_tools((replace(TOOL, fingerprint="b" * 64),))
    await permissions.authorize(TOOL.name, {"a": 1, "b": 2})
    assert len(asked) == 2
    await permissions.authorize(TOOL.name, {"a": 2, "b": 2})
    assert len(asked) == 3
    restarted = manager(tmp_path, tool=replace(TOOL, fingerprint="b" * 64))
    if duration == "permanent":
        await restarted.authorize(TOOL.name, {"a": 2, "b": 2})
        assert restarted.config.load().approvals[0].kind == "mcp"
    else:
        with pytest.raises(ToolError):
            await restarted.authorize(TOOL.name, {"a": 2, "b": 2})
    permissions.grants.revoke(duration)
    assert not permissions.grants.session
    assert not permissions.config.load().approvals


@async_test
async def test_deny_precedes_existing_grant_and_save_failure_is_once(tmp_path, monkeypatch):
    async def approve(request, cancel):
        return "permanent"
    permissions = manager(tmp_path, responder=approve)
    def fail(*args):
        raise PermissionConfigError("测试失败")
    monkeypatch.setattr(permissions.config, "save_approvals", fail)
    await permissions.authorize(TOOL.name, {"value": 1})
    permissions.responder = None
    with pytest.raises(ToolError):
        await permissions.authorize(TOOL.name, {"value": 1})
    permissions.mode = "bypass"
    rules(tmp_path, "deny")
    with pytest.raises(ToolError):
        await permissions.authorize(TOOL.name, {"value": 1})


@async_test
async def test_approval_shows_safe_connection_without_url_credentials(tmp_path):
    import asyncio
    from io import StringIO
    from mewcode.mcp.config import ServerConfig
    from mewcode.permissions.terminal import InputReader, TerminalApproval
    config = ServerConfig("server", "http", "a" * 64, url="https://example.test:8443/SECRET?token=SECRET#SECRET",
                          headers={"Authorization": "Bearer SECRET"})
    tool = replace(TOOL, connection_description=config.safe_description)
    output = StringIO()
    approve = TerminalApproval(InputReader(StringIO("2\n"), interactive=True), output, root=tmp_path)
    permissions = manager(tmp_path, responder=approve, tool=tool)
    await permissions.authorize(tool.name, {"value": 1}, cancel_event=asyncio.Event())
    shown = output.getvalue()
    assert "HTTP · https://example.test:8443" in shown
    assert "外部 Server> server" in shown and "原始工具> tool" in shown
    assert "SECRET" not in shown
