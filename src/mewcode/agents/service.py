"""统一委派系统入口；管理器独立持有子运行。"""

from ..tools.base import ToolError, ToolResult
from .runtime import ChildRuntime, freeze_child


class AgentService:
    def __init__(self, parent):
        self.parent = parent

    def timeout_for(self, arguments):
        return None

    async def __call__(self, arguments, *, cancel_event, on_event=None):
        parent = self.parent
        context = parent._task_context
        if context is None or cancel_event.is_set():
            raise ToolError("agent_submission_closed", "没有可接受委派的主任务", not_started=True)
        role = parent.roles.get(arguments["role"]) if arguments["type"] == "defined" else None
        snapshot = freeze_child(parent, arguments, role=role)
        record = parent.tasks.submit(context.task_id, lambda record: ChildRuntime(parent, record).run(),
            type=arguments["type"], background=arguments.get("background", False), snapshot=snapshot,
            role=role.name if role else "", source=str(role.path) if role else "父已发送请求")
        report = await parent.tasks.wait(record.task_id)
        return ToolResult.success(report, truncated=report.get("truncated", False))
