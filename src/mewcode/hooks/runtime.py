"""会话内派发、一次性领取和有限后台资源的唯一所有者。"""

import asyncio
from collections import deque
from uuid import uuid4

from ..async_utils import protected
from .actions import diagnostic, LOGGER
from .events import HookEvent
from .models import ActionResult


def create_runtime(root, permissions, current_mode):
    from .actions import ActionRunner
    from .config import load_config
    runner = ActionRunner(root, permissions=permissions, current_mode=current_mode)
    return HookRuntime(load_config(root), runner, current_mode=current_mode)


class HookRuntime:
    def __init__(self, snapshot, runner, *, current_mode=None, max_running=4,
                 max_pending=32, close_timeout=5):
        self.snapshot, self.runner = snapshot, runner
        self.prompts = runner.prompts
        self.current_mode = current_mode or (lambda: "execute")
        self.session_id = uuid4().hex
        self.once: set[str] = set()
        self.started = self.closed = self._closing = self._paused = False
        self.max_running, self.max_pending, self.close_timeout = max_running, max_pending, close_timeout
        self._background: set[asyncio.Task] = set()
        self._pending = deque()
        self._close_operation = None
        self.serial_tools = any(rule.event in {"tool.before", "tool.after"} and
                                rule.action.type in {"command", "http"} and not rule.background
                                for rule in snapshot.rules)
        for item in snapshot.diagnostics:
            try:
                LOGGER.warning("Hook 配置 %s index=%s event=%s: %s", item.source, item.index, item.event, item.message)
            except Exception:
                pass

    @property
    def background_count(self):
        return len(self._background) + len(self._pending)

    def event(self, name: str, **fields):
        return HookEvent.create(name, session_id=self.session_id,
                                mode=fields.pop("mode", self.current_mode()),
                                permission_mode=self.runner.permissions.mode, **fields)

    async def emit(self, name, *, cancel_event=None, fields=None, **values):
        """快照构造也属于 Hook 故障边界，不改变正常代理结果。"""
        try:
            extra = fields() if callable(fields) else (fields or {})
            return await self.dispatch(self.event(name, **extra, **values), cancel_event=cancel_event)
        except asyncio.CancelledError:
            if cancel_event is not None:
                cancel_event.set()
            raise
        except Exception as error:
            try:
                LOGGER.warning('Hook 快照 event=%s: %s', name, type(error).__name__)
            except Exception:
                pass
            return ActionResult('failed')

    async def start(self, *, source="new", cancel_event=None, **fields):
        if self.started or self.closed or self._closing:
            return
        self.started = True
        await self.emit("session.start", source=source, **fields, cancel_event=cancel_event)

    async def dispatch(self, event, *, cancel_event=None):
        cancel = cancel_event if cancel_event is not None else asyncio.Event()
        if self.closed or (self._closing and event.event != "session.end"):
            return ActionResult("skipped")
        for rule in self.snapshot.rules:
            if rule.event != event.event:
                continue
            try:
                if rule.condition is not None and not rule.condition.matches(event):
                    continue
                if cancel.is_set() or (self._paused and rule.action.type in {"command", "http"}):
                    diagnostic(rule, event, "取消或模式清理跳过")
                    continue
                if rule.background and self._closing:
                    diagnostic(rule, event, "关闭时不接收后台工作")
                    continue
                if not self.runner.can_submit(rule, event, background=rule.background, cancel_event=cancel):
                    diagnostic(rule, event, "安全状态或后台许可跳过")
                    continue
                if rule.once and rule.identity in self.once:
                    continue
                if rule.background and len(self._background) >= self.max_running and len(self._pending) >= self.max_pending:
                    diagnostic(rule, event, "后台队列容量不足，未提交")
                    continue
                # 本事件循环中的原子提交区间不含 await；失败也不返还。
                if rule.once:
                    self.once.add(rule.identity)
                if rule.background:
                    if len(self._background) < self.max_running:
                        self._spawn(rule, event)
                    else:
                        self._pending.append((rule, event))
                    continue
                result = await self.runner.run(rule, event, cancel_event=cancel,
                                               allow_approval=not self._closing)
                if event.event == "tool.before" and result.decision == "deny":
                    return result
            except asyncio.CancelledError:
                cancel.set()
                raise
            except Exception as error:
                diagnostic(rule, event, type(error).__name__)
        return ActionResult()

    def _spawn(self, rule, event):
        # 与触发任务的取消事件分离，保留原始不可变快照。
        task = asyncio.create_task(self.runner.run(rule, event, cancel_event=asyncio.Event(), background=True))
        self._background.add(task)

        def finished(done):
            self._background.discard(done)
            if not done.cancelled():
                try:
                    done.result()
                except Exception as error:
                    diagnostic(rule, event, type(error).__name__)
            if not self._closing and not self.closed and not self._paused:
                while self._pending and len(self._background) < self.max_running:
                    next_rule, next_event = self._pending.popleft()
                    self._spawn(next_rule, next_event)

        task.add_done_callback(finished)

    async def drain_background(self):
        while self._background:
            await asyncio.gather(*tuple(self._background), return_exceptions=True)

    async def quiesce(self, *, cancel_event=None):
        """切入规划前禁止提交，并清理已接收的后台副作用。"""
        self._paused = True
        self._pending.clear()
        tasks = tuple(self._background)
        for task in tasks:
            task.cancel()
        await protected(asyncio.gather(*tasks, return_exceptions=True), cancel_event=cancel_event)

    def resume_mutations(self):
        self._paused = False

    async def close(self, *, reason="closed", **fields):
        if self.closed:
            return
        if self._close_operation is None:
            self._closing = True
            self._close_operation = asyncio.create_task(self._close(reason, fields))
        await protected(self._close_operation)

    async def _close(self, reason, fields):
        """并发关闭者等待同一清理；资源释放之前不提前返回。"""
        try:
            if self.started:
                try:
                    await asyncio.wait_for(self.emit("session.end", reason=reason, **fields), self.close_timeout)
                except (Exception, asyncio.CancelledError):
                    # 关闭拥有独立期限，不能使既有任务或会话资源跳过清理。
                    pass
        finally:
            await self.quiesce()
            try:
                await protected(self.runner.close())
            except Exception as error:
                try:
                    LOGGER.warning('Hook 资源关闭: %s', type(error).__name__)
                except Exception:
                    pass
            finally:
                self.closed = True
