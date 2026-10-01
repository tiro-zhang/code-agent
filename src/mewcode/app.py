"""提示符式终端对话循环。"""

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from .config import ConfigError, ProviderConfig, load_config
from .providers import make_provider
from .session import ChatSession
from .types import Provider, ProviderError


def terminal_text(text: str, secret: str, *, limit: int | None = None) -> str:
    """摘要脱敏后转义控制字符，防止工具元信息改变终端状态。"""
    text = text.replace(secret, "[已隐藏]") if secret else text
    escaped = "".join(char if char.isprintable() else repr(char)[1:-1] for char in text)
    return escaped if limit is None or len(escaped) <= limit else escaped[:limit] + "…"


def run(
    config_path: str | Path,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    provider_factory: Callable[[ProviderConfig], Provider] | None = None,
) -> int:
    """运行一次仅保存在内存中的交互会话。"""
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    error_stream = stderr or sys.stderr

    try:
        config = load_config(config_path)
    except ConfigError as error:
        error_stream.write(f"配置错误：{error}\n")
        return 2

    factory = provider_factory or make_provider
    provider: Provider = factory(config)
    try:
        session = ChatSession(provider)
    except ValueError as error:
        error_stream.write(f"启动失败：{error}\n")
        return 2
    output_stream.write(f"MewCode · {config.name}\n")

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

        active_section: str | None = None
        try:
            for event in session.ask(question):
                if event.kind in {"thinking_delta", "text_delta"} and event.text:
                    if event.kind != active_section:
                        if active_section is not None:
                            output_stream.write("\n")
                        output_stream.write("思考> " if event.kind == "thinking_delta" else "MewCode> ")
                        active_section = event.kind
                    output_stream.write(event.text)
                    output_stream.flush()
                elif event.kind == "completed":
                    output_stream.write("\n")
                elif event.kind == "history_trimmed":
                    if active_section is not None:
                        output_stream.write("\n")
                    active_section = None
                    output_stream.write(f"提示> {terminal_text(event.text, config.api_key, limit=200)}\n")
                    output_stream.flush()
                elif event.kind in {"tool_started", "tool_result"}:
                    if active_section is not None:
                        output_stream.write("\n")
                    active_section = None
                    name = terminal_text(event.tool_name, config.api_key, limit=60)
                    if event.kind == "tool_started":
                        summary = terminal_text(event.text, config.api_key, limit=160)
                        output_stream.write(f"工具> {name} · {summary}\n")
                    else:
                        result = event.result
                        if result.ok:
                            status = "成功"
                        else:
                            code = result.error["code"]
                            label = {"timeout": "超时", "cancelled": "取消"}.get(code, "失败")
                            detail = terminal_text(f"{code}：{result.error['message']}", config.api_key, limit=200)
                            status = f"{label} · {detail}"
                        suffix = " · 输出已截断" if result.truncated else ""
                        output_stream.write(f"工具> {name} · {status}{suffix}\n")
                    output_stream.flush()
        except (ProviderError, KeyboardInterrupt) as error:
            if active_section is not None:
                output_stream.write("\n")
            detail = "用户中断" if isinstance(error, KeyboardInterrupt) else str(error)
            output_stream.write(f"本轮未完成：{terminal_text(detail, config.api_key, limit=300)}\n")
            output_stream.flush()
