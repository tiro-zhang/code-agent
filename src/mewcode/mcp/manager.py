"""父进程连接缓存；Server 故障、调用取消及关闭互相隔离。"""

import asyncio
from collections import Counter
from dataclasses import replace
import inspect
import signal
import time

from ..tools.base import OUTPUT_LIMIT, ToolResult
from .config import Diagnostic
from .runtime import Invocation, ServerRuntime, silence_sdk_diagnostics
from .tools import adapt_tools


class MCPManager:
    def __init__(self, snapshot, registry, *, notify=None, start_timeout=15, call_timeout=30,
                 close_grace=3, close_timeout=5):
        self.snapshot, self.registry = snapshot, registry
        self.notify = notify
        self.start_timeout, self.call_timeout = start_timeout, call_timeout
        self.close_grace, self.close_timeout = close_grace, close_timeout
        self.records, self.tools = {}, ()
        self.diagnostics = list(snapshot.diagnostics)
        self._started = self._closed = False
        self._close_task = None
        self.loop = None

    def _emit(self, diagnostic):
        self.diagnostics.append(diagnostic)
        if self.notify:
            self.notify(diagnostic)

    async def start(self, *, cancel_event=None):
        if self._started:
            return
        self._started = True
        self.loop = asyncio.get_running_loop()
        cancel_event = cancel_event or asyncio.Event()
        silence_sdk_diagnostics()
        for diagnostic in self.snapshot.diagnostics:
            if self.notify:
                self.notify(diagnostic)
        semaphore = asyncio.Semaphore(4)

        def emit(diagnostic):
            if not self.loop.is_closed():
                self.loop.call_soon_threadsafe(self._emit, diagnostic)

        async def prepare(config):
            async with semaphore:
                if cancel_event.is_set():
                    return
                record = ServerRuntime(config, emit)
                self.records[config.name] = record
                record.thread.start()
                future = asyncio.wrap_future(record.prepared)
                done, _ = await asyncio.wait([future], timeout=self.start_timeout)
                if not done:
                    record.fail("初始化／发现", "超过 15 秒完整发现期限" if self.start_timeout == 15 else "完整发现超时")
                    record.request_stop(force=True)

        preparations = asyncio.gather(*(prepare(c) for c in self.snapshot.servers))
        cancelled = asyncio.create_task(cancel_event.wait())
        try:
            await asyncio.wait([preparations, cancelled], return_when=asyncio.FIRST_COMPLETED)
            if cancel_event.is_set():
                preparations.cancel()
                await asyncio.gather(preparations, return_exceptions=True)
                await self.close()
                return
            await preparations
        finally:
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)
        candidates = []
        for name, record in sorted(self.records.items()):
            if record.state != "ready":
                continue
            tools, diagnostics = adapt_tools(name, record.config.fingerprint, record.definitions, self.registry.names())
            candidates.extend(replace(tool, connection_description=record.config.safe_description) for tool in tools)
            for diagnostic in diagnostics:
                self._emit(diagnostic)
        counts = Counter(tool.name for tool in candidates)
        self.tools = tuple(tool for tool in candidates if counts[tool.name] == 1)
        for tool in candidates:
            if counts[tool.name] != 1:
                self._emit(Diagnostic(tool.server_name, "工具适配", "跨 Server 别名冲突，相关项未注册"))
        for tool in self.tools:
            self.registry.register(tool)
        for name, record in sorted(self.records.items()):
            if record.state == "ready":
                count = sum(t.server_name == name for t in self.tools)
                self._emit(Diagnostic(name, "就绪", f"已注册 {count} 个工具"))

    async def call(self, tool, arguments, *, cancel_event=None, on_event=None, limit=OUTPUT_LIMIT):
        record = self.records.get(tool.server_name)
        if self._closed or record is None or record.state != "ready" or record.loop is None:
            return ToolResult.failure("mcp_unavailable", "所属 MCP Server 不可用", details={"not_started": True})
        cancel_event = cancel_event or asyncio.Event()
        def notify():
            async def deliver():
                if on_event:
                    result = on_event({"kind": "tool_started"})
                    if inspect.isawaitable(result):
                        await result
            return asyncio.run_coroutine_threadsafe(deliver(), self.loop)
        invocation = Invocation(notify)
        if cancel_event.is_set():
            invocation.cancelled.set()
        future = asyncio.run_coroutine_threadsafe(record.invoke(tool, arguments, invocation, self.call_timeout, limit), record.loop)
        operation = asyncio.wrap_future(future)
        watcher = asyncio.create_task(cancel_event.wait())
        try:
            await asyncio.wait([operation, watcher], return_when=asyncio.FIRST_COMPLETED)
            if not operation.done() and cancel_event.is_set():
                record.cancel(invocation)
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            cancel_event.set()
            record.cancel(invocation)
            from ..async_utils import protected
            return await protected(operation, cancel_event=cancel_event)
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    async def close(self):
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close_all())
        await asyncio.shield(self._close_task)

    async def _close_all(self):
        async def close_one(record):
            started = time.monotonic()
            record.request_stop()
            future = asyncio.wrap_future(record.finished)
            done, _ = await asyncio.wait([future], timeout=self.close_grace)
            if not done:
                record.request_stop(force=True)
            # SDK owner 结束不代表进程组为空：主进程可先于其后代退出。
            if not done or record.process_group_alive():
                record.signal_process(signal.SIGTERM)
                await asyncio.sleep(min(.2, max(0, self.close_timeout - (time.monotonic() - started))))
                record.signal_process(signal.SIGKILL)
                done, _ = await asyncio.wait([future], timeout=max(0, self.close_timeout - (time.monotonic() - started)))
            while record.process_group_alive() and time.monotonic() - started < self.close_timeout:
                await asyncio.sleep(min(.02, max(0, self.close_timeout - (time.monotonic() - started))))
            if not done or record.cleanup_failed or record.process_group_alive():
                record.state = "failed"
                self._emit(Diagnostic(record.config.name, "关闭", "清理失败或超过期限；未确认资源已回收"))
            else:
                record.state = "closed"
        await asyncio.gather(*(close_one(record) for record in self.records.values()))
