"""外部描述和结果保持统一工具语义。"""

import json
import pickle
import re

import pytest

from mewcode.mcp.tools import adapt_result, adapt_tools, tool_alias
from mewcode.mcp.tools import adapt_request_error
from mewcode.tools import default_registry
from mewcode.tools.base import OUTPUT_LIMIT
from mewcode.tools.base import ToolError


def descriptor(name="read_file", **kwargs):
    return {"name": name, "inputSchema": {"type": "object"}, **kwargs}


def test_alias_is_stable_bounded_and_distinct():
    names = [tool_alias(server, name) for server in ("A", "a", "项目./" * 20) for name in ("read_file", "read-file", "工" * 100)]
    assert len(set(names)) == len(names)
    assert all(len(n) <= 64 and re.fullmatch(r"mcp__[A-Za-z0-9_]+", n) for n in names)
    assert tool_alias("A", "read_file") == tool_alias("A", "read_file")


def test_descriptors_serializable_conservative_and_deterministic():
    registry = default_registry()
    tools, diagnostics = adapt_tools("测试", "a" * 64, [descriptor("z"), descriptor("a", annotations={"readOnlyHint": True})], registry.names())
    assert [t.original_name for t in tools] == ["a", "z"]
    assert not diagnostics
    assert all(not t.read_only for t in tools)
    for tool in tools:
        registry.register(tool)
    assert pickle.loads(pickle.dumps(registry)).names() == registry.names()


@pytest.mark.parametrize("schema", [
    {"type": "invalid"}, {"type": "array"}, {"type": "object", "$schema": "http://json-schema.org/draft-07/schema#"},
    {"type": "object", "$ref": "https://example.test/schema"},
    {"type": "object", "$ref": "#/missing"},
    {"type": "object", "$defs": {"cycle": {"$ref": "#/$defs/cycle"}}, "$ref": "#/$defs/cycle"},
    {"type": "object", "$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}}},
    {"type": "object", "description": "not a schema", "$ref": "#/description"},
])
def test_bad_schema_skip_only_bad_tool(schema):
    tools, errors = adapt_tools("s", "a" * 64, [descriptor("bad", inputSchema=schema), descriptor("good")], ())
    assert [t.original_name for t in tools] == ["good"]
    assert errors


def test_schema_validation_failure_is_normalized_before_scheduler():
    from mewcode.mcp.tools import MCPTool
    registry = default_registry()
    schema = {"type": "object", "$defs": {"cycle": {"$ref": "#/$defs/cycle"}}, "$ref": "#/$defs/cycle"}
    # 绕过发现过滤，验证统一参数校验仍可给调度器返回配对错误。
    tool = MCPTool("test", "测试", schema, "s", "t", "a" * 64)
    registry.register(tool)
    with pytest.raises(ToolError, match="Schema"):
        registry.prepare("test", "{}")


def test_shared_local_references_still_validate_arguments():
    schema = {"type": "object", "$defs": {"text": {"type": "string"}}, "properties": {
        "a": {"$ref": "#/$defs/text"}, "b": {"$ref": "#/$defs/text"}}}
    tools, errors = adapt_tools("s", "a" * 64, [descriptor(inputSchema=schema)])
    assert not errors
    registry = default_registry()
    registry.register(tools[0])
    assert registry.prepare(tools[0].name, '{"a":"ok","b":"ok"}')[1] == {"a": "ok", "b": "ok"}
    with pytest.raises(ToolError):
        registry.prepare(tools[0].name, '{"b":42}')


def test_recursive_object_schema_is_supported_when_instances_progress():
    schema = {"type": "object", "$defs": {"node": {"type": "object", "properties": {
        "value": {"type": "integer"}, "child": {"$ref": "#/$defs/node"}}}}, "$ref": "#/$defs/node"}
    tools, errors = adapt_tools("s", "a" * 64, [descriptor(inputSchema=schema)])
    assert not errors
    registry = default_registry()
    registry.register(tools[0])
    assert registry.prepare(tools[0].name, '{"child":{"value":1}}')[1] == {"child": {"value": 1}}
    with pytest.raises(ToolError):
        registry.prepare(tools[0].name, '{"child":{"value":"bad"}}')


def test_duplicates_task_only_and_alias_collision_rejected(monkeypatch):
    tools, errors = adapt_tools("s", "a" * 64, [descriptor("x"), descriptor("x"), descriptor("task", execution={"taskSupport": "required"}), descriptor("ok")], ())
    assert [t.original_name for t in tools] == ["ok"]
    assert len(errors) == 2
    monkeypatch.setattr("mewcode.mcp.tools.tool_alias", lambda *args: "mcp__collision")
    tools, errors = adapt_tools("s", "a" * 64, [descriptor("a"), descriptor("b")], ())
    assert not tools and errors


def test_result_text_structure_binary_and_links():
    result = adapt_result({"content": [
        {"type": "text", "text": "一"}, {"type": "image", "mimeType": "image/png", "data": "SECRET_BASE64"},
        {"type": "resource_link", "uri": "https://example.test", "name": "链接"},
        {"type": "resource", "resource": {"uri": "note:test", "text": "内嵌"}},
        {"type": "text", "text": "二"}], "structuredContent": {"result": [1, 2]}})
    assert result.ok and not result.truncated
    assert result.data["structuredContent"] == {"result": [1, 2]}
    assert result.data["content"][1]["unsupported"] is True
    assert result.data["content"][3]["resource"]["text"] == "内嵌"
    assert "SECRET_BASE64" not in result.to_json()


def test_shared_budget_structure_first_and_safe_utf8_json():
    result = adapt_result({"content": [{"type": "text", "text": "猫" * 100000},
                                     {"type": "resource_link", "uri": "u" * 100000, "name": "n" * 100000}],
                           "structuredContent": {"small": "优先"}, "isError": True})
    assert not result.ok and result.error["code"] == "mcp_tool_error"
    assert result.truncated and result.data["omissions"]
    assert result.data["structuredContent"] == {"small": "优先"}
    assert len(json.dumps({k: v for k, v in result.data.items() if k != "omissions"}, ensure_ascii=False).encode()) < OUTPUT_LIMIT + 100
    json.loads(result.to_json())
    huge = adapt_result({"content": [{"type": "text", "text": "保留"}], "structuredContent": {"x": "x" * OUTPUT_LIMIT}})
    assert huge.truncated and "structuredContent" not in huge.data
    assert huge.data["content"][0]["text"] == "保留"


def test_remote_cancelled_is_ordinary_error_and_input_required_unsupported():
    result = adapt_result({"isError": True, "content": [{"type": "text", "text": "cancelled"}]})
    assert result.error["code"] == "mcp_tool_error"
    unsupported = adapt_result({"resultType": "input_required", "inputRequests": {}})
    assert unsupported.error["code"] == "mcp_unsupported_capability"


def test_request_error_details_share_budget_and_redact_connection_secrets():
    error = {"code": -32000, "message": "cancelled TEST_SECRET", "data": {"uri": "a" * 100000, "token": "TEST_SECRET"}}
    result = adapt_request_error(error, secrets=("TEST_SECRET",), limit=1000)
    assert result.error["code"] == "mcp_request_error"
    assert result.truncated and "TEST_SECRET" not in result.to_json()
    assert len(result.to_json().encode()) < 1500
