"""与供应商协议无关的对话数据。"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from uuid import uuid4
from typing import Literal, Protocol

from .tools.base import ToolDefinition, ToolResult

AgentMode = Literal["execute", "plan"]
StopReason = Literal["model_done", "max_iterations", "cancelled", "unknown_tool_limit", "stream_error", "context_blocked"]


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    complete: bool = False
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    total_input_tokens: int | None = None
    cache_miss_tokens: int | None = None
    cache_complete: bool = False
    incomplete_fields: frozenset[str] = frozenset()

    @property
    def cache_hit_rate(self) -> float | None:
        total, hit, miss = self.total_input_tokens, self.cache_read_tokens, self.cache_miss_tokens
        if (not self.cache_complete or not isinstance(total, int) or isinstance(total, bool)
                or total <= 0 or not isinstance(hit, int) or isinstance(hit, bool) or not 0 <= hit <= total):
            return None
        if miss is not None and (type(miss) is not int or miss < 0 or hit + miss != total):
            return None
        return hit / total


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool", "context"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    tool_result: ToolResult | None = None
    provider_content: tuple[dict, ...] = ()
    # 本地身份和来源不进入协议字段，也不改变已有消息的值比较。
    id: str = field(default_factory=lambda: uuid4().hex, compare=False, kw_only=True)
    context_kind: str = field(default="", kw_only=True)
    cache_path: str = field(default="", kw_only=True)


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
                  "permission_requested", "permission_resolved", "usage", "progress", "history_trimmed", "context_compaction", "finished"]
    run_id: str = ""
    iteration: int = 0
    mode: AgentMode = "execute"
    permission_mode: str = "default"
    permission_request: object | None = None
    permission_decision: str = ""
    warning: str = ""
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
    purpose: Literal["work", "summary"] = "work"
    estimated_before: int | None = None
    estimated_after: int | None = None
    spilled: int = 0
    failures: int = 0
    circuit_open: bool = False


class Provider(Protocol):
    def stream(self, messages: Sequence[Message], *, tools: Sequence[ToolDefinition] = (),
               tool_choice: Literal["auto", "none"] = "auto",
               system_prompt: str = "") -> AsyncIterator[ProviderEvent]: ...

    async def aclose(self) -> None: ...


class ProviderError(RuntimeError):
    """可安全展示给用户的模型请求错误。"""


class ContextLimitError(ProviderError):
    """服务报告上下文超限；仅允许有界摘要恢复。"""
