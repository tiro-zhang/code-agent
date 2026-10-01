"""与供应商协议无关的对话数据。"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from .tools.base import ToolDefinition, ToolResult

AgentMode = Literal["execute", "plan"]
StopReason = Literal["model_done", "max_iterations", "cancelled", "unknown_tool_limit", "stream_error"]


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    complete: bool = False
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


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
class ProviderEvent:
    kind: Literal["thinking_delta", "text_delta", "completed", "usage"]
    text: str = ""
    message: Message | None = None
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class CollectedResponse:
    message: Message
    usage: TokenUsage


@dataclass(frozen=True)
class AgentEvent:
    kind: Literal["thinking_delta", "text_delta", "tool_call", "tool_started", "tool_result",
                  "usage", "progress", "history_trimmed", "finished"]
    run_id: str = ""
    iteration: int = 0
    mode: AgentMode = "execute"
    text: str = ""
    message: Message | None = None
    call: ToolCall | None = None
    tool_call_id: str = ""
    tool_name: str = ""
    result: ToolResult | None = None
    usage: TokenUsage | None = None
    phase: str = ""
    max_iterations: int = 20
    reason: StopReason | None = None


class Provider(Protocol):
    def stream(self, messages: Sequence[Message], *, tools: Sequence[ToolDefinition] = (),
               tool_choice: Literal["auto", "none"] = "auto",
               system_prompt: str = "") -> AsyncIterator[ProviderEvent]: ...

    async def aclose(self) -> None: ...


class ProviderError(RuntimeError):
    """可安全展示给用户的模型请求错误。"""


class ContextLimitError(ProviderError):
    """历史长度超出模型上下文窗口；丢弃较早轮次后可重试。"""
