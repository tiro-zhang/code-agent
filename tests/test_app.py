"""同步启动入口中的异步会话、指令、信号与事件展示。"""
import asyncio
import json
import signal
from io import StringIO
from pathlib import Path
import pytest
from conftest import ScriptedProvider
from conftest import run_work_app as run
from mewcode.types import ContextLimitError, Message, ProviderError, ProviderEvent, ToolCall
from test_agent_loop import answer, calls


class FakeProvider(ScriptedProvider):
    def __init__(self):
        super().__init__([answer("答复")] * 20)


def config_file(tmp_path: Path) -> Path:
    path = tmp_path / ".env.test"
    path.write_text("name=测试后端\nprotocol=openai\nmodel=test-model\nbase_url=https://example.com/v1\napi_key=dummy\ncontext_window=128000\nthinking=false\n")
    return path


def invoke(tmp_path, provider, text, output=None):
    output = output if output is not None else StringIO()
    code = run(config_file(tmp_path), stdin=StringIO(text), stdout=output,
               provider_factory=lambda config: provider)
    return code, output.getvalue()


def test_prompt_loop_accepts_two_turns_empty_input_and_exit(tmp_path):
    provider = FakeProvider()
    code, shown = invoke(tmp_path, provider, "第一问\n\n第二问\n/exit\n")
    assert code == 0 and "测试后端" in shown and shown.count("你> ") == 4
    assert tuple(m for m in provider.requests[1][0] if m.role != "context") == (Message("user", "第一问"), Message("assistant", "答复"), Message("user", "第二问"))
    assert provider.closed


@pytest.mark.parametrize("text", ["", "/exit\n"])
def test_idle_exit_closes_client_without_request(tmp_path, text):
    provider = FakeProvider()
    assert invoke(tmp_path, provider, text)[0] == 0
    assert provider.closed and provider.requests == []


def test_idle_ctrl_c_exits_and_closes_client(tmp_path):
    class InterruptedInput(StringIO):
        def readline(self): raise KeyboardInterrupt
    provider = FakeProvider()
    assert run(config_file(tmp_path), stdin=InterruptedInput(), stdout=StringIO(), provider_factory=lambda c: provider) == 0
    assert provider.closed


class ObservedOutput(StringIO):
    flushed = ""
    def flush(self): self.flushed = self.getvalue()


def test_streaming_flushes_thinking_and_answer_before_completion(tmp_path):
    output = ObservedOutput()
    async def response():
        yield ProviderEvent("thinking_delta", "先分析")
        assert "先分析" not in output.flushed
        yield ProviderEvent("text_delta", "最终回答")
        assert "MewCode> 最终回答" in output.flushed
        yield ProviderEvent("completed", message=Message("assistant", "最终回答"))
    provider = ScriptedProvider([response])
    _, shown = invoke(tmp_path, provider, "问题\n/exit\n", output)
    assert '先分析' not in shown and 'MewCode> 最终回答' in shown


def test_stream_failure_discards_candidate_and_reports_reason(tmp_path):
    provider = ScriptedProvider([[ProviderEvent("text_delta", "残缺"), ProviderError("模拟故障")], answer("正常")])
    _, shown = invoke(tmp_path, provider, "第一问\n第二问\n/exit\n")
    assert "本轮未完成" in shown and "stream_error" in shown
    assert tuple(m for m in provider.requests[1][0] if m.role != "context") == (Message("user", "第二问"),)


def test_real_sigint_during_stream_closes_stream_and_continues_input(tmp_path):
    async def interrupted():
        yield ProviderEvent("text_delta", "残缺")
        signal.raise_signal(signal.SIGINT)
        await asyncio.Event().wait()
    provider = ScriptedProvider([interrupted, answer("正常")])
    _, shown = invoke(tmp_path, provider, "第一问\n第二问\n/exit\n")
    assert "cancelled" in shown and "正常" in shown
    assert tuple(m for m in provider.requests[1][0] if m.role != "context") == (Message("user", "第二问"),)
    assert provider.closed_streams == 2 and provider.closed


def test_plan_commands_and_repeated_do_only_switch_mode(tmp_path):
    provider = ScriptedProvider([answer("目标A步骤A验证A"), answer("目标B步骤B验证B"), answer("执行完成"), answer("下一答")])
    _, shown = invoke(tmp_path, provider, "/plan\n任务A\n修订B\n/do\n/do\n下一问\n/exit\n")
    assert len(provider.requests) == 3
    assert len(provider.requests[0][1]["tools"]) == len(provider.requests[1][1]["tools"]) == 4
    assert len(provider.requests[2][1]["tools"]) == 7
    assert [m for m in provider.requests[2][0] if m.role == "user"][-1].content == "下一问"
    assert "执行模式" in shown and shown.count("你> ") == 7


def test_context_trim_notice_and_unknown_usage_are_visible(tmp_path):
    provider = ScriptedProvider([answer(), [ContextLimitError("超限")], answer()])
    _, shown = invoke(tmp_path, provider, "第一问\n第二问\n/exit\n")
    assert "context_blocked" in shown and "未知" in shown and "统计不完整" in shown
    assert "输入 0" not in shown and "本轮未完成" in shown


def test_tool_metadata_is_flushed_redacted_and_body_not_printed(tmp_path, monkeypatch):
    from mewcode.tools.base import ToolResult
    output = ObservedOutput()
    provider = ScriptedProvider([calls(ToolCall("a", "write_file", json.dumps({"path":"dummy\x1b[31m\nfile", "content":"正文不要打印" * 100}))), answer()])
    async def execute(self, *args, **options):
        assert "已接收" not in output.flushed
        assert "\x1b" not in output.flushed and "正文不要打印" not in output.flushed
        await options["on_event"]({"kind": "tool_started"})
        return ToolResult.success({"path":"x"}, truncated=True)
    monkeypatch.setattr("mewcode.tools.executor.ToolExecutor.execute", execute)
    _, shown = invoke(tmp_path, provider, "创建\n/exit\n", output)
    assert "成功" in shown and "截断" in shown and "dummy" not in shown
    assert "已接收" not in shown and "开始" not in shown
    assert shown.index("成功") < shown.index("MewCode> 完成")


@pytest.mark.parametrize("code,label", [("file_exists", "失败"), ("timeout", "超时"), ("cancelled", "取消")])
def test_tool_failures_have_visible_status(tmp_path, monkeypatch, code, label):
    from mewcode.tools.base import ToolResult
    provider = ScriptedProvider([calls(ToolCall("a", "write_file", '{"path":"x","content":"data"}')), answer()])
    async def execute(self, *args, **options):
        return ToolResult.failure(code, "dummy\x1b详情")
    monkeypatch.setattr("mewcode.tools.executor.ToolExecutor.execute", execute)
    _, shown = invoke(tmp_path, provider, "创建\n/exit\n")
    assert label in shown and "dummy" not in shown and "\x1b" not in shown
    assert len(provider.requests) == (1 if code == "cancelled" else 2)


def test_iteration_limit_is_reported_without_completion_claim(tmp_path):
    provider = ScriptedProvider([calls(ToolCall("a", "read_file", "{"))] * 20)
    _, shown = invoke(tmp_path, provider, "操作\n/exit\n")
    assert "max_iterations" in shown and "本轮未完成" in shown and "20/20" in shown
    assert "model_done" not in shown and shown.count("你> ") == 2
