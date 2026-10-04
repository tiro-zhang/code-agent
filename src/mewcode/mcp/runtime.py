"""单 Server 的 SDK 所有者；固定任务持有上下文、独立期限及可回收进程组。"""

import asyncio
from concurrent.futures import Future
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
import json
import logging
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
from urllib.parse import parse_qsl, urlsplit

import anyio
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
import mcp_types as types
from pydantic import ValidationError

from ..tools.base import ToolResult
from .config import Diagnostic
from .http import LifecycleHTTPClient, RequestLifetime
from .tools import adapt_result, adapt_request_error


_invocation = ContextVar("mewcode_mcp_invocation", default=None)


def silence_sdk_diagnostics():
    for name in ("mcp", "client", "httpx2", "httpcore2"):
        logger = logging.getLogger(name)
        logger.propagate = False
        logger.handlers[:] = [logging.NullHandler()]
    # SDK 子 logger 若没有自己的 handler 会走到上述安全边界。


class _ReadStream:
    def __init__(self, inner, runtime):
        self.inner, self.runtime = inner, runtime

    @property
    def last_context(self):
        return getattr(self.inner, "last_context", None)

    async def receive(self):
        try:
            return await self.inner.receive()
        except (anyio.EndOfStream, anyio.BrokenResourceError, anyio.ClosedResourceError):
            if not self.runtime.closing.is_set():
                self.runtime.fail("连接", "连接已结束")
            raise

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.receive()
        except anyio.EndOfStream:
            raise StopAsyncIteration

    async def aclose(self):
        await self.inner.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()


class _WriteStream:
    def __init__(self, inner):
        self.inner = inner

    async def send(self, item):
        invocation = _invocation.get()
        if invocation is not None and getattr(item.message, "method", None) == "tools/call":
            if invocation.cancelled.is_set():
                raise asyncio.CancelledError
            await self.inner.send(item)
            invocation.started = True
            await asyncio.wrap_future(invocation.notify())
        else:
            await self.inner.send(item)

    async def aclose(self):
        await self.inner.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()


@asynccontextmanager
async def _observe(transport, runtime):
    async with transport as (read, write):
        try:
            yield _ReadStream(read, runtime), _WriteStream(write)
        finally:
            # 在 SDK 关闭传输及临时目录删除前保存自己启动的进程组身份。
            runtime.capture_pid()


class Invocation:
    def __init__(self, notify):
        self.cancelled = threading.Event()
        self.started = False
        self.notify = notify
        self.task = None


class ServerRuntime:
    def __init__(self, config, emit):
        self.config, self.emit = config, emit
        self.state = "starting"
        self.prepared, self.finished = Future(), Future()
        self.closing = threading.Event()
        self.loop = self.stop = self.scope = self.client = None
        self.definitions = ()
        self.pid_file = None
        self.pid = None
        self.cleanup_failed = False
        self.invocations = set()
        # 独立守护线程使不响应取消的第三方 owner 不会卡住主 asyncio.run 退出。
        # 每个线程内仍只有一个固定 SDK 上下文 owner，普通收尾必须完成并退出线程。
        self.thread = threading.Thread(target=self._run, name=f"mcp-{config.name}", daemon=True)

    def _run(self):
        try:
            asyncio.run(self._own())
        except BaseException:
            self.cleanup_failed = True
            self.fail("关闭" if self.closing.is_set() else "连接", "连接任务异常结束")
        finally:
            if not self.prepared.done():
                self.prepared.set_result(None)
            if self.closing.is_set() and not self.cleanup_failed:
                self.state = "closed"
            self.finished.set_result(None)

    def fail(self, stage, reason):
        if self.state != "failed" and not self.closing.is_set():
            self.state = "failed"
            self.emit(Diagnostic(self.config.name, stage, reason))

    async def _own(self):
        self.loop = asyncio.get_running_loop()
        self.stop = asyncio.Event()
        try:
            with anyio.CancelScope() as scope:
                self.scope = scope
                if self.closing.is_set():
                    return
                async with AsyncExitStack() as stack:
                    cfg = self.config
                    if cfg.transport == "stdio":
                        directory = stack.enter_context(tempfile.TemporaryDirectory(prefix="mewcode-mcp-"))
                        self.pid_file = Path(directory) / "pid"
                        stderr = stack.enter_context(open(os.devnull, "w"))
                        params = StdioServerParameters(command=sys.executable,
                            args=[str(Path(__file__).with_name("_launch.py")), str(self.pid_file),
                                  json.dumps(list(cfg.env)), cfg.command, *cfg.args],
                            env=cfg.env, cwd=cfg.cwd)
                        transport = stdio_client(params, errlog=stderr)
                    else:
                        http = await stack.enter_async_context(LifecycleHTTPClient(headers=cfg.headers, timeout=None, trust_env=False))
                        transport = streamable_http_client(cfg.url, http_client=http)
                    async with Client(_observe(transport, self), mode="auto", cache=None,
                                      client_info=types.Implementation(name="MewCode", version="0.1.0")) as client:
                        self.client = client
                        definitions, cursors, cursor = [], set(), None
                        if client.server_capabilities.tools is not None:
                            while True:
                                page = await client.session.list_tools(params=types.PaginatedRequestParams(cursor=cursor) if cursor else None)
                                definitions.extend(page.tools)
                                cursor = page.next_cursor
                                if cursor is None:
                                    break
                                if cursor in cursors:
                                    raise ValueError("重复 cursor")
                                cursors.add(cursor)
                        if self.state == "starting" and not self.closing.is_set():
                            self.definitions = tuple(definitions)
                            self.state = "ready"
                        if not self.prepared.done():
                            self.prepared.set_result(None)
                        await self.stop.wait()
        except asyncio.CancelledError:
            pass
        except BaseException:
            if self.closing.is_set():
                self.cleanup_failed = True
            else:
                self.fail("初始化／发现", "协议协商或完整工具发现失败")
        finally:
            self.client = None
            if not self.prepared.done():
                self.prepared.set_result(None)

    def request_stop(self, *, force=False):
        self.closing.set()
        def stop():
            for invocation in tuple(self.invocations):
                invocation.cancelled.set()
                if invocation.task:
                    invocation.task.cancel()
            if self.stop:
                self.stop.set()
            if self.scope and (force or self.state == "starting"):
                self.scope.cancel()
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(stop)

    def capture_pid(self):
        if self.config.transport != "stdio":
            return
        if self.pid is None and self.pid_file is not None:
            try:
                self.pid = int(self.pid_file.read_text())
            except (OSError, ValueError):
                return

    def process_group_alive(self):
        self.capture_pid()
        if self.pid is None or self.pid <= 1 or self.pid == os.getpgrp():
            return False
        try:
            os.killpg(self.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            self.cleanup_failed = True
            return True

    def signal_process(self, sig):
        self.capture_pid()
        if self.pid is not None and self.pid > 1 and self.pid != os.getpgrp():
            try:
                os.killpg(self.pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                self.cleanup_failed = True

    def cancel(self, invocation):
        invocation.cancelled.set()
        def cancel():
            if invocation.task and not invocation.task.done():
                invocation.task.cancel()
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(cancel)

    async def invoke(self, tool, arguments, invocation, timeout, limit):
        invocation.task = asyncio.current_task()
        self.invocations.add(invocation)
        token = _invocation.set(invocation)
        lifetime = RequestLifetime(self.loop.time() + timeout)
        try:
            if invocation.cancelled.is_set():
                return ToolResult.failure("cancelled", "任务取消，外部工具未启动", details={"not_started": True})
            if self.state != "ready" or self.client is None or self.closing.is_set():
                return ToolResult.failure("mcp_unavailable", "所属 MCP Server 不可用", details={"not_started": True})
            with lifetime.bind():
                async with asyncio.timeout(timeout):
                    result = await self.client.session.call_tool(tool.original_name, arguments, allow_input_required=True)
            return adapt_result(result, limit=limit)
        except (asyncio.CancelledError, TimeoutError) as error:
            code = "cancelled" if isinstance(error, asyncio.CancelledError) else "timeout"
            message = "外部请求已取消" if code == "cancelled" else "外部请求等待超时"
            if invocation.started:
                message += "；远端可能仍在执行或已有副作用"
            return ToolResult.failure(code, message, details={"not_started": not invocation.started,
                "side_effects_may_have_occurred": invocation.started})
        except MCPError as error:
            if self.loop.time() >= lifetime.deadline:
                return ToolResult.failure("timeout", "外部请求等待超时；远端可能仍在执行或已有副作用",
                                          details={"side_effects_may_have_occurred": invocation.started})
            if error.code == types.INVALID_REQUEST and error.error.message == "Session terminated":
                self.fail("调用", "远端会话已失效")
            code = "mcp_unavailable" if self.state == "failed" else "mcp_request_error"
            cfg = self.config
            secrets = [*cfg.env.values(), *cfg.headers.values(), *(v for _, v in parse_qsl(urlsplit(cfg.url).query))]
            secrets.extend(v.split(" ", 1)[1] for v in cfg.headers.values() if v.lower().startswith(("bearer ", "basic ")))
            return adapt_request_error(error.error.model_dump(), secrets=secrets, limit=limit,
                                       code=code, started=invocation.started)
        except (ValueError, RuntimeError, ValidationError):
            return ToolResult.failure("mcp_protocol_error", "外部响应不符合工具协议", details={"side_effects_may_have_occurred": invocation.started})
        except Exception:
            return ToolResult.failure("mcp_request_error", "外部请求未能完成", details={"side_effects_may_have_occurred": invocation.started})
        finally:
            lifetime.close()
            _invocation.reset(token)
            self.invocations.discard(invocation)
