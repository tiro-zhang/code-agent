"""保持调用顺序，以连续只读组并发执行，副作用调用形成边界。"""

import asyncio
from collections.abc import AsyncIterator, Sequence

from ..async_utils import protected
from ..types import AgentEvent, AgentMode, ToolCall
from .base import ToolError, ToolResult
from .executor import ToolExecutor


class ToolScheduler:
    def __init__(self, executor: ToolExecutor, *, max_parallel: int = 4, on_result=None) -> None:
        if max_parallel < 1:
            raise ValueError("并发上限必须是正整数")
        self.executor = executor
        self.max_parallel = min(max_parallel, 4)
        self.results: tuple[ToolResult, ...] = ()
        self.on_result = on_result
        self.storage_error = None

    async def run(self, calls: Sequence[ToolCall], *, allowed_tools: frozenset[str] | None,
                  run_id: str, iteration: int, mode: AgentMode,
                  cancel_event: asyncio.Event | None = None) -> AsyncIterator[AgentEvent]:
        cancel = cancel_event if cancel_event is not None else asyncio.Event()
        queue: asyncio.Queue[tuple[int, AgentEvent]] = asyncio.Queue(maxsize=self.max_parallel)
        results: dict[int, ToolResult] = {}
        jobs: dict[int, asyncio.Task] = {}
        self.results = ()
        normal_end = False

        def event(kind, index, **fields):
            call = calls[index]
            return AgentEvent(kind, run_id=run_id, iteration=iteration, mode=mode,
                              permission_mode=getattr(getattr(self.executor, "permissions", None), "mode", "default"),
                              tool_call_id=call.id, tool_name=call.name, **fields)

        def unstarted():
            return ToolResult.failure("cancelled", "任务取消，工具未启动", details={"not_started": True})

        async def execute_one(index):
            call = calls[index]
            operation = None

            async def notify(notification):
                fields = {}
                if notification["kind"] == "skill_loaded":
                    fields = {"text": notification.get("text", ""), "phase": "skill"}
                elif notification["kind"] == "skill_event":
                    fields = {"child_event": notification['event']}
                if notification["kind"].startswith("permission_"):
                    fields = {"permission_request": notification.get("request"),
                              "permission_decision": notification.get("decision", ""),
                              "warning": notification.get("warning", "")}
                await queue.put((index, event(notification["kind"], index, **fields)))

            if cancel.is_set():
                result = unstarted()
            else:
                try:
                    current = allowed_tools() if callable(allowed_tools) else allowed_tools
                    self.executor.registry.prepare(call.name, call.arguments, allowed_tools=current)
                except ToolError as error:
                    result = error.result()
                else:
                    operation = asyncio.create_task(self.executor.execute(
                        call.name, call.arguments, allowed_tools=allowed_tools, cancel_event=cancel,
                        on_event=notify))
                    try:
                        result = await operation
                    except asyncio.CancelledError:
                        cancel.set()
                        result = await protected(operation, cancel_event=cancel)
                    except OSError as error:
                        self.storage_error = error
                        result = ToolResult.failure('storage_error', '执行过程存档失败，停止后续工作；已完成操作保留')
                    except Exception:
                        result = ToolResult.failure("execution_error", "工具执行入口异常结束")
            if self.on_result and self.storage_error is None:
                try:
                    self.on_result(call, result)
                except OSError as error:
                    # 已产生的真实结果仍回传；后续未启动工具停在存档边界。
                    self.storage_error = error
            await queue.put((index, event("tool_result", index, result=result)))

        async def drain_and_cleanup():
            # 关闭消费者时也排空有界通道，使清理不被背压锁住。
            while any(not task.done() for task in jobs.values()):
                while not queue.empty():
                    index, item = queue.get_nowait()
                    if item.kind == "tool_result":
                        results[index] = item.result
                await asyncio.sleep(0.01)
            if jobs:
                await asyncio.gather(*jobs.values(), return_exceptions=True)
            while not queue.empty():
                index, item = queue.get_nowait()
                if item.kind == "tool_result":
                    results[index] = item.result

        try:
            for index, call in enumerate(calls):
                yield event("tool_call", index, call=call)
            readonly = self.executor.registry.names(read_only=True)
            position = 0
            while position < len(calls) and not cancel.is_set() and self.storage_error is None:
                end = position + 1
                limit = 1
                if calls[position].name in readonly:
                    limit = self.max_parallel
                    while end < len(calls) and calls[end].name in readonly:
                        end += 1
                next_index = position
                active: dict[int, asyncio.Task] = {}
                while next_index < end or active:
                    while next_index < end and len(active) < limit and not cancel.is_set() and self.storage_error is None:
                        task = asyncio.create_task(execute_one(next_index))
                        jobs[next_index] = active[next_index] = task
                        next_index += 1
                    if not active:
                        break
                    try:
                        index, item = await queue.get()
                    except asyncio.CancelledError:
                        cancel.set()
                        continue
                    if item.kind == "tool_result":
                        results[index] = item.result
                        await protected(active.pop(index), cancel_event=cancel)
                    yield item
                position = end
            for index in range(len(calls)):
                if index not in results:
                    results[index] = (ToolResult.failure('storage_error', '存档失败，工具未启动',
                                      details={'not_started': True}) if self.storage_error else unstarted())
                    yield event("tool_result", index, result=results[index])
            normal_end = True
        finally:
            if not normal_end:
                cancel.set()
            await protected(drain_and_cleanup(), cancel_event=cancel)
            self.results = tuple(results.get(index, unstarted()) for index in range(len(calls)))
