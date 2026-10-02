"""工具批次提交、真实副作用及失败/取消后的完整历史。"""
import asyncio
import pytest
from conftest import ScriptedProvider as BaseProvider, async_test, collect
from mewcode.session import ChatSession
from mewcode.tools import default_registry
from mewcode.tools.base import ToolContext
from mewcode.tools.executor import ToolExecutor
from mewcode.types import ProviderError, ProviderEvent, ToolCall
from test_agent_loop import answer, calls as call
from test_tool_execution import wait_file


class ScriptedProvider(BaseProvider):
    def __init__(self, *responses):
        super().__init__(responses)


class CountingExecutor(ToolExecutor):
    def __init__(self, root):
        super().__init__(default_registry(), ToolContext(root))
        self.count = 0
    async def execute(self, name, raw, **options):
        self.count += 1
        return await super().execute(name, raw, **options)


@pytest.fixture
def executor(tmp_path):
    return CountingExecutor(tmp_path)


@async_test
async def test_multi_call_batch_commits_in_order_before_next_request(executor, tmp_path):
    provider = ScriptedProvider(call(ToolCall("a", "write_file", '{"path":"x","content":"猫"}'),
                                    ToolCall("b", "read_file", '{"path":"x"}')), answer(), answer("下一轮"))
    session = ChatSession(provider, executor=executor)
    await collect(session.ask("创建并读取"))
    assert (tmp_path / "x").read_text() == "猫" and executor.count == 2
    assert provider.requests[1][1]["tool_choice"] == "auto"
    assert [m.tool_call_id for m in provider.requests[1][0] if m.role == "tool"] == ["a", "b"]
    assert [m for m in provider.requests[1][0] if m.role == "tool"][-1].tool_result.data["content"] == "猫"
    await collect(session.ask("刚才做了什么"))
    assert provider.requests[-1][0] == tuple(session.history[:-1]) and executor.count == 2


@pytest.mark.parametrize("raw,code,count", [("{", "invalid_arguments", 0), ('{"path":"missing"}', "file_not_found", 1)])
@async_test
async def test_errors_return_once_without_scheduler_retry(executor, raw, code, count):
    session = ChatSession(ScriptedProvider(call(ToolCall("a", "read_file", raw)), answer("失败")), executor=executor)
    await collect(session.ask("读文件"))
    assert executor.count == count and [m for m in session.history if m.role == "tool"][0].tool_result.error["code"] == code


@async_test
async def test_write_then_stream_error_keeps_real_result_for_next_task(executor, tmp_path):
    provider = ScriptedProvider(call(ToolCall("a", "write_file", '{"path":"x","content":"done"}')),
        [ProviderEvent("text_delta", "残缺"), ProviderError("断流")], answer("已创建"))
    session = ChatSession(provider, executor=executor)
    events = await collect(session.ask("创建"))
    assert events[-1].reason == "stream_error" and [m.role for m in session.history] == ["user", "context", "assistant", "tool"]
    assert (tmp_path / "x").read_text() == "done"
    await collect(session.ask("继续"))
    assert [m.role for m in provider.requests[-1][0]] == ["user", "context", "assistant", "tool", "user", "context"] and "残缺" not in repr(provider.requests[-1][0]) and executor.count == 1


@async_test
async def test_cancel_during_second_model_stream_keeps_pairs(executor, tmp_path):
    cancel = asyncio.Event()
    async def interrupted():
        yield ProviderEvent("text_delta", "残缺")
        cancel.set()
        await asyncio.Event().wait()
    provider = ScriptedProvider(call(ToolCall("a", "write_file", '{"path":"x","content":"done"}')), interrupted, answer("下一轮"))
    session = ChatSession(provider, executor=executor)
    events = await collect(session.ask("创建", cancel_event=cancel))
    assert events[-1].reason == "cancelled" and [m.role for m in session.history] == ["user", "context", "assistant", "tool"]
    assert session.history[-1].tool_result.ok and (tmp_path / "x").exists()
    await collect(session.ask("继续"))
    assert "残缺" not in repr(provider.requests[-1][0])


@async_test
async def test_tool_cancel_fills_later_calls_and_next_input_has_whole_batch(executor, tmp_path):
    cancel = asyncio.Event()
    provider = ScriptedProvider(call(ToolCall("active", "execute_command", '{"command":"touch started; sleep 20"}'),
        ToolCall("queued", "write_file", '{"path":"never","content":"x"}')), answer("下轮"))
    session = ChatSession(provider, executor=executor)
    task = asyncio.create_task(collect(session.ask("操作", cancel_event=cancel)))
    await wait_file(tmp_path / "started")
    cancel.set()
    events = await task
    assert events[-1].reason == "cancelled"
    assert [m.tool_call_id for m in session.history if m.role == "tool"] == ["active", "queued"]
    assert session.history[-1].tool_result.error["details"]["not_started"]
    assert not (tmp_path / "never").exists() and executor.count == 1
    await collect(session.ask("继续"))
    assert [m.role for m in provider.requests[-1][0]] == ["user", "context", "assistant", "tool", "tool", "user", "context"]


@async_test
async def test_cancel_during_cleanup_preserves_completed_file(executor, tmp_path, monkeypatch):
    from mewcode.tools import executor as module
    cleanup = module._cleanup
    cancel = asyncio.Event()
    loop = asyncio.get_running_loop()
    def cancel_cleanup(process, grouped):
        loop.call_soon_threadsafe(cancel.set)
        return cleanup(process, grouped)
    monkeypatch.setattr(module, "_cleanup", cancel_cleanup)
    provider = ScriptedProvider(call(ToolCall("a", "write_file", '{"path":"x","content":"done"}')))
    session = ChatSession(provider, executor=executor)
    events = await collect(session.ask("创建", cancel_event=cancel))
    assert events[-1].reason == "cancelled" and len(provider.requests) == 1
    assert (tmp_path / "x").read_text() == "done"
    assert session.history[-1].tool_result.ok and session.history[-1].tool_result.data["bytes_written"] == 4
