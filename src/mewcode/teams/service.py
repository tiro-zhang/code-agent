"""团队动作的可信系统入口，身份只取运行绑定。"""
import asyncio
from contextlib import ExitStack
from dataclasses import asdict
import json
from pathlib import Path
from uuid import uuid4

from ..tools.base import ToolError, ToolResult
from .capabilities import TeamScope, guard_action
from .store import TeamStore
from .tasks import TaskBoard
from .mailbox import Mailbox


class TeamService:
    def __init__(self, session):
        self.session = session
        self.store = TeamStore(session.user_root / 'teams')
        self.runners = {}
        self.handles = {}
        self.changed = asyncio.Event()
        self.closed = False
        self.runtime = None
        self.monitor = None
        self.goal_parent = None
        self.goal_parent_goal_id = None
        self.consumed_ids = set()
        self.pending = []
        self.instance_id = uuid4().hex
        self.wake_locks = {}
        self.lead_journal = None
        self.prior_lead_handles = None
        self.activated = False
        self.prior_cache_reader = None
        self.pause_deferred = False
        self.resume_waiting = False
        self.pause_unknown = []

    @property
    def scope(self):
        return self.session.team_scope

    def lead_control_path(self):
        """Lead 的运行控制记录属于用户域，不能经普通工具写入。"""
        return f'members/{self.scope.member_id}/lead-control.json'

    @staticmethod
    def _map_values(value, mapping):
        if isinstance(value, str):
            return mapping.get(value, value)
        if isinstance(value, list):
            return [TeamService._map_values(item, mapping) for item in value]
        if isinstance(value, dict):
            return {key: TeamService._map_values(item, mapping) for key, item in value.items()}
        return value

    def _transfer_cache(self, source, target, history, state):
        """只复制来源缓存已经登记的文件，历史及摘要引用一起重映射。"""
        from ..sessions.codec import encode_message, decode_messages
        from ..types import Message
        mapping = {}
        for path in sorted(source.paths):
            header = source.describe(path)
            message = Message('tool', tool_call_id=header.get('tool_call_id'),
                              tool_result=source.restore(path), id=header['source'])
            mapping[path] = target.save(message, header.get('tool_name', 'read_file'),
                                        _index=Path(path).name == 'results-index.jsonl',
                                        provenance=header.get('provenance'))
        candidate = decode_messages(self._map_values([encode_message(m) for m in history], mapping))
        return candidate, self._map_values(state, mapping)

    def _lead_payload(self, history=None, *, used=None):
        from dataclasses import asdict
        history = self.session.history if history is None else history
        team = self.store.load(self.scope.team_name)
        parent = self.goal_parent
        value = None
        if parent:
            value = {'task_id': parent.task_id, 'question': parent.question, 'mode': parent.mode,
                     'limit': parent.budget.limit, 'used': parent.budget.used if used is None else used,
                     'usage': [dict(asdict(item), incomplete_fields=sorted(item.incomplete_fields))
                               for item in parent.budget.usage]}
        ids = self.consumed_ids | {m.id[5:] for m in history if m.role == 'context' and m.id.startswith('mail-message-')}
        return {'version': 1, 'team_id': team.team_id, 'member_id': team.lead_id,
                'journal_id': self.session.journal.id, 'goal_id': team.active_goal_id,
                'consumed_ids': sorted(ids), 'parent': value}

    def checkpoint_lead(self, history=None, *, used=None):
        """预算预留和消费身份必须先持久保存，失败关闭后续请求。"""
        if not self.scope or not self.scope.lead or not self.lead_journal:
            return
        try:
            self.store.require_lead(self.scope.team_name)
            self.store.write_json(self.scope.team_name, self.lead_control_path(),
                                  self._lead_payload(history, used=used))
        except (OSError, ValueError):
            self.session.agent.storage_blocked = True
            raise

    async def checkpoint_goal_budget(self):
        if not self.scope or not self.scope.lead or not self.goal_parent:
            return
        team = self.store.load(self.scope.team_name)
        goal = team.goals.get(self.goal_parent_goal_id)
        if goal is None:
            return
        if team.active_goal_id != goal.goal_id:
            raise RuntimeError('Lead 预算绑定已过期，不能写入另一目标检查点')
        used = self.goal_parent.budget.used
        if goal.budget_used != used:
            def save(candidate):
                current = candidate.goals[goal.goal_id]
                if used < current.budget_used or used > current.budget_limit:
                    raise ValueError('Lead 目标预算不能倒退或扩大')
                current.budget_used = used
            try:
                await self.store.update_team(team.name, save)
            except (OSError, ValueError):
                self.session.agent.storage_blocked = True
                raise
        self.checkpoint_lead()

    def _bind_goal_budget(self, parent):
        """保留同一 TaskBudget 对象，覆盖所有工作及摘要请求的 take。"""
        self.goal_parent = parent
        goal_id = self.store.load(self.scope.team_name).active_goal_id
        self.goal_parent_goal_id = goal_id
        budget = parent.budget
        def take():
            if not self.scope or not self.scope.lead or self.goal_parent is not parent:
                raise RuntimeError('原团队目标已暂停或解绑，不能由遗留运行继续消费预算')
            team = self.store.load(self.scope.team_name)
            state = team.goals[goal_id].state
            finishing = (state == 'completed' and self.session._main_running
                         and self.session._task_context is parent)
            if team.active_goal_id != goal_id or (state != 'active' and not finishing):
                raise RuntimeError('原目标已取消或结束，不能消费旧预算或复活旧调度')
            if budget.remaining <= 0:
                raise RuntimeError('任务请求预算已耗尽')
            self.checkpoint_lead(used=budget.used + 1)
            budget.used += 1
        budget.take = take
        self.checkpoint_lead()

    async def parent_for_input(self, question, cancel):
        """明确用户输入才重新激活原目标；不会创建补充预算。"""
        if not self.scope or not self.scope.lead:
            return None
        team = self.store.load(self.scope.team_name)
        if team.status == 'needs_review':
            self.activated = False
            raise ToolError('team_needs_review', '团队操作尚未核查，不能由新输入恢复调度', not_started=True)
        self.resume_waiting = False
        self.activated = self.session.mode == 'execute'
        goal = team.goals.get(team.active_goal_id)
        if goal is None or goal.state != 'active':
            return None
        parent = self.goal_parent
        if parent is None:
            parent = self.session.tasks.new_parent(goal.description, limit=goal.budget_limit,
                mode=self.session.mode, generation=self.session.generation, cancel=cancel)
            parent.budget.used = goal.budget_used
            self._bind_goal_budget(parent)
        parent.cancel, parent.generation, parent.mode = cancel, self.session.generation, self.session.mode
        parent.finished, parent.reason = False, ''
        parent.wake_allowed = self.session.mode == 'execute' and parent.budget.remaining > 0
        self.activated = self.session.mode == 'execute'
        if self.activated and (team.status != 'active' or team.mode != self.session.mode):
            def activate(candidate):
                candidate.status='active'
                candidate.mode=self.session.mode
            await self.store.update_team(team.name,activate)
        self.checkpoint_lead()
        self.changed.set()
        return parent

    def _validate_lead_control(self, data, team):
        """版本、身份及有界预算必须严格解析，不从正文恢复许可。"""
        from ..teams.models import validate_id
        from ..types import TokenUsage
        keys = {'version', 'team_id', 'member_id', 'journal_id', 'goal_id', 'consumed_ids', 'parent'}
        if (not isinstance(data, dict) or set(data) != keys or type(data['version']) is not int
                or data['version'] != 1 or data['team_id'] != team.team_id or data['member_id'] != team.lead_id
                or not isinstance(data['journal_id'], str) or not data['journal_id']):
            raise ValueError('Lead 检查点身份或版本无效')
        if team.members[team.lead_id].session_ref and data['journal_id'] != team.members[team.lead_id].session_ref:
            raise ValueError('Lead 存档引用与花名册权威登记不一致')
        if data['goal_id'] != team.active_goal_id:
            raise ValueError('Lead 检查点与当前目标不一致')
        ids = data['consumed_ids']
        if not isinstance(ids, list) or len(set(ids)) != len(ids):
            raise ValueError('Lead 消费 ID 无效')
        for identity in ids:
            validate_id(identity, 'message')
        parent = data['parent']
        if parent is not None:
            if (not isinstance(parent, dict) or set(parent) != {'task_id','question','mode','limit','used','usage'}
                    or not isinstance(parent['task_id'], str) or not parent['task_id'].startswith('parent_')
                    or not isinstance(parent['question'], str) or parent['mode'] not in {'execute','plan'}
                    or type(parent['limit']) is not int or type(parent['used']) is not int
                    or not 0 <= parent['used'] <= parent['limit'] or parent['limit'] < 1
                    or not isinstance(parent['usage'], list)):
                raise ValueError('Lead 原目标预算无效')
            goal = team.goals.get(team.active_goal_id)
            if goal is None or parent['limit'] != goal.budget_limit:
                raise ValueError('Lead 预算与共享目标不一致')
            usage = []
            for item in parent['usage']:
                if not isinstance(item, dict):
                    raise ValueError('Lead 用量记录无效')
                item = dict(item)
                incomplete = item.get('incomplete_fields')
                if not isinstance(incomplete, list) or any(not isinstance(key, str) for key in incomplete):
                    raise ValueError('Lead 用量字段无效')
                item['incomplete_fields'] = frozenset(incomplete)
                try:
                    usage.append(TokenUsage(**item))
                except TypeError:
                    raise ValueError('Lead 用量结构无效') from None
            parent = dict(parent, usage=usage)
        return parent

    async def bind_lead(self, team, *, resume=False):
        """绑定私有追加式存档；恢复投影、引用和预算，但不发送请求。"""
        from ..sessions import Journal
        from ..context.spill import ResultCache
        from ..tasks.context import TaskContext
        from ..skills.budget import TaskBudget
        from ..context.partition import validate_pairs
        directory = self.store.member_path(team.name, team.lead_id)
        self.store.private_directory(directory)
        record_path = directory / 'lead-control.json'
        data = self.store.read_json(team.name, self.lead_control_path()) if resume and record_path.exists() else None
        parent_data = self._validate_lead_control(data, team) if data is not None else None
        if resume and data is None and (team.active_goal_id or team.members[team.lead_id].session_ref):
            raise ValueError('Lead 必要检查点缺失，不能以空历史恢复')
        journal = (Journal.resume(Path(team.workspace_root), data['journal_id'], self.session.config.protocol,
                    self.session.config.model, record_activity=False, storage_root=directory) if data is not None else
                   Journal.create(Path(team.workspace_root), self.session.config.protocol,
                                  self.session.config.model, storage_root=directory))
        cache = ResultCache(Path(team.workspace_root), session_id=journal.id, persistent=True, storage_root=directory)
        old_journal, old_cache = self.session.journal, self.session.context.cache
        old_passthrough = self.session.agent.system_passthrough
        old_reader = getattr(self.session.executor, "cache_reader", None)
        old_history, old_state = list(self.session.history), self.session.context.state()
        try:
            if data is not None:
                projection = journal.projection
                history, state = projection.history, projection.state
                cache.reopen(projection.cache_paths)
                required = projection.cache_paths | set(state.get('summary_files', [])) | {m.cache_path for m in history if m.cache_path}
                for path in required:
                    cache.restore(path)
                self.session.warnings.extend(journal.warnings)
            else:
                history, state = self._transfer_cache(old_cache, cache, old_history, old_state)
            validate_pairs(history)
            self.prior_lead_handles = (old_journal, old_cache)
            self.prior_system_passthrough = old_passthrough
            self.session.agent.system_passthrough = False
            self.prior_cache_reader = old_reader
            self.session.executor.cache_reader = self.read_lead_cache
            self.session.journal, self.session.agent.journal = journal, journal
            self.session.context.cache = cache
            self.session.context.checkpoint = self.session._checkpoint
            self.session.history[:] = history
            self.session.context.restore_state(state)
            self.lead_journal = journal
            self.consumed_ids = set(data['consumed_ids']) if data is not None else set()
            self.consumed_ids.update(m.id[5:] for m in history if m.role == 'context' and m.id.startswith('mail-message-'))
            if data is not None:
                self.session.warnings.extend(self.session.skills.restore(journal.projection.active_skills))
            if parent_data:
                goal = team.goals[team.active_goal_id]
                budget = TaskBudget(parent_data['limit'], parent_data['task_id'],
                                    max(parent_data['used'], goal.budget_used), parent_data['usage'])
                parent = TaskContext(parent_data['task_id'], parent_data['question'], budget,
                                     self.session.mode, self.session.generation, wake_allowed=False)
                self.session.tasks.parents[parent.task_id] = parent
                self._bind_goal_budget(parent)
            self.activated = not resume
            self.resume_waiting = resume
            self.session._checkpoint(self.session.history, self.session.context.state())
            interaction = getattr(self.session.agent, 'active_interaction', None)
            if interaction:
                journal.append('interaction_started', interaction)
            await self.store.update_team(team.name, lambda candidate: setattr(candidate.members[team.lead_id], 'session_ref', journal.id))
            await self.inbox().reconcile(team.lead_id, self.consumed_ids)
        except BaseException:
            self.session.agent.system_passthrough = old_passthrough
            self.session.journal, self.session.agent.journal = old_journal, old_journal
            self.session.context.cache = old_cache
            self.session.executor.cache_reader = old_reader
            self.session.context.checkpoint = self.session._checkpoint if old_journal else None
            self.session.history[:] = old_history
            self.session.context.restore_state(old_state)
            self.lead_journal = None
            self.prior_lead_handles = None
            cache.close()
            journal.close()
            raise

    def detach_lead(self):
        """暂停保存的 Lead 不再被后续普通会话改写。"""
        if not self.lead_journal:
            return
        old_journal, old_cache = self.prior_lead_handles
        history, state = self._transfer_cache(self.session.context.cache, old_cache,
                                             self.session.history, self.session.context.state())
        self.session.context.cache.close()
        self.lead_journal.close()
        self.session.journal, self.session.agent.journal = old_journal, old_journal
        self.session.agent.system_passthrough = self.prior_system_passthrough
        self.session.context.cache = old_cache
        self.session.executor.cache_reader = self.prior_cache_reader
        self.prior_cache_reader = None
        self.session.context.checkpoint = self.session._checkpoint if old_journal else None
        self.session.history[:] = history
        self.session.context.restore_state(state)
        self.lead_journal, self.prior_lead_handles = None, None

    async def read_lead_cache(self, args):
        """只提供当前 Lead 已登记的相对缓存引用，不扩大工具工作根。"""
        import os
        import stat
        cache = self.session.context.cache
        path = args['path']
        if path not in cache.paths:
            return await self.prior_cache_reader(args) if self.prior_cache_reader else None
        with os.fdopen(cache._read_fd(path), encoding='utf-8') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise OSError('Lead 缓存不是普通文件')
            start, count = args.get('start_line', 1), args.get('max_lines', 200)
            selected, size, truncated = [], 0, False
            for index, line in enumerate(stream, 1):
                if index < start:
                    continue
                if index >= start + count or size + len(line.encode()) > self.session.executor.context.output_limit:
                    truncated = True
                    break
                selected.append(line)
                size += len(line.encode())
        return ToolResult.success({'path': path, 'content': ''.join(selected), 'start_line': start,
                                   'end_line': start + len(selected) - 1}, truncated=truncated)

    def lead_notice(self):
        """系统提供实际绑定与协议步骤，避免模型猜测控制字段。"""
        if not self.scope or not self.scope.lead:
            return ''
        team = self.store.load(self.scope.team_name)
        goal = team.goals.get(team.active_goal_id)
        snapshot = {'team_name': team.name, 'lead_id': team.lead_id,
                    'goal_id': team.active_goal_id, 'state': team.status,
                    'coordinator': self.scope.coordinator(self.session.config),
                    'remaining': self.goal_parent.budget.remaining if self.goal_parent else None}
        return ('可信团队绑定：' + json.dumps(snapshot, ensure_ascii=False) + '\n'
            '先用 team goal(description) 建立用户目标；目标进行中的输入与通知沿用原目标预算。'
            '用 team_task create(title, code, dependencies) 拆解任务，代码任务 code=true，依赖填写 task_id。'
            '再用 team_member spawn(name, role, backend, require_plan_approval) 创建稳定成员。'
            'Lead 用 team_task reassign(task_id, member_id) 取得新的 claim_id；随后 team_message send '
            'recipient=成员名或稳定 ID，type=task_assignment，fields={goal_id,task_id,claim_id}。'
            '读取真实成员 plan_request 后复制其 fields 的 goal_id/task_id/claim_id/plan_id/plan_version，'
            '回复 type=plan_decision 并追加 approved 布尔值，正文说明审批理由；旧版本不能批准。'
            '成员成果 result={summary:字符串,verified:布尔值,evidence:非空列表,branch:代码分支,commit:完整提交SHA}，'
            '代码任务必须有 branch/commit；提交成果不等于接纳。'
            'Lead 独立验证代码后 team_task accept(task_id, expected_revision, reason, validation_command)。'
            '依赖按序 team_integrate integrate(task_id, validation_command)，未整合不能满足代码依赖；'
            '用户目标分支只在 team_integrate finalize(validation_command) 后推进。'
            '无剩余额度时只展示待决事项，不创建新目标来刷新额度。' +
            ('当前 coordinator 只负责派工、决策、收尾与 Git 整合；代码修改和冲突内容交给成员。'
             if snapshot['coordinator'] else ''))

    def timeout_for(self, arguments):
        return None

    def guard(self, name, arguments):
        from .capabilities import COLLABORATION
        if name in {'team', *COLLABORATION}:
            guard_action(self.scope, name, arguments, mode=self.session.mode,
                         awaiting_plan=bool(self.runtime and self.runtime.control.awaiting_plan))
            if self.scope and self.store.load(self.scope.team_name).status == 'needs_review' and arguments:
                if not (name == 'team' and arguments.get('action') == 'pause'):
                    guard_action(self.scope, name, arguments, mode='plan')
        if self.scope and self.scope.coordinator(self.session.config):
            if name not in self.session.effective_tools():
                raise ToolError('tool_not_allowed', 'coordinator 自用工具范围禁止此工具', not_started=True)
            if name == 'load_skill' and arguments and 'resource' not in arguments:
                if self.session.skills.catalog.get(arguments['name']).mode == 'isolated':
                    raise ToolError('tool_not_allowed', 'coordinator 不能执行独立 Skill', not_started=True)
        if self.runtime:
            self.runtime.guard(name, arguments)
        if name in {'write_file', 'edit_file'} and arguments:
            path = Path(arguments['path'])
            path = path if path.is_absolute() else self.session.executor.context.root / path
            conflict_allowed=bool(self.runtime and self.runtime.conflict_lease and self.runtime.conflict_root
                                  and path.resolve().is_relative_to(self.runtime.conflict_root))
            if path.resolve().is_relative_to(self.store.root) and not conflict_allowed:
                raise ToolError('permission_denied', '团队控制文件只能经可信团队入口修改', not_started=True)

    def handler(self, name):
        async def invoke(arguments, *, cancel_event, on_event=None, tool_call_id=''):
            self.guard(name, arguments)
            if cancel_event.is_set():
                raise ToolError('cancelled', '团队操作未启动', not_started=True)
            try:
                if name == 'team':
                    result = await self.control(arguments)
                else:
                    self.require_participant()
                    if name == 'team_task':
                        result = await self.task(arguments)
                    elif name == 'team_message':
                        result = await self.message(arguments,application_call_id=(
                            f'{self.session.session_id or self.instance_id}:{tool_call_id}' if tool_call_id else None))
                    elif name == 'team_member':
                        result = await self.member(arguments)
                    else:
                        result = await self.integrate(arguments)
                return ToolResult.success(result)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
                return ToolResult.failure('team_operation_failed', str(error))
        invoke.timeout_for = self.timeout_for
        return invoke

    def require_participant(self, *, lead=False):
        if self.scope is None or (lead and not self.scope.lead):
            raise ToolError('tool_not_allowed', '此动作需要可信团队身份', not_started=True)
        team = self.store.load(self.scope.team_name)
        if self.scope.member_id not in team.members or (self.scope.lead and team.lead_id != self.scope.member_id):
            raise ToolError('tool_not_allowed', '运行身份与花名册不一致', not_started=True)
        if lead:
            self.store.require_lead(team.name)
        return team

    def _notify(self, text):
        callback = self.session._notify
        if callback:
            callback({'kind':'team_update', 'text':text, 'status':'team'})
        self.session.warnings.append(text)

    async def isolate_member_projection(self, member, projection, *, receiver=False):
        """未确认意图按当时领取身份隔离，重新指派不继承旧交互的阻塞。"""
        team = self.store.load(self.scope.team_name)
        current = team.members[member.member_id]
        board = TaskBoard(self.store, team.name)
        _, tasks = board._load()
        affected = False
        grouped = {}
        for intent in projection.unconfirmed_interactions:
            if intent['team_id'] is not None and (
                    intent['team_id'], intent['member_id'], intent['lead_id']) != (
                    team.team_id, member.member_id, team.lead_id):
                raise ValueError('未确认交互的团队控制身份不匹配')
            key = (intent['task_id'], intent['claim_id'])
            grouped.setdefault(key, []).append(intent)
        for (task_id, claim_id), intents in grouped.items():
            reason = ('成员恢复发现未确认工具交互 ' + ','.join(item['interaction_id'] for item in intents)
                      + '，结果缺失，可能已有副作用；必须由 Lead 核查并明确重新指派')
            task = tasks.get(task_id)
            if task and (task.owner_id, task.claim_id) == (member.member_id, claim_id):
                used = max(task.budget_used, *(item['budget_used'] for item in intents))
                audited = any(entry.get('late') is True and entry.get('reported_state') == 'blocked'
                    and entry.get('owner_id') == member.member_id and entry.get('claim_id') == claim_id
                    and entry.get('reason') == reason and entry.get('budget_used') == used for entry in task.history)
                if not audited and (task.state not in {'blocked', 'needs_review'} or task.reason != reason or task.budget_used != used):
                    await board.report(member.member_id, task_id, claim_id,
                                       state='blocked', reason=reason, budget_used=used)
            if (current.task_id, current.claim_id) == (task_id, claim_id):
                affected = True
                record = self.store.recovery_data(team.name, current, projection)
                self.store.write_json(team.name, f'members/{member.member_id}/recovery.json', record)
                await self.store.update_team(team.name,
                    lambda candidate: setattr(candidate.members[member.member_id], 'state', 'needs_review'))
                self._notify(f'成员 {member.name} · {reason}')
        return affected

    async def recover_member_contexts(self):
        """显式 Lead 恢复只核查权威成员存档，不创建模型或成员运行实例。"""
        from ..sessions import Journal
        from .runtime import validate_definition
        team = self.require_participant(lead=True)
        try:
            for member in team.members.values():
                if member.member_id == team.lead_id or not member.active or not member.session_ref:
                    continue
                lease = self.store.member_lease(team.name, member.member_id)
                journal = None
                try:
                    definition = self.store.read_json(team.name, f'members/{member.member_id}/definition.json')
                    if not member.definition_fingerprint:
                        raise ValueError('成员冻结能力缺少权威指纹，不能恢复')
                    validate_definition(definition, member)
                    config = self.session.config if definition['model'] == 'inherit' else self.session.config.for_agent(definition['model'])
                    journal = Journal.resume(Path(member.workspace_root), member.session_ref,
                        config.protocol, config.model, storage_root=self.store.member_path(team.name, member.member_id),
                        record_activity=False)
                    await self.isolate_member_projection(member, journal.projection)
                finally:
                    if journal is not None:
                        journal.close()
                    lease.close()
        except BaseException:
            await self.store.update_team(team.name, lambda candidate: setattr(candidate, 'status', 'needs_review'))
            raise

    async def control(self, arguments):
        action = arguments['action']
        name = arguments.get('name') or (self.scope.team_name if self.scope else None)
        if action == 'list':
            return [team.to_dict() for team in self.store.list()]
        if action == 'status':
            if not name:
                return {'team':None, 'coordinator':False}
            team = self.store.load(name)
            from .integration import IntegrationService
            tasks=await TaskBoard(self.store,name).list(team.lead_id,goal_id=team.active_goal_id)
            mailbox=Mailbox(self.store,name)
            messages=[]
            for member in team.members.values():
                for message in await mailbox.read(member.member_id,unread=False):
                    receipt={}
                    from .store import TeamStoreError
                    try:
                        receipt=self.store.read_json(name,f'receipt-{message.message_id}.json')
                    except TeamStoreError as error:
                        if not isinstance(error.__cause__,FileNotFoundError):
                            raise
                    messages.append({**message.to_dict(),'saved':True,'notified':receipt.get('notified'),
                                     'wake_error':receipt.get('error','没有通知回执时不能推断唤醒成功')})
            goal=team.goals.get(team.active_goal_id)
            integration={'state':'not_initialized'}
            if goal:
                service=IntegrationService(Path(team.workspace_root),self.store.team_path(name)/'integration',
                    goal.goal_id,goal.baseline_commit,goal.target_branch)
                if service.record_path.exists():
                    integration=await service.status()
            plans={m.member_id:{'task_id':m.task_id,'plan_id':m.plan_id,'plan_version':m.plan_version,
                               'approved':m.plan_approved,'reason':''} for m in team.members.values()}
            for message in messages:
                member=team.members[message['recipient_id']]
                fields=message['fields']
                if (message['type']=='plan_decision' and message['sender_id']==team.lead_id
                        and (fields.get('plan_id'),fields.get('plan_version'),fields.get('claim_id'))==
                            (member.plan_id,member.plan_version,member.claim_id)):
                    plans[member.member_id]['reason']=message['body']
            return {'team':team.to_dict(), 'tasks':[task.to_dict() for task in tasks], 'messages':messages,
                    'integration':integration,'plans':plans,
                    'coordinator':bool(self.scope and self.scope.team_name==name and self.scope.member_id==team.lead_id
                                       and self.scope.coordinator(self.session.config)),
                    'coordinator_capability':getattr(self.session.config, 'team_coordinator_enabled', False),
                    'coordinator_environment':__import__('os').environ.get('MEWCODE_COORDINATOR') == '1'}
        if action in {'create','resume'}:
            if self.scope is not None:
                raise ValueError('当前运行已绑定团队，请先暂停')
            if not name:
                raise ValueError('需要团队名称')
            root = self.session.executor.context.root
            team = await (self.store.create(name, root) if action == 'create' else self.store.resume(name, root))
            self.session.team_scope = TeamScope(name, team.lead_id, True)
            self.closed = False
            try:
                await self.bind_lead(team, resume=action == 'resume')
            except BaseException:
                await self.store.pause(name)
                self.session.team_scope = None
                raise
            if action == 'resume':
                await self.recover_member_contexts()
                await self.recover_team_integration()
            self._notify(f'团队 {name} 已绑定；恢复保持空闲，等待明确任务')
            self.monitor = asyncio.create_task(self.watch())
            return self.store.load(name).to_dict()
        self.require_participant(lead=True)
        if name != self.scope.team_name:
            raise ToolError('tool_not_allowed','团队变更只能作用于当前运行绑定',not_started=True)
        if action == 'pause':
            await self.pause()
            team = self.store.load(name)
            uncertain = set(self.pause_unknown) | {
                member.member_id for member in team.members.values() if member.state == 'needs_review'}
            # 返回停止的实际结果；只公开运行标识，不导出启动凭据或租约令牌。
            return {'state': 'needs_review' if uncertain or team.status == 'needs_review' else 'paused',
                    'members': [{'member_id': identity, 'generation': team.members[identity].generation,
                                 'backend': team.members[identity].backend,
                                 'pane': self.handles[identity].pane if identity in self.handles else None}
                                for identity in sorted(uncertain)]}
        if action == 'goal':
            description = arguments.get('description')
            if not description:
                raise ValueError('目标描述不能为空')
            await self.checkpoint_goal_budget()
            await self.store.update_team(name,lambda team:setattr(team,'status','active'))
            goal = await self.store.start_goal(name, description, budget_limit=self.session.agent.max_iterations)
            await self.activate_goal_members(goal.goal_id)
            self.activated = self.session.mode == 'execute'
            self.resume_waiting = False
            parent = (self.session._task_context if self.session._main_running else None) or self.session.tasks.new_parent(description,
                limit=goal.budget_limit, mode=self.session.mode, generation=self.session.generation)
            parent.wake_allowed = bool(self.session._main_running)
            self._bind_goal_budget(parent)
            await self.checkpoint_goal_budget()
            return goal.to_dict()
        raise ValueError('未知团队操作')

    def board(self):
        return TaskBoard(self.store, self.scope.team_name)

    def inbox(self):
        return Mailbox(self.store, self.scope.team_name, notify=self.notify_member)

    async def task(self, args):
        team = self.require_participant()
        board, actor, action = self.board(), self.scope.member_id, args['action']
        identity = args.get('task_id')
        if action == 'list':
            return [item.to_dict() for item in await board.list(actor, goal_id=args.get('goal_id'))]
        if action == 'get':
            result = await board.get(actor, identity)
        elif action == 'create':
            result = await board.create(actor, args.get('goal_id') or team.active_goal_id, args['title'],
                args.get('description',''), depends_on=args.get('dependencies'), code=args.get('code', True),
                budget_limit=self.session.agent.max_iterations)
        elif action == 'update':
            changes = {key:args[key] for key in ('title','description') if key in args}
            if 'dependencies' in args:
                changes['depends_on'] = args['dependencies']
            result = await board.update(actor, identity, args['expected_revision'], **changes)
        elif action == 'delete':
            await board.delete(actor, identity, args['expected_revision'])
            return {'deleted':identity}
        elif action == 'claim':
            result = await board.claim(actor, identity)
        elif action == 'start':
            if self.runtime is None:
                raise ValueError('任务开工需要实际成员工作根')
            result = await self.runtime.start_task(identity, args['claim_id'])
        elif action == 'submit':
            result = await board.submit(actor, identity, args['claim_id'], args['result'])
        elif action in {'accept','reject'}:
            self.require_participant(lead=True)
            task=await board.get(actor,identity)
            if action=='accept' and task.code:
                member=team.members[task.owner_id]
                if member.state not in {'idle','stopped'}:
                    raise ValueError('接纳代码前必须等待成员操作全部停止')
                validation=await self.validate_command(Path(member.workspace_root),args.get('validation_command'))
                if not validation['ok']:
                    await board.reject(actor,identity,args['expected_revision'],reason='Lead 本次独立验证失败')
                    raise ToolError('team_validation_failed','代码验证失败，成果未被接纳',not_started=False)
                from .integration import IntegrationService
                goal=team.goals[task.goal_id]
                integration=IntegrationService(Path(team.workspace_root),self.store.team_path(team.name)/'integration',
                    goal.goal_id,goal.baseline_commit,goal.target_branch,current_mode=lambda:self.session.mode,
                    authorize=lambda root,args:self.authorize_git(root,*args))
                await integration.initialize()
                await integration.register_input(identity,Path(member.workspace_root),member.branch,
                                                 task.result['commit'],accepted=True)
            result = await getattr(board, action)(actor, identity, args['expected_revision'], reason=args['reason'])
        elif action == 'reassign':
            self.require_participant(lead=True)
            previous = await board.get(actor, identity)
            board._actor(team, actor, lead=True, mutation=True)
            board._actor(team, args['member_id'])
            board._goal(team, previous)
            if previous.state in {'accepted', 'integrated', 'completed'}:
                raise ValueError('已验收成果不能重新指派')
            old = team.members.get(previous.owner_id)
            target=team.members[args['member_id']]
            # 两把真实租约只确认已停止；新 claim 发布成功之前不解除审核。
            review = {member.member_id: member for member in (old, target) if member and member.state == 'needs_review'}
            with ExitStack() as leases:
                for member in review.values():
                    if member.member_id in self.runners or member.member_id in self.handles:
                        await self.stop_member(member.member_id, resumable=True)
                    current = self.store.load(team.name).members[member.member_id]
                    leases.enter_context(self.store.reviewed_member(team.name, current))
                team = self.store.load(team.name)
                old = team.members.get(previous.owner_id)
                target = team.members[args['member_id']]
                if target.state=='stopped':
                    runner=self.runners.get(target.member_id)
                    if runner and (runner.busy or not runner._closed or not runner.task.done()):
                        raise ValueError('目标成员收尾未确认，不能重新指派')
                    if target.member_id in self.handles:
                        await self.stop_member(target.member_id,resumable=True)
                    lease=self.store.member_lease(team.name,target.member_id)
                    leases.callback(lease.close)
                result = await board.reassign(actor, identity, args['member_id'], expected_revision=previous.revision,
                    old_run_stopped=old is None or old.member_id in review
                        or old.state in {'idle','stopped','blocked','failed','registered'})
        else:
            raise ValueError('未知任务动作')
        if action in {'claim','reassign'}:
            def bind(candidate):
                if action=='reassign':
                    for identity in review:
                        if candidate.members[identity].state=='needs_review':
                            candidate.members[identity].state='stopped'
                member = candidate.members[result.owner_id]
                member.task_id, member.claim_id = result.task_id, result.claim_id
                member.plan_id, member.plan_version, member.plan_approved = None, 0, False
                if action=='reassign' and member.state=='stopped':
                    member.state,member.resume_allowed='idle',True
            await self.store.update_team(team.name, bind)
            runner=self.runners.get(result.owner_id)
            if runner and runner._closed and runner.task.done():
                del self.runners[result.owner_id]
            if self.runtime and result.owner_id == actor:
                self.runtime.control.bind_task(result.task_id, result.claim_id)
                self.runtime.save_control()
                self.runtime.restore_budget()
        if action == 'submit' and self.runtime:
            await self.inbox().send(actor, team.lead_id, result.result['summary'], type='result_submitted',
                fields={'goal_id':result.goal_id,'task_id':identity,'claim_id':args['claim_id'],
                        'run_id':self.runtime.run_id},call_id=f'result:{identity}:{args["claim_id"]}:{result.revision}')
        return result.to_dict()

    async def message(self, args, *, application_call_id=None):
        team = self.require_participant()
        actor, action = self.scope.member_id, args['action']
        if action == 'read':
            return [message.to_dict() for message in await self.inbox().read(actor)]
        if self.scope.lead and self.session.mode == 'execute':
            self.activated = True
        kind, fields = args.get('type','text'), args.get('fields',{})
        if kind == 'plan_request':
            if self.runtime is None:
                raise ValueError('计划请求需要实际成员运行')
            fields = await self.runtime.propose(args.get('body',''))
        if kind == 'plan_decision':
            self.require_participant(lead=True)
        if action == 'send':
            delivery = await self.inbox().send(actor,args['recipient'],args.get('body',''),
                type=kind,fields=fields,call_id=application_call_id or args.get('call_id'))
            self.store.write_json(team.name,f'receipt-{delivery.message_id}.json',delivery.to_dict())
            return delivery.to_dict()
        if kind != 'text' or fields:
            raise ValueError('广播只发送普通正文；结构化决定需要逐一匹配接收者')
        deliveries = await self.inbox().broadcast(actor,args.get('body',''),broadcast_id=args['broadcast_id'])
        for delivery in deliveries:
            if delivery.message_id:
                self.store.write_json(team.name,f'receipt-{delivery.message_id}.json',delivery.to_dict())
        return [delivery.to_dict() for delivery in deliveries]

    async def ensure_receiver(self,recipient):
        if not self.scope or not self.scope.lead or self.closed:
            return
        team=self.store.load(self.scope.team_name)
        if not self.activated and team.mode != 'plan':
            return
        member=team.members.get(recipient) or next((m for m in team.members.values() if m.name==recipient),None)
        if member and member.member_id!=team.lead_id and member.state=='idle' and member.resume_allowed:
            async with self.wake_locks.setdefault(member.member_id,asyncio.Lock()):
                if member.member_id not in self.runners and member.member_id not in self.handles:
                    from .runtime import launch_member
                    definition=self.store.read_json(team.name,f'members/{member.member_id}/definition.json')
                    await launch_member(self,member,definition)

    async def publish_mode(self, mode):
        """实际停止旧实例后发布可信上限；规划只启用已登记成员的消息接收。"""
        if self.scope is None or not self.scope.lead:
            return
        team=self.require_participant(lead=True)
        if team.status=='needs_review':
            raise ValueError('团队仍有待核查操作，不能通过模式切换重新启动')
        leases=[]
        try:
            def publish(candidate):
                candidate.mode=mode
                candidate.status='active' if mode=='plan' else 'paused'
            await self.store.update_team(team.name,publish)
            if mode=='plan':
                identities=[]
                for member in team.members.values():
                    if member.member_id!=team.lead_id and member.state=='stopped' and member.resume_allowed:
                        leases.append(self.store.member_lease(team.name,member.member_id))
                        identities.append(member.member_id)
                def receive(candidate):
                    for identity in identities:
                        candidate.members[identity].state='idle'
                await self.store.update_team(team.name,receive)
        finally:
            for lease in leases:
                lease.close()

    async def member(self, args):
        team = self.require_participant(lead=True)
        action = args['action']
        if action == 'status':
            return [m.to_dict() for m in team.members.values()]
        if action == 'stop':
            identity=args['member']
            if identity not in team.members:
                identity=next((m.member_id for m in team.members.values() if m.name==identity),identity)
            await self.stop_member(identity)
            return {'member_id':identity, 'state':'stopped'}
        from .runtime import spawn_member
        return (await spawn_member(self, args)).to_dict()

    async def notify_member(self, identity):
        if self.scope and identity == self.scope.member_id:
            self.changed.set()
            self.session.tasks.changed.set()
            return
        await self.ensure_receiver(identity)
        runner = self.runners.get(identity)
        if runner:
            runner.wake.set()
            return
        handle = self.handles.get(identity)
        if handle:
            await self.tmux.notify(handle)
            return
        # 独立成员由保存的受管窗格地址直接唤醒；Lead 不作为中转。
        team = self.store.load(self.scope.team_name)
        member = team.members[identity]
        if member.backend == 'tmux':
            from .backends import notify_peer
            from .runtime import observe_worker
            from .worker import _private_json, load_startup
            startup = self.store.member_path(team.name, identity) / 'startup.json'
            # 独立登记和当前花名册先确认代次，实际 socket/pane 再由通知后端核对。
            registered = _private_json(startup.parent / 'registration.json')
            record = load_startup(startup, Path(registered['config_path']), require_starting=False)
            if record.handle is None:
                raise ValueError('目标成员尚无可信窗格身份')
            await notify_peer(record.handle,observe=lambda launch:observe_worker(self,launch))

    async def stop_member(self, identity, *, resumable=False):
        team = self.require_participant(lead=True)
        member = team.members[identity]
        if identity == team.lead_id:
            raise ValueError('Lead 请使用暂停入口')
        if identity in self.runners:
            await self.runners[identity].stop()
            del self.runners[identity]
        elif identity in self.handles:
            async def request_stop(launch):
                await self.inbox().send(team.lead_id, identity, '停止成员运行', type='shutdown_request',
                    fields={'generation':member.generation})
            report = await self.tmux.stop(self.handles[identity], request_stop=request_stop if member.state!='stopped' else None)
            if not report.confirmed:
                raise ValueError('成员停止尚未确认，需核查实际进程')
            del self.handles[identity]
        elif member.state == 'needs_review':
            with self.store.reviewed_member(team.name, member):
                pass
        elif member.state not in {'stopped','registered','idle','blocked','failed'}:
            raise ValueError('成员未知运行不能冒充停止')
        else:
            lease=self.store.member_lease(team.name,identity)
            lease.close()
        def stopped(candidate):
            candidate.members[identity].state='needs_review' if member.state=='needs_review' else 'stopped'
            candidate.members[identity].resume_allowed=resumable
        await self.store.update_team(team.name,stopped)

    async def pause(self):
        if self.scope is None or not self.scope.lead or self.closed:
            return
        self.closed = True
        if self.goal_parent:
            self.goal_parent.wake_allowed = False
        if self.monitor and self.monitor is not asyncio.current_task():
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        unknown = []
        for identity in list(self.runners.keys() | self.handles.keys()):
            try:
                await self.stop_member(identity,resumable=True)
            except (OSError, ValueError, RuntimeError):
                unknown.append(identity)
        self.session._checkpoint(self.session.history, self.session.context.state())
        await self.checkpoint_goal_budget()
        self.pause_unknown = unknown
        if not await self.close_backend():
            self.pause_unknown.extend(m.member_id for m in self.store.load(self.scope.team_name).members.values()
                                      if m.backend=='tmux' and m.member_id!=self.scope.member_id and m.member_id not in unknown)
        self.activated = False
        if getattr(self.session.agent, 'active_interaction', None):
            # 当前 pause 工具的真实结果和整个批次尚未提交，暂不关闭存档。
            self.pause_deferred = True
            await self.store.update_team(self.scope.team_name,
                lambda team: setattr(team, 'status', 'paused') if team.status != 'needs_review' else None)
        else:
            await self.finish_pause()
        self.changed.set()

    async def finish_pause(self):
        if not self.scope or not self.scope.lead:
            return
        await self.store.pause(self.scope.team_name, unknown_members=self.pause_unknown)
        self.detach_lead()
        self.pause_deferred = False
        self.session.team_scope = None

    async def watch(self):
        while not self.closed:
            self.changed.clear()
            try:
                messages = await self.inbox().read(self.scope.member_id)
                if any(m.message_id not in self.consumed_ids for m in messages):
                    self.pending = [m.message_id for m in messages if m.message_id not in self.consumed_ids]
                    self.session.tasks.changed.set()
                team=self.store.load(self.scope.team_name)
                if self.activated and team.status=='active' and self.session.mode=='execute':
                    for member in team.members.values():
                        if member.member_id!=team.lead_id and member.state=='idle' and member.resume_allowed and member.member_id not in self.runners and member.member_id not in self.handles:
                            if await self.inbox().read(member.member_id):
                                await self.ensure_receiver(member.member_id)
                await asyncio.wait_for(self.changed.wait(), .25)
            except TimeoutError:
                pass
            except (OSError, ValueError) as error:
                self._notify(f'团队邮箱读取失败：{error}')
                return

    async def consume_lead(self):
        if not self.scope or not self.scope.lead or self.closed:
            return
        from ..types import Message
        await self.inbox().reconcile(self.scope.member_id, self.consumed_ids)
        messages = await self.inbox().read(self.scope.member_id)
        new = [m for m in messages if m.message_id not in self.consumed_ids]
        if new:
            candidate = list(self.session.history)
            for message in new:
                if not any(item.id == 'mail-' + message.message_id for item in candidate):
                    candidate.append(Message('context', json.dumps(message.to_dict(), ensure_ascii=False),
                        id='mail-' + message.message_id, context_kind='task_resume'))
            parent = (self.session._task_context if self.session._main_running
                      else self.goal_parent or self.session._task_context)
            # 锁等待后再核查本次父取消；历史提交前新信件保持未消费。
            # 开头的 reconcile 只确认此前持久提交过的消费身份。
            if parent and parent.cancel.is_set():
                return
            self.session._checkpoint(candidate, self.session.context.state())
            self.session.history[:] = candidate
            self.consumed_ids.update(message.message_id for message in new)
        self.pending = []
        await self.inbox().ack(self.scope.member_id, self.consumed_ids)

    async def goal_pending(self, parent):
        if self.scope is None or not self.scope.lead or self.closed or self.goal_parent is not parent:
            return False
        team = self.store.load(self.scope.team_name)
        goal = team.goals.get(team.active_goal_id)
        return goal is not None and goal.state == 'active'

    async def cancel_goal(self):
        if self.scope is None or not self.scope.lead:
            return
        team = self.store.load(self.scope.team_name)
        identity = team.active_goal_id
        # 先实际停止运行并保存原预算，再提交取消终态；下一目标显式新建。
        await self.quiesce()
        if identity and team.goals[identity].state == 'active':
            await self.store.update_team(team.name, lambda candidate: setattr(candidate.goals[identity], 'state', 'cancelled'))
            self.checkpoint_lead()

    async def activate_goal_members(self, goal_id):
        """新目标只激活已确认停止的既有成员，不重建上下文或工作目录。"""
        team = self.store.load(self.scope.team_name)
        leases = []
        identities = []
        try:
            for member in team.members.values():
                if member.member_id != team.lead_id and member.active and member.resume_allowed and member.state == 'stopped':
                    leases.append(self.store.member_lease(team.name, member.member_id))
                    identities.append(member.member_id)
            def activate(candidate):
                if candidate.active_goal_id != goal_id:
                    raise ValueError('新目标绑定已改变')
                for identity in identities:
                    member = candidate.members[identity]
                    if member.state == 'stopped' and member.resume_allowed:
                        member.state = 'idle'
                # 旧领取与计划保留在任务及成员历史，不作为新目标的执行许可。
                for member in candidate.members.values():
                    if member.member_id != candidate.lead_id and member.state != 'needs_review':
                        member.task_id, member.claim_id = None, None
                        member.plan_id, member.plan_version, member.plan_approved = None, 0, False
            await self.store.update_team(team.name, activate)
        finally:
            for lease in leases:
                lease.close()

    async def quiesce(self):
        if self.scope is None or not self.scope.lead:
            return
        self.activated = False
        if self.goal_parent:
            self.goal_parent.wake_allowed = False
        for identity in list(self.runners.keys() | self.handles.keys()):
            await self.stop_member(identity, resumable=True)
        if not await self.close_backend():
            raise ValueError('成员后端资源停止尚未确认，需核查')
        self.session._checkpoint(self.session.history, self.session.context.state())
        await self.checkpoint_goal_budget()
        await self.store.update_team(self.scope.team_name,
            lambda team: setattr(team, 'status', 'paused') if team.status != 'needs_review' else None)

    async def close_backend(self):
        if not hasattr(self,'tmux') or not getattr(self.tmux,'probed',True):
            return True
        report=await self.tmux.close()
        if not report.confirmed:
            self._notify(f'团队后端停止待核查：{report.reason}')
        return report.confirmed

    async def integrate(self, args):
        from .integration import IntegrationService
        team = self.require_participant(lead=True)
        goal = team.goals.get(team.active_goal_id)
        if goal is None:
            raise ValueError('尚未建立活动目标')
        service = IntegrationService(Path(team.workspace_root), self.store.team_path(team.name)/'integration',
            goal.goal_id, goal.baseline_commit, goal.target_branch, current_mode=lambda:self.session.mode,
            authorize=lambda root,args:self.authorize_git(root,*args))
        action = args['action']
        if action == 'status':
            return await service.status() if service.record_path.exists() else {'state':'not_initialized'}
        await service.initialize()
        await self.reconcile_integration(service)
        async def validate(root,commit):
            return await self.validate_command(root,args.get('validation_command'))
        if action == 'integrate':
            task = await self.board().get(team.lead_id,args['task_id'])
            member = team.members[task.owner_id]
            if member.state not in {'idle','stopped'}:
                raise ValueError('成员仍在运行，不能整合')
            op = await service.integrate(task.task_id,Path(member.workspace_root),member.branch,
                task.result['commit'],accepted=task.state=='accepted',validate=validate,member_id=member.member_id)
            if op['state'] == 'published':
                await self.board().integrate(team.lead_id,task.task_id,task.revision,commit=op['result_head'])
            return op
        if action == 'rollback':
            status=await service.status()
            operation=next(op for op in status['operation_records'] if op['operation_id']==args['operation_id'])
            if operation.get('conflict_owner'):
                merge_head=__import__('mewcode.worktrees.paths',fromlist=['git_read']).git_read(
                    service.worktree_root,'rev-parse','--path-format=absolute','--git-path','MERGE_HEAD')
                command=('merge','--abort') if Path(merge_head).exists() else ('reset','--hard',operation['pre_head'])
                await self.authorize_git(service.worktree_root,*command)
                identity=operation['conflict_owner']
                await self.stop_member(identity)
                await service.release_conflict(args['operation_id'],identity,operation['conflict_token'])
            return await service.rollback(args['operation_id'])
        if action == 'sync':
            member = team.members[args['member_id']]
            if member.state not in {'idle','stopped','registered','blocked'}:
                raise ValueError('同步必须等待成员工具全部停止')
            state = await service.status()
            return await service.sync_member(Path(member.workspace_root),member.branch,state['head'])
        if action == 'handoff':
            identity=args['member_id']
            member=team.members[identity]
            if identity==team.lead_id or member.state not in {'idle','stopped','registered','blocked'}:
                raise ValueError('冲突只能交接给已收尾的普通成员')
            if member.state=='stopped' and not member.resume_allowed:
                raise ValueError('成员已显式终止，不能隐式恢复')
            task=await self.board().create(team.lead_id,goal.goal_id,'解决整合冲突',
                description=f'处理操作 {args["operation_id"]} 的冲突，验证并提交合并后交由 Lead 验收',
                code=False,budget_limit=self.session.agent.max_iterations)
            assigned=await self.task({'action':'reassign','task_id':task.task_id,'member_id':identity})
            grant=await service.handoff_conflict(args['operation_id'],identity)
            grant={'version':1,'goal_id':goal.goal_id,'task_id':task.task_id,'claim_id':assigned['claim_id'],**grant}
            self.store.write_json(team.name,f'members/{identity}/conflict.json',grant)
            delivery=await self.inbox().send(team.lead_id,identity,
                f'处理受管整合目录中的冲突：{grant["conflict_files"]}。本次运行自动取得目录租约；先 start 任务，完成合并提交和验证后 submit 非代码证据，等待 Lead finish_conflict。',
                type='task_assignment',fields={'goal_id':goal.goal_id,'task_id':task.task_id,'claim_id':assigned['claim_id']},
                call_id=f'conflict:{args["operation_id"]}:{assigned["claim_id"]}')
            return {**grant,'delivery':delivery.to_dict()}
        if action == 'finish_conflict':
            member=team.members[args['member_id']]
            if member.state not in {'idle','stopped'}:
                raise ValueError('冲突成员实际动作尚未收尾')
            grant=self.store.read_json(team.name,f'members/{member.member_id}/conflict.json')
            if (grant['operation_id'],grant['member_id'],grant['token'])!=(args['operation_id'],member.member_id,args['token']):
                raise ValueError('冲突交接身份不匹配')
            resolution=await self.board().get(team.lead_id,grant['task_id'])
            if resolution.state!='completed' or resolution.claim_id!=grant['claim_id']:
                raise ValueError('冲突处理任务尚未明确验收')
            result=await service.finish_conflict(args['operation_id'],args['member_id'],args['token'],validate=validate)
            if result['state']=='published':
                original=await self.board().get(team.lead_id,result['task_id'])
                await self.board().integrate(team.lead_id,original.task_id,original.revision,commit=result['result_head'])
            return result
        if action == 'finalize':
            state = await service.status()
            tasks = await self.board().list(team.lead_id,goal_id=goal.goal_id)
            required = [{'task_id':t.task_id,'accepted':t.state in {'accepted','integrated','completed'},
                         'code':t.code,'evidence':t.result.get('evidence'),
                         'operation_id':next((op['operation_id'] for op in state['operation_records']
                           if op.get('task_id')==t.task_id and op['state']=='published'),None)} for t in tasks]
            result = await service.finalize(required,validate=validate)
            if result.get('state') == 'published':
                for task in tasks:
                    if task.state=='integrated':
                        await self.board().complete(team.lead_id,task.task_id,task.revision)
                await self.store.update_team(team.name,lambda candidate:setattr(candidate.goals[goal.goal_id],'state','completed'))
            return result
        raise ValueError('未知整合动作')

    async def reconcile_integration(self, service):
        """发布证据与任务快照对账；不重新执行 Git 合并或覆盖用户内容。"""
        team=self.require_participant(lead=True)
        state=await service.status()
        active=next((op for op in state['operation_records'] if op['operation_id']==state.get('active_operation')),None)
        if (state['state'] in {'integrating','syncing','needs_review'} or
                (active and active['state']=='published')):
            await service.recover()
            state=await service.status()
        for op in state['operation_records']:
            if op['kind']!='integrate' or op['state']!='published' or not op.get('validation',{}).get('ok'):
                continue
            task=await self.board().get(team.lead_id,op['task_id'])
            if task.state=='accepted':
                await self.board().integrate(team.lead_id,task.task_id,task.revision,commit=op['result_head'])
            elif task.state in {'integrated','completed'} and task.integrated_commit!=op['result_head']:
                raise ValueError('已发布整合提交与任务证据不一致，需核查')
        if state['state']=='published':
            tasks=await self.board().list(team.lead_id,goal_id=service.goal_id)
            if any(t.state not in {'integrated','completed'} for t in tasks):
                raise ValueError('最终发布与任务接纳状态不一致，需核查')
            for task in tasks:
                if task.state=='integrated':
                    await self.board().complete(team.lead_id,task.task_id,task.revision)
            await self.store.update_team(team.name,lambda candidate:setattr(candidate.goals[service.goal_id],'state','completed'))
        return state

    async def recover_team_integration(self):
        team=self.require_participant(lead=True)
        goal=team.goals.get(team.active_goal_id)
        if not goal:
            return
        from .integration import IntegrationService
        service=IntegrationService(Path(team.workspace_root),self.store.team_path(team.name)/'integration',
            goal.goal_id,goal.baseline_commit,goal.target_branch,
            authorize=lambda root,args:self.authorize_git(root,*args))
        if service.record_path.exists():
            await service.recover()
            state=await self.reconcile_integration(service)
            if state['state']=='needs_review':
                await self.store.update_team(team.name,lambda candidate:setattr(candidate,'status','needs_review'))
                raise ValueError('团队整合存在未确认 Git 或外部副作用，恢复保持空闲且需人工核查')

    async def validate_command(self,root,command):
        if not command:
            raise ToolError('team_validation_required','代码验收和整合必须提供明确 validation_command',not_started=True)
        from ..tools.base import ToolContext
        from ..tools.executor import ToolExecutor
        permissions=self.session.permissions.fork(root=root)
        executor=ToolExecutor(self.session.executor.registry,ToolContext(root),permissions=permissions)
        result=await executor.execute('execute_command',json.dumps({'command':command}),
                                     allowed_tools=frozenset({'execute_command'}))
        return {'ok':result.ok and result.data.get('exit_code')==0,'evidence':result.to_dict()}

    async def authorize_git(self,root,*args):
        """内部 Git 也按实际 cwd 与本次父策略判断；不搬迁会话授权。"""
        import shlex
        # 受管命令关闭 Hook 的固定参数不改变用户对 Git 子命令的规则。
        canonical=[]
        index=0
        while index<len(args):
            if args[index]=='-c' and index+1<len(args) and args[index+1]=='core.hooksPath=/dev/null':
                index+=2
            else:
                canonical.append(args[index])
                index+=1
        permissions=self.session.permissions.fork(root=Path(root))
        permissions.authorize_noninteractive('execute_command',{'command':shlex.join(['git',*canonical])})
