"""OpenAI 兼容流式协议测试。"""

from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from mewcode.config import ProviderConfig
from mewcode.providers.openai import OpenAIProvider
from mewcode.types import Message, ProviderError, StreamEvent


def chunk(text: str | None = None, finish_reason: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason=finish_reason)]
    )


class FakeStream:
    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        self.chunks = chunks

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def __iter__(self) -> Iterator[SimpleNamespace]:
        return iter(self.chunks)


class FakeCompletions:
    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        self.chunks = chunks
        self.request: dict[str, object] = {}

    def create(self, **kwargs: object) -> FakeStream:
        self.request = kwargs
        return FakeStream(self.chunks)


def fake_client(chunks: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(chunks)))


def config() -> ProviderConfig:
    return ProviderConfig("兼容服务", "openai", "third-party-model", "https://service.example/v1", "test-key", False)


def test_openai_compatible_stream_uses_chat_completions_and_emits_text() -> None:
    client = fake_client(
        [chunk("你"), SimpleNamespace(choices=[]), chunk("好"), chunk(finish_reason="stop")]
    )
    provider = OpenAIProvider(config(), client=client)

    received = list(provider.stream([Message("user", "问候")]))

    assert received == [
        StreamEvent("text_delta", "你"),
        StreamEvent("text_delta", "好"),
        StreamEvent("completed"),
    ]
    assert client.chat.completions.request == {
        "model": "third-party-model",
        "messages": [{"role": "user", "content": "问候"}],
        "stream": True,
    }


@pytest.mark.parametrize("chunks", [[chunk("部分")], [chunk("部分"), chunk(finish_reason="length")]])
def test_openai_compatible_stream_rejects_incomplete_answer(chunks: list[SimpleNamespace]) -> None:
    stream = OpenAIProvider(config(), client=fake_client(chunks)).stream([Message("user", "问题")])

    assert next(stream) == StreamEvent("text_delta", "部分")
    with pytest.raises(ProviderError, match="未正常完成"):
        list(stream)


def test_openai_client_receives_configured_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []

    def make_client(**kwargs: str) -> SimpleNamespace:
        calls.append(kwargs)
        return fake_client([chunk("答复"), chunk(finish_reason="stop")])

    monkeypatch.setattr("mewcode.providers.openai.openai.OpenAI", make_client)
    provider = OpenAIProvider(config())
    list(provider.stream([Message("user", "问题")]))

    assert calls == [{"api_key": "test-key", "base_url": "https://service.example/v1"}]


def test_openai_request_error_is_safe_to_display() -> None:
    client = fake_client([])

    def fail(**kwargs: object) -> FakeStream:
        raise RuntimeError("test-key appeared in transport error")

    client.chat.completions.create = fail

    with pytest.raises(ProviderError) as error:
        list(OpenAIProvider(config(), client=client).stream([Message("user", "问题")]))
    assert "test-key" not in str(error.value)


def test_openai_midstream_context_error_has_no_completion() -> None:
    class ContextError(RuntimeError):
        status_code = 400

    class InterruptedStream(FakeStream):
        def __iter__(self) -> Iterator[SimpleNamespace]:
            yield chunk("部分")
            raise ContextError("maximum context length exceeded; test-key")

    client = fake_client([])
    client.chat.completions.create = lambda **kwargs: InterruptedStream([])
    stream = OpenAIProvider(config(), client=client).stream([Message("user", "问题")])

    assert next(stream) == StreamEvent("text_delta", "部分")
    with pytest.raises(ProviderError, match="上下文") as error:
        list(stream)
    assert "test-key" not in str(error.value)
