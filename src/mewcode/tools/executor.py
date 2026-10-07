"""用可终止工作进程执行工具，父进程掌握截止时间和结果。"""

from dataclasses import replace
import multiprocessing
import os
import signal
import asyncio
import inspect
import json

from ..async_utils import protected
from ..permissions.runtime import PermissionManager
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
    def __init__(self, registry: ToolRegistry, context: ToolContext, *, timeout: float = 30,
                 permissions: PermissionManager | None = None, mcp=None, system_handlers=None) -> None:
        if os.name != "posix":
            raise ValueError("工具执行目前需要 macOS 或 Linux 等 POSIX 环境")
        self.registry, self.context, self.timeout = registry, context, timeout
        self.permissions = permissions if permissions is not None else PermissionManager(context.root)
        self.mcp = mcp
        self.system_handlers = dict(system_handlers or {})
        self.guard = None

    def _prepare(self, name, raw, allowed_tools):
        """先保留未知名称错误，再对系统入口和普通入口执行同一硬检查。"""
        self.registry.get(name)
        if self.guard:
            self.guard(name, None)
        tool, arguments = self.registry.prepare(name, raw, allowed_tools=allowed_tools)
        if self.guard:
            self.guard(name, arguments)
        return tool, arguments

    async def execute(self, name: str, raw: str, *, allowed_tools: frozenset[str] | None = None,
                      cancel_event: asyncio.Event | None = None, on_event=None,
                      hooks=None, hook_fields=None, hook_tool=None, call_id='') -> ToolResult:
        try:
            current = allowed_tools() if callable(allowed_tools) else allowed_tools
            tool, arguments = self._prepare(name, raw, current)
        except ToolError as error:
            return ToolResult.failure(error.code, error.message, details={**error.details, 'not_started': True})
        cancel_event = cancel_event if cancel_event is not None else asyncio.Event()
        if cancel_event.is_set():
            return ToolResult.failure("cancelled", "任务取消，工具未启动", details={"not_started": True})
        try:
            targets = None
            if name in {"glob_files", "search_code"}:
                from .search import enumerate_candidates
                targets = await enumerate_candidates(name, arguments, self.context, cancel_event)
            authorization = await self.permissions.authorize(
                name, arguments, targets=targets, cancel_event=cancel_event, notify=on_event)
            context = replace(self.context, authorized_paths=authorization.targets,
                              permission_skipped_files=authorization.skipped_files)
            raw = json.dumps(authorization.arguments, ensure_ascii=False)
            # 授权等待后仍按当前状态检查，批准不扩大工具范围。
            current = allowed_tools() if callable(allowed_tools) else allowed_tools
            self._prepare(name, raw, current)
            if hooks is not None:
                from ..hooks.events import tool_snapshot
                def fields_for_hook():
                    data = tool_snapshot(self, call_id, name, raw, arguments=authorization.arguments)
                    if hook_tool is not None:
                        hook_tool.update(data)
                    fields = hook_fields() if callable(hook_fields) else (hook_fields or {})
                    return {**fields, 'tool': data}
                decision = await hooks.emit('tool.before', fields=fields_for_hook, cancel_event=cancel_event)
                if cancel_event.is_set():
                    return ToolResult.failure('cancelled', '任务取消，工具未启动', details={'not_started': True})
                if decision.decision == 'deny':
                    return ToolResult.failure('hook_denied', decision.reason,
                        details={'not_started': True, 'hook_source': decision.source})
                current = allowed_tools() if callable(allowed_tools) else allowed_tools
                self._prepare(name, raw, current)
        except ToolError as error:
            return ToolResult.failure(error.code, error.message, details={**error.details, 'not_started': True})
        except asyncio.CancelledError:
            cancel_event.set()
            return ToolResult.failure("cancelled", "任务取消，工具未启动", details={"not_started": True})
        # 人工审批不消耗工具的执行时间预算。
        if getattr(tool, "system", False):
            return await self._execute_system(name, authorization.arguments, cancel_event, on_event, call_id=call_id)
        if name == 'read_file' and getattr(self, 'cache_reader', None) is not None:
            cached = await self.cache_reader(authorization.arguments)
            if cached is not None:
                return cached
        from ..mcp.tools import MCPTool
        if isinstance(tool, MCPTool):
            if self.mcp is None:
                return ToolResult.failure("mcp_unavailable", "外部连接未初始化", details={"not_started": True})
            return await self.mcp.call(tool, authorization.arguments, cancel_event=cancel_event,
                                       on_event=on_event, limit=context.output_limit)
        deadline = time.monotonic() + (arguments.get("timeout_seconds", self.timeout) if name == "execute_command" else self.timeout)
        mp = multiprocessing.get_context("spawn")
        receive, send = mp.Pipe(duplex=False)
        process = mp.Process(target=_worker, args=(send, self.registry, context, name, raw))
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        grouped, truncated = False, False
        failure = "execution_error"
        completed_result = None
        try:
            process.start()
            send.close()
            if on_event:
                notification = on_event({"kind": "tool_started"})
                if inspect.isawaitable(notification):
                    await notification
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

    async def _execute_system(self, name, arguments, cancel, on_event, *, call_id=''):
        handler = self.system_handlers.get(name)
        if handler is None:
            return ToolResult.failure("skill_unavailable", "系统加载服务未初始化", details={"not_started": True})
        timeout = handler.timeout_for(arguments) if hasattr(handler, "timeout_for") else self.timeout
        extra = {'tool_call_id':call_id} if getattr(self.registry.get(name),'requires_scope',False) else {}
        operation = asyncio.create_task(handler(arguments, cancel_event=cancel, on_event=on_event, **extra))
        watcher = asyncio.create_task(cancel.wait())
        try:
            if on_event:
                await on_event({"kind": "tool_started"})
            done, _ = await asyncio.wait((operation, watcher), timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if operation in done:
                return operation.result()
            operation.cancel()
            results = await protected(asyncio.gather(operation, return_exceptions=True), cancel_event=cancel)
            if isinstance(results[0], ToolResult):
                return results[0]
            return ToolResult.failure("cancelled" if cancel.is_set() else "timeout",
                                      "系统加载已取消" if cancel.is_set() else "系统加载超时")
        except ToolError as error:
            return error.result()
        except asyncio.CancelledError:
            cancel.set()
            operation.cancel()
            results = await protected(asyncio.gather(operation, return_exceptions=True), cancel_event=cancel)
            if isinstance(results[0], ToolResult):
                return results[0]
            return ToolResult.failure("cancelled", "系统加载取消，已完成操作保留")
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
