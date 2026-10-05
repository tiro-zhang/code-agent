"""统一委派 Schema 不枚举角色，参数组合在执行前校验。"""

import json

import pytest

from mewcode.tools import default_registry
from mewcode.tools.base import ToolError
from mewcode.providers.tool_messages import anthropic_tools, openai_tools


def registered():
    registry = default_registry()
    assert "agent" in registry.names(), "缺少统一 Agent 系统入口"
    return registry


@pytest.mark.parametrize("arguments", [
    {"type":"defined", "role":"explore", "prompt":"目标"},
    {"type":"defined", "role":"general", "prompt":"目标", "background":True},
    {"type":"fork", "prompt":"目标"}, {"type":"fork", "prompt":"目标", "background":True},
])
def test_valid_defined_and_fork_use_one_serial_system_tool(arguments):
    registry = registered()
    tool, result = registry.prepare("agent", json.dumps(arguments), allowed_tools=frozenset())
    assert tool.system and not tool.read_only
    assert result == arguments
    definition = next(item for item in registry.definitions(allowed_tools=frozenset()) if item.name == "agent")
    assert anthropic_tools([definition])[0]["input_schema"] == openai_tools([definition])[0]["function"]["parameters"]


@pytest.mark.parametrize("arguments", [
    {"type":"defined", "prompt":"目标"}, {"type":"fork", "role":"general", "prompt":"目标"},
    {"type":"fork", "prompt":"目标", "background":False}, {"type":"fork", "prompt":" "},
    {"type":"fork", "prompt":"目标", "model":"opus"}, {"type":"fork", "prompt":"目标", "background":1},
    {"type":"other", "prompt":"目标"},
])
def test_invalid_combinations_never_pass_preparation(arguments):
    registry = registered()
    with pytest.raises(ToolError) as error:
        registry.prepare("agent", json.dumps(arguments))
    assert error.value.code == "invalid_arguments"
