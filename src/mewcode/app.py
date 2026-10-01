"""终端输入与异步事件展示；活动任务统一接收取消信号。"""

import asyncio
import signal
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from .async_utils import protected
from .config import ConfigError, ProviderConfig, load_config
from .providers import make_provider
from .session import ChatSession, PlanStateError, operation_summary
from .types import AgentEvent, Provider, ProviderError, TokenUsage


def terminal_text(text: str, secret: str, *, limit: int | None = None, multiline: bool = False) -> str:
    """先脱敏再转义控制字符；正文保留换行和制表符。"""
    text = text.replace(secret, "[已隐藏]") if secret else text
    escaped = "".join(char if char.isprintable() or (multiline and char in "\n\t")
                      else repr(char)[1:-1] for char in text)
    return escaped if limit is None or len(escaped) <= limit else escaped[:limit] + "…"


class _Renderer:
    def __init__(self, output: TextIO, secret: str) -> None:
        self.output, self.secret = output, secret
        self.section = None
        self.calls = {}

    def safe(self, value, limit=200):
        return terminal_text(str(value), self.secret, limit=limit)

    def line(self, text):
        if self.section is not None:
            self.output.write("\n")
            self.section = None
        self.output.write(text + "\n")
        self.output.flush()

    @staticmethod
    def usage_text(usage: TokenUsage):
        incoming = "未知" if usage.input_tokens is None else str(usage.input_tokens)
        outgoing = "未知" if usage.output_tokens is None else str(usage.output_tokens)
        suffix = "统计完整" if usage.complete else "统计不完整"
        return f"输入 {incoming}，输出 {outgoing} · {suffix}"

    def show(self, event: AgentEvent):
        if event.kind in {"text_delta", "thinking_delta"} and event.text:
            if event.kind != self.section:
                if self.section is not None:
                    self.output.write("\n")
                self.output.write("思考> " if event.kind == "thinking_delta" else "MewCode> ")
                self.section = event.kind
            self.output.write(terminal_text(event.text, self.secret, multiline=True))
            self.output.flush()
        elif event.kind == "progress":
            if event.phase == "model":
                self.calls.clear()
            mode = "规划" if event.mode == "plan" else "执行"
            phase = "请求模型" if event.phase == "model" else "执行工具"
            self.line(f"进度> {mode} · 请求 {event.iteration}/{event.max_iterations} · {phase}")
        elif event.kind == "history_trimmed":
            self.line(f"提示> {self.safe(event.text)}")
        elif event.kind == "usage":
            self.line(f"用量> 本次 Token · {self.usage_text(event.usage)}")
        elif event.kind in {"tool_call", "tool_started", "tool_result"}:
            name = self.safe(event.tool_name, 60)
            id = self.safe(event.tool_call_id, 60)
            prefix = f"工具> [{id}] {name}"
            if event.kind == "tool_call":
                self.calls[event.tool_call_id] = event.call
                self.line(f"{prefix} · 已接收 · {self.safe(operation_summary(event.call), 160)}")
            elif event.kind == "tool_started":
                call = self.calls.get(event.tool_call_id)
                summary = operation_summary(call) if call else "执行工具"
                self.line(f"{prefix} · 开始 · {self.safe(summary, 160)}")
            else:
                result = event.result
                if result.ok:
                    status = "成功"
                else:
                    code = result.error["code"]
                    label = {"timeout": "超时", "cancelled": "取消"}.get(code, "失败")
                    status = f"{label} · {self.safe(code + '：' + result.error['message'])}"
                suffix = " · 输出已截断" if result.truncated else ""
                self.line(f"{prefix} · {status}{suffix}")
        elif event.kind == "finished":
            label = "结束" if event.reason == "model_done" else "本轮未完成"
            self.line(f"{label}> {event.reason} · {self.safe(event.text, 300)} · 请求 {event.iteration}/{event.max_iterations}")
            self.line(f"用量> 累计已知 Token · {self.usage_text(event.usage)}")


async def _run(config, input_stream, output_stream, error_stream, factory):
    provider = factory(config)
    active_cancel = None
    previous_handler = None
    has_handler = threading.current_thread() is threading.main_thread()

    def interrupt(signum, frame):
        if active_cancel is not None:
            active_cancel.set()
        else:
            raise KeyboardInterrupt

    if has_handler:
        previous_handler = signal.signal(signal.SIGINT, interrupt)
    renderer = _Renderer(output_stream, config.api_key)
    try:
        try:
            session = ChatSession(provider, max_iterations=config.max_iterations)
        except ValueError as error:
            error_stream.write(f"启动失败：{renderer.safe(error)}\n")
            return 2
        output_stream.write(f"MewCode · {renderer.safe(config.name, 100)}\n")
        while True:
            output_stream.write("你> ")
            output_stream.flush()
            try:
                line = input_stream.readline()
            except KeyboardInterrupt:
                output_stream.write("\n")
                return 0
            if not line:
                output_stream.write("\n")
                return 0
            question = line.strip()
            if question == "/exit":
                return 0
            if not question:
                continue
            parts = question.split(maxsplit=1)
            if parts[0] == "/plan":
                session.enter_plan()
                if len(parts) == 1:
                    renderer.line("提示> 已进入只读规划模式，请输入任务；计划完成后输入 /do 直接执行")
                    continue
                question = parts[1]
            active_cancel = asyncio.Event()
            source = session.execute_plan(cancel_event=active_cancel) if question == "/do" else session.ask(question, cancel_event=active_cancel)
            try:
                async for event in source:
                    renderer.show(event)
            except (PlanStateError, ProviderError) as error:
                renderer.line(f"提示> {renderer.safe(error, 300)}")
            finally:
                await protected(source.aclose(), cancel_event=active_cancel)
                active_cancel = None
    finally:
        active_cancel = asyncio.Event()
        try:
            await protected(provider.aclose(), cancel_event=active_cancel)
        finally:
            if has_handler:
                signal.signal(signal.SIGINT, previous_handler)


def run(config_path: str | Path, *, stdin: TextIO | None = None, stdout: TextIO | None = None,
        stderr: TextIO | None = None, provider_factory: Callable[[ProviderConfig], Provider] | None = None) -> int:
    """保留同步启动接口，由单个事件循环管理会话与客户端。"""
    input_stream = stdin if stdin is not None else sys.stdin
    output_stream = stdout if stdout is not None else sys.stdout
    error_stream = stderr if stderr is not None else sys.stderr
    try:
        config = load_config(config_path)
    except ConfigError as error:
        error_stream.write(f"配置错误：{error}\n")
        return 2
    return asyncio.run(_run(config, input_stream, output_stream, error_stream, provider_factory or make_provider))
