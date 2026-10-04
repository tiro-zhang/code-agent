"""保护必要的资源收尾，重复取消只记录状态。"""

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


async def protected(awaitable: Awaitable[T], *, cancel_event: asyncio.Event | None = None) -> T:
    task = asyncio.ensure_future(awaitable)
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if cancel_event is not None:
                cancel_event.set()
            if task.done():
                return task.result()


async def next_event(stream, cancel: asyncio.Event):
    """同时观察网络等待和取消；关闭正在等待的迭代后才返回。"""
    pending = asyncio.create_task(anext(stream))
    waiter = asyncio.create_task(cancel.wait())
    try:
        await asyncio.wait((pending, waiter), return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        cancel.set()
    finally:
        waiter.cancel()
        await protected(asyncio.gather(waiter, return_exceptions=True), cancel_event=cancel)
        if cancel.is_set():
            # 只取消一次；再次取消会打断 Provider 的异步流关闭。
            pending.cancel()
            await protected(asyncio.gather(pending, return_exceptions=True), cancel_event=cancel)
    if cancel.is_set():
        raise asyncio.CancelledError
    return pending.result()
