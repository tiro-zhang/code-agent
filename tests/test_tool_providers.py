from conftest import async_test, collect
from conftest import AsyncRawStream
import asyncio
"""验证真正发出的工具协议，以及正常流结束前的碎片组装。"""
import json
from types import SimpleNamespace as NS
import pytest
from mewcode.providers.openai import OpenAIProvider
from mewcode.providers.anthropic import AnthropicProvider
from mewcode.tools import default_registry
from mewcode.tools.base import ToolResult
from mewcode.types import Message, ProviderError, ToolCall
from mewcode.types import TokenUsage
from test_openai_provider import config as openai_config, fake_client as openai_client
from test_anthropic_provider import config as anthropic_config, deepseek_config, fake_client as anthropic_client, event
DEFINITIONS = default_registry().definitions()

def oc(*calls, finish=None):
    return NS(choices=[NS(delta=NS(content=None, tool_calls=list(calls)), finish_reason=finish)])

def fragment(index, id=None, name=None, args=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=args))

def anthropic_call(raw='{"path":"x"}', *, id='a', name='read_file', stop='tool_use'):
    return [event('content_block_start', index=0, content_block=NS(type='tool_use', id=id, name=name, input={})), event('content_block_delta', index=0, delta=event('input_json_delta', partial_json=raw[:4])), event('content_block_delta', index=0, delta=event('input_json_delta', partial_json=raw[4:])), event('content_block_stop', index=0), event('message_delta', delta=NS(stop_reason=stop)), event('message_stop')]


@async_test
async def test_openai_usage_after_finish_is_not_extra_content():
    from test_openai_provider import chunk
    client = openai_client([chunk("好"), chunk(finish_reason="stop"),
                           NS(choices=[], usage=NS(prompt_tokens=11, completion_tokens=9,
                                                  prompt_tokens_details=NS(cached_tokens=3)))])
    events = await collect(OpenAIProvider(openai_config(), client=client).stream(
        [Message("user", "问")], system_prompt="独立指令"))
    assert events[-1].message.content == "好"
    assert [e.usage for e in events if e.kind == "usage"][-1] == TokenUsage(11, 9, True, 3)
    assert client.chat.completions.request["messages"][0] == {"role": "system", "content": "独立指令"}
    assert client.chat.completions.request["stream_options"] == {"include_usage": True}


@async_test
async def test_anthropic_usage_is_cumulative():
    items = [event("message_start", message=NS(usage=NS(input_tokens=11, output_tokens=0,
                   cache_read_input_tokens=4, cache_creation_input_tokens=2))),
             event("message_delta", delta=NS(stop_reason=None), usage=NS(output_tokens=5)),
             event("message_delta", delta=NS(stop_reason="end_turn"), usage=NS(output_tokens=9)),
             event("message_stop")]
    client = anthropic_client(items)
    received = await collect(AnthropicProvider(anthropic_config(), client=client).stream([], system_prompt="规划"))
    assert [e.usage for e in received if e.kind == "usage"][-1] == TokenUsage(11, 9, True, 4, 2)
    assert client.messages.request["system"] == "规划"


@async_test
async def test_anthropic_interruption_preserves_input_only_usage():
    client = anthropic_client([event("message_start", message=NS(usage=NS(input_tokens=17))),
                              event("error")])
    received = []
    with pytest.raises(ProviderError):
        async for item in AnthropicProvider(anthropic_config(), client=client).stream([]):
            received.append(item)
    assert received[0].usage.input_tokens == 17
    assert received[0].usage.output_tokens is None and not received[0].usage.complete


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@async_test
async def test_cancel_network_wait_closes_raw_stream_and_client(protocol):
    entered = asyncio.Event()
    class WaitingStream(AsyncRawStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield None
    raw = WaitingStream([])
    client = openai_client([]) if protocol == "openai" else anthropic_client([])
    async def create(**options):
        return raw
    async def close():
        client.closed = True
    client.close = close
    client.closed = False
    if protocol == "openai":
        client.chat.completions.create = create
        provider = OpenAIProvider(openai_config(), client=client)
    else:
        client.messages.create = create
        provider = AnthropicProvider(anthropic_config(), client=client)
    stream = provider.stream([])
    waiting = asyncio.create_task(anext(stream))
    await entered.wait()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert raw.closed
    await provider.aclose()
    assert client.closed


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@async_test
async def test_multi_result_order_and_local_metadata_are_preserved(protocol):
    calls = (ToolCall("a", "read_file", '{"path":"a"}'),
             ToolCall("b", "glob_files", '{"pattern":"*"}'),
             ToolCall("c", "search_code", '{"pattern":"a"}'))
    history = [Message("user", "问"), Message("assistant", tool_calls=calls),
               Message("tool", tool_call_id="a", tool_result=ToolResult.success({"content":"a"})),
               Message("tool", tool_call_id="b", tool_result=ToolResult.failure("timeout", "超时")),
               Message("tool", tool_call_id="c", tool_result=ToolResult.failure("cancelled", "取消"))]
    registry = default_registry()
    definitions = registry.definitions(allowed_tools=registry.names(read_only=True))
    if protocol == "openai":
        from test_openai_provider import chunk
        client = openai_client([chunk("结束"), chunk(finish_reason="stop")])
        provider = OpenAIProvider(openai_config(), client=client)
    else:
        client = anthropic_client([event("message_delta", delta=NS(stop_reason="end_turn")), event("message_stop")])
        provider = AnthropicProvider(anthropic_config(), client=client)
    await collect(provider.stream(history, tools=definitions))
    request = client.chat.completions.request if protocol == "openai" else client.messages.request
    assert len(request["tools"]) == 3
    assert "read_only" not in json.dumps(request["tools"])
    if protocol == "openai":
        assert [m["tool_call_id"] for m in request["messages"][-3:]] == ["a", "b", "c"]
    else:
        assert [b["tool_use_id"] for b in request["messages"][-1]["content"]] == ["a", "b", "c"]

@async_test
async def test_openai_interleaved_call_fragments_and_request_controls():
    client = openai_client([oc(fragment(0, 'a', 'read_file', '{"pa'), fragment(1, 'b', 'glob_files', '{"pattern":')), oc(fragment(1, args='"*.py"}'), fragment(0, args='th":"x"}')), oc(finish='tool_calls')])
    received = await collect(OpenAIProvider(openai_config(), client=client).stream([Message('user', '问')], tools=DEFINITIONS))
    assert len(received) == 1
    assert received[0].message.tool_calls == (ToolCall('a', 'read_file', '{"path":"x"}'), ToolCall('b', 'glob_files', '{"pattern":"*.py"}'))
    request = client.chat.completions.request
    assert request['parallel_tool_calls'] is True and request['tool_choice'] == 'auto'
    assert len(request['tools']) == 6
    assert request['tools'][0]['function']['parameters'] == DEFINITIONS[0].input_schema

@pytest.mark.parametrize('calls,finish', [([fragment(0, None, 'read_file', '{}')], 'tool_calls'), ([fragment(0, 'a', None, '{}')], 'tool_calls'), ([fragment(0, 'a', 'read_file', '{}'), fragment(1, 'a', 'read_file', '{}')], 'tool_calls'), ([fragment(0, 'a', 'read_file', '{}')], 'length'), ([fragment(0, 'a', 'read_file', '{}')], 'stop')])
@async_test
async def test_openai_invalid_or_incomplete_tool_stream_never_completes(calls, finish):
    with pytest.raises(ProviderError):
        await collect(OpenAIProvider(openai_config(), client=openai_client([oc(*calls), oc(finish=finish)])).stream([]))

@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
@async_test
async def test_invalid_json_is_preserved_for_validation_and_error_pairing(protocol):
    if protocol == 'openai':
        client = openai_client([oc(fragment(0, 'a', 'read_file', '{"path":')), oc(finish='tool_calls')])
        provider = OpenAIProvider(openai_config(), client=client)
    else:
        client = anthropic_client(anthropic_call('{"path":'))
        provider = AnthropicProvider(anthropic_config(), client=client)
    response = (await collect(provider.stream([], tools=DEFINITIONS)))[-1].message
    assert response.tool_calls[0].arguments == '{"path":'
    error = ToolResult.failure('invalid_arguments', '参数不是完整 JSON')
    history = [Message('user', '问'), response, Message('tool', tool_call_id='a', tool_result=error)]
    await collect(provider.stream(history, tools=DEFINITIONS, tool_choice='none'))
    if protocol == 'openai':
        request = client.chat.completions.request
        assert request['tool_choice'] == 'none'
        assert request['messages'][-1]['tool_call_id'] == 'a'
        assert json.loads(request['messages'][-1]['content'])['error']['code'] == 'invalid_arguments'
    else:
        request = client.messages.request
        assert request['tool_choice'] == {'type': 'none'} and request['stream'] is True
        assert isinstance(request['messages'][-2]['content'][0]['input'], dict)
        result = request['messages'][-1]['content'][0]
        assert result['tool_use_id'] == 'a' and result['is_error'] is True
        assert json.loads(result['content'])['error']['code'] == 'invalid_arguments'

@pytest.mark.parametrize('thinking', [True, False])
@async_test
async def test_anthropic_preserves_signed_and_redacted_blocks_even_if_display_disabled(thinking):
    blocks = [event('content_block_start', index=0, content_block=NS(type='thinking', thinking='', signature='')), event('content_block_delta', index=0, delta=event('thinking_delta', thinking='分析')), event('content_block_delta', index=0, delta=event('signature_delta', signature='sig-')), event('content_block_delta', index=0, delta=event('signature_delta', signature='123')), event('content_block_stop', index=0), event('content_block_start', index=1, content_block=NS(type='redacted_thinking', data='opaque')), event('content_block_stop', index=1)]
    call = anthropic_call()
    for item in call:
        if hasattr(item, 'index'):
            item.index += 2
    client = anthropic_client(blocks + call)
    provider = AnthropicProvider(anthropic_config(thinking=thinking), client=client)
    received = await collect(provider.stream([], tools=DEFINITIONS))
    assert [r.text for r in received if r.kind == 'thinking_delta'] == (['分析'] if thinking else [])
    response = received[-1].message
    assert response.provider_content[:2] == ({'type': 'thinking', 'thinking': '分析', 'signature': 'sig-123'}, {'type': 'redacted_thinking', 'data': 'opaque'})
    await collect(provider.stream([response, Message('tool', tool_call_id='a', tool_result=ToolResult.success({'content': 'x'}))], tools=DEFINITIONS, tool_choice='none'))
    assert client.messages.request['messages'][0]['content'] == list(response.provider_content)

@pytest.mark.parametrize('mutate', ['missing_id', 'duplicate_id', 'missing_stop', 'open_block', 'max_tokens'])
@async_test
async def test_anthropic_rejects_unidentifiable_or_incomplete_calls(mutate):
    events = anthropic_call()
    if mutate == 'missing_id':
        events[0].content_block.id = ''
    elif mutate == 'duplicate_id':
        duplicate = anthropic_call()[:4]
        for item in duplicate:
            item.index = 1
        events[4:4] = duplicate
    elif mutate == 'missing_stop':
        events.pop()
    elif mutate == 'open_block':
        events.pop(3)
    elif mutate == 'max_tokens':
        events[-2].delta.stop_reason = 'max_tokens'
    with pytest.raises(ProviderError):
        await collect(AnthropicProvider(anthropic_config(), client=anthropic_client(events)).stream([]))

@async_test
async def test_deepseek_multiple_calls_keep_each_error_body_and_thinking_controls():
    events = anthropic_call()[:4]
    second = anthropic_call(id='b')
    for item in second:
        if hasattr(item, 'index'):
            item.index = 1
    client = anthropic_client(events + second)
    provider = AnthropicProvider(deepseek_config(thinking=True), client=client)
    response = (await collect(provider.stream([], tools=DEFINITIONS)))[-1].message
    assert client.messages.request['tool_choice'] == {'type': 'auto'}
    assert [d['input_schema'] for d in client.messages.request['tools']] == [d.input_schema for d in DEFINITIONS]
    error = ToolResult.failure('too_many_tool_calls', '每轮最多一个工具，所有调用均未执行')
    await collect(provider.stream([response, *[Message('tool', tool_call_id=c.id, tool_result=error) for c in response.tool_calls]], tools=DEFINITIONS, tool_choice='none'))
    request = client.messages.request
    assert len(request['messages']) == 2
    assert [b['tool_use_id'] for b in request['messages'][1]['content']] == ['a', 'b']
    assert all((json.loads(b['content'])['error']['code'] == 'too_many_tool_calls' for b in request['messages'][1]['content']))
    assert request['thinking'] == {'type': 'enabled', 'budget_tokens': 2048}

@pytest.mark.parametrize('status,detail,expected', [(400, 'tools unsupported test-key', '工具协议'), (401, 'test-key', '认证'), (413, 'test-key', '上下文')])
@async_test
async def test_provider_classifies_errors_without_exposing_credentials(status, detail, expected):
    from mewcode.errors import safe_provider_error
    error = RuntimeError(detail)
    error.status_code = status
    result = str(safe_provider_error('兼容服务', error))
    assert expected in result and 'test-key' not in result
