"""空历史角色与原始请求 Fork 共用独立、非交互的子循环。"""

from copy import deepcopy
from dataclasses import dataclass

import asyncio

from ..agent import Agent
from ..async_utils import protected
from ..context.spill import ResultCache
from ..prompts import PromptState, build_system_prompt
from ..skills.budget import TaskBudget
from ..skills.runtime import SkillRuntime
from ..skills.service import SkillService
from ..tasks.manager import TaskOutcome
from ..tools.base import ToolContext, ToolError
from ..tools.executor import ToolExecutor
from ..types import TokenUsage

DEFAULT_BACKGROUND_TOOLS = frozenset({"read_file", "write_file", "edit_file", "execute_command", "glob_files", "search_code", "load_skill"})
CHILD_REMINDER = "你是独立子 Agent，非交互执行当前子任务，不能请求人工输入或嵌套委派。缺少工具许可时报告实际限制。最终答复只报告实际结果、证据和未完成事项。"


@dataclass(frozen=True)
class ChildSnapshot:
    type: str
    prompt: str
    role: object
    config: object
    permissions: object
    parent_tools: frozenset[str]
    background_tools: frozenset[str]
    history: tuple
    prompt_state: PromptState
    skill_catalog: object
    active_skills: tuple
    declared_tools: tuple
    system_prompt: str
    max_iterations: int
    mode: str


def freeze_child(parent, arguments, *, role=None):
    """提交时复制所有控制状态，后续父变更只影响新任务。"""
    type = arguments["type"]
    upper = frozenset(parent.effective_tools())
    configured = getattr(parent.config, "agent_background_tools", None)
    background = DEFAULT_BACKGROUND_TOOLS if configured is None else configured
    if type == "fork":
        sent = parent.agent.last_request
        if sent is None:
            raise ToolError("agent_snapshot_unavailable", "没有可继承的已发送请求", not_started=True)
        sent = sent.copy()
        controls = sent.control_state or {}
        prompt = deepcopy(controls.get("prompt_state", parent.prompt_state))
        upper &= frozenset(tool.name for tool in sent.tools)
        if prompt.allowed_tools is not None:
            upper &= prompt.allowed_tools
        catalog = controls.get("skill_catalog", parent.skills.catalog)
        active = deepcopy(controls.get("active_skills", parent.skills.active))
        config, history, system, tools = sent.config, sent.messages, sent.system_prompt, sent.tools
        iterations, permissions = parent.agent.max_iterations, parent.permissions.fork()
    else:
        if role is None:
            raise ToolError("agent_role_not_found", "定义式必须选择有效角色", not_started=True)
        try:
            config = parent.config if role.model == "inherit" else parent.config.for_agent(role.model)
        except (ValueError, AttributeError):
            raise ToolError("agent_model_unavailable", f"未配置 Agent 模型别名 {role.model}", not_started=True) from None
        prompt = PromptState(parent.executor.context.root, custom_instructions=parent.prompt_state.custom_instructions)
        prompt.environment = parent.prompt_state.environment
        prompt.enter_mode(parent.mode)
        catalog, active, history = parent.skills.catalog, (), ()
        system = build_system_prompt() + "\n\n## 固定角色\n" + role.body + "\n\n## 子运行约束\n" + CHILD_REMINDER
        start = role.tools(upper) - {"agent"}
        if parent.mode == "plan":
            start &= {"read_file", "glob_files", "search_code", "load_skill"}
        if arguments.get("background", False):
            start &= background
        tools = deepcopy(parent.executor.registry.definitions(allowed_tools=start, system_passthrough=False))
        iterations, permissions = role.max_iterations, parent.permissions.fork(role.permission_mode)
    if not hasattr(parent.provider, "fork") and getattr(config, "model", None) != getattr(parent.config, "model", None):
        raise ToolError("agent_model_unavailable", "当前供应商不能借用同一传输切换模型", not_started=True)
    return ChildSnapshot(type, arguments["prompt"], role, deepcopy(config), permissions, upper,
                         frozenset(background), deepcopy(tuple(history)), prompt, deepcopy(catalog), active,
                         tuple(tools), system, iterations, parent.mode)


class ChildRuntime:
    def __init__(self, parent, record):
        self.parent, self.record, self.snapshot = parent, record, record.snapshot
        snapshot = self.snapshot
        self.history = list(deepcopy(snapshot.history))
        self.prompt = deepcopy(snapshot.prompt_state)
        self.skills = SkillRuntime(snapshot.skill_catalog, parent_tools=snapshot.parent_tools)
        self.skills.active = deepcopy(snapshot.active_skills)
        context = ToolContext(parent.executor.context.root, output_limit=parent.executor.context.output_limit,
                              file_limit=parent.executor.context.file_limit)
        self.executor = ToolExecutor(parent.executor.registry, context, timeout=parent.executor.timeout,
                                     permissions=snapshot.permissions, mcp=parent.executor.mcp)
        self.executor.guard = self.guard
        self.executor.system_handlers["load_skill"] = SkillService(self.skills, self.allowed_tools, timeout=self.executor.timeout)
        self.provider = parent.provider.fork(snapshot.config) if hasattr(parent.provider, "fork") else parent.provider
        self.hooks = parent.hooks.scope(record.task_id, permissions=snapshot.permissions, current_mode=self.current_mode)
        self.agent = Agent(self.provider, self.executor, max_iterations=snapshot.max_iterations,
                           config=snapshot.config, prompt_state=self.prompt, hooks=self.hooks,
                           before_request=self.prepare, allowed_tools=self.allowed_tools,
                           system_prompt=snapshot.system_prompt, declared_tools=snapshot.declared_tools,
                           preserve_first_prefix=snapshot.type == "fork", owns_cache=False)
        self.agent.context.cache.close()
        self.agent.context.cache = ResultCache(context.root, session_id=record.task_id)
        parent.tasks.retain(self.agent.context.cache)
        self.budget = TaskBudget(snapshot.max_iterations, record.task_id)

    def current_mode(self):
        return "plan" if self.parent.mode == "plan" or self.snapshot.mode == "plan" else "execute"

    def allowed_tools(self):
        allowed = self.skills.allowed_tools(self.executor.registry.names(), mode=self.current_mode()) & self.snapshot.parent_tools
        if self.snapshot.role is not None:
            allowed = self.snapshot.role.tools(allowed)
        if self.record.display_mode == "background":
            allowed &= self.snapshot.background_tools
        return frozenset(allowed - {"agent"})

    def guard(self, name, arguments):
        if name not in self.allowed_tools():
            raise ToolError("tool_not_allowed", "子 Agent 的运行范围禁止此工具", not_started=True)
        if name == "load_skill" and arguments and "resource" not in arguments:
            if self.skills.catalog.get(arguments["name"]).mode == "isolated":
                raise ToolError("tool_not_allowed", "子 Agent 不能启动独立 Skill", not_started=True)

    def prepare(self):
        self.prompt.active_skills = self.skills.render_active()
        self.prompt.skill_index = self.skills.catalog.index_text()
        self.prompt.allowed_tools = self.allowed_tools()

    async def run(self):
        record = self.record
        question = CHILD_REMINDER + "\n\n子任务：" + self.snapshot.prompt
        fields = self.agent.hook_fields(self.budget, record.task_id, record.parent_task_id)
        source = self.agent.run(question, history=self.history, mode=self.snapshot.mode,
                                cancel_event=record.cancel, budget=self.budget, run_id=record.task_id,
                                turn_owned=True, parent_run_id=record.parent_task_id)
        finished = None
        names = {}
        async def consume():
            nonlocal finished
            async for event in source:
                if event.kind == "tool_call":
                    names[event.call.id] = event.call.name
                if event.kind == "finished":
                    finished = event
        try:
            await self.hooks.emit("turn.start", **fields, message={"text":question}, cancel_event=record.cancel)
            await self.hooks.emit("message.user", **fields, message={"text":question, "role":"user"}, cancel_event=record.cancel)
            await consume()
        except asyncio.CancelledError:
            record.cancel.set()
            await protected(consume(), cancel_event=record.cancel)
        finally:
            await protected(source.aclose(), cancel_event=record.cancel)
            await protected(self.hooks.emit("turn.end", **self.agent.hook_fields(self.budget, record.task_id, record.parent_task_id),
                            reason=finished.reason if finished else "cancelled", cancel_event=record.cancel), cancel_event=record.cancel)
            await protected(self.agent.aclose(), cancel_event=record.cancel)
            if self.provider is not self.parent.provider:
                await protected(self.provider.aclose(), cancel_event=record.cancel)
        task = self.agent.last_task or {}
        reason = finished.reason if finished else "cancelled" if record.cancel.is_set() else "stream_error"
        final = task.get("final_message")
        text = final.content if final else finished.text if finished else "子 Agent 未完整结束"
        latest = {message.tool_call_id: message for message in self.history if message.role == "tool"}
        evidence = tuple({"source":message.id, "call_id":message.tool_call_id,
                          "tool_name":names.get(message.tool_call_id, ""), "result":deepcopy(message.tool_result.to_dict()),
                          "cache_path":getattr(latest.get(message.tool_call_id), "cache_path", "")}
                         for message in task.get("tools", ()))
        started_effect = any(not self.executor.registry.get(item["tool_name"]).read_only
                             and not (item["result"].get("error") or {}).get("details", {}).get("not_started", False)
                             for item in evidence if item["tool_name"])
        return TaskOutcome(reason, text, evidence=evidence, usage=finished.usage if finished else TokenUsage(),
                           request_usage=tuple(self.budget.usage),
                           side_effects="possible" if started_effect or self.hooks.runner.side_effects_possible else "none")
