"""有序事件环和非阻塞订阅；同一事件循环中原子取得快照高水位。"""

import asyncio
from collections import deque
from copy import deepcopy
import json

from .errors import WebError


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8'))


class Subscription:
    def __init__(self, hub):
        self.hub = hub
        self._queue = deque()
        self._initial = None
        self._bytes = 0
        self._ready = asyncio.Event()
        self.closed = False
        self.reason = ''

    def _push(self, event, size):
        if self.closed:
            return
        if len(self._queue) >= self.hub.queue_events or self._bytes + size > self.hub.queue_bytes:
            self.close('slow_consumer')
            return
        self._queue.append((event, size))
        self._bytes += size
        self._ready.set()

    def close(self, reason='closed'):
        if self.closed:
            return
        self.closed, self.reason = True, reason
        self._queue.clear()
        self._initial = None
        self._bytes = 0
        self.hub._subscribers.discard(self)
        self._ready.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        while not self.closed:
            if self._initial is not None:
                event, self._initial = self._initial, None
                return event
            if self._queue:
                event, size = self._queue.popleft()
                self._bytes -= size
                return deepcopy(event)
            self._ready.clear()
            await self._ready.wait()
        raise StopAsyncIteration

    async def aclose(self):
        self.close()


class EventHub:
    def __init__(self, server_instance_id, *, snapshot, max_events=4096,
                 max_bytes=8 * 1024 * 1024, queue_events=256,
                 queue_bytes=1024 * 1024, max_subscribers=2):
        if min(max_events, max_bytes, queue_events, queue_bytes, max_subscribers) < 1:
            raise ValueError('事件限额必须大于零')
        self.server_instance_id = server_instance_id
        self._snapshot = snapshot
        self.max_events, self.max_bytes = max_events, max_bytes
        self.queue_events, self.queue_bytes = queue_events, queue_bytes
        self.max_subscribers = max_subscribers
        self.seq = 0
        self._events = deque()
        self.retained_bytes = 0
        self._subscribers = set()
        self.closed = False

    def snapshot(self):
        """回调必须同步且无副作用；取得状态和序号之间不交还事件循环。"""
        state = deepcopy(self._snapshot())
        state.update(server_instance_id=self.server_instance_id, seq=self.seq)
        return state

    def _envelope(self, kind, payload, session_id, runtime_generation, run_id):
        return {'schema_version': 1, 'server_instance_id': self.server_instance_id, 'seq': self.seq,
                'session_id': session_id, 'runtime_generation': runtime_generation,
                'parent_run_id': payload.get('parent_run_id', '') if isinstance(payload, dict) else '',
                'run_id': run_id, 'kind': kind, 'payload': deepcopy(payload)}

    def publish(self, kind, payload, *, session_id='', runtime_generation=0, run_id=''):
        if self.closed:
            return None
        # 在递增序号前验证正文，非法数据不能制造假的重连缺口。
        _size(payload)
        self.seq += 1
        event = self._envelope(kind, payload, session_id, runtime_generation, run_id)
        size = _size(event)
        self._events.append((event, size))
        self.retained_bytes += size
        while len(self._events) > self.max_events or self.retained_bytes > self.max_bytes:
            _, dropped = self._events.popleft()
            self.retained_bytes -= dropped
        for subscriber in tuple(self._subscribers):
            subscriber._push(event, size)
        return deepcopy(event)

    def subscribe(self, after=None):
        if self.closed:
            raise WebError('server_closing', '服务正在关闭', 503)
        if len(self._subscribers) >= self.max_subscribers:
            raise WebError('event_connection_limit', '事件连接达到上限，请使用状态轮询', 429)
        if after is not None and (type(after) is not int or after < 0):
            raise WebError('invalid_cursor', '事件游标无效', 400)
        subscriber = Subscription(self)
        earliest = self._events[0][0]['seq'] if self._events else self.seq + 1
        replace = after is None or after < earliest - 1 or after > self.seq
        replay = [] if replace else [(event, size) for event, size in self._events if event['seq'] > after]
        # 首次重连积压超过发送队列时直接给出一致快照，不建立注定变慢的队列。
        if len(replay) > self.queue_events or sum(size for _, size in replay) > self.queue_bytes:
            replace = True
        if replace:
            state = self.snapshot()
            runtime = state.get('runtime', {})
            event = self._envelope('snapshot_replace', state, runtime.get('session_id', ''),
                                   runtime.get('generation', 0), runtime.get('run_id', ''))
            # 基线快照独立持有一次；仅后续积压受实时发送队列限制。
            subscriber._initial = event
        else:
            for event, size in replay:
                subscriber._push(event, size)
        if not subscriber.closed:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber):
        if subscriber.hub is self:
            subscriber.close()

    def close(self):
        self.closed = True
        for subscriber in tuple(self._subscribers):
            subscriber.close('server_closing')
