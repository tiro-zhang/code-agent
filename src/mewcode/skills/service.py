"""共享激活、独立调度和包资源共用的加载入口。"""

from ..tools.base import OUTPUT_LIMIT, ToolError, ToolResult
from .loader import read_resource


class SkillService:
    def __init__(self, runtime, allowed_tools, *, run_isolated=None, timeout=30):
        self.runtime = runtime
        self.allowed_tools = allowed_tools
        self.run_isolated = run_isolated
        self.timeout = timeout

    def timeout_for(self, arguments):
        try:
            isolated = self.runtime.catalog.get(arguments["name"]).mode == "isolated"
        except ToolError:
            isolated = False
        return None if isolated and "resource" not in arguments else self.timeout

    async def __call__(self, arguments, *, cancel_event, on_event=None):
        if cancel_event.is_set():
            raise ToolError("cancelled", "Skill 未启动", not_started=True)
        skill = self.runtime.catalog.get(arguments["name"])
        if "resource" in arguments:
            return ToolResult.success({"name": skill.name, "source": str(skill.path.parent),
                "resource": arguments["resource"], "content": read_resource(skill, arguments["resource"])})
        args = arguments.get("args", "")
        if skill.mode == "isolated":
            if self.run_isolated is None:
                raise ToolError("skill_nested_isolated", "本对话不能再启动独立 Skill", not_started=True)
            return await self.run_isolated(skill, args, arguments.get("history", skill.history),
                                           cancel_event=cancel_event, on_event=on_event)
        if "history" in arguments:
            raise ToolError("invalid_arguments", "共享 Skill 不接受 history 覆盖", not_started=True)
        body = skill.render(args)
        result = ToolResult.success({"name": skill.name, "mode": skill.mode,
                                    "source": str(skill.path), "instructions": body})
        if len(result.to_json().encode("utf-8")) > OUTPUT_LIMIT:
            raise ToolError("skill_content_too_large", "完整加载内容超过 64 KiB，不予激活", not_started=True)
        self.runtime.activate(skill.name, args)
        # 工具范围从已提交状态获得；下一模型请求还会重新导出。
        if on_event:
            await on_event({"kind": "skill_loaded", "text":
                f"已激活 {skill.name}；普通工具：{', '.join(sorted(self.allowed_tools() - {'load_skill'})) or '无'}。"
                "可用 /skills deactivate <name|--all> 停用，/reset 清空对话。"})
        return result
