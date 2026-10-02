"""工具定义和历史消息的协议转换。"""

from collections.abc import Sequence
from copy import deepcopy
import json

from ..tools.base import ToolDefinition, strict_json
from ..types import Message, ProviderError, ToolCall


def validate_calls(calls: Sequence[ToolCall]) -> None:
    if any(not isinstance(call.id, str) or not call.id.strip()
           or not isinstance(call.name, str) or not call.name.strip() for call in calls):
        raise ProviderError("工具协议错误：调用 ID 或工具名称必须是非空字符串")
    ids = [call.id for call in calls]
    if len(set(ids)) != len(ids):
        raise ProviderError("工具协议错误：调用 ID 缺失、重复或工具名称缺失")


def input_object(raw: str) -> dict:
    try:
        value = strict_json(raw)
        if isinstance(value, dict):
            return value
    except (ValueError, RecursionError):
        pass
    # Messages 要求 input 是对象；保留坏参数供模型理解失败记录，不用于执行。
    return {"_mewcode_invalid_arguments": raw}


def openai_tools(definitions: Sequence[ToolDefinition]) -> list[dict]:
    return [{"type": "function", "function": {"name": d.name, "description": d.description,
             "parameters": d.input_schema}} for d in definitions]


def anthropic_tools(definitions: Sequence[ToolDefinition]) -> list[dict]:
    return [{"name": d.name, "description": d.description, "input_schema": d.input_schema} for d in definitions]


def openai_messages(messages: Sequence[Message]) -> list[dict]:
    result = []
    for message in messages:
        item = {"role": "user" if message.role == "context" else message.role, "content": message.content}
        if message.role == "tool":
            item.update(tool_call_id=message.tool_call_id, content=message.tool_result.to_json())
        if message.tool_calls:
            item["tool_calls"] = [{"id": c.id, "type": "function", "function": {
                "name": c.name, "arguments": c.arguments}} for c in message.tool_calls]
            for block in message.provider_content:
                if block.get("type") == "openai_reasoning":
                    item["reasoning_content"] = block["reasoning_content"]
        result.append(item)
    return result


def anthropic_messages(messages: Sequence[Message]) -> list[dict]:
    result: list[dict] = []
    for message in messages:
        if message.role == "tool":
            content = [{"type": "tool_result", "tool_use_id": message.tool_call_id,
                        "content": message.tool_result.to_json(), "is_error": not message.tool_result.ok}]
            role = "user"
        else:
            role = "user" if message.role == "context" else message.role
            if message.provider_content:
                content = deepcopy(list(message.provider_content))
            elif message.tool_calls:
                content = ([{"type": "text", "text": message.content}] if message.content else [])
                content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": input_object(c.arguments)}
                            for c in message.tool_calls]
            else:
                content = message.content
        # 多个结果必须同处紧随 tool_use 的用户消息；失败后的新问题也并入该消息。
        if role == "user" and result and result[-1]["role"] == "user":
            previous = result[-1]["content"]
            if isinstance(previous, str):
                previous = [{"type": "text", "text": previous}]
            result[-1]["content"] = previous + ([{"type": "text", "text": content}] if isinstance(content, str) else content)
        else:
            result.append({"role": role, "content": content})
    return result
