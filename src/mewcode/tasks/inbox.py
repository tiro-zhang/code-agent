"""父身份分箱的终态结果；预览不消费，发送才确认。"""

import asyncio
from collections import OrderedDict
from copy import deepcopy


class ResultInbox:
    def __init__(self):
        self._pending = OrderedDict()
        self._seen = set()
        self.changed = asyncio.Event()

    def put(self, report) -> bool:
        task_id = report["task_id"]
        if task_id in self._seen:
            return False
        self._seen.add(task_id)
        self._pending[task_id] = deepcopy(report)
        self.changed.set()
        return True

    def peek(self, parent_task_id):
        return tuple(deepcopy(report) for report in self._pending.values()
                     if report["parent_task_id"] == parent_task_id)

    def consume(self, reports):
        """仅实际发请求时确认预览中的任务；后来结果留待下次。"""
        for report in reports:
            self._pending.pop(report["task_id"], None)
        if not self._pending:
            self.changed.clear()

    def discard_parent(self, parent_task_id):
        self.consume(self.peek(parent_task_id))

    def parent_ids(self):
        return tuple(dict.fromkeys(report["parent_task_id"] for report in self._pending.values()))
