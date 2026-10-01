"""OpenAI Chat Completions 兼容流式适配器。"""

from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal

import openai

from ..config import ProviderConfig
from ..errors import safe_provider_error
from ..tools.base import ARGUMENT_LIMIT, ToolDefinition
from ..types import Message, ProviderError, ProviderEvent, TokenUsage, ToolCall
from .tool_messages import openai_messages, openai_tools, validate_calls


class OpenAIProvider:
    def __init__(self, config: ProviderConfig, *, client: Any | None = None) -> None:
        self.config = config
        self.client = client or openai.AsyncOpenAI(api_key=config.api_key, base_url=config.base_url, max_retries=0)

    async def aclose(self) -> None:
        await self.client.close()

    async def stream(self, messages: Sequence[Message], *, tools: Sequence[ToolDefinition] = (),
                     tool_choice: Literal["auto", "none"] = "auto",
                     system_prompt: str = "") -> AsyncIterator[ProviderEvent]:
        serialized = openai_messages(messages)
        if system_prompt:
            serialized.insert(0, {"role": "system", "content": system_prompt})
        request = {"model": self.config.model, "messages": serialized, "stream": True,
                   "stream_options": {"include_usage": True}}
        if tools:
            request.update(tools=openai_tools(tools), tool_choice=tool_choice, parallel_tool_calls=True)
        finish_reason = None
        calls: dict[int, dict[str, str]] = {}
        text, reasoning = [], []
        argument_bytes = 0
        try:
            async with await self.client.chat.completions.create(**request) as stream:
                async for response in stream:
                    usage = getattr(response, "usage", None)
                    if usage is not None:
                        input_tokens = getattr(usage, "prompt_tokens", None)
                        output_tokens = getattr(usage, "completion_tokens", None)
                        cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
                        yield ProviderEvent("usage", usage=TokenUsage(input_tokens, output_tokens,
                                            input_tokens is not None and output_tokens is not None, cached))
                    if not response.choices:
                        continue
                    choice = response.choices[0]
                    delta = choice.delta
                    if finish_reason is not None:
                        raise ProviderError("OpenAI 兼容服务在结束标记后继续返回内容")
                    if delta.content:
                        text.append(delta.content)
                        yield ProviderEvent("text_delta", delta.content)
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
            yield ProviderEvent("completed", message=Message("assistant", "".join(text), complete, provider_content=blocks))
        else:
            yield ProviderEvent("completed", message=Message("assistant", "".join(text)))
