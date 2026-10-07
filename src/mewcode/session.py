"""会话历史的所有者；单次任务交由 Agent 编排。"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import asdict, replace
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4

from .agent import Agent
from .async_utils import protected
from .prompts import PromptState, build_system_prompt
from .tools import default_registry
from .tools.base import ToolContext, ToolError, strict_json
from .tools.executor import ToolExecutor
from .types import AgentEvent, AgentMode, Message, Provider, ToolCall
from .tasks.ownership import serialized_turn


def operation_summary(call: ToolCall) -> str:
    """只摘取路径、命令或模式，写入与替换正文不进入摘要。"""
    try:
        arguments = strict_json(call.arguments)
        if isinstance(arguments, dict):
            if call.name == 'load_skill' and isinstance(arguments.get('name'), str):
                return f'Skill {arguments["name"]}' + (f' · 资源 {arguments["resource"]}' if 'resource' in arguments else '')
            for key in ("path", "command", "pattern"):
                if isinstance(arguments.get(key), str):
                    return arguments[key]
    except (ValueError, RecursionError):
        pass
    return "校验参数"



class ChatSession:
    def __init__(self, provider: Provider, *, executor: ToolExecutor | None = None,
                 max_iterations: int = 20, permission_mode: str = "default",
                 approval_responder=None, config=None, persistent=False, resume=None,
                 user_root=None, memory_enabled=True, notify=None, skill_catalog=None, provider_factory=None,
                 session_storage=None, team_scope=None) -> None:
        self.provider = provider
        from .providers import make_provider
        self.provider_factory = provider_factory or make_provider
        self._task_budget = None
        self._task_question = ''
        self._task_context = None
        self._main_running = False
        self._main_owner = asyncio.Lock()
        self.generation = 0
        self._prepared_results = ()
        from .tasks.manager import TaskManager
        self.tasks = TaskManager()
        self.tasks.on_parent_cancelled = self._finish_parent
        self.config = config or getattr(provider, 'config', None)
        self.team_scope = team_scope
        self.session_storage = Path(session_storage) if session_storage is not None else None
        self.journal = None
        self.memory = None
        self._notify = notify
        self.memory_usage = None
        self.restore_usage = None
        self.memory_tasks = {}
        self.memory_status = '尚未提取'
        self.warnings = []
        self.resumed = bool(resume)
        from .permissions.runtime import PermissionManager
        if executor is None:
            context = ToolContext(Path.cwd())
            permissions = PermissionManager(context.root, mode=permission_mode, responder=approval_responder)
            executor = ToolExecutor(default_registry(), context, permissions=permissions)
        self.executor = executor
        self.permissions = getattr(executor, "permissions", None)
        if self.permissions is None:
            self.permissions = PermissionManager(executor.context.root, mode=permission_mode,
                                                 responder=approval_responder)
        self.history: list[Message] = []
        self.mode: AgentMode = "execute"
        self._hook_turn = None
        self._restore_prepared = False
        root = self.executor.context.root
        self.user_root = Path(user_root) if user_root is not None else Path.home() / ".mewcode"
        from .agents.definitions import discover_roles
        self._role_options = {'user_root': self.user_root,
                              'plugin_dirs': getattr(self.config, 'agent_plugin_dirs', ())}
        self.roles = discover_roles(root, **self._role_options)
        self.warnings.extend(self.roles.warnings)
        from .agents.service import AgentService
        if not hasattr(self.executor, 'system_handlers'):
            self.executor.system_handlers = {}
        self.executor.system_handlers['agent'] = AgentService(self)
        from .skills.catalog import discover_skills
        from .skills.runtime import SkillRuntime
        from .skills.service import SkillService
        self.skills = SkillRuntime(skill_catalog or discover_skills(root, user_root=self.user_root))
        self.warnings.extend(self.skills.catalog.warnings)
        self.skill_service = SkillService(self.skills, self.effective_tools, run_isolated=self._run_isolated,
                                         timeout=getattr(self.executor, 'timeout', 30))
        if not hasattr(self.executor, 'system_handlers'):
            self.executor.system_handlers = {}
        self.executor.system_handlers['load_skill'] = self.skill_service
        from .instructions import load_instructions
        instructions = load_instructions(root, user_root)
        self.warnings.extend(instructions.warnings)
        if persistent or resume:
            from .sessions import Journal, cleanup_expired
            if self.session_storage is None:
                self.warnings.extend(cleanup_expired(root))
            self.journal = (Journal.resume(root, resume, self.config.protocol, self.config.model,
                                          record_activity=False, storage_root=self.session_storage) if resume else
                            Journal.create(root, self.config.protocol, self.config.model, storage_root=self.session_storage))
            self.warnings.extend(self.journal.warnings)
        self.prompt_state = PromptState(root, custom_instructions=instructions.text)
        from .hooks.runtime import create_runtime
        self.hooks = create_runtime(root, self.permissions, lambda: self.mode)
        from .teams.service import TeamService
        self.teams = TeamService(self)
        self.permissions.protected_roots.add(self.teams.store.root)
        previous_guard = getattr(self.executor,'guard',None)
        def guard(name, arguments):
            if previous_guard:
                previous_guard(name, arguments)
            self.teams.guard(name, arguments)
        self.executor.guard = guard
        for name in ('team','team_member','team_task','team_message','team_integrate'):
            self.executor.system_handlers[name] = self.teams.handler(name)
        from .worktrees.cleanup import WorktreeCleaner
        self.worktree_cleaner = WorktreeCleaner(root, lambda: self.mode, self.warnings)
        self.agent = Agent(provider, self.executor, max_iterations=max_iterations, prompt_state=self.prompt_state,
                           config=config, journal=self.journal, before_request=self._before_model_request,
                           allowed_tools=self.effective_tools, hooks=self.hooks,
                           on_request_sent=self._consume_results, on_history_committed=self._confirm_results,
                           request_state=lambda: {'prompt_state': self.prompt_state, 'active_skills': self.skills.active,
                                                 'skill_catalog': self.skills.catalog})
        self.context = self.agent.context
        self.skills.commit = self._save_skills
        try:
            if self.journal:
                from .context.spill import ResultCache
                self.context.cache.close()
                self.context.cache = ResultCache(root, session_id=self.journal.id, persistent=True,
                                                 storage_root=self.session_storage)
                self.context.checkpoint = self._checkpoint
                if resume:
                    projection = self.journal.projection
                    self.history[:] = projection.history
                    self.mode = projection.mode
                    self.warnings.extend(self.skills.restore(projection.active_skills))
                    self.prompt_state.enter_mode(self.mode)
                    self.context.restore_state(projection.state)
                    self.context.cache.reopen(projection.cache_paths)
                    required = self.context.summary_files | {m.cache_path for m in self.history if m.cache_path}
                    try:
                        for path in required:
                            self.context.cache.restore(path)
                    except (OSError, ValueError, KeyError, StopIteration, TypeError):
                        raise ValueError('恢复所需结果缓存缺失或损坏，请核查存档与实际状态') from None
                    last = projection.last_activity
                    gap = datetime.now(timezone.utc) - last
                    if gap > timedelta(hours=24):
                        candidate = [*self.history, Message('context',
                            f'会话最后活动于 {last.isoformat()}，距今 {gap.days} 天 {gap.seconds // 3600} 小时。涉及当前文件时请重新读取，历史内容仅为当时快照。',
                            context_kind='resume')]
                        self._checkpoint(candidate, self.context.state())
                        self.history[:] = candidate
            if self.journal and memory_enabled:
                from .memory import MemoryManager
                self.memory = MemoryManager(root, user_root=user_root, provider=provider,
                                            config=self.config, notify=self._memory_notification)
                self.refresh_memory()
        except Exception:
            self.close()
            raise

    @property
    def session_id(self):
        return self.journal.id if self.journal else ''

    def _checkpoint(self, history, state):
        from .sessions import encode_message
        try:
            self.journal.append('history_checkpoint', {'history': [encode_message(m) for m in history],
                'state': state, 'active_skills': self.skills.descriptors()})
            self.teams.checkpoint_lead(history)
        except OSError:
            self.agent.storage_blocked = True
            raise

    def _save_skills(self, descriptors):
        if self.agent.storage_blocked:
            raise OSError('存档已不可安全继续')
        if self.journal:
            try:
                self.journal.append('skills_changed', {'active_skills': descriptors})
            except OSError:
                self.agent.storage_blocked = True
                raise

    def reset(self):
        """原子保存空投影；保留会话、模式、长期记忆和所有历史证据。"""
        self.generation += 1
        for parent in self.tasks.parents.values():
            parent.wake_allowed = False
            parent.cancel.set()
            self.tasks.inbox.discard_parent(parent.task_id)
        self._task_context = None
        self._prepared_results = ()
        self.prompt_state.task_results = ''
        from .context.manager import ContextManager
        if self.agent.storage_blocked:
            raise OSError('存档已不可安全继续')
        fresh = ContextManager(self.executor.context.root, context_window=self.context.window,
                               max_output_tokens=self.context.output, protocol=self.context.estimator.protocol)
        fresh.cache = self.context.cache
        fresh.checkpoint = self.context.checkpoint
        if self.journal:
            try:
                self.journal.append('history_checkpoint', {'history': [], 'active_skills': [],
                    'state': fresh.state(), 'mode': self.mode, 'reset': True})
            except OSError:
                self.agent.storage_blocked = True
                raise
        self.history.clear()
        self.skills.active = ()
        self.context = self.agent.context = fresh
        self.agent.last_task = None
        self.agent.current_user = None
        self.agent.task_budget = self._task_budget = None
        self._task_question = ''
        self.prompt_state.enter_mode(self.mode)
        self._before_request()

    async def reset_async(self):
        """先撤销唤醒和提交，再等待旧子运行收尾，最后发布空历史。"""
        self.tasks.paused = True
        try:
            await self.teams.pause()
            await asyncio.gather(*(self.tasks.cancel_parent(task_id) for task_id in self.tasks.parents))
            self.reset()
        finally:
            self.tasks.paused = False

    def refresh_memory(self):
        if self.memory:
            self.prompt_state.update_memory(self.memory.refresh())

    def effective_tools(self):
        from .teams.capabilities import allowed_tools, COLLABORATION
        base = self.skills.allowed_tools(self.executor.registry.names(), mode=self.mode) | {'team'}
        if self.team_scope is not None:
            base |= COLLABORATION
        return allowed_tools(base, self.team_scope, self.config)

    def delegation_tools(self):
        from .teams.capabilities import allowed_tools, COLLABORATION
        base = self.skills.allowed_tools(self.executor.registry.names(), mode=self.mode)
        if self.team_scope is not None:
            base |= COLLABORATION
        return allowed_tools(base, self.team_scope, self.config, delegation=True)

    def _before_request(self):
        marker = '\n\n## 团队 Lead 协作流程\n'
        notice = self.prompt_state.workspace_notice.split(marker, 1)[0]
        lead_notice = self.teams.lead_notice()
        self.prompt_state.workspace_notice = notice + (marker + lead_notice if lead_notice else '')
        self.refresh_memory()
        self.prompt_state.active_skills = self.skills.render_active()
        self.prompt_state.skill_index = self.skills.catalog.index_text()
        self.prompt_state.allowed_tools = self.effective_tools()
        self.prompt_state.agent_index = self.roles.index_text()
        context = self._task_context
        self._prepared_results = (self.tasks.inbox.peek(context.task_id)
                                  if context and context.generation == self.generation and context.wake_allowed else ())
        self.prompt_state.task_results = (json.dumps(self._prepared_results, ensure_ascii=False)
                                          if self._prepared_results else '')

    async def _before_model_request(self):
        if self.teams.scope and self.teams.scope.lead and (self.teams.resume_waiting or self.teams.closed):
            return False
        await self.teams.consume_lead()
        await self.teams.checkpoint_goal_budget()
        self._before_request()

    def _confirm_results(self, commit_seq):
        """只在包含本次结果的主历史持久提交后确认回流。"""
        if self.journal:
            for report in getattr(self, '_sent_results', ()):
                info = report.get('worktree')
                if info and info.get('workspace_root'):
                    self.journal.append('worktree_event', {
                        'parent_task_id': report['parent_task_id'], 'run_id': report['task_id'],
                        'stage': 'returned', 'workspace_root': info['workspace_root'],
                        'worktree': info, 'cache_mappings': {}, 'history_commit_seq': commit_seq})
        self._sent_results = ()

    def _consume_results(self):
        self._sent_results = self._prepared_results
        self.tasks.inbox.consume(self._prepared_results)
        self._prepared_results = ()
        self.hooks.prompts.consume(getattr(self, '_startup_injections', ()))
        self._startup_injections = ()

    def validate_skills(self):
        self.skills.catalog.validate_tools(self.executor.registry.names())
        self.roles.validate_tools(self.executor.registry.names())
        if hasattr(self.config, 'validate_agent_tools'):
            self.config.validate_agent_tools(self.executor.registry.names())

    async def _run_isolated(self, skill, args, history, **options):
        from .skills.runner import run_isolated
        return await run_isolated(self, skill, args, history, **options)

    @serialized_turn
    async def run_skill(self, name, args='', *, cancel_event=None, user_text=None):
        from .skills.invocation import run_skill
        from .skills.budget import TaskBudget
        skill = self.skills.catalog.get(name)
        cancel = cancel_event if cancel_event is not None else asyncio.Event()
        question = user_text if user_text is not None else f'/{name}' + (' ' + args if args else '')
        self._task_context = self.tasks.new_parent(question, limit=self.agent.max_iterations,
            mode=self.mode, generation=self.generation, cancel=cancel)
        budget = self._task_budget = self._task_context.budget
        if self.journal:
            self.journal.append('task_started', {'task_id': self._task_context.task_id, 'input': question, 'mode': self.mode})
        await protected(self._begin_hook_turn(question, budget, cancel), cancel_event=cancel)
        source = run_skill(self, name, args, cancel_event=cancel)
        reason = None
        try:
            async for item in source:
                if item.kind == 'finished':
                    reason = item.reason
                    parent = self._task_context
                    pending = any(self.tasks.get(child).outcome is None for child in parent.children)
                    if reason != 'model_done' or (not pending and not self.tasks.inbox.peek(parent.task_id)):
                        if self._finish_parent(parent, reason):
                            from .agent import _total_usage
                            if skill.mode == 'isolated':
                                task = self.agent.last_task or {}
                                parent.user_message, parent.final_message = task.get('user_message'), task.get('final_message')
                                self._enqueue_parent_memory(parent, task)
                            yield AgentEvent('task_finished', run_id=parent.task_id, reason=reason,
                                             usage=_total_usage(parent.budget.usage))
                yield item
        finally:
            if reason is None:
                cancel.set()
            await protected(source.aclose(), cancel_event=cancel)
            await self._end_hook_turn(budget, reason or 'cancelled', cancel)
            if cancel.is_set():
                await protected(self.tasks.cancel_parent(budget.root_run_id), cancel_event=cancel)
            self._task_budget = None

    def refresh_skills(self):
        """空闲输入边界先验证完整候选，再共同发布目录和激活。"""
        from .commands.builtins import build_registry
        from .skills.catalog import discover_skills
        from .agents.definitions import discover_roles
        try:
            roles = discover_roles(self.executor.context.root, **self._role_options)
            roles.validate_tools(self.executor.registry.names())
            candidate = discover_skills(self.executor.context.root, user_root=self.user_root)
            if candidate == self.skills.catalog:
                self.roles = roles
                self._before_request()
                return None, list(roles.warnings)
            registry = build_registry(candidate)
            candidate.validate_tools(self.executor.registry.names())
            warnings = [*candidate.warnings, *self.skills.replace_catalog(candidate)]
            self.roles = roles
            warnings.extend(roles.warnings)
            self._before_request()
            return registry, warnings
        except (ValueError, ToolError) as error:
            return None, [f'Skill / Agent 刷新失败，保留上一完整快照：{error}']

    def skills_text(self, args=''):
        parts = args.split()
        if parts[:1] == ['deactivate'] and len(parts) == 2:
            self.skills.deactivate(parts[1])
            self._before_request()
        elif parts not in ([], ['list'], ['active']):
            raise ValueError('用法：/skills [list|active]；/skills deactivate <name|--all>')
        active = {a.skill.name for a in self.skills.active}
        selected = [s for s in self.skills.catalog.skills if parts != ['active'] or s.name in active]
        lines = [f'{s.name} · {s.description} · {s.mode} · {s.layer}:{s.path} · '
                 + ('已激活' if s.name in active else '未激活') for s in selected]
        lines.append('普通工具：' + (', '.join(sorted(self.effective_tools() - {'load_skill'})) or '无'))
        lines.append('系统入口：load_skill；/skills deactivate <name|--all> 停用；/reset 清空对话与激活。')
        return '\n'.join(lines)

    def agents_text(self, args=''):
        parts = args.split()
        if parts in ([], ['list']):
            return '\n'.join(f'{role.name} · {role.description} · {role.layer}:{role.path}' for role in self.roles.roles)
        if len(parts) == 2 and parts[0] == 'show':
            role = self.roles.get(parts[1])
            return (f'{role.name} · {role.description}\n来源：{role.layer}:{role.path}\n'
                    f'模型：{role.model} · 最大轮次：{role.max_iterations} · 权限：{role.permission_mode}\n'
                    f'白名单：{role.allowed_tools} · 黑名单：{role.disallowed_tools}\n{role.body}')
        raise ValueError('用法：/agents [list|show <name>]')

    async def tasks_text(self, args=''):
        parts = args.split()
        if parts in ([], ['list']):
            return '后台任务仅在当前进程有效\n' + ('\n'.join(
                f'{record.task_id} · 父 {record.parent_task_id} · {record.type}/{record.role} · '
                f'{record.display_mode} · {record.state}' for record in self.tasks.records.values()) or '没有子任务')
        if len(parts) == 2:
            action, task_id = parts
            if action == 'show':
                report = self.tasks.parent_report(task_id) if task_id in self.tasks.parents else self.tasks.get(task_id).report()
                return json.dumps(report, ensure_ascii=False, indent=2)
            if action == 'cancel':
                record = await self.tasks.cancel(task_id)
                return f'{task_id} · {record.state}；已发生操作保留，远端状态可能未知'
            if action == 'cancel-parent':
                parent = await self.tasks.cancel_parent(task_id)
                self.tasks.inbox.discard_parent(task_id)
                if self.team_scope and self.team_scope.lead and self.teams.goal_parent is parent:
                    await self.teams.cancel_goal()
                return f'{parent.task_id} · 已取消该父及其子任务'
        raise ValueError('用法：/tasks [list|show <id>|cancel <id>|cancel-parent <parent_id>]')

    def _memory_notification(self, notification):
        kind = notification.get('kind')
        if kind == 'usage':
            self.memory_usage = notification.get('usage')
        elif kind == 'restore_usage':
            self.restore_usage = notification.get('usage')
        elif kind == 'memory_update':
            self.memory_status = notification.get('text', '')
            if notification.get('task_id'):
                self.memory_tasks[notification['task_id']] = notification.get('status', 'unknown')
        if notification.get('kind') == 'usage' and self.journal:
            usage = asdict(notification['usage'])
            usage['incomplete_fields'] = sorted(usage['incomplete_fields'])
            try:
                self.journal.append('maintenance_finished', {'purpose': 'memory', 'usage': usage,
                    'task_id': notification.get('task_id', '')})
            except OSError:
                self.warnings.append('记忆维护用量存档失败')
        if getattr(self, '_notify', None):
            self._notify(notification)

    def permission_command(self, question: str) -> str:
        """只在空闲期执行的本地控制，不触碰对话历史。"""
        parts = question.split()
        usage = "用法：/permissions；/permissions mode strict|default|bypass；/permissions revoke session|permanent"
        if parts == ["/permissions"]:
            snapshot = self.permissions.config.load()
            root = str(self.executor.context.root)
            permanent = [grant for grant in snapshot.approvals if str(grant.root) == root]
            session = self.permissions.grants.session
            return (f"权限模式> {self.permissions.mode} · 项目 {root}\n"
                    f"会话授权> {len(session)} 项 · {session}\n永久授权> {len(permanent)} 项 · {permanent}")
        if len(parts) == 3 and parts[1] == "mode" and parts[2] in {"strict", "default", "bypass"}:
            self.permissions.mode = parts[2]
            return f"权限模式> 已切换为 {parts[2]}"
        if len(parts) == 3 and parts[1] == "revoke" and parts[2] in {"session", "permanent"}:
            self.permissions.grants.revoke(parts[2])
            return f"权限> 已撤销当前项目的{'会话' if parts[2] == 'session' else '永久'}授权"
        raise ValueError(usage)

    def enter_plan(self) -> None:
        if self.journal:
            self.journal.append('mode_changed', {'mode': 'plan'})
        self.mode = "plan"
        self.worktree_cleaner.suspend()
        self.prompt_state.enter_mode("plan")

    @serialized_turn
    async def ask(self, question: str, *, cancel_event: asyncio.Event | None = None) -> AsyncIterator[AgentEvent]:
        self.validate_skills()
        owns_turn = self._hook_turn is None
        cancel = cancel_event if cancel_event is not None else asyncio.Event()
        if owns_turn:
            self._task_context = await self.teams.parent_for_input(question, cancel)
            if self._task_context is None:
                self._task_context = self.tasks.new_parent(question, limit=self.agent.max_iterations,
                    mode=self.mode, generation=self.generation, cancel=cancel)
            self._task_budget = self._task_context.budget
            if self.journal:
                self.journal.append('task_started', {'task_id': self._task_context.task_id, 'input': question, 'mode': self.mode})
        budget = self._task_budget
        self._task_question = question
        if owns_turn:
            await protected(self._begin_hook_turn(question, budget, cancel), cancel_event=cancel)
        async for event in self._segment(question, budget, cancel, owns_turn=owns_turn):
            yield event

    @serialized_turn
    async def resume_parent(self, task_id, *, cancel_event=None):
        """同一父目标接续剩余预算；不重放委派或凭据。"""
        parent = self.tasks.parent(task_id)
        if (not parent.wake_allowed or parent.finished or parent.generation != self.generation
                or parent.cancel.is_set() or not (self.tasks.inbox.peek(task_id) or self.teams.pending)):
            return
        if not parent.budget.remaining:
            self._finish_parent(parent, 'max_iterations')
            yield AgentEvent('task_finished', run_id=task_id, reason='max_iterations', text='原父预算耗尽；子结果仅可本地查看')
            return
        self._task_context = parent
        self._task_budget = parent.budget
        self._task_question = parent.question
        cancel = cancel_event if cancel_event is not None else parent.cancel
        parent.cancel = cancel
        await protected(self._begin_hook_turn(parent.question, parent.budget, cancel, resume=True), cancel_event=cancel)
        async for event in self._segment('接续原父任务：' + parent.question + '\n请结合应用提供的有来源子结果继续完成原目标。', parent.budget, cancel, continuation=True):
            yield event

    def next_parent(self):
        if self._main_running or self._main_owner.locked() or self.tasks.paused:
            return None
        parent = self.teams.goal_parent
        if (self.teams.pending and parent and parent.budget.remaining and parent.wake_allowed
                and not parent.finished and parent.generation == self.generation):
            return parent.task_id
        for task_id in self.tasks.inbox.parent_ids():
            parent = self.tasks.parent(task_id)
            if parent.wake_allowed and not parent.finished and parent.generation == self.generation:
                return task_id
        return None

    def _finish_parent(self, parent, reason):
        """父终态存档只有一个提交点，取消与预算边界也使用它。"""
        if parent.finished:
            return False
        from .agent import _total_usage
        parent.finished, parent.reason, parent.wake_allowed = True, reason, False
        if self.journal:
            usage = asdict(_total_usage(parent.budget.usage))
            usage['incomplete_fields'] = sorted(usage['incomplete_fields'])
            self.journal.append('task_finished', {'task_id': parent.task_id, 'reason': reason,
                'mode': parent.mode, 'state': self.context.state(), 'usage': usage})
        return True

    def _enqueue_parent_memory(self, parent, task):
        if parent.reason == 'model_done' and self.memory and not parent.memory_enqueued:
            final = dict(task, task_id=parent.task_id, question=parent.question,
                         user_message=parent.user_message, tools=tuple(parent.evidence))
            parent.memory_enqueued = self.memory.enqueue(final)
            if parent.memory_enqueued:
                self.memory_tasks[parent.task_id] = 'queued'

    async def _segment(self, question, budget, cancel, *, owns_turn=True, continuation=False):
        if self._main_running:
            raise RuntimeError('主运行已有所有者')
        self._main_running = True
        parent = self._task_context
        run_id = uuid4().hex
        usage_start = len(budget.usage)
        source = self.agent.run(question, history=self.history, mode=self.mode, cancel_event=cancel,
                                budget=budget, run_id=run_id, parent_run_id=parent.task_id if parent else '', turn_owned=True,
                                input_message=Message('context', question, context_kind='task_resume') if continuation else None,
                                segment=True)
        reason = None
        try:
            async for event in source:
                if event.kind == 'finished':
                    from .agent import _total_usage
                    event = replace(event, usage=_total_usage(budget.usage[usage_start:]))
                    reason = event.reason
                    if parent:
                        task = self.agent.last_task or {}
                        parent.user_message = parent.user_message or task.get('user_message')
                        parent.final_message = task.get('final_message')
                        parent.evidence.extend(task.get('tools', ()))
                        pending = any(self.tasks.get(child).outcome is None for child in parent.children) or await self.teams.goal_pending(parent)
                        reports = self.tasks.inbox.peek(parent.task_id)
                        terminal = reason != 'model_done' or (not pending and not reports)
                        if terminal and self._finish_parent(parent, reason):
                            self._enqueue_parent_memory(parent, task)
                            yield AgentEvent('task_finished', run_id=parent.task_id, reason=reason,
                                             usage=_total_usage(parent.budget.usage), parent_run_id=parent.task_id)
                yield event
        finally:
            if reason is None:
                cancel.set()
            await protected(source.aclose(), cancel_event=cancel)
            if parent and (cancel.is_set() or reason not in {None, 'model_done'}):
                parent.wake_allowed = False
                if cancel.is_set():
                    await protected(self.tasks.cancel_parent(parent.task_id), cancel_event=cancel)
                    await protected(self.teams.cancel_goal(), cancel_event=cancel)
            if owns_turn:
                await self._end_hook_turn(budget, reason or 'cancelled', cancel)
                self._task_budget = None
            try:
                if self.teams.scope and self.teams.scope.lead and self.teams.lead_journal:
                    self._checkpoint(self.history, self.context.state())
                    await self.teams.checkpoint_goal_budget()
                if self.teams.pause_deferred:
                    await self.teams.finish_pause()
            finally:
                self._main_running = False

    async def start(self, *, cancel_event=None):
        """终端初始化或首次无界面调用后开始；恢复准备不会重复。"""
        if self.resumed and not self._restore_prepared:
            await self.prepare_restore(cancel_event=cancel_event)
        self.worktree_cleaner.start()
        await self.hooks.start(source='resume' if self.resumed else 'new',
                               cancel_event=cancel_event, archive_session_id=self.session_id)

    async def _begin_hook_turn(self, question, budget, cancel, *, resume=False):
        await self.start(cancel_event=cancel)
        scope = self.hooks.scope(budget.root_run_id)
        self._startup_injections = self.hooks.prompts.snapshot()
        for injection in self._startup_injections:
            scope.prompts.add(injection.text, source=injection.source, event=injection.event)
        self.agent.hooks = scope
        fields = self.agent.hook_fields(budget, budget.root_run_id)
        self._hook_turn = budget.root_run_id
        await scope.emit('turn.start', **fields, message={'text': question}, cancel_event=cancel)
        if not resume:
            await scope.emit('message.user', **fields, message={'text': question, 'role': 'user'}, cancel_event=cancel)

    async def _end_hook_turn(self, budget, reason, cancel):
        try:
            await protected(self.agent.hooks.emit('turn.end', **self.agent.hook_fields(budget, budget.root_run_id), reason=reason, cancel_event=cancel), cancel_event=cancel)
        finally:
            self._hook_turn = None

    async def set_mode(self, mode, *, cancel_event=None):
        """可信控制入口先收尾副作用，再提交模式并派发事件。"""
        if mode not in {'plan', 'execute'}:
            raise ValueError('模式必须为 plan 或 execute')
        if mode == self.mode:
            if mode == 'plan' and (cancel_event is None or not cancel_event.is_set()):
                self.enter_plan()
            return
        previous = self.mode
        if mode == 'plan':
            await self.teams.quiesce()
            await self.worktree_cleaner.pause()
            self.tasks.paused = True
            for parent in self.tasks.parents.values():
                if any(self.tasks.get(child).mode == 'execute' and self.tasks.get(child).state in {'queued', 'running'}
                       for child in parent.children):
                    await self.tasks.cancel_parent(parent.task_id)
            await self.hooks.quiesce(cancel_event=cancel_event)
        elif self.team_scope is not None and self.team_scope.lead:
            # 只读实例也先收尾，裸 /do 不把存量消息升级成执行任务。
            await self.teams.quiesce()
        try:
            if cancel_event is not None and cancel_event.is_set():
                return
            self.enter_plan() if mode == 'plan' else self.enter_execute()
            await self.teams.publish_mode(mode)
        finally:
            if self.mode == 'execute':
                self.hooks.resume_mutations()
            self.tasks.paused = False
        await self.hooks.emit('mode.changed', mode_change={'from': previous, 'to': self.mode}, cancel_event=cancel_event)

    def enter_execute(self) -> None:
        """只切换模式；重复切换不重置当前请求周期。"""
        if self.mode == "execute":
            return
        if self.journal:
            self.journal.append('mode_changed', {'mode': 'execute'})
        self.mode = "execute"
        self.worktree_cleaner.resume()
        self.prompt_state.enter_mode("execute")

    def context_status(self) -> str:
        self._before_request()
        allowed = self.effective_tools()
        self.context.estimate([*self.history, self.prompt_state.peek_request()], build_system_prompt(),
                              self.executor.registry.definitions(allowed_tools=allowed))
        identity = f'会话> {self.session_id or "未启用存档"} · {"显式恢复" if self.resumed else "新建"}\n'
        pending = sum(status in {'queued', 'running'} for status in self.memory_tasks.values())
        maintenance = (f'\n记忆> 待处理 {pending} · {self.memory_status} · 索引版本 {self.memory.version[:12]}'
                       if self.memory else '')
        from .terminal.text import usage_text
        if self.memory_usage is not None:
            maintenance += '\n记忆维护实际用量> ' + usage_text(self.memory_usage)
        if self.restore_usage is not None:
            maintenance += '\n恢复摘要实际用量> ' + usage_text(self.restore_usage)
        return identity + self.context.status_text() + maintenance

    def close(self) -> None:
        """释放私有句柄；产品存档的缓存随存档保留。"""
        self.worktree_cleaner.stop()
        if (hasattr(self, 'hooks') and self.hooks.snapshot.rules and not self.hooks.closed) or self.tasks.records or (getattr(self, 'team_scope', None) and self.team_scope.lead):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(self.aclose())
            else:
                if not getattr(self, '_hook_close_task', None):
                    self._hook_close_task = loop.create_task(self.aclose())
            return
        if self.worktree_cleaner.task is not None and not self.worktree_cleaner.task.done():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(self.worktree_cleaner.close())
            else:
                loop.create_task(self.worktree_cleaner.close())
        self._close_handles()

    def _close_handles(self):
        try:
            if hasattr(self, 'context'):
                self.context.cache.close()
        finally:
            if self.journal:
                self.journal.close()

    async def aclose(self):
        """后台任务停止后才释放存档，供应商由应用随后关闭。"""
        if hasattr(self, 'teams'):
            await self.teams.pause()
        try:
            await self.worktree_cleaner.close()
            await self.tasks.aclose()
            if hasattr(self, 'hooks'):
                await self.hooks.close(archive_session_id=self.session_id)
            if self.memory:
                await self.memory.aclose(wait_seconds=2)
        finally:
            self._close_handles()

    async def prepare_restore(self, *, cancel_event=None):
        """当前 MCP 工具确定后，仅一次独立恢复摘要，不执行历史任务。"""
        if self._restore_prepared:
            return
        await self._prepare_restore(cancel_event=cancel_event)
        self._restore_prepared = True

    async def _prepare_restore(self, *, cancel_event=None):
        if not self.resumed:
            return
        self._before_request()
        allowed = self.effective_tools()
        tools = self.executor.registry.definitions(allowed_tools=allowed)
        system = build_system_prompt()
        reminder = self.prompt_state.peek_request(force_full=True)
        estimate = self.context.estimate([*self.history, reminder], system, tools)
        if self.context.fits(estimate):
            if self.journal:
                self.journal.confirm_resume()
            return
        cancel = cancel_event or asyncio.Event()
        spilled, warnings = self.context.spill(self.history)
        self.warnings.extend(warnings)
        estimate = self.context.estimate([*self.history, reminder], system, tools)
        if self.context.fits(estimate):
            if spilled:
                self.warnings.append(f'恢复已将 {spilled} 项结果落盘，当前预算可继续')
            if self.journal:
                self.journal.confirm_resume()
            return
        user = next((m for m in reversed(self.history) if m.role == 'user'), None)
        manual_usage = self.context.manual_usage
        try:
            result = await self.context.compact(self.provider, self.history, user, tools, system, reminder,
                                                 cancel, manual=True, hooks=self.hooks,
                                                 hook_fields={'archive_session_id': self.session_id}, purpose='restore')
        finally:
            # 恢复借用手动摘要的输入余量，用量仍归属于独立恢复用途。
            self.context.manual_usage = manual_usage
        self._maintenance_record('restore', result.usage, called=result.called)
        if result.called:
            self._memory_notification({'kind': 'restore_usage', 'usage': result.usage, 'purpose': 'restore'})
        if not result.success:
            raise ValueError('恢复无法继续：' + result.text)
        self.prompt_state.history_trimmed()
        if self.journal:
            self.journal.confirm_resume()

    def _maintenance_record(self, purpose, usage=None, *, called=False):
        if self.journal:
            data = asdict(usage) if called and usage else None
            if data is not None:
                data['incomplete_fields'] = sorted(data['incomplete_fields'])
            self.journal.append('maintenance_finished', {'purpose': purpose, 'state': self.context.state(), 'usage': data})

    async def compact(self, *, cancel_event: asyncio.Event | None = None) -> AsyncIterator[AgentEvent]:
        """独立维护操作，保留模式和工作任务记录。"""
        cancel = cancel_event if cancel_event is not None else asyncio.Event()
        run_id = uuid4().hex
        iteration = 0
        def event(kind, **fields):
            return AgentEvent(kind, run_id=run_id, iteration=iteration, max_iterations=1,
                              mode=self.mode, permission_mode=self.permissions.mode, purpose='summary', **fields)
        if cancel.is_set():
            fields = {'run_id': run_id, 'archive_session_id': self.session_id}
            await self.hooks.emit('context.before_compact', **fields, context={'purpose': 'manual',
                'estimated_tokens': self.context.last_estimate, 'threshold': self.context.window}, cancel_event=cancel)
            await self.hooks.emit('context.after_compact', **fields, context={'purpose': 'manual', 'result': 'cancelled',
                'reason': 'cancelled', 'estimated_tokens': self.context.last_estimate, 'threshold': self.context.window}, cancel_event=cancel)
            self._maintenance_record('summary')
            yield event('context_compaction', phase='cancelled', text='摘要已取消，历史保留')
            return
        spilled, warnings = self.context.spill(self.history)
        for warning in warnings:
            yield event('context_compaction', phase='spill_failed', text=warning)
        yield event('context_compaction', phase='summary', spilled=spilled, text='正在准备手动摘要（最多一次请求）')
        user = next((m for m in reversed(self.history) if m.role == 'user'), None)
        self._before_request()
        allowed = self.effective_tools()
        def started():
            nonlocal iteration
            iteration = 1
        result = await self.context.compact(self.provider, self.history, user,
            self.executor.registry.definitions(allowed_tools=allowed), build_system_prompt(),
            self.prompt_state.peek_request(force_full=True), cancel, manual=True, on_start=started,
            hooks=self.hooks, hook_fields={'run_id': run_id, 'archive_session_id': self.session_id})
        self._maintenance_record('summary', result.usage, called=result.called)
        if result.called:
            yield event('usage', usage=result.usage)
        if result.success:
            self.prompt_state.history_trimmed()
        yield event('context_compaction', phase='success' if result.success else 'cancelled' if result.cancelled else 'failed' if result.called else 'noop',
                    text=result.text, estimated_before=result.before, estimated_after=result.after,
                    spilled=self.context.cache.count, failures=self.context.failures, circuit_open=self.context.circuit_open)
