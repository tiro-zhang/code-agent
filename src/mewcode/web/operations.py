"""服务持有的幂等操作任务与有界回执；HTTP 等待者不拥有执行。"""

import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from uuid import uuid4

from .errors import WebError


@dataclass
class _Receipt:
    digest: str
    task: asyncio.Task | None = None
    response: tuple | None = None


@dataclass
class _Client:
    highest: int = 0
    receipts: OrderedDict = field(default_factory=OrderedDict)


class OperationLedger:
    def __init__(self, server_instance_id, *, max_clients=32, max_receipts=256):
        if max_clients < 1 or max_receipts < 1:
            raise ValueError('操作回执限额必须大于零')
        self.server_instance_id = server_instance_id
        self.max_clients, self.max_receipts = max_clients, max_receipts
        self._clients = {}
        self._tasks = set()
        self.closed = False

    @property
    def tasks(self):
        return tuple(self._tasks)

    def register(self):
        if self.closed:
            raise WebError('server_closing', '服务正在关闭', 503)
        if len(self._clients) >= self.max_clients:
            raise WebError('client_capacity', '本次服务的客户端登记已达上限', 429)
        identity = uuid4().hex
        self._clients[identity] = _Client()
        return {'server_instance_id': self.server_instance_id,
                'client_id': identity, 'next_sequence': 1}

    def _client(self, identity):
        if not isinstance(identity, str) or identity not in self._clients:
            raise WebError('unknown_client', '客户端身份不存在，请重新登记', 404)
        return self._clients[identity]

    @staticmethod
    def _sequence(value):
        if type(value) is not int or value < 1:
            raise WebError('invalid_sequence', '操作序号必须为正整数', 400)
        return value

    @staticmethod
    def _metadata(identity, sequence):
        return {'client_id': identity, 'sequence': sequence, 'next_sequence': sequence + 1}

    def lookup(self, client_id, sequence):
        client, sequence = self._client(client_id), self._sequence(sequence)
        receipt = client.receipts.get(sequence)
        if receipt is None:
            if sequence <= client.highest:
                raise WebError('operation_expired', '此操作回执已过期，不能重新执行', 410)
            raise WebError('operation_unknown', '尚未接收此操作', 404)
        if receipt.response is not None:
            return deepcopy(receipt.response)
        return 202, {'status': 'pending', 'pending': True, 'operation': self._metadata(client_id, sequence)}

    async def run(self, envelope, payload, operation):
        if not isinstance(envelope, dict):
            raise WebError('invalid_operation', '操作身份格式不合法', 400)
        if envelope.get('server_instance_id') != self.server_instance_id:
            raise WebError('server_instance_changed', '服务已重启，请同步状态后明确提交', 409)
        identity, sequence = envelope.get('client_id'), self._sequence(envelope.get('sequence'))
        client = self._client(identity)
        try:
            encoded = json.dumps({'identity': envelope, 'payload': payload}, sort_keys=True,
                                 ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        except (TypeError, ValueError, RecursionError, UnicodeError):
            raise WebError('invalid_operation', '操作内容无法规范化', 400) from None
        digest = hashlib.sha256(encoded).hexdigest()
        receipt = client.receipts.get(sequence)
        if receipt is not None:
            if receipt.digest != digest:
                raise WebError('operation_conflict', '相同操作身份不能携带不同内容', 409)
            if receipt.response is not None:
                return deepcopy(receipt.response)
            return deepcopy(await asyncio.shield(receipt.task))
        if sequence <= client.highest:
            raise WebError('operation_expired', '此操作回执已过期，不能重新执行', 410)
        if sequence != client.highest + 1:
            raise WebError('sequence_conflict', '操作序号不连续，请先核查未确认操作', 409)
        if self.closed:
            raise WebError('server_closing', '服务正在关闭', 503)
        if len(client.receipts) >= self.max_receipts:
            oldest, previous = next(iter(client.receipts.items()))
            if previous.task is not None and not previous.task.done():
                raise WebError('operation_capacity', '仍有未完成操作，回执容量不足', 429)
            del client.receipts[oldest]
        receipt = _Receipt(digest)
        client.receipts[sequence] = receipt
        client.highest = sequence

        async def execute():
            try:
                status, body = await operation()
                body = deepcopy(body)
                if not isinstance(body, dict):
                    raise ValueError('操作回执必须是对象')
            except WebError as error:
                status, body = error.status, error.body()
            except asyncio.CancelledError:
                status, body = 409, {'error': {'code': 'operation_cancelled', 'message': '操作已取消，请核查当前状态'}}
            except Exception:
                status, body = 500, {'error': {'code': 'operation_failed', 'message': '操作失败，请核查当前状态'}}
            body['operation'] = self._metadata(identity, sequence)
            receipt.response = status, body
            return receipt.response

        receipt.task = asyncio.create_task(execute(), name=f'mewcode-operation-{identity}-{sequence}')
        self._tasks.add(receipt.task)
        def finished(task):
            self._tasks.discard(task)
            if task.cancelled() and receipt.response is None:
                receipt.response = 409, {
                    'error': {'code': 'operation_cancelled', 'message': '操作已取消，请核查当前状态'},
                    'operation': self._metadata(identity, sequence)}
        receipt.task.add_done_callback(finished)
        return deepcopy(await asyncio.shield(receipt.task))

    async def aclose(self, *, timeout=10, cancel=True):
        """停止登记，取消或等待既有操作；未结束的任务仍保留所有权。"""
        self.closed = True
        tasks = tuple(self._tasks)
        if cancel:
            for task in tasks:
                task.cancel()
        if not tasks:
            return True
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        return not pending
