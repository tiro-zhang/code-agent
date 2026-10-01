"""与供应商协议无关的对话数据。"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from .tools.base import ToolDefinition, ToolResult


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    tool_result: ToolResult | None = None
    provider_content: tuple[dict, ...] = ()


@dataclass(frozen=True)
class StreamEvent:
    kind: Literal["thinking_delta", "text_delta", "completed", "history_trimmed", "tool_started", "tool_result"]
    text: str = ""
    message: Message | None = None
    tool_name: str = ""
    result: ToolResult | None = None


class Provider(Protocol):
    def stream(self, messages: Sequence[Message], *, tools: Sequence[ToolDefinition] = (),
               tool_choice: Literal["auto", "none"] = "auto") -> Iterator[StreamEvent]: ...


class ProviderError(RuntimeError):
    """可安全展示给用户的模型请求错误。"""


class ContextLimitError(ProviderError):
    """历史长度超出模型上下文窗口；丢弃较早轮次后可重试。"""
