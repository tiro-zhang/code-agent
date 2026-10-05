"""已发送模型输入的独立副本，不记录尚未配对的响应。"""

from copy import deepcopy
from dataclasses import dataclass

from ..tools.base import ToolDefinition
from ..types import Message


@dataclass(frozen=True)
class RequestSnapshot:
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...]
    system_prompt: str
    config: object
    control_state: object = None

    @classmethod
    def capture(cls, messages, tools, system_prompt, config, *, control_state=None):
        return cls(deepcopy(tuple(messages)), deepcopy(tuple(tools)), system_prompt, deepcopy(config), deepcopy(control_state))

    def copy(self):
        """消息、工具 Schema 及签名内的对象也不能共享。"""
        return deepcopy(self)
