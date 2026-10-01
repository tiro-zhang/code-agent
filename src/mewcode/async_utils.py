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
