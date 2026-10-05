"""并发模型包装不能篡改父配置或提前关闭传输。"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from conftest import AsyncRawStream, async_test, collect
from mewcode.config import ProviderConfig
from mewcode.providers.anthropic import AnthropicProvider
from mewcode.providers.openai import OpenAIProvider
from mewcode.types import Message


class SharedClient:
    def __init__(self, protocol):
        self.protocol = protocol
        self.requests = []
        self.closes = 0
        self.messages = self
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **request):
        self.requests.append(request)
        await asyncio.sleep(0)
        if self.protocol == "anthropic":
            events = [SimpleNamespace(type="content_block_start", index=0, content_block=SimpleNamespace(type="text", text="答复")),
                      SimpleNamespace(type="content_block_stop", index=0),
                      SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="end_turn")),
                      SimpleNamespace(type="message_stop")]
        else:
            events = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="答复"), finish_reason="stop")])]
        return AsyncRawStream(events)

    async def close(self):
        self.closes += 1


@pytest.mark.parametrize("protocol,provider_type", [("openai", OpenAIProvider), ("anthropic", AnthropicProvider)])
@async_test
async def test_model_views_share_transport_but_have_independent_config_and_close(protocol, provider_type):
    config = ProviderConfig("服务", protocol, "parent-model", "https://service.example", "test-key", False, context_window=128000)
    client = SharedClient(protocol)
    parent = provider_type(config, client=client)
    assert hasattr(parent, "fork"), "供应商缺少借用传输的模型视图"
    child = parent.fork(replace(config, model="child-model", max_output_tokens=4096))
    responses = await asyncio.gather(collect(parent.stream([Message("user", "父目标")])), collect(child.stream([Message("user", "子目标")])))
    assert [events[-1].message.content for events in responses] == ["答复", "答复"]
    assert {(request["model"], request["max_tokens"]) for request in client.requests} == {("parent-model", 8192), ("child-model", 4096)}
    await child.aclose()
    assert client.closes == 0
    await collect(parent.stream([Message("user", "父继续")]))
    assert client.requests[-1]["model"] == "parent-model"
    await parent.aclose()
    await parent.aclose()
    assert client.closes == 1


@pytest.mark.parametrize("provider_type,protocol", [(OpenAIProvider, "openai"), (AnthropicProvider, "anthropic")])
def test_model_view_cannot_rebind_shared_transport_service(provider_type, protocol):
    config = ProviderConfig("服务", protocol, "parent-model", "https://service.example", "test-key", False, context_window=128000)
    parent = provider_type(config, client=SharedClient(protocol))
    assert hasattr(parent, "fork"), "供应商缺少借用传输的模型视图"
    for overrides in ({"base_url": "https://other.example"}, {"api_key": "changed"}, {"protocol": "other"}):
        with pytest.raises(ValueError):
            parent.fork(replace(config, **overrides))
