"""有界子任务调度；工具等待者不拥有执行和结果生命周期。"""

import asyncio
from collections import deque
from dataclasses import asdict, dataclass, field
import json
from uuid import uuid4

from ..skills.budget import TaskBudget
from ..tools.base import OUTPUT_LIMIT, ToolError, utf8_prefix
from ..types import TokenUsage
from .context import TaskContext
from .inbox import ResultInbox


@dataclass(frozen=True)
class TaskOutcome:
    reason: str
    text: str
    evidence: tuple[dict, ...] = ()
    usage: TokenUsage = TokenUsage()
    request_usage: tuple = ()
    side_effects: str = "unknown"


@dataclass
class TaskRecord:
    task_id: str
    parent_task_id: str
    type: str
    mode: str
    generation: int
    snapshot: object = None
    role: str = ""
    source: str = ""
    display_mode: str = "foreground"
    state: str = "queued"
    outcome: TaskOutcome | None = None
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    detached: asyncio.Event = field(default_factory=asyncio.Event)
    channel: str = "pending"
    published: bool = False
    handle: asyncio.Task | None = None

    def receipt(self):
        data = {"task_id": self.task_id, "parent_task_id": self.parent_task_id,
                "type": self.type, "role": self.role, "source": self.source,
                "display_mode": self.display_mode, "state": self.state, "generation": self.generation,
                "mode": self.mode}
        if self.snapshot is not None:
            data.update(model=getattr(self.snapshot.config, 'model', 'inherit'),
                        max_iterations=self.snapshot.max_iterations,
                        permission_mode=self.snapshot.permissions.mode)
        return data

    def report(self, *, limit=None):
        report = self.receipt()
        if self.outcome is None:
            return report
        body = json.dumps({"text": self.outcome.text, "evidence": self.outcome.evidence}, ensure_ascii=False, allow_nan=False)
        raw = body.encode("utf-8")
        truncated = limit is not None and len(raw) > limit
        if truncated:
            suffix = "\n[正文已截断；使用 /tasks show 查看完整结果]"
            body = utf8_prefix(raw, max(0, limit - len(suffix.encode()))) + suffix
        usage = asdict(self.outcome.usage)
        usage["incomplete_fields"] = sorted(self.outcome.usage.incomplete_fields)
        report.update(reason=self.outcome.reason, content=body, truncated=truncated,
                      usage=usage, side_effects=self.outcome.side_effects)
        report['request_usage'] = [dict(asdict(item), incomplete_fields=sorted(item.incomplete_fields))
                                   for item in self.outcome.request_usage]
        return report


class TaskManager:
    def __init__(self, *, max_running=4, max_queued=32, foreground_timeout=30,
                 summary_limit=OUTPUT_LIMIT, cleanup_timeout=2):
        if max_running < 1 or max_queued < 0 or foreground_timeout < 0 or summary_limit < 128 or cleanup_timeout <= 0:
            raise ValueError("后台任务上限无效")
        self.max_running, self.max_queued = max_running, max_queued
        self.foreground_timeout, self.summary_limit = foreground_timeout, summary_limit
        self.cleanup_timeout = cleanup_timeout
        self.records, self.parents = {}, {}
        self._active, self._queued, self._runners = set(), deque(), {}
        self._retained = []
        self._closed = False
        self.paused = False
        self.foreground_task_id = None
        self.inbox = ResultInbox()
        self.changed = asyncio.Event()
        self.events = asyncio.Queue()
        self.on_parent_cancelled = None

    def new_parent(self, question, *, limit=20, mode="execute", generation=0, cancel=None):
        task_id = "parent_" + uuid4().hex
        parent = TaskContext(task_id, question, TaskBudget(limit, task_id), mode, generation,
                             cancel=cancel if cancel is not None else asyncio.Event())
        self.parents[task_id] = parent
        return parent

    def get(self, task_id):
        try:
            return self.records[task_id]
        except KeyError:
            raise ToolError("task_not_found", f"本进程未发现任务：{task_id}", not_started=True) from None

    def parent(self, task_id):
        try:
            return self.parents[task_id]
        except KeyError:
            raise ToolError("task_not_found", f"本进程未发现父任务：{task_id}", not_started=True) from None

    def parent_report(self, task_id):
        """按真实请求帐本合计；子汇总不重复加入，未知字段仍保留未知。"""
        from ..agent import _total_usage
        parent = self.parent(task_id)
        records = [self.get(child) for child in sorted(parent.children)]
        children = []
        for record in records:
            children.extend(record.outcome.request_usage if record.outcome and record.outcome.request_usage else [TokenUsage()])
        def usage(items):
            value = _total_usage(items)
            return dict(asdict(value), incomplete_fields=sorted(value.incomplete_fields))
        return {'task_id': parent.task_id, 'question': parent.question, 'mode': parent.mode,
                'generation': parent.generation, 'finished': parent.finished, 'reason': parent.reason,
                'wake_allowed': parent.wake_allowed, 'requests_used': parent.budget.used,
                'max_iterations': parent.budget.limit,
                'own_usage': usage(parent.budget.usage), 'children_usage': usage(children),
                'total_usage': usage([*parent.budget.usage, *children]),
                'request_usage': [dict(asdict(item), incomplete_fields=sorted(item.incomplete_fields)) for item in parent.budget.usage],
                'children': [record.report() for record in records]}

    def submit(self, parent_task_id, runner, *, type, background=False, snapshot=None, role="", source=""):
        parent = self.parent(parent_task_id)
        if self._closed or self.paused or parent.cancel.is_set() or not parent.wake_allowed:
            raise ToolError("agent_submission_closed", "此父任务不再接受子任务", not_started=True)
        if type not in {"defined", "fork"}:
            raise ToolError("invalid_arguments", "Agent 类型无效", not_started=True)
        background = background or type == "fork"
        if len(self._active) >= self.max_running and len(self._queued) >= self.max_queued:
            raise ToolError("agent_queue_full", "后台活动及排队容量已满", not_started=True)
        record = TaskRecord("agent_" + uuid4().hex, parent_task_id, type, parent.mode, parent.generation,
                            snapshot=snapshot, role=role, source=source,
                            display_mode="background" if background else "foreground",
                            channel="background" if background else "pending")
        self.records[record.task_id] = record
        self._runners[record.task_id] = runner
        parent.children.add(record.task_id)
        if len(self._active) < self.max_running:
            self._launch(record)
        else:
            self._queued.append(record.task_id)
            self._notice(record)
        return record

    def _notice(self, record):
        self.events.put_nowait(record.receipt())
        self.changed.set()

    def _launch(self, record):
        if record.cancel.is_set():
            self._runners.pop(record.task_id, None)
            self._finish(record, TaskOutcome("cancelled", "排队任务取消，未启动", side_effects="not_started"))
            return
        record.state = "running"
        self._active.add(record.task_id)
        record.handle = asyncio.create_task(self._execute(record))
        self._notice(record)

    async def _execute(self, record):
        try:
            outcome = await self._runners[record.task_id](record)
            if not isinstance(outcome, TaskOutcome) or (outcome.reason == "model_done" and not outcome.text.strip()):
                outcome = TaskOutcome("stream_error", "子 Agent 未返回完整非空答复")
        except asyncio.CancelledError:
            outcome = TaskOutcome("cancelled", "子 Agent 已取消；已发生操作保留，远端副作用可能未知")
        except Exception:
            outcome = TaskOutcome("stream_error", "子 Agent 运行失败，请检查模型及工具状态")
        finally:
            self._active.discard(record.task_id)
            self._runners.pop(record.task_id, None)
        self._finish(record, outcome)
        while self._queued and len(self._active) < self.max_running:
            self._launch(self.get(self._queued.popleft()))

    def _finish(self, record, outcome):
        if record.outcome is not None:
            return
        record.outcome = outcome
        record.state = "completed" if outcome.reason == "model_done" else "cancelled" if outcome.reason == "cancelled" else "failed"
        record.done.set()
        self._notice(record)
        self._publish(record)

    def _publish(self, record):
        if record.outcome is not None and record.channel == "background" and not record.published:
            record.published = True
            self.inbox.put(record.report(limit=self.summary_limit))

    def detach(self, task_id):
        record = self.get(task_id)
        # 无 await：与终态确认共享一次事件循环边界，完成时保留前台通道。
        if record.channel != "pending" or record.outcome is not None:
            return False
        record.channel, record.display_mode = "background", "background"
        record.detached.set()
        self._notice(record)
        self._publish(record)
        return True

    async def wait(self, task_id):
        record = self.get(task_id)
        if record.channel == "background":
            return record.receipt()
        self.foreground_task_id = task_id
        done = asyncio.create_task(record.done.wait())
        detached = asyncio.create_task(record.detached.wait())
        try:
            await asyncio.wait((done, detached), timeout=self.foreground_timeout, return_when=asyncio.FIRST_COMPLETED)
            if record.outcome is not None and record.channel == "pending":
                record.channel = "foreground"
                return record.report(limit=self.summary_limit)
            self.detach(task_id)
            return record.receipt()
        finally:
            if self.foreground_task_id == task_id:
                self.foreground_task_id = None
            done.cancel()
            detached.cancel()
            await asyncio.gather(done, detached, return_exceptions=True)

    async def wait_terminal(self, task_id):
        record = self.get(task_id)
        await record.done.wait()
        return record

    async def cancel(self, task_id):
        record = self.get(task_id)
        record.cancel.set()
        if record.outcome is not None:
            return record
        if record.handle is None:
            try:
                self._queued.remove(task_id)
            except ValueError:
                pass
            self._runners.pop(task_id, None)
            self._finish(record, TaskOutcome("cancelled", "排队任务取消，未启动", side_effects="not_started"))
            return record
        try:
            await asyncio.wait_for(asyncio.shield(record.handle), self.cleanup_timeout)
        except asyncio.TimeoutError:
            record.handle.cancel()
            await asyncio.wait((record.handle,), timeout=self.cleanup_timeout)
            if not record.done.is_set():
                self._finish(record, TaskOutcome("cancelled", "取消清理达到期限；已发生操作及远端状态可能未知"))
        return record

    async def cancel_parent(self, task_id):
        parent = self.parent(task_id)
        parent.wake_allowed = False
        parent.cancel.set()
        if not parent.finished:
            parent.reason = "cancelled"
        records = [self.get(child) for child in parent.children]
        for record in records:
            record.cancel.set()
        await asyncio.gather(*(self.cancel(record.task_id) for record in records))
        if self.on_parent_cancelled:
            self.on_parent_cancelled(parent, 'cancelled')
        return parent

    def retain(self, resource):
        if not any(item is resource for item in self._retained):
            self._retained.append(resource)

    async def aclose(self):
        if self._closed:
            return
        self._closed = True
        await asyncio.gather(*(self.cancel_parent(parent) for parent in self.parents))
        for resource in self._retained:
            resource.close()
        self._retained.clear()
