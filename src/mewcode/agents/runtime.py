"""空历史角色与原始请求 Fork 共用独立、非交互的子循环。"""

from copy import deepcopy
from dataclasses import dataclass, replace

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
    worktree: object = None


def freeze_child(parent, arguments, *, role=None):
    """提交时复制所有控制状态，后续父变更只影响新任务。"""
    type = arguments["type"]
    worktree = None
    upper = frozenset(parent.effective_tools()) - {'team', 'team_member', 'team_task', 'team_message', 'team_integrate'}
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
        if role.isolation == 'worktree':
            from ..worktrees.paths import freeze_repository
            from ..worktrees.config import load_config
            repository = freeze_repository(parent.executor.context.root)
            worktree = (repository, load_config(repository.origin_root))
    if not hasattr(parent.provider, "fork") and getattr(config, "model", None) != getattr(parent.config, "model", None):
        raise ToolError("agent_model_unavailable", "当前供应商不能借用同一传输切换模型", not_started=True)
    return ChildSnapshot(type, arguments["prompt"], role, deepcopy(config), permissions, upper,
                         frozenset(background), deepcopy(tuple(history)), prompt, deepcopy(catalog), active,
                         tuple(tools), system, iterations, parent.mode, worktree)


class ChildRuntime:
    def __init__(self, parent, record):
        self.parent, self.record, self.snapshot = parent, record, record.snapshot
        self.tree = self.lease = self.manager = None
        if self.snapshot.worktree is None:
            self.configure(parent.executor.context.root)

    def configure(self, root):
        parent, record = self.parent, self.record
        snapshot = self.snapshot
        self.history = list(deepcopy(snapshot.history))
        self.prompt = deepcopy(snapshot.prompt_state)
        permissions, catalog = snapshot.permissions, snapshot.skill_catalog
        if self.tree is not None:
            from ..instructions import load_instructions
            from ..skills.catalog import discover_skills
            from ..memory.store import MemoryStore, render_index
            instructions = load_instructions(root, user_root=parent.user_root)
            self.prompt = PromptState(root, custom_instructions=instructions.text)
            self.prompt.enter_mode(snapshot.mode)
            self.prompt.workspace_notice = (
                f'任务：{record.task_id}\n原项目根：{self.tree.origin_root}\n'
                f'子实际工作根：{root}\n工作树根：{self.tree.worktree_root}\n'
                f'分支：{self.tree.branch}\n冻结 HEAD：{self.tree.base_commit}\n'
                '此目录从冻结的 HEAD 创建，不包含父目录未提交修改。所有内置工具以此目录为 cwd。'
                '这是工作副本隔离；shell 可访问目录外，依赖软链和 MCP 服务沿用共享资源合同。'
                '父项目指令和项目记忆不自动迁移。'
            )
            if instructions.warnings:
                self.prompt.workspace_notice += '\n项目指令诊断：' + '；'.join(instructions.warnings)
            if parent.memory is not None:
                notes = []
                for store in (MemoryStore(root), MemoryStore(parent.user_root, scope='user')):
                    try:
                        notes.extend(store.query().notes)
                    except (OSError, ValueError):
                        self.prompt.workspace_notice += '\n有自动记忆不可读取，已跳过。'
                self.prompt.memory = render_index(notes, combined=True)
            permissions = permissions.fork(root=root)
            catalog = discover_skills(root, user_root=parent.user_root)
        self.skills = SkillRuntime(catalog, parent_tools=snapshot.parent_tools)
        self.skills.active = deepcopy(snapshot.active_skills)
        context = ToolContext(root, output_limit=parent.executor.context.output_limit,
                              file_limit=parent.executor.context.file_limit)
        self.executor = ToolExecutor(parent.executor.registry, context, timeout=parent.executor.timeout,
                                     permissions=permissions, mcp=parent.executor.mcp)
        self.executor.guard = self.guard
        self.executor.system_handlers["load_skill"] = SkillService(self.skills, self.allowed_tools, timeout=self.executor.timeout)
        self.provider = parent.provider.fork(snapshot.config) if hasattr(parent.provider, "fork") else parent.provider
        self.hooks = parent.hooks.scope(record.task_id, permissions=permissions, current_mode=self.current_mode, root=root)
        journal = None
        if self.tree is not None and parent.journal is not None:
            from ..worktrees.evidence import WorktreeJournal
            journal = WorktreeJournal(parent.journal, record, root)
        self.agent = Agent(self.provider, self.executor, max_iterations=snapshot.max_iterations,
                           config=snapshot.config, prompt_state=self.prompt, hooks=self.hooks,
                           before_request=self.prepare, allowed_tools=self.allowed_tools,
                           system_prompt=snapshot.system_prompt, declared_tools=snapshot.declared_tools,
                           preserve_first_prefix=snapshot.type == "fork", owns_cache=False, journal=journal)
        self.agent.context.cache.close()
        self.agent.context.cache = ResultCache(context.root, session_id=record.task_id, persistent=self.tree is not None)
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
        if self.snapshot.worktree is None:
            return await self.run_loop()
        from ..worktrees.manager import WorktreeManager
        from ..worktrees.evidence import archive_cache, journal_event
        from ..worktrees.records import load_record
        repository, config = self.snapshot.worktree
        self.manager = WorktreeManager(repository, config, current_mode=self.current_mode)
        name, cancel = self.record.task_id, self.record.cancel
        try:
            self.tree = await self.manager.create(name, task_id=name, cancel_event=cancel)
            if cancel.is_set():
                raise asyncio.CancelledError
            if not self.tree.recovered:
                self.manager.check_mutations(cancel)
            self.lease = self.manager.enter(self.tree)
            self.configure(self.tree.workspace_root)
            journal_event(self.parent, self.record, self.tree, 'created', self.tree.metadata(), {})
        except (ToolError, OSError, ValueError, asyncio.CancelledError) as error:
            info = {'state': 'not_created', 'initialization': 'failed', 'archive': 'not_started',
                    'base_commit': repository.base_commit,
                    'reason': getattr(error, 'message', '工作树启动取消或初始化失败')}
            try:
                record = load_record(self.manager.record_path(name))
                if record.get('task_id') == name:
                    info.update({key: record[key] for key in ('worktree_root', 'workspace_root', 'branch', 'base_commit')})
                    info.update(state='retained', initialization=record['stage'])
            except (OSError, ValueError, KeyError):
                pass
            if self.lease is not None:
                await protected(self.manager.exit(self.lease), cancel_event=cancel)
            if hasattr(self, 'agent'):
                await protected(self.hooks.close(cancel_event=cancel), cancel_event=cancel)
                await protected(self.agent.aclose(), cancel_event=cancel)
                if self.provider is not self.parent.provider:
                    await protected(self.provider.aclose(), cancel_event=cancel)
            return TaskOutcome('cancelled' if cancel.is_set() or isinstance(error, asyncio.CancelledError) else 'worktree_startup_failed',
                               info['reason'], side_effects='possible' if info['state'] == 'retained' else 'none', worktree=info)
        try:
            outcome = await self.run_loop()
        except (Exception, asyncio.CancelledError) as error:
            if isinstance(error, asyncio.CancelledError):
                cancel.set()
            outcome = TaskOutcome('cancelled' if cancel.is_set() else 'stream_error',
                                  '子运行异常结束；请核查保留的实际证据', side_effects='possible')
        mapping, evidence_safe = {}, False
        info = {**self.tree.metadata(), 'initialization': 'ready', 'archive': 'failed'}
        cache = self.agent.context.cache
        try:
            mapping = archive_cache(cache, self.parent.context.cache, name)
            info['archive'] = 'complete'
            journal_event(self.parent, self.record, self.tree, 'archived', info, mapping)
            evidence = tuple(dict(item, cache_path=mapping.get(str(cache.root/item['cache_path']), ''),
                                  source_root=str(cache.root), source_cache_path=item['cache_path'])
                             for item in outcome.evidence)
            outcome = replace(outcome, evidence=evidence)
            cache.persistent = False
            cache.close()
            evidence_safe = not self.agent.storage_blocked
        except (OSError, ValueError, TypeError, KeyError):
            info['archive'] = 'failed'
            cache.close()
        try:
            deletion = await protected(self.manager.exit(self.lease, evidence_safe=evidence_safe), cancel_event=cancel)
        except (OSError, ValueError, ToolError):
            deletion = {**self.tree.metadata(), 'state': 'retained', 'branch_state': 'retained',
                        'reason': '退出状态保存失败，目录和持久证据保护保持原样'}
        info.update(deletion)
        try:
            journal_event(self.parent, self.record, self.tree, 'finished', info, mapping)
        except (OSError, ValueError):
            info['audit'] = 'failed'
        return replace(outcome, worktree=info)

    async def run_loop(self):
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
            await protected(self.hooks.close(cancel_event=record.cancel), cancel_event=record.cancel)
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
