"""严格转换消息，不从正文猜测角色、控制状态或应用来源。"""
import json

from ..tools.base import ToolResult
from ..types import Message, ToolCall


MESSAGE_FIELDS = {'id', 'role', 'content', 'context_kind', 'tool_calls', 'tool_call_id',
                  'tool_result', 'provider_content', 'cache_path'}
CONTEXT_KINDS = {'', 'runtime', 'summary', 'boundary', 'resume', 'skill_background', 'task_resume'}


def json_value(value):
    """通过严格 JSON 往返拒绝浮点特殊值及非 JSON 对象。"""
    def check(item):
        if item is None or type(item) in (str, bool, int, float):
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                check(child)
            return
        if isinstance(item, dict) and all(isinstance(key, str) for key in item):
            for child in item.values():
                check(child)
            return
        raise ValueError('不是合法 JSON 数据')
    check(value)
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def decode_result(value) -> ToolResult:
    if (not isinstance(value, dict) or set(value) != {'ok', 'data', 'error', 'truncated'}
            or type(value['ok']) is not bool or type(value['truncated']) is not bool):
        raise ValueError('工具结果结构无效')
    error = value['error']
    if error is not None:
        if (not isinstance(error, dict) or set(error) != {'code', 'message', 'details'}
                or not isinstance(error['code'], str) or not isinstance(error['message'], str)
                or not isinstance(error['details'], dict)):
            raise ValueError('工具错误结构无效')
    if value['ok'] and error is not None:
        raise ValueError('成功结果不能携带错误')
    return ToolResult.from_dict(json_value(value))


def decode_message(value) -> Message:
    if not isinstance(value, dict) or set(value) != MESSAGE_FIELDS:
        raise ValueError('消息字段缺失或未知')
    for key in ('id', 'role', 'content', 'context_kind', 'cache_path'):
        if not isinstance(value[key], str):
            raise ValueError('消息字符串字段无效')
    role = value['role']
    if not value['id'] or role not in {'user', 'assistant', 'tool', 'context'}:
        raise ValueError('消息身份或角色无效')
    if value['context_kind'] not in CONTEXT_KINDS or (role != 'context' and value['context_kind']):
        raise ValueError('应用上下文来源无效')
    if not isinstance(value['tool_calls'], list) or not isinstance(value['provider_content'], list):
        raise ValueError('调用或供应商续接内容无效')
    calls = []
    for call in value['tool_calls']:
        if (not isinstance(call, dict) or set(call) != {'id', 'name', 'arguments'}
                or any(not isinstance(call[key], str) for key in call)
                or not call['id'] or not call['name']):
            raise ValueError('工具调用结构无效')
        calls.append(ToolCall(**call))
    if len({call.id for call in calls}) != len(calls):
        raise ValueError('工具调用身份重复')
    blocks = value['provider_content']
    if any(not isinstance(block, dict) for block in blocks):
        raise ValueError('供应商续接块无效')
    if role != 'assistant' and (calls or blocks):
        raise ValueError('非助手消息不能携带调用或供应商续接块')
    call_id = value['tool_call_id']
    result = value['tool_result']
    if role == 'tool':
        if not isinstance(call_id, str) or not call_id or result is None:
            raise ValueError('工具消息缺少调用身份或真实结果')
        result = decode_result(result)
    elif call_id is not None or result is not None or value['cache_path']:
        raise ValueError('非工具消息携带工具结果字段')
    return Message(role, value['content'], tuple(calls), call_id, result,
                   tuple(json_value(blocks)), id=value['id'], context_kind=value['context_kind'],
                   cache_path=value['cache_path'])


def encode_message(message: Message) -> dict:
    value = {'id': message.id, 'role': message.role, 'content': message.content,
             'context_kind': message.context_kind,
             'tool_calls': [{'id': call.id, 'name': call.name, 'arguments': call.arguments}
                            for call in message.tool_calls],
             'tool_call_id': message.tool_call_id,
             'tool_result': message.tool_result.to_dict() if message.tool_result else None,
             'provider_content': list(message.provider_content), 'cache_path': message.cache_path}
    decode_message(value)
    return json_value(value)


def decode_messages(values) -> list[Message]:
    if not isinstance(values, list):
        raise ValueError('消息集合无效')
    return [decode_message(value) for value in values]
