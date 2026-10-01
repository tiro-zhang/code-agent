"""一轮最多执行一个工具；已执行的结果先于最终答复提交。"""

from collections.abc import Generator, Iterator, Sequence
from pathlib import Path

from .providers.tool_messages import validate_calls
from .tools import default_registry
from .tools.base import ToolContext, ToolResult, strict_json
from .tools.executor import ToolExecutor
from .types import ContextLimitError, Message, Provider, ProviderError, StreamEvent, ToolCall


def operation_summary(call: ToolCall) -> str:
    """只摘取路径、命令或模式，写入与替换正文不进入摘要。"""
    try:
        arguments = strict_json(call.arguments)
        if isinstance(arguments, dict):
            for key in ("path", "command", "pattern"):
                if isinstance(arguments.get(key), str):
                    return arguments[key]
    except (ValueError, RecursionError):
        pass
    return "校验参数"


class ChatSession:
    def __init__(self, provider: Provider, *, executor: ToolExecutor | None = None) -> None:
        self.provider = provider
        self.executor = executor or ToolExecutor(default_registry(), ToolContext(Path.cwd()))
        self.history: list[Message] = []

    def _drop_oldest_turns(self, count: int, *, keep_last_turn: bool) -> int:
        """从头部删除至多 count 轮完整对话并返回实际删除数；一轮自 user 消息起至下一个 user 消息之前。"""
        starts = [index for index, message in enumerate(self.history) if message.role == "user"]
        removable = max(len(starts) - (1 if keep_last_turn else 0), 0)
        count = min(count, removable)
        if count:
            end = starts[count] if count < len(starts) else len(self.history)
            del self.history[:end]
        return count

    def _response(self, pending: Sequence[Message], choice: str) -> Generator[StreamEvent, None, Message]:
        """请求既有历史加 pending；上下文超限时按指数步长丢弃最早轮次并重试。"""
        drops = 1
        while True:
            parts, response, completed, yielded = [], None, False, False
            try:
                for event in self.provider.stream([*self.history, *pending],
                                                  tools=self.executor.registry.definitions(), tool_choice=choice):
                    if completed:
                        raise ProviderError("模型在完整响应之后继续返回数据")
                    if event.kind == "text_delta":
                        parts.append(event.text)
                    if event.kind == "completed":
                        completed, response = True, event.message
                    else:
                        yielded = True
                        yield event
            except ContextLimitError:
                # 已向终端输出片段后重试会重复显示，只允许在请求被拒、尚无输出时裁剪。
                if yielded:
                    raise ProviderError("上下文超限出现在流式输出之后，本轮未完成") from None
                # pending 携带本轮 user 消息时全部历史皆可丢弃，否则历史最后一组属于当前轮。
                keep_last_turn = not any(message.role == "user" for message in pending)
                dropped = self._drop_oldest_turns(drops, keep_last_turn=keep_last_turn)
                if not dropped:
                    raise
                yield StreamEvent("history_trimmed", f"上下文超限，已丢弃 {dropped} 轮较早对话并重试")
                drops *= 2
                continue
            if not completed:
                raise ProviderError("本轮流式回答未完成")
            return response or Message("assistant", "".join(parts))

    def ask(self, question: str) -> Iterator[StreamEvent]:
        user = Message("user", question)
        first = yield from self._response((user,), "auto")
        if not first.tool_calls:
            if not first.content:
                raise ProviderError("模型返回空回答，本轮未完成")
            self.history.extend((user, Message("assistant", first.content)))
            yield StreamEvent("completed")
            return
        validate_calls(first.tool_calls)
        results: list[Message] = []
        if len(first.tool_calls) > 1:
            result = ToolResult.failure("too_many_tool_calls", "每轮最多一个工具，本次所有调用均未执行；请下一轮只选择一个工具")
            results = [Message("tool", tool_call_id=call.id, tool_result=result) for call in first.tool_calls]
        else:
            call = first.tool_calls[0]
            yield StreamEvent("tool_started", operation_summary(call), tool_name=call.name)
            try:
                result = self.executor.execute(call.name, call.arguments)
            except KeyboardInterrupt:
                # 覆盖校验/启动交界处的取消；执行器内部负责终止已启动的进程。
                result = ToolResult.failure("cancelled", "用户取消工具执行；可能已有副作用，请先检查实际状态",
                                            details={"side_effects_may_have_occurred": True})
            results.append(Message("tool", tool_call_id=call.id, tool_result=result))
        # 在任何展示或网络请求之前提交成对记录，避免操作已完成却丢失历史。
        self.history.extend((user, first, *results))
        for call, message in zip(first.tool_calls, results):
            yield StreamEvent("tool_result", tool_name=call.name, result=message.tool_result)
        if any(m.tool_result.error and m.tool_result.error["code"] == "cancelled" for m in results):
            return
        final = yield from self._response((), "none")
        if final.tool_calls:
            raise ProviderError("工具协议不兼容：最终答复仍请求工具，已阻止执行；之前的结果已保留")
        if not final.content:
            raise ProviderError("模型返回空回答；之前的工具结果已保留")
        self.history.append(Message("assistant", final.content))
        yield StreamEvent("completed")
