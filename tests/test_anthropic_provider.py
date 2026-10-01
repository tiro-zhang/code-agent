from conftest import async_test, collect
"""Claude SSE 事件到统一事件的转换测试。"""
from collections.abc import Iterator
from types import SimpleNamespace
import pytest
from mewcode.config import ProviderConfig
from mewcode.providers.anthropic import AnthropicProvider
from mewcode.types import Message, ProviderError, ProviderEvent as StreamEvent

def event(kind: str, **fields: object) -> SimpleNamespace:
    return SimpleNamespace(type=kind, **fields)

class FakeStream:

    def __init__(self, events: list[SimpleNamespace]) -> None:
        self.events = events

    async def __aenter__(self) -> 'FakeStream':
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def __aiter__(self) -> Iterator[SimpleNamespace]:
        for item in self.events:
            yield item

class FakeMessages:

    def __init__(self, events: list[SimpleNamespace]) -> None:
        self.events = events
        self.request: dict[str, object] = {}

    async def create(self, **kwargs: object) -> FakeStream:
        self.request = kwargs
        return FakeStream(self.events)

def fake_client(events: list[SimpleNamespace]) -> SimpleNamespace:
    normalized = []
    for item in events:
        if item.type == 'content_block_delta' and (not hasattr(item, 'index')):
            index = len(normalized)
            field = 'text' if item.delta.type == 'text_delta' else 'thinking'
            normalized.append(event('content_block_start', index=index, content_block=event(field, **{field: ''})))
            item.index = index
            normalized.extend([item, event('content_block_stop', index=index)])
        else:
            normalized.append(item)
    return SimpleNamespace(messages=FakeMessages(normalized))

def config(*, thinking: bool=False, model: str='claude-sonnet-4-6') -> ProviderConfig:
    return ProviderConfig('Claude', 'anthropic', model, 'https://api.anthropic.com', 'test-key', thinking)

def deepseek_config(*, thinking: bool) -> ProviderConfig:
    return ProviderConfig('DeepSeek', 'anthropic', 'deepseek-v4-pro', 'https://api.deepseek.com/anthropic', 'test-key', thinking)

@async_test
async def test_claude_stream_yields_text_before_normal_completion() -> None:
    client = fake_client([event('content_block_delta', delta=event('text_delta', text='你')), event('content_block_delta', delta=event('text_delta', text='好')), event('message_delta', delta=SimpleNamespace(stop_reason='end_turn')), event('message_stop')])
    provider = AnthropicProvider(config(), client=client)
    received = await collect(provider.stream([Message('user', '问候'), Message('assistant', '您好')]))
    assert [(e.kind, e.text) for e in received] == [(e.kind, e.text) for e in [StreamEvent('text_delta', '你'), StreamEvent('text_delta', '好'), StreamEvent('completed')]]
    assert client.messages.request['messages'] == [{'role': 'user', 'content': '问候'}, {'role': 'assistant', 'content': '您好'}]
    assert 'thinking' not in client.messages.request

@async_test
async def test_claude_stream_does_not_expose_thinking_when_disabled() -> None:
    client = fake_client([event('content_block_delta', delta=event('thinking_delta', thinking='隐藏内容')), event('content_block_delta', delta=event('text_delta', text='回答')), event('message_delta', delta=SimpleNamespace(stop_reason='end_turn')), event('message_stop')])
    assert [(e.kind, e.text) for e in await collect(AnthropicProvider(config(), client=client).stream([Message('user', '问题')]))] == [(e.kind, e.text) for e in [StreamEvent('text_delta', '回答'), StreamEvent('completed')]]

@async_test
async def test_claude_stream_requires_normal_stop_event() -> None:
    client = fake_client([event('content_block_delta', delta=event('text_delta', text='部分'))])
    stream = AnthropicProvider(config(), client=client).stream([Message('user', '问题')])
    assert await anext(stream) == StreamEvent('text_delta', '部分')
    with pytest.raises(ProviderError, match='未正常结束'):
        await collect(stream)

@async_test
async def test_claude_stream_error_never_signals_completion() -> None:
    client = fake_client([event('error', error=SimpleNamespace(type='overloaded_error'))])
    with pytest.raises(ProviderError, match='Claude'):
        await collect(AnthropicProvider(config(), client=client).stream([Message('user', '问题')]))

@async_test
async def test_adaptive_thinking_streams_readable_summary() -> None:
    client = fake_client([event('content_block_delta', delta=event('thinking_delta', thinking='先分析')), event('content_block_delta', delta=event('text_delta', text='结论')), event('message_delta', delta=SimpleNamespace(stop_reason='end_turn')), event('message_stop')])
    received = await collect(AnthropicProvider(config(thinking=True), client=client).stream([Message('user', '问题')]))
    assert [(e.kind, e.text) for e in received] == [(e.kind, e.text) for e in [StreamEvent('thinking_delta', '先分析'), StreamEvent('text_delta', '结论'), StreamEvent('completed')]]
    assert client.messages.request['thinking'] == {'type': 'adaptive', 'display': 'summarized'}

@async_test
async def test_legacy_thinking_has_valid_budget_below_max_tokens() -> None:
    client = fake_client([event('content_block_delta', delta=event('text_delta', text='答复')), event('message_delta', delta=SimpleNamespace(stop_reason='end_turn')), event('message_stop')])
    provider = AnthropicProvider(config(thinking=True, model='claude-sonnet-4-5-20250929'), client=client)
    await collect(provider.stream([Message('user', '问题')]))
    thinking = client.messages.request['thinking']
    assert thinking['type'] == 'enabled'
    assert thinking['display'] == 'summarized'
    assert 1024 <= thinking['budget_tokens'] < client.messages.request['max_tokens']

@async_test
async def test_deepseek_anthropic_streams_thinking_and_answer() -> None:
    client = fake_client([event('content_block_delta', delta=event('thinking_delta', thinking='先比较')), event('content_block_delta', delta=event('text_delta', text='结论')), event('message_delta', delta=SimpleNamespace(stop_reason='end_turn')), event('message_stop')])
    received = await collect(AnthropicProvider(deepseek_config(thinking=True), client=client).stream([Message('user', '问题')]))
    assert [(e.kind, e.text) for e in received] == [(e.kind, e.text) for e in [StreamEvent('thinking_delta', '先比较'), StreamEvent('text_delta', '结论'), StreamEvent('completed')]]
    assert client.messages.request['thinking'] == {'type': 'enabled', 'budget_tokens': 2048}

@async_test
async def test_deepseek_anthropic_explicitly_disables_default_thinking() -> None:
    client = fake_client([event('content_block_delta', delta=event('text_delta', text='回答')), event('message_delta', delta=SimpleNamespace(stop_reason='end_turn')), event('message_stop')])
    await collect(AnthropicProvider(deepseek_config(thinking=False), client=client).stream([Message('user', '问题')]))
    assert client.messages.request['thinking'] == {'type': 'disabled'}

@async_test
async def test_unknown_thinking_model_is_rejected_before_request() -> None:
    client = fake_client([])
    provider = AnthropicProvider(config(thinking=True, model='unknown-model'), client=client)
    with pytest.raises(ProviderError, match='思考'):
        await collect(provider.stream([Message('user', '问题')]))
    assert client.messages.request == {}

@async_test
async def test_claude_sdk_error_is_safe_to_display() -> None:
    client = fake_client([])

    async def fail(**kwargs: object) -> FakeStream:
        raise RuntimeError('test-key appeared in transport error')
    client.messages.create = fail
    with pytest.raises(ProviderError) as error:
        await collect(AnthropicProvider(config(), client=client).stream([Message('user', '问题')]))
    assert 'test-key' not in str(error.value)
