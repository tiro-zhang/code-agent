"""用可终止工作进程执行工具，父进程掌握截止时间和结果。"""

from dataclasses import replace
import multiprocessing
import os
import signal
import asyncio

from ..async_utils import protected
import time

from .base import ToolContext, ToolError, ToolResult
from .processes import output_text
from .registry import ToolRegistry


def _worker(connection, registry: ToolRegistry, context: ToolContext, name: str, raw: str) -> None:
    try:
        os.setsid()
        connection.send({"kind": "ready"})
        result = registry.invoke(name, raw, replace(context, emit=connection.send))
        connection.send({"kind": "result", "result": result.to_dict()})
    finally:
        connection.close()


def _cleanup(process, grouped: bool) -> None:
    """在辅助线程回收进程，不修改进程级信号处理器。"""
    if process.pid is None:
        process.close()
        return

    def stop(sig):
        nonlocal grouped
        try:
            # ready 尚未读到也可检查组长；绝不使用父进程组。
            grouped |= os.getpgid(process.pid) == process.pid
        except ProcessLookupError:
            pass
        try:
            if grouped:
                os.killpg(process.pid, sig)
            elif process.is_alive():
                os.kill(process.pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            # macOS 对只剩僵尸成员的组可能返回 EPERM。
            if process.is_alive():
                raise

    try:
        stop(signal.SIGTERM)
        # 保留组长直到强制清理后代，再回收 PID。
        time.sleep(0.2)
    finally:
        stop(signal.SIGKILL)
        process.join()
        process.close()


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, context: ToolContext, *, timeout: float = 30) -> None:
        if os.name != "posix":
            raise ValueError("工具执行目前需要 macOS 或 Linux 等 POSIX 环境")
        self.registry, self.context, self.timeout = registry, context, timeout

    async def execute(self, name: str, raw: str, *, allowed_tools: frozenset[str] | None = None,
                      cancel_event: asyncio.Event | None = None) -> ToolResult:
        try:
            tool, arguments = self.registry.prepare(name, raw, allowed_tools=allowed_tools)
        except ToolError as error:
            return error.result()
        cancel_event = cancel_event if cancel_event is not None else asyncio.Event()
        if cancel_event.is_set():
            return ToolResult.failure("cancelled", "任务取消，工具未启动", details={"not_started": True})
        deadline = time.monotonic() + (arguments.get("timeout_seconds", self.timeout) if name == "execute_command" else self.timeout)
        mp = multiprocessing.get_context("spawn")
        receive, send = mp.Pipe(duplex=False)
        process = mp.Process(target=_worker, args=(send, self.registry, self.context, name, raw))
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        grouped, truncated = False, False
        failure = "execution_error"
        completed_result = None
        try:
            process.start()
            send.close()
            while True:
                if cancel_event.is_set():
                    failure = "cancelled"
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = "timeout"
                    break
                if not await asyncio.to_thread(receive.poll, min(remaining, 0.025)):
                    if not process.is_alive():
                        break
                    continue
                event = await asyncio.to_thread(receive.recv)
                if event["kind"] == "ready":
                    grouped = True
                elif event["kind"] == "output":
                    buffer = buffers[event["stream"]]
                    buffer.extend(event["chunk"][:max(0, self.context.output_limit // 2 - len(buffer))])
                    truncated |= event["truncated"]
                elif event["kind"] == "result":
                    completed_result = ToolResult.from_dict(event["result"])
                    break
        except asyncio.CancelledError:
            cancel_event.set()
            failure = "cancelled"
        except Exception:
            failure = "execution_error"
        finally:
            await protected(asyncio.to_thread(_cleanup, process, grouped), cancel_event=cancel_event)
            if cancel_event.is_set():
                failure = "cancelled"
            receive.close()
            send.close()
        if completed_result is not None:
            return completed_result
        detail = {"timeout": "工具执行超时", "cancelled": "用户取消工具执行", "execution_error": "工具工作进程异常结束"}[failure]
        mutating = not bool(getattr(tool, "read_only", False))
        if mutating:
            detail += "；可能已有副作用，请先检查实际状态"
        data = None
        if name == "execute_command":
            decoded = {key: output_text(value, self.context.output_limit // 2) for key, value in buffers.items()}
            data = {"exit_code": None, **{key: value[0] for key, value in decoded.items()}}
            truncated |= any(value[1] for value in decoded.values())
        details = {"side_effects_may_have_occurred": mutating}
        return ToolResult.failure(failure, detail, details=details, data=data, truncated=truncated)
