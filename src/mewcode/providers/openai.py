"""OpenAI Chat Completions 兼容流式适配器。"""

from collections.abc import Iterator, Sequence
from typing import Any, Literal

import openai

from ..config import ProviderConfig
from ..errors import safe_provider_error
from ..tools.base import ARGUMENT_LIMIT, ToolDefinition
from ..types import Message, ProviderError, StreamEvent, ToolCall
from .tool_messages import openai_messages, openai_tools, validate_calls


class OpenAIProvider:
    def __init__(self, config: ProviderConfig, *, client: Any | None = None) -> None:
        self.config = config
        self.client = client or openai.OpenAI(api_key=config.api_key, base_url=config.base_url)

    def stream(self, messages: Sequence[Message], *, tools: Sequence[ToolDefinition] = (),
               tool_choice: Literal["auto", "none"] = "auto") -> Iterator[StreamEvent]:
        request = {"model": self.config.model, "messages": openai_messages(messages), "stream": True}
        if tools:
            request.update(tools=openai_tools(tools), tool_choice=tool_choice, parallel_tool_calls=False)
        finish_reason = None
        calls: dict[int, dict[str, str]] = {}
        text, reasoning = [], []
        argument_bytes = 0
        try:
            with self.client.chat.completions.create(**request) as stream:
                for response in stream:
                    if not response.choices:
                        continue
                    choice = response.choices[0]
                    delta = choice.delta
                    if finish_reason is not None:
                        raise ProviderError("OpenAI 兼容服务在结束标记后继续返回内容")
                    if delta.content:
                        text.append(delta.content)
                        yield StreamEvent("text_delta", delta.content)
                    if getattr(delta, "reasoning_content", None):
                        reasoning.append(delta.reasoning_content)
                    for fragment in getattr(delta, "tool_calls", None) or []:
                        index = fragment.index
                        if not isinstance(index, int) or index < 0 or index >= 128:
                            raise ProviderError("工具协议错误：调用索引无效或调用过多")
                        call = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        if fragment.id:
                            call["id"] += fragment.id
                        if fragment.function:
                            if fragment.function.name:
                                call["name"] += fragment.function.name
                            if fragment.function.arguments:
                                argument_bytes += len(fragment.function.arguments.encode("utf-8"))
                                if argument_bytes > ARGUMENT_LIMIT:
                                    raise ProviderError("工具参数流超过 2 MiB 上限，未执行工具")
                                call["arguments"] += fragment.function.arguments
                        if len(call["id"]) > 4096 or len(call["name"]) > 4096:
                            raise ProviderError("工具协议错误：调用元信息过长")
                    if choice.finish_reason is not None:
                        finish_reason = choice.finish_reason
        except ProviderError:
            raise
        except Exception as error:
            raise safe_provider_error("OpenAI 兼容服务", error) from None

        if finish_reason != ("tool_calls" if calls else "stop"):
            raise ProviderError("OpenAI 兼容服务的回答未正常完成")
        if calls:
            complete = tuple(ToolCall(**calls[index]) for index in sorted(calls))
            validate_calls(complete)
            blocks = ({"type": "openai_reasoning", "reasoning_content": "".join(reasoning)},) if reasoning else ()
            yield StreamEvent("completed", message=Message("assistant", "".join(text), complete, provider_content=blocks))
        else:
            yield StreamEvent("completed")
