"""委派只继承已发送输入，不继承正在配对的调用。"""

from pathlib import Path

from conftest import ScriptedProvider, async_test, collect, permission_bypass
from mewcode.agent import Agent
from mewcode.tools import default_registry
from mewcode.tools.base import ToolContext, ToolDefinition, ToolResult
from mewcode.tools.executor import ToolExecutor
from mewcode.types import Message, ProviderEvent, ToolCall


@async_test
async def test_sent_request_snapshot_excludes_current_batch_and_is_deeply_isolated(tmp_path):
    calls = (ToolCall("c1", "read_file", '{"path":"input.txt"}'), ToolCall("c2", "missing", '{}'))
    provider = ScriptedProvider([[ProviderEvent("completed", message=Message("assistant", "委派批次", calls))]])
    (tmp_path / "input.txt").write_text("目标内容")
    executor = ToolExecutor(default_registry(), ToolContext(tmp_path), permissions=permission_bypass(tmp_path))
    agent = Agent(provider, executor, max_iterations=1)
    assert hasattr(agent, "last_request"), "缺少已发送请求快照"
    old_call = ToolCall("old", "read_file", '{"path":"old.txt"}')
    blocks = ({"type":"thinking", "thinking":"原思考", "signature":"原签名"},
              {"type":"tool_use", "id":"old", "name":"read_file", "input":{"path":"old.txt"}})
    history = [Message("user", "旧目标"), Message("assistant", tool_calls=(old_call,), provider_content=blocks),
               Message("tool", tool_call_id="old", tool_result=ToolResult.success({"lines":["旧结果"]}))]
    try:
        await collect(agent.run("当前目标", history=history, mode="execute"))
        snapshot = agent.last_request.copy()
        assert tuple(snapshot.messages) == provider.requests[0][0]
        assert [call.id for message in snapshot.messages for call in message.tool_calls] == ["old"]
        assert snapshot.system_prompt == provider.requests[0][1]["system_prompt"]
        assert [tool.name for tool in snapshot.tools] == [tool.name for tool in provider.requests[0][1]["tools"]]
        history[1].provider_content[0]["signature"] = "父改写"
        history[2].tool_result.data["lines"].append("父追加")
        assert snapshot.messages[1].provider_content[0]["signature"] == "原签名"
        assert snapshot.messages[2].tool_result.data == {"lines":["旧结果"]}
        snapshot.messages[1].provider_content[1]["input"]["path"] = "子改写"
        assert agent.last_request.messages[1].provider_content[1]["input"]["path"] == "old.txt"
    finally:
        await agent.aclose()
