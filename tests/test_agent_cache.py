"""首次 Fork 追加目标时保留协议共同前缀。"""

from dataclasses import replace

import pytest

from conftest import async_test, collect
from mewcode.config import ProviderConfig
from mewcode.providers.anthropic import AnthropicProvider
from mewcode.tools.base import ToolDefinition, ToolResult
from mewcode.types import Message, ToolCall
from test_provider_sharing import SharedClient


@async_test
async def test_official_claude_caches_history_and_fork_preserves_common_blocks():
    config = ProviderConfig("Claude", "anthropic", "claude-sonnet-4-6", "https://api.anthropic.com", "test-key", False, context_window=128000)
    client = SharedClient("anthropic")
    provider = AnthropicProvider(config, client=client)
    call = ToolCall("old", "read_file", '{"path":"input.txt"}')
    history = [Message("user", "旧目标"), Message("assistant", tool_calls=(call,), provider_content=(
        {"type":"thinking", "thinking":"分析", "signature":"保留签名"},
        {"type":"tool_use", "id":"old", "name":"read_file", "input":{"path":"input.txt"}})),
        Message("tool", tool_call_id="old", tool_result=ToolResult.success({"content":"证据"})),
        Message("user", "父目标"), Message("context", "当前上下文")]
    tools = (ToolDefinition("read_file", "读文件", {"type":"object"}), ToolDefinition("agent", "委派", {"type":"object"}))
    await collect(provider.stream(history, tools=tools, system_prompt="固定系统"))
    await collect(provider.stream([*history, Message("user", "Fork 子目标"), Message("context", "子身份")], tools=tools, system_prompt="固定系统"))
    parent, fork = client.requests
    assert parent.get("cache_control") == {"type":"ephemeral"}, "必须缓存消息历史而不仅是系统"
    assert fork.get("cache_control") == {"type":"ephemeral"}
    assert parent["tools"] == fork["tools"]
    assert parent["system"] == fork["system"]
    assert parent["messages"][:-1] == fork["messages"][:-1]
    assert parent["messages"][-1]["content"] == fork["messages"][-1]["content"][:-2]
    assert fork["messages"][1]["content"][0]["signature"] == "保留签名"


@pytest.mark.parametrize("url", ["https://unknown.example/v1", "https://api.deepseek.com/anthropic", "https://api.anthropic.com.evil.example"])
@async_test
async def test_compatible_services_never_receive_claude_cache_extensions(url):
    config = ProviderConfig("兼容", "anthropic", "other-model", url, "test-key", False, context_window=128000)
    client = SharedClient("anthropic")
    await collect(AnthropicProvider(config, client=client).stream([Message("user", "目标")], system_prompt="系统"))
    request = client.requests[-1]
    assert "cache_control" not in request
    assert request["system"] == "系统"
