"""验证实时展示与完整响应的提交边界。"""

import asyncio
import pytest
from conftest import async_test, collect
from mewcode.types import Message, ProviderError, ProviderEvent, TokenUsage, ToolCall


@async_test
async def test_first_delta_arrives_before_next_fragment():
    from mewcode.collector import StreamCollector
    next_fragment = asyncio.Event()
    async def source():
        yield ProviderEvent("thinking_delta", "推理")
        yield ProviderEvent("text_delta", "你")
        await next_fragment.wait()
        yield ProviderEvent("text_delta", "好")
        yield ProviderEvent("completed", message=Message("assistant", "你好"))
    collector = StreamCollector(source())
    stream = collector.events(run_id="run", iteration=2, mode="plan")
    assert (await anext(stream)).text == "推理"
    first = await anext(stream)
    assert first.text == "你" and first.run_id == "run" and first.iteration == 2
    assert collector.response is None
    next_fragment.set()
    assert [e.text for e in await collect(stream)] == ["好"]
    assert collector.response.message.content == "你好"
    assert collector.usage == TokenUsage()


@pytest.mark.parametrize("case", ["empty", "missing", "duplicate", "after", "broken", "bad_id", "bad_name", "blank_id"])
@async_test
async def test_only_normally_completed_response_is_accepted(case):
    from mewcode.collector import StreamCollector
    call = ToolCall("a", "read_file", '{"path":"a"}')
    events = [ProviderEvent("completed", message=Message("assistant", tool_calls=(call,)))]
    if case == "empty": events = [ProviderEvent("completed", message=Message("assistant"))]
    if case == "missing": events = [ProviderEvent("text_delta", "部分")]
    if case == "duplicate": events *= 2
    if case == "after": events += [ProviderEvent("text_delta", "多余")]
    if case == "broken": events += [ProviderError("断流")]
    if case == "bad_id": events = [ProviderEvent("completed", message=Message("assistant", tool_calls=(ToolCall("", "read_file", "{}"),)))]
    if case == "bad_name": events = [ProviderEvent("completed", message=Message("assistant", tool_calls=(ToolCall("a", ["read_file"], "{}"),)))]
    if case == "blank_id": events = [ProviderEvent("completed", message=Message("assistant", tool_calls=(ToolCall("  ", "read_file", "{}"),)))]
    async def source():
        for event in events:
            if isinstance(event, Exception): raise event
            yield event
    collector = StreamCollector(source())
    with pytest.raises(ProviderError):
        await collect(collector.events(run_id="r", iteration=1, mode="execute"))
    assert collector.response is None


@async_test
async def test_tool_only_and_final_usage_overwrite():
    from mewcode.collector import StreamCollector
    call = ToolCall("a", "read_file", "{}")
    async def source():
        yield ProviderEvent("usage", usage=TokenUsage(11))
        yield ProviderEvent("usage", usage=TokenUsage(11, 5))
        yield ProviderEvent("usage", usage=TokenUsage(11, 9, True))
        yield ProviderEvent("completed", message=Message("assistant", tool_calls=(call,)))
    collector = StreamCollector(source())
    assert await collect(collector.events(run_id="r", iteration=1, mode="execute")) == []
    assert collector.response.message.tool_calls == (call,)
    assert collector.response.usage == TokenUsage(11, 9, True)


@async_test
async def test_partial_usage_survives_stream_failure():
    from mewcode.collector import StreamCollector
    async def source():
        yield ProviderEvent("usage", usage=TokenUsage(17))
        raise ProviderError("断流")
    collector = StreamCollector(source())
    with pytest.raises(ProviderError):
        await collect(collector.events(run_id="r", iteration=1, mode="execute"))
    assert collector.usage == TokenUsage(17)
    assert collector.response is None
