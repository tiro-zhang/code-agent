"""不依赖终端消费者，验证自主循环、预算与五种终止原因。"""

import asyncio
from types import SimpleNamespace
import pytest
from conftest import ScriptedProvider, async_test, collect, permission_bypass
from mewcode.config import ProviderConfig
from mewcode.providers.openai import OpenAIProvider
from mewcode.tools import default_registry
from mewcode.tools.base import ToolContext
from mewcode.tools.executor import ToolExecutor
from mewcode.types import Message, ProviderEvent, ProviderError, TokenUsage, ToolCall


def answer(text="完成", usage=None):
    events = [ProviderEvent("text_delta", text)]
    if usage is not None: events.append(ProviderEvent("usage", usage=usage))
    return events + [ProviderEvent("completed", message=Message("assistant", text))]


def calls(*items):
    return [ProviderEvent("completed", message=Message("assistant", tool_calls=tuple(items)))]


def tool(id="a", name="read_file", arguments="{"):
    return ToolCall(id, name, arguments)


def make_agent(root, responses, **options):
    from mewcode.agent import Agent
    provider = ScriptedProvider(responses)
    agent = Agent(provider, ToolExecutor(default_registry(), ToolContext(root), permissions=permission_bypass(root)), **options)
    return agent, provider


async def run(agent, history=None, mode="execute", cancel=None, question="任务"):
    history = history if history is not None else []
    events = await collect(agent.run(question, history=history, mode=mode, cancel_event=cancel))
    return events, history


@async_test
async def test_react_read_edit_check_and_parameter_correction(tmp_path):
    (tmp_path / "a").write_text("cat")
    agent, provider = make_agent(tmp_path, [calls(tool("read", arguments='{"path":"a"}')),
        calls(tool("bad", "edit_file", '{"path":"a","old_text":"dog","new_text":"mew"}')),
        calls(tool("edit", "edit_file", '{"path":"a","old_text":"cat","new_text":"mew"}')),
        calls(tool("check", "execute_command", '{"command":"cat a"}')), answer("已检查")])
    events, history = await run(agent)
    assert (tmp_path / "a").read_text() == "mew"
    results = [m.tool_result for m in history if m.role == "tool"]
    assert not results[1].ok and results[2].ok and results[3].data["stdout"] == "mew"
    assert len(provider.requests) == 5
    assert all(options["tool_choice"] == "auto" for _, options in provider.requests)
    assert events[-1].reason == "model_done" and events[-1].iteration == 5
    assert all(event.run_id == events[-1].run_id for event in events)
    assert [m.role for m in provider.requests[4][0] if m.role != "context"] == ["user", "assistant", "tool", "assistant", "tool", "assistant", "tool", "assistant", "tool"]


@pytest.mark.parametrize("budget", [1, 2, 20])
@async_test
async def test_budget_stops_after_whole_last_batch_without_summary(tmp_path, budget):
    agent, provider = make_agent(tmp_path, [calls(tool("a"), tool("b"))] * budget,
                                 **({} if budget == 20 else {"max_iterations": budget}))
    events, history = await run(agent)
    assert len(provider.requests) == budget
    assert events[-1].reason == "max_iterations" and events[-1].iteration == budget
    assert len([m for m in history if m.role == "tool"]) == budget * 2
    assert len([e for e in events if e.kind == "usage"]) == budget


@async_test
async def test_last_text_response_and_new_request_reset_budget(tmp_path):
    agent, provider = make_agent(tmp_path, [answer("一"), answer("二")], max_iterations=1)
    one, history = await run(agent)
    two, _ = await run(agent, history=history, question="第二问")
    assert one[-1].reason == two[-1].reason == "model_done"
    assert one[-1].iteration == two[-1].iteration == 1
    assert one[-1].run_id != two[-1].run_id
    assert len(provider.requests) == 2


@async_test
async def test_three_all_unknown_responses_win_over_budget(tmp_path):
    batch = calls(*(tool(str(i), "missing", "{}") for i in range(3)))
    agent, provider = make_agent(tmp_path, [batch] * 3, max_iterations=3)
    events, history = await run(agent)
    assert len(provider.requests) == 3 and events[-1].reason == "unknown_tool_limit"
    assert len([m for m in history if m.role == "tool"]) == 9
    assert all(m.tool_result.error["code"] == "unknown_tool" for m in history if m.role == "tool")


@pytest.mark.parametrize("known,mode", [(tool(), "execute"), (tool("b", "write_file", "{"), "plan")])
@async_test
async def test_registered_invalid_or_forbidden_name_resets_unknown_counter(tmp_path, known, mode):
    missing = calls(tool("u", "absent", "{}"))
    agent, provider = make_agent(tmp_path, [missing, missing, calls(known, tool("u", "absent", "{}")),
                                          missing, missing, answer()])
    events, history = await run(agent, mode=mode)
    assert events[-1].reason == "model_done" and len(provider.requests) == 6
    assert [m for m in history if m.role == "assistant"][2].tool_calls[0].name == known.name


@pytest.mark.parametrize("response", [[ProviderError("断流")], answer(""),
    [ProviderEvent("text_delta", "残缺"), ProviderError("断流")]])
@async_test
async def test_stream_failure_drops_candidate_and_does_not_retry(tmp_path, response):
    agent, provider = make_agent(tmp_path, [response])
    events, history = await run(agent)
    assert events[-1].reason == "stream_error" and history == []
    assert len(provider.requests) == 1
    assert len([e for e in events if e.kind == "finished"]) == 1


@async_test
async def test_network_cancel_closes_stream_and_preserves_partial_usage(tmp_path):
    entered = asyncio.Event()
    async def blocked():
        yield ProviderEvent("usage", usage=TokenUsage(17))
        entered.set()
        await asyncio.Event().wait()
        yield ProviderEvent("completed")
    agent, provider = make_agent(tmp_path, [blocked])
    cancel = asyncio.Event()
    task = asyncio.create_task(run(agent, cancel=cancel))
    await entered.wait()
    cancel.set()
    events, history = await asyncio.wait_for(task, 3)
    assert events[-1].reason == "cancelled" and history == []
    assert provider.closed_streams == 1
    assert [e.usage for e in events if e.kind == "usage"] == [TokenUsage(17)]


@pytest.mark.parametrize("cancellation", ["event", "task", "repeated_task"])
@async_test
async def test_network_cancel_waits_for_async_provider_close(tmp_path, cancellation):
    from mewcode.agent import Agent

    class ClosingStream:
        def __init__(self):
            self.reading = asyncio.Event()
            self.closing = asyncio.Event()
            self.closed = False
            self.close_interrupted = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closing.set()
            try:
                # 连接关闭需要异步等待，不能在等待期间再次取消读取任务。
                await asyncio.sleep(0.05)
                self.closed = True
            except asyncio.CancelledError:
                self.close_interrupted = True
                raise

        async def __aiter__(self):
            self.reading.set()
            await asyncio.Event().wait()
            yield ProviderEvent("completed")

    stream = ClosingStream()

    async def create(**kwargs):
        return stream

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    config = ProviderConfig("测试", "openai", "test", "https://example.invalid", "dummy", False, context_window=128000)
    provider = OpenAIProvider(config, client=client)
    agent = Agent(provider, ToolExecutor(default_registry(), ToolContext(tmp_path)))
    cancel = asyncio.Event()
    task = asyncio.create_task(run(agent, cancel=cancel))
    await asyncio.wait_for(stream.reading.wait(), 2)
    if cancellation == "event":
        cancel.set()
    else:
        task.cancel()
    await asyncio.wait_for(stream.closing.wait(), 2)
    if cancellation == "repeated_task":
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
    events, history = await asyncio.wait_for(task, 2)
    assert stream.closed and not stream.close_interrupted
    assert history == []
    assert [event.reason for event in events if event.kind == "finished"] == ["cancelled"]


@async_test
async def test_cancellation_after_last_tool_batch_wins_budget(tmp_path):
    agent, provider = make_agent(tmp_path, [calls(tool())], max_iterations=1)
    cancel, history, events = asyncio.Event(), [], []
    async for event in agent.run("问", history=history, mode="execute", cancel_event=cancel):
        events.append(event)
        if event.kind == "tool_result": cancel.set()
    assert events[-1].reason == "cancelled" and [m.role for m in history] == ["user", "context", "assistant", "tool"]
    assert len(provider.requests) == 1


@async_test
async def test_usage_one_per_request_and_incomplete_total(tmp_path):
    agent, provider = make_agent(tmp_path, [calls(tool()), answer(usage=TokenUsage(11, 9, True))])
    events, _ = await run(agent)
    usages = [e.usage for e in events if e.kind == "usage"]
    assert usages == [TokenUsage(), TokenUsage(11, 9, True)]
    assert events[-1].usage.input_tokens == 11 and events[-1].usage.output_tokens == 9
    assert not events[-1].usage.complete
    assert {"input_tokens", "output_tokens"} <= events[-1].usage.incomplete_fields
