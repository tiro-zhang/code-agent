"""终端输入、输出与会话生命周期测试。"""

from collections.abc import Iterator, Sequence
from io import StringIO
from pathlib import Path

from mewcode.app import run
from mewcode.types import ContextLimitError, Message, ProviderError, StreamEvent


class FakeProvider:
    def __init__(self) -> None:
        self.requests: list[list[Message]] = []

    def stream(self, messages: Sequence[Message], **options) -> Iterator[StreamEvent]:
        self.requests.append(list(messages))
        yield StreamEvent("text_delta", "答复")
        yield StreamEvent("completed")


def config_file(tmp_path: Path) -> Path:
    path = tmp_path / ".env.test"
    path.write_text(
        "name=测试后端\n"
        "protocol=openai\n"
        "model=test-model\n"
        "base_url=https://example.com/v1\n"
        "api_key=dummy\n"
        "thinking=false\n"
    )
    return path


def test_prompt_loop_accepts_two_turns_empty_input_and_exit(tmp_path: Path) -> None:
    provider = FakeProvider()
    output = StringIO()

    exit_code = run(
        config_file(tmp_path),
        stdin=StringIO("第一问\n\n第二问\n/exit\n"),
        stdout=output,
        provider_factory=lambda config: provider,
    )

    assert exit_code == 0
    assert "测试后端" in output.getvalue()
    assert output.getvalue().count("你> ") == 4
    assert len(provider.requests) == 2
    assert provider.requests[1] == [
        Message("user", "第一问"),
        Message("assistant", "答复"),
        Message("user", "第二问"),
    ]


def test_prompt_loop_exits_on_eof(tmp_path: Path) -> None:
    provider = FakeProvider()
    output = StringIO()

    assert run(
        config_file(tmp_path),
        stdin=StringIO(""),
        stdout=output,
        provider_factory=lambda config: provider,
    ) == 0
    assert provider.requests == []


def test_streaming_flushes_thinking_and_answer_before_completion(tmp_path: Path) -> None:
    class ObservedOutput(StringIO):
        flushed = ""

        def flush(self) -> None:
            self.flushed = self.getvalue()
            super().flush()

    output = ObservedOutput()

    class StreamingProvider:
        def stream(self, messages: Sequence[Message], **options) -> Iterator[StreamEvent]:
            yield StreamEvent("thinking_delta", "先分析")
            assert "思考> 先分析" in output.flushed
            yield StreamEvent("text_delta", "最终回答")
            assert "MewCode> 最终回答" in output.flushed
            yield StreamEvent("completed")

    run(
        config_file(tmp_path),
        stdin=StringIO("问题\n/exit\n"),
        stdout=output,
        provider_factory=lambda config: StreamingProvider(),
    )

    assert output.getvalue().index("思考> 先分析") < output.getvalue().index("MewCode> 最终回答")


def test_answer_without_thinking_does_not_show_thinking_label(tmp_path: Path) -> None:
    output = StringIO()
    run(
        config_file(tmp_path),
        stdin=StringIO("问题\n/exit\n"),
        stdout=output,
        provider_factory=lambda config: FakeProvider(),
    )

    assert "MewCode> 答复" in output.getvalue()
    assert "思考>" not in output.getvalue()


def test_failed_turn_does_not_enter_next_request(tmp_path: Path) -> None:
    class FailsFirstProvider:
        def __init__(self) -> None:
            self.requests: list[list[Message]] = []

        def stream(self, messages: Sequence[Message], **options) -> Iterator[StreamEvent]:
            self.requests.append(list(messages))
            if len(self.requests) == 1:
                yield StreamEvent("text_delta", "残缺")
                raise ProviderError("模拟故障")
            yield StreamEvent("text_delta", "正常")
            yield StreamEvent("completed")

    provider = FailsFirstProvider()
    output = StringIO()
    run(
        config_file(tmp_path),
        stdin=StringIO("第一问\n第二问\n/exit\n"),
        stdout=output,
        provider_factory=lambda config: provider,
    )

    assert "本轮未完成" in output.getvalue()
    assert provider.requests[1] == [Message("user", "第二问")]


def test_ctrl_c_during_stream_discards_turn_and_returns_to_prompt(tmp_path: Path) -> None:
    class InterruptedProvider:
        def __init__(self) -> None:
            self.requests: list[list[Message]] = []

        def stream(self, messages: Sequence[Message], **options) -> Iterator[StreamEvent]:
            self.requests.append(list(messages))
            if len(self.requests) == 1:
                yield StreamEvent("text_delta", "残缺")
                raise KeyboardInterrupt
            yield StreamEvent("text_delta", "正常")
            yield StreamEvent("completed")

    provider = InterruptedProvider()
    output = StringIO()
    run(
        config_file(tmp_path),
        stdin=StringIO("第一问\n第二问\n/exit\n"),
        stdout=output,
        provider_factory=lambda config: provider,
    )

    assert "本轮未完成" in output.getvalue()
    assert provider.requests[1] == [Message("user", "第二问")]


def test_context_trim_notice_is_displayed(tmp_path: Path) -> None:
    class OverflowProvider:
        def __init__(self) -> None:
            self.requests: list[list[Message]] = []

        def stream(self, messages: Sequence[Message], **options) -> Iterator[StreamEvent]:
            self.requests.append(list(messages))
            if len(messages) > 2:
                raise ContextLimitError("服务 上下文长度已超出模型限制")
            yield StreamEvent("text_delta", "答复")
            yield StreamEvent("completed")

    provider = OverflowProvider()
    output = StringIO()
    run(
        config_file(tmp_path),
        stdin=StringIO("第一问\n第二问\n/exit\n"),
        stdout=output,
        provider_factory=lambda config: provider,
    )

    shown = output.getvalue()
    assert "提示>" in shown and "已丢弃 1 轮" in shown
    assert "本轮未完成" not in shown
    assert provider.requests[-1] == [Message("user", "第二问")]


def test_tool_status_is_flushed_before_execution_and_summary_hides_body_and_secret(tmp_path,monkeypatch):
    from mewcode.tools.base import ToolResult
    from mewcode.types import ToolCall
    from test_tool_session import ScriptedProvider, call, answer
    import json

    class ObservedOutput(StringIO):
        flushed=''
        def flush(self):
            self.flushed=self.getvalue()

    output=ObservedOutput()
    provider=ScriptedProvider(call(ToolCall('a','write_file',json.dumps({'path':'dummy\x1b[31m\nfile','content':'正文不要打印'*100}))),answer())
    def execute(self,*args):
        assert '工具> write_file' in output.flushed and '[已隐藏]' in output.flushed
        assert '\x1b' not in output.flushed and '正文不要打印' not in output.flushed
        return ToolResult.success({'path':'x'},truncated=True)
    monkeypatch.setattr('mewcode.tools.executor.ToolExecutor.execute',execute)
    run(config_file(tmp_path),stdin=StringIO('创建\n/exit\n'),stdout=output,provider_factory=lambda config:provider)
    shown=output.getvalue()
    assert '成功' in shown and '截断' in shown and 'dummy' not in shown
    assert shown.index('工具>')<shown.index('成功')<shown.index('MewCode> 答复')


def test_tool_failure_timeout_and_cancel_have_visible_status(tmp_path,monkeypatch):
    from mewcode.tools.base import ToolResult
    from mewcode.types import ToolCall
    from test_tool_session import ScriptedProvider, call, answer
    for code,label in [('file_exists','失败'),('timeout','超时'),('cancelled','取消')]:
        output=StringIO()
        provider=ScriptedProvider(call(ToolCall('a','write_file','{"path":"x","content":"data"}')),answer())
        monkeypatch.setattr('mewcode.tools.executor.ToolExecutor.execute',lambda *args:ToolResult.failure(code,'dummy\x1b详情'))
        run(config_file(tmp_path),stdin=StringIO('创建\n/exit\n'),stdout=output,provider_factory=lambda config:provider)
        shown=output.getvalue()
        assert label in shown and 'dummy' not in shown and '\x1b' not in shown
        assert shown.count('你> ')==2
        assert len(provider.requests)==(1 if code=='cancelled' else 2)
