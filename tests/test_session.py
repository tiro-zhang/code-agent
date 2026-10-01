"""会话只依赖统一 Provider 事件。"""

from collections.abc import Iterator, Sequence

import pytest

from mewcode.session import ChatSession
from mewcode.types import ContextLimitError, Message, ProviderError, StreamEvent


class FakeProvider:
    def __init__(self) -> None:
        self.requests: list[list[Message]] = []

    def stream(self, messages: Sequence[Message], **options) -> Iterator[StreamEvent]:
        self.requests.append(list(messages))
        yield StreamEvent("text_delta", "答")
        yield StreamEvent("text_delta", "复")
        yield StreamEvent("completed")


def test_session_uses_only_unified_events_and_sends_previous_text() -> None:
    provider = FakeProvider()
    session = ChatSession(provider)

    first = list(session.ask("第一问"))
    second = list(session.ask("第二问"))

    assert first == second == [
        StreamEvent("text_delta", "答"),
        StreamEvent("text_delta", "复"),
        StreamEvent("completed"),
    ]
    assert provider.requests == [
        [Message("user", "第一问")],
        [
            Message("user", "第一问"),
            Message("assistant", "答复"),
            Message("user", "第二问"),
        ],
    ]


def test_session_rejects_stream_without_completion_and_keeps_history_clean() -> None:
    class IncompleteProvider:
        def stream(self, messages: Sequence[Message], **options) -> Iterator[StreamEvent]:
            yield StreamEvent("text_delta", "部分")

    session = ChatSession(IncompleteProvider())

    with pytest.raises(ProviderError, match="未完成"):
        list(session.ask("问题"))
    assert session.history == []


def test_session_stores_only_answer_not_thinking_summary() -> None:
    class ThinkingProvider:
        def stream(self, messages: Sequence[Message], **options) -> Iterator[StreamEvent]:
            yield StreamEvent("thinking_delta", "秘密摘要")
            yield StreamEvent("text_delta", "最终答案")
            yield StreamEvent("completed")

    session = ChatSession(ThinkingProvider())

    list(session.ask("问题"))
    assert session.history == [Message("user", "问题"), Message("assistant", "最终答案")]


def test_session_rejects_empty_completed_answer() -> None:
    class EmptyProvider:
        def stream(self, messages: Sequence[Message], **options) -> Iterator[StreamEvent]:
            yield StreamEvent("completed")

    session = ChatSession(EmptyProvider())

    with pytest.raises(ProviderError, match="空"):
        list(session.ask("问题"))
    assert session.history == []


def test_session_trims_oldest_turns_on_context_limit_and_informs_user() -> None:
    class OverflowProvider:
        def __init__(self) -> None:
            self.requests: list[list[Message]] = []

        def stream(self, messages: Sequence[Message], **options) -> Iterator[StreamEvent]:
            self.requests.append(list(messages))
            if len(messages) > 3:
                raise ContextLimitError("服务 上下文长度已超出模型限制")
            yield StreamEvent("text_delta", "答复")
            yield StreamEvent("completed")

    provider = OverflowProvider()
    session = ChatSession(provider)
    list(session.ask("第一问"))
    list(session.ask("第二问"))

    events = list(session.ask("第三问"))

    assert events == [
        StreamEvent("history_trimmed", "上下文超限，已丢弃 1 轮较早对话并重试"),
        StreamEvent("text_delta", "答复"),
        StreamEvent("completed"),
    ]
    assert provider.requests[-1] == [
        Message("user", "第二问"),
        Message("assistant", "答复"),
        Message("user", "第三问"),
    ]
    assert session.history == [
        Message("user", "第二问"),
        Message("assistant", "答复"),
        Message("user", "第三问"),
        Message("assistant", "答复"),
    ]


def test_session_raises_context_limit_when_no_history_can_be_dropped() -> None:
    class AlwaysOverflowProvider:
        def stream(self, messages: Sequence[Message], **options) -> Iterator[StreamEvent]:
            raise ContextLimitError("服务 上下文长度已超出模型限制")
            yield  # 使函数成为生成器，异常在迭代时抛出。

    session = ChatSession(AlwaysOverflowProvider())

    with pytest.raises(ContextLimitError, match="上下文"):
        list(session.ask("过长的问题"))
    assert session.history == []


def test_session_does_not_retry_context_limit_after_streamed_output() -> None:
    class LateOverflowProvider:
        def __init__(self) -> None:
            self.requests = 0

        def stream(self, messages: Sequence[Message], **options) -> Iterator[StreamEvent]:
            self.requests += 1
            yield StreamEvent("text_delta", "部分")
            raise ContextLimitError("服务 上下文长度已超出模型限制")

    provider = LateOverflowProvider()
    session = ChatSession(provider)

    with pytest.raises(ProviderError, match="流式输出之后"):
        list(session.ask("问题"))
    assert provider.requests == 1
    assert session.history == []
