"""HTTP 单请求生命周期；让 SDK 旧协议响应恢复服从本地取消。"""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import math

import anyio
import httpx2


_current_request = ContextVar("mewcode_mcp_request", default=None)


class RequestEnded(httpx2.SSEError, httpx2.StreamError):
    """本地请求结束；同时适用于 SDK 的 SSE 和普通响应读取边界。"""


class RequestLifetime:
    def __init__(self, deadline=math.inf):
        self.deadline = deadline
        self.closed = False
        self._scopes = set()

    @contextmanager
    def bind(self):
        token = _current_request.set(self)
        try:
            yield self
        finally:
            _current_request.reset(token)

    @contextmanager
    def io(self):
        if self.closed or anyio.current_time() >= self.deadline:
            raise RequestEnded("本地 MCP 请求已结束")
        with anyio.CancelScope(deadline=self.deadline) as scope:
            self._scopes.add(scope)
            try:
                yield
            finally:
                self._scopes.discard(scope)
        if scope.cancel_called:
            # 不让 CancelledError 逃出 SDK 的请求 task group，避免连带取消会话。
            raise RequestEnded("本地 MCP 请求已结束")

    def close(self):
        self.closed = True
        for scope in tuple(self._scopes):
            scope.cancel()


class _ResponseStream(httpx2.AsyncByteStream):
    def __init__(self, stream, lifetime):
        self.stream, self.lifetime = stream, lifetime

    async def __aiter__(self):
        iterator = self.stream.__aiter__()
        while True:
            try:
                with self.lifetime.io():
                    chunk = await anext(iterator)
            except StopAsyncIteration:
                return
            yield chunk

    async def aclose(self):
        # 已取消的请求仍需释放自身连接池资源。
        with anyio.move_on_after(1, shield=True):
            await self.stream.aclose()


class LifecycleHTTPClient(httpx2.AsyncClient):
    async def send(self, request, **kwargs):
        lifetime = _current_request.get()
        if request.method == "POST":
            # 礼貌通知不继承已取消的原请求，但不能无限占用 SDK 的共享 writer。
            try:
                if json.loads(request.content).get("method") == "notifications/cancelled":
                    lifetime = RequestLifetime(anyio.current_time() + .05)
            except (ValueError, httpx2.RequestNotRead):
                pass
        if lifetime is None:
            return await super().send(request, **kwargs)
        try:
            with lifetime.io():
                response = await super().send(request, **kwargs)
        except RequestEnded:
            # 尚无响应头时以空的本地超时响应结束 SDK 请求，不伪造 JSON-RPC
            # 或工具结果，也不进行网络发送。调用方仍使用自己的取消/超时结果。
            return httpx2.Response(408, request=request, content=b"")
        response.stream = _ResponseStream(response.stream, lifetime)
        return response
