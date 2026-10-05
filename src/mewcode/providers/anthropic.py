"""Anthropic Messages 流式适配器。"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
import json
import re
from typing import Any, Literal
from urllib.parse import urlsplit

import anthropic

from ..config import ProviderConfig
from ..errors import safe_provider_error
from ..tools.base import ARGUMENT_LIMIT, ToolDefinition
from ..types import Message, ProviderError, ProviderEvent, TokenUsage, ToolCall
from .usage import anthropic_usage
from .tool_messages import anthropic_messages, anthropic_tools, input_object, validate_calls


def _thinking_options(model: str, max_tokens: int) -> dict[str, Any]:
    """按已知 Claude 模型族选择可显示摘要的思考模式。"""
    if model.startswith("claude-3-7-sonnet"):
        return {"type": "enabled", "budget_tokens": 2048, "display": "summarized"}

    match = re.match(r"^claude-(?:opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d+))?(?:-|$)", model)
    if not match:
        raise ProviderError("所选 Claude 模型的思考模式未知，请检查 model 配置")

    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    if major >= 5 or (major == 4 and minor >= 6):
        return {"type": "adaptive", "display": "summarized"}
    if major == 4 and 2048 < max_tokens:
        return {"type": "enabled", "budget_tokens": 2048, "display": "summarized"}
    raise ProviderError("所选 Claude 模型不支持当前思考配置")


def _is_deepseek_anthropic(base_url: str) -> bool:
    """识别 DeepSeek 官方的 Anthropic 兼容地址。"""
    parsed = urlsplit(base_url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "api.deepseek.com"
        and parsed.path.rstrip("/") == "/anthropic"
    )


def _is_official_claude(base_url: str) -> bool:
    parsed = urlsplit(base_url)
    return (parsed.scheme == "https" and parsed.netloc in {"api.anthropic.com", "api.anthropic.com:443"}
            and parsed.path.rstrip("/") in {"", "/v1"})


class AnthropicProvider:
    def __init__(self, config: ProviderConfig, *, client: Any | None = None, owns_client: bool = True) -> None:
        self.config = config
        self._owns_client, self._closed = owns_client, False
        self.client = client or anthropic.AsyncAnthropic(
            api_key=config.api_key,
            base_url=config.base_url,
            max_retries=0,
        )

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            if self._owns_client:
                await self.client.close()

    def fork(self, config: ProviderConfig):
        """模型包装借用会话传输，只允许同一服务。"""
        if any(getattr(config, key) != getattr(self.config, key) for key in ("protocol", "base_url", "api_key")):
            raise ValueError("共享传输不能更换模型服务")
        if self._closed:
            raise ValueError("模型服务已关闭")
        return AnthropicProvider(config, client=self.client, owns_client=False)

    async def stream(self, messages: Sequence[Message], *, tools: Sequence[ToolDefinition] = (),
                     tool_choice: Literal["auto", "none"] = "auto",
                     system_prompt: str = "") -> AsyncIterator[ProviderEvent]:
        deepseek_compatible = _is_deepseek_anthropic(self.config.base_url)
        official_claude = _is_official_claude(self.config.base_url)
        service = "deepseek" if deepseek_compatible else "claude" if official_claude else "compatible"
        provider_name = "DeepSeek" if deepseek_compatible else "Claude"
        request: dict[str, Any] = {
            "model": self.config.model, "max_tokens": self.config.max_output_tokens, "stream": True,
            "messages": anthropic_messages(messages),
        }
        if official_claude:
            # 官方自动断点覆盖最后可缓存消息块；Fork 追加少量块保留共同前缀。
            request["cache_control"] = {"type": "ephemeral"}
        if system_prompt:
            request["system"] = ([{"type": "text", "text": system_prompt,
                                  "cache_control": {"type": "ephemeral"}}] if official_claude else system_prompt)
        if tools:
            request["tools"] = anthropic_tools(tools)
            request["tool_choice"] = ({"type": "auto"}
                                      if tool_choice == "auto" else {"type": "none"})
        if deepseek_compatible:
            request["thinking"] = ({"type": "enabled", "budget_tokens": 2048}
                                   if self.config.thinking else {"type": "disabled"})
        elif self.config.thinking:
            request["thinking"] = _thinking_options(self.config.model, request["max_tokens"])
        saw_stop, stop_reason = False, None
        blocks: dict[int, dict] = {}
        opened: set[int] = set()
        raw_arguments: dict[int, str] = {}
        argument_bytes = 0
        usage = TokenUsage()
        counts: dict[str, int] = {}
        try:
            # 原始流不会提前解析工具 JSON；坏参数也能得到配对的错误结果。
            async with await self.client.messages.create(**request) as stream:
                async for event in stream:
                    if saw_stop and event.type != "ping":
                        raise ProviderError(f"{provider_name} 在结束标记后继续返回内容")
                    if event.type == "message_start":
                        source = getattr(event.message, "usage", None)
                        if source is not None:
                            usage = anthropic_usage(counts, source, service=service)
                            yield ProviderEvent("usage", usage=usage)
                    elif event.type == "content_block_start":
                        index = event.index
                        if index in blocks or not isinstance(index, int) or index < 0 or index >= 128:
                            raise ProviderError("工具协议错误：内容块索引无效或重复")
                        block = (event.content_block.model_dump(exclude_none=True)
                                 if hasattr(event.content_block, "model_dump") else vars(event.content_block).copy())
                        blocks[index] = block
                        opened.add(index)
                        if block["type"] == "text" and block.get("text"):
                            yield ProviderEvent("text_delta", block["text"])
                        elif block["type"] == "thinking" and self.config.thinking and block.get("thinking"):
                            yield ProviderEvent("thinking_delta", block["thinking"])
                    elif event.type == "content_block_delta":
                        if event.index not in opened:
                            raise ProviderError("工具协议错误：内容片段缺少起始块")
                        block, delta = blocks[event.index], event.delta
                        field = {"text_delta": "text", "thinking_delta": "thinking", "signature_delta": "signature"}.get(delta.type)
                        if field:
                            expected = "text" if field == "text" else "thinking"
                            if block["type"] != expected:
                                raise ProviderError("工具协议错误：片段与内容块类型不一致")
                            value = getattr(delta, field)
                            block[field] = block.get(field, "") + value
                            if value and (field == "text" or (field == "thinking" and self.config.thinking)):
                                yield ProviderEvent("text_delta" if field == "text" else "thinking_delta", value)
                        elif delta.type == "input_json_delta" and block["type"] == "tool_use":
                            argument_bytes += len(delta.partial_json.encode("utf-8"))
                            if argument_bytes > ARGUMENT_LIMIT:
                                raise ProviderError("工具参数流超过 2 MiB 上限，未执行工具")
                            raw_arguments[event.index] = raw_arguments.get(event.index, "") + delta.partial_json
                        elif delta.type == "citations_delta" and block["type"] == "text":
                            citation = delta.citation.model_dump(exclude_none=True)
                            block.setdefault("citations", []).append(citation)
                        else:
                            raise ProviderError("工具协议错误：无法保留未知内容片段")
                    elif event.type == "content_block_stop":
                        if event.index not in opened:
                            raise ProviderError("工具协议错误：内容块未打开或重复结束")
                        opened.remove(event.index)
                    elif event.type == "message_delta":
                        source = getattr(event, "usage", None)
                        if source is not None:
                            usage = anthropic_usage(counts, source, service=service)
                            yield ProviderEvent("usage", usage=usage)
                        if event.delta.stop_reason is not None:
                            stop_reason = event.delta.stop_reason
                    elif event.type == "message_stop":
                        saw_stop = True
                    elif event.type == "error":
                        raise ProviderError(f"{provider_name} 流式请求失败")
        except ProviderError:
            raise
        except Exception as error:
            raise safe_provider_error(provider_name, error, thinking=self.config.thinking) from None

        if not saw_stop or opened:
            raise ProviderError(f"{provider_name} 流未正常结束")
        calls = []
        for index in sorted(blocks):
            block = blocks[index]
            if block["type"] == "tool_use":
                raw = raw_arguments.get(index, json.dumps(block.get("input", {}), ensure_ascii=False))
                calls.append(ToolCall(block.get("id", ""), block.get("name", ""), raw))
                block["input"] = input_object(raw)
        if stop_reason != ("tool_use" if calls else "end_turn"):
            raise ProviderError(f"{provider_name} 回答未正常完成")
        validate_calls(calls)
        ordered = tuple(blocks[index] for index in sorted(blocks))
        text = "".join(b["text"] for b in ordered if b["type"] == "text")
        if usage.input_tokens is not None or usage.output_tokens is not None:
            usage = replace(usage, complete=usage.input_tokens is not None and usage.output_tokens is not None)
            yield ProviderEvent("usage", usage=usage)
        yield ProviderEvent("completed", message=Message("assistant", text, tuple(calls), provider_content=ordered))
