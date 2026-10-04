"""基于实际序列化内容的字符估算和总输入 usage 锚点。"""
from dataclasses import dataclass
import json
import math

from ..providers.tool_messages import anthropic_messages, anthropic_tools, openai_messages, openai_tools
from ..types import TokenUsage


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def estimate_text(text: str) -> int:
    return math.ceil(sum(1 if ord(char) < 128 else 4 for char in text) / 4)


@dataclass(frozen=True)
class Snapshot:
    prefix: str
    frames: tuple[str, ...]

    @property
    def message_tokens(self) -> int:
        return sum(estimate_text(frame) + 8 for frame in self.frames)

    @property
    def tokens(self) -> int:
        return estimate_text(self.prefix) + 16 + self.message_tokens


def snapshot(messages, system, tools, protocol) -> Snapshot:
    serialize, definitions = ((anthropic_messages, anthropic_tools) if protocol == 'anthropic'
                              else (openai_messages, openai_tools))
    # 按逻辑消息估算封装开销，避免 Messages 合并相邻用户消息导致追加锚点失效。
    return Snapshot(dump({'system': system, 'tools': definitions(tools)}),
                    tuple(dump(serialize([message])) for message in messages))


class Estimator:
    def __init__(self, protocol: str):
        self.protocol = protocol
        self.anchor: tuple[Snapshot, int] | None = None

    def snapshot(self, messages, system, tools) -> Snapshot:
        return snapshot(messages, system, tools, self.protocol)

    def estimate(self, messages, system, tools) -> int:
        current = self.snapshot(messages, system, tools)
        if self.anchor:
            previous, total = self.anchor
            if current.prefix == previous.prefix and current.frames[:len(previous.frames)] == previous.frames:
                return total + sum(estimate_text(frame) + 8 for frame in current.frames[len(previous.frames):])
            self.invalidate()
        return current.tokens

    def observe(self, request: Snapshot, usage: TokenUsage, *, purpose='work') -> None:
        if purpose != 'work':
            return
        value = usage.total_input_tokens
        self.anchor = ((request, value) if type(value) is int and value >= 0
                       and 'total_input_tokens' not in usage.incomplete_fields else None)

    def invalidate(self) -> None:
        self.anchor = None
