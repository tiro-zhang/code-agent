"""实时转发模型片段，只在整个响应完整时交出可提交消息。"""

from collections.abc import AsyncIterator
from dataclasses import replace

from .providers.tool_messages import validate_calls
from .types import AgentEvent, AgentMode, CollectedResponse, Message, ProviderError, ProviderEvent, TokenUsage


class StreamCollector:
    def __init__(self, stream: AsyncIterator[ProviderEvent]) -> None:
        self.stream = stream
        self.response: CollectedResponse | None = None
        self.usage = TokenUsage()

    async def events(self, *, run_id: str, iteration: int, mode: AgentMode) -> AsyncIterator[AgentEvent]:
        text: list[str] = []
        message = None
        completed = False
        try:
            async for event in self.stream:
                if event.kind == "usage":
                    if event.usage is not None:
                        fields = {key: getattr(event.usage, key) for key in
                                  ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
                                   "total_input_tokens", "cache_miss_tokens")
                                  if getattr(event.usage, key) is not None}
                        self.usage = replace(self.usage, **fields, complete=event.usage.complete,
                                             cache_complete=event.usage.cache_complete,
                                             incomplete_fields=event.usage.incomplete_fields)
                    continue
                if completed:
                    raise ProviderError("模型在完成响应后继续返回内容")
                if event.kind in {"text_delta", "thinking_delta"}:
                    if event.kind == "text_delta":
                        text.append(event.text)
                    yield AgentEvent(event.kind, run_id=run_id, iteration=iteration, mode=mode, text=event.text)
                elif event.kind == "completed":
                    completed = True
                    message = event.message or Message("assistant", "".join(text))
                    validate_calls(message.tool_calls)
                else:
                    raise ProviderError("模型返回未知响应事件")
            if not completed or message is None:
                raise ProviderError("模型流未正常结束")
            if message.role != "assistant" or (not message.tool_calls and not message.content.strip()):
                raise ProviderError("模型没有返回有效的非空答复")
            self.response = CollectedResponse(message, self.usage)
        except BaseException:
            self.usage = replace(self.usage, complete=False)
            raise
        finally:
            close = getattr(self.stream, "aclose", None)
            if close is not None:
                await close()
