"""异步测试共享替身；网络边界之外仍使用实际组件。"""

import asyncio
from functools import wraps


async def collect(stream):
    return [event async for event in stream]


class ScriptedProvider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.closed_streams = 0
        self.closed = False

    async def stream(self, messages, **options):
        self.requests.append((tuple(messages), options))
        try:
            response = next(self.responses)
            if callable(response):
                async for event in response():
                    yield event
            else:
                for event in response:
                    if isinstance(event, BaseException):
                        raise event
                    await asyncio.sleep(0)
                    yield event
        finally:
            self.closed_streams += 1

    async def aclose(self):
        self.closed = True


class AsyncRawStream:
    def __init__(self, items):
        self.items = items
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        self.closed = True

    async def __aiter__(self):
        for item in self.items:
            await asyncio.sleep(0)
            if isinstance(item, BaseException):
                raise item
            yield item


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run
