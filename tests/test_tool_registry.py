"""校验工具发现和拒绝非法参数，防止错误输入进入实际操作。"""

import json
import pytest


def test_registry_rejects_duplicates_and_invalid_inputs():
    from mewcode.tools.base import ToolError, ToolResult
    from mewcode.tools.registry import ToolRegistry

    class Echo:
        name = "echo"
        description = "返回文字"
        input_schema = {"type": "object", "properties": {"text": {"type": "string"}},
                        "required": ["text"], "additionalProperties": False}

        def execute(self, arguments, context):
            return ToolResult.success({"text": arguments["text"]})

    tool = Echo()
    registry = ToolRegistry([tool])
    assert registry.definitions()[0].name == "echo"
    assert registry.get("echo") is tool
    with pytest.raises(ValueError, match="重复"):
        registry.register(Echo())
    for raw in ['{', '[]', '{}', '{"text":2}', '{"text":"a","other":1}']:
        with pytest.raises(ToolError) as error:
            registry.prepare("echo", raw)
        assert error.value.code == "invalid_arguments"
    with pytest.raises(ToolError) as error:
        registry.prepare("missing", '{}')
    assert error.value.code == "unknown_tool"
    _, arguments = registry.prepare("echo", '{"text":"猫"}')
    result = tool.execute(arguments, None)
    assert json.loads(result.to_json()) == {
        "ok": True, "data": {"text": "猫"}, "error": None, "truncated": False,
    }
    failed = ToolResult.failure("timeout", "执行超时", data={"stdout": "部分"}, truncated=True)
    assert ToolResult.from_dict(json.loads(failed.to_json())) == failed


def test_message_retains_call_result_and_provider_blocks():
    from mewcode.types import Message, StreamEvent, ToolCall
    from mewcode.tools.base import ToolResult

    call = ToolCall("call-1", "read_file", '{"path":"a"}')
    message = Message("assistant", "读取中", tool_calls=(call,),
                      provider_content=({"type": "thinking", "thinking": "思考", "signature": "sig"},))
    completed = StreamEvent("completed", message=message)
    assert completed.message.tool_calls[0].id == "call-1"
    result = Message("tool", tool_call_id=call.id, tool_result=ToolResult.failure("file_not_found", "不存在"))
    assert result.tool_result.error["code"] == "file_not_found"
    assert completed.message.provider_content[0]["signature"] == "sig"
