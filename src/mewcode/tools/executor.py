"""用可终止工作进程执行工具，父进程掌握截止时间和结果。"""

from dataclasses import replace
import multiprocessing
import os
import signal
import threading
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


def _cleanup(process, grouped: bool) -> bool:
    if process.pid is None:
        return False
    cancelled = False

    def remember_cancel(signum, frame):
        nonlocal cancelled
        cancelled = True

    # 清理期间记住 Ctrl+C，先回收资源，再让调用方保存取消结果。
    main_thread = threading.current_thread() is threading.main_thread()
    previous = signal.signal(signal.SIGINT, remember_cancel) if main_thread else None
    try:
        # ready 消息可能尚未读取，检查组号以处理启动阶段取消。
        try:
            grouped |= os.getpgid(process.pid) == process.pid
        except ProcessLookupError:
            pass

        def stop(sig):
            try:
                if grouped:
                    os.killpg(process.pid, sig)
                elif process.is_alive():
                    os.kill(process.pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                # macOS 对只剩僵尸成员的组返回 EPERM，而非 ESRCH。
                if process.is_alive():
                    raise

        try:
            stop(signal.SIGTERM)
            # 强制清理前保留组长，避免组长已回收但后代尚未退出的竞争。
            time.sleep(0.2)
        finally:
            stop(signal.SIGKILL)
            process.join()
            process.close()
    finally:
        if main_thread:
            signal.signal(signal.SIGINT, previous)
    return cancelled


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, context: ToolContext, *, timeout: float = 30) -> None:
        if os.name != "posix":
            raise ValueError("工具执行目前需要 macOS 或 Linux 等 POSIX 环境")
        self.registry, self.context, self.timeout = registry, context, timeout

    def execute(self, name: str, raw: str) -> ToolResult:
        try:
            _, arguments = self.registry.prepare(name, raw)
        except ToolError as error:
            return error.result()
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
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = "timeout"
                    break
                if not receive.poll(min(remaining, 0.05)):
                    if not process.is_alive():
                        break
                    continue
                event = receive.recv()
                if event["kind"] == "ready":
                    grouped = True
                elif event["kind"] == "output":
                    buffer = buffers[event["stream"]]
                    buffer.extend(event["chunk"][:max(0, self.context.output_limit // 2 - len(buffer))])
                    truncated |= event["truncated"]
                elif event["kind"] == "result":
                    completed_result = ToolResult.from_dict(event["result"])
                    break
        except KeyboardInterrupt:
            failure = "cancelled"
        except (EOFError, OSError, ValueError):
            failure = "execution_error"
        finally:
            if _cleanup(process, grouped):
                failure = "cancelled"
            receive.close()
            send.close()
        if completed_result is not None and failure != "cancelled":
            return completed_result
        detail = {"timeout": "工具执行超时", "cancelled": "用户取消工具执行", "execution_error": "工具工作进程异常结束"}[failure]
        mutating = name in {"write_file", "edit_file", "execute_command"}
        if mutating:
            detail += "；可能已有副作用，请先检查实际状态"
        data = None
        if name == "execute_command":
            decoded = {key: output_text(value, self.context.output_limit // 2) for key, value in buffers.items()}
            data = {"exit_code": None, **{key: value[0] for key, value in decoded.items()}}
            truncated |= any(value[1] for value in decoded.values())
        details = {"side_effects_may_have_occurred": mutating}
        if completed_result is not None:
            data, truncated = completed_result.data, completed_result.truncated
            details.update(execution_completed=True, execution_ok=completed_result.ok)
            detail = "用户取消本轮；工具已返回结果，实际结果已保留"
        return ToolResult.failure(failure, detail, details=details, data=data, truncated=truncated)
