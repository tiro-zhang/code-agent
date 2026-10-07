"""项目内唯一运行所有者；HTTP 连接与浏览视图均不拥有任务。"""

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from ..commands.dispatcher import dispatch
from ..commands.parser import parse_command
from ..runtime import RuntimeResources
from ..sessions.browse import ArchiveBrowser
from .commands import WebCommandContext
from .errors import WebError
from .projection import Projection


def alive(task):
    return task is not None and not task.done()


class WebManager:
    def __init__(self, config, *, root, config_path=None, provider_factory=None,
                 memory_enabled=True, user_root=None, permission_mode='default',
                 stop_timeout=30, resources_factory=RuntimeResources):
        from .approval import ApprovalBroker
        from .events import EventHub
        from .operations import OperationLedger
        from .security import Redactor
        self.config, self.root = config, Path(root).resolve()
        self.server_instance_id = uuid4().hex
        self.redactor = Redactor(config.api_key)
        self.resources_factory = resources_factory
        self.resource_options = dict(root=self.root, config_path=config_path,
            provider_factory=provider_factory, memory_enabled=memory_enabled,
            user_root=user_root, permission_mode=permission_mode)
        self.browser = ArchiveBrowser(self.root, secret=config.api_key)
        self.resources = None
        self._target_identity = None
        self._activating = False
        self.generation, self.state_version = 0, 0
        self.phase, self.reason, self.error = 'unloaded', '', ''
        self.run_id = None
        self.projection = Projection(redact=self.redactor.text)
        self.cancel = asyncio.Event()
        self._claimed = self.closing = self._closing_requested = False
        self._job = self._stop_job = self._watchdog = self._monitor = self._notices = None
        self._team_reasons = []
        self.stop_timeout = stop_timeout
        self.ledger = OperationLedger(self.server_instance_id)
        self.hub = EventHub(self.server_instance_id, snapshot=self.snapshot)
        self.approvals = ApprovalBroker(root=self.root, server_instance_id=self.server_instance_id,
            context=lambda: {'session_id': self.session.session_id if self.session else '',
                             'generation': self.generation, 'run_id': self.run_id or ''},
            notify=self._approval_changed, redact=self.redactor.text)
        # 只校验定义，构造阶段不打开 Provider 或存档。
        validation = resources_factory(config, **self.resource_options)
        self.registry = validation.validate()

    @property
    def session(self):
        return self.resources.session if self.resources else None

    def _approval_changed(self, event):
        if event.get('status') == 'expired':
            self.projection.notice('approval_expired：审批超过 10 分钟，已拒绝本次操作。', self.run_id or '')
        self.publish('approval')

    def activity_reasons(self):
        session = self.session
        if session is None:
            return []
        reasons = []
        for record in session.tasks.records.values():
            if record.state in {'queued', 'running'}:
                reasons.append('子任务运行或排队')
            elif alive(record.handle):
                reasons.append('子任务清理')
        if session.next_parent() is not None:
            reasons.append('后台结果待接续')
        memory = session.memory
        if memory and (alive(memory._worker) or not memory._queue.empty()):
            reasons.append('记忆维护')
        if session.hooks.background_count:
            reasons.append('后台 Hook')
        cleaner = session.worktree_cleaner
        if alive(cleaner._scan) or alive(cleaner._initializing):
            reasons.append('工作树维护')
        teams = session.teams
        for runner in teams.runners.values():
            if runner.busy or getattr(runner, 'revising', False):
                reasons.append('团队成员运行')
            if (runner.stop_requested and alive(runner.task)) or getattr(runner, '_close_failed', False):
                reasons.append('团队成员清理')
        if teams.pause_unknown:
            reasons.append('团队停止待核查')
        reasons.extend(self._team_reasons)
        if session.agent.storage_blocked:
            reasons.append('存档不可安全继续')
        return list(dict.fromkeys(reasons))

    def snapshot(self):
        activities = self.activity_reasons()
        busy = self._claimed or bool(activities) or self.closing
        phase = self.phase
        if phase == 'idle' and activities:
            phase = 'maintenance'
        approvals = self.approvals.summaries()
        if approvals and phase in {'running', 'background', 'maintenance'}:
            phase = 'awaiting_approval'
        return {'server_instance_id': self.server_instance_id, 'seq': self.hub.seq,
            'project': {'root': str(self.root), 'key': hashlib.sha256(str(self.root).encode()).hexdigest()[:24],
                        'model': self.config.model},
            'runtime': {'session_id': self._target_identity or (self.session.session_id if self.session else None),
                        'generation': self.generation, 'state_version': self.state_version,
                        'phase': phase, 'busy': busy, 'run_id': self.run_id,
                        'mode': self.session.mode if self.session else 'execute',
                        'permission_mode': self.session.permissions.mode if self.session else self.resource_options['permission_mode'],
                        'reason': self.reason, 'error': self.error, 'activities': activities},
            'projection': self.projection.snapshot(), 'approvals': approvals,
            'commands': [{**{key: getattr(command, key) for key in ('name', 'aliases', 'description', 'usage', 'kind')},
                          'kind': 'terminal' if command.name in {'clear', 'exit'} else command.kind}
                         for command in self.registry.definitions]}

    def publish(self, kind='state'):
        self.hub.publish(kind, {}, session_id=self.session.session_id if self.session else '',
                         runtime_generation=self.generation, run_id=self.run_id or '')

    def _event(self, event, generation):
        if generation != self.generation:
            return
        self.projection.event(event)
        # 投影先更新；增量只发布明确允许的身份和种类。正文由状态接口读取。
        self.hub.publish('agent', {'kind': event.kind, 'parent_run_id': event.parent_run_id,
                                  'run_id': event.run_id, 'purpose': event.purpose},
                         session_id=self.session.session_id if self.session else '',
                         runtime_generation=generation, run_id=event.run_id)

    def _check(self, generation, version=None, identity=None):
        if self.closing or self._closing_requested:
            raise WebError('server_closing', '服务正在关闭', 503)
        if generation != self.generation or version is not None and version != self.state_version:
            raise WebError('stale_state', '运行代次或状态已变化，请刷新状态后重试')
        current = self._target_identity or (self.session.session_id if self.session else None)
        if identity is not None and current != identity:
            raise WebError('session_not_active', '请先明确继续此会话')

    def _claim(self, phase):
        if self._claimed or self.activity_reasons():
            raise WebError('project_busy', '项目正被任务或维护占用，请等待完成或停止')
        self._claimed, self.phase = True, phase
        self.reason = self.error = ''
        self.state_version += 1
        self.cancel = asyncio.Event()
        self.publish()

    async def activate(self, identity, generation, version):
        self._check(generation, version)
        if self.session and self.session.session_id == identity and self.phase == 'idle' and not self._claimed:
            return 200, {'session_id': identity, 'handled': True}
        self._claim('preparing')
        self._target_identity = identity
        self._activating = True
        operation_id = uuid4().hex
        self._job = asyncio.create_task(self._activate(identity))
        return 202, {'operation_id': operation_id, 'session_id': identity}

    async def _stop_monitors(self):
        tasks = [task for task in (self._monitor, self._notices) if task is not None and task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._monitor = self._notices = None

    async def _activate(self, identity):
        try:
            history = await self.browser.history(identity)
            if self.cancel.is_set():
                return
            await self._stop_monitors()
            if self.resources:
                report = await self.resources.aclose(cancel_event=self.cancel, timeout=self.stop_timeout)
                if not report.complete:
                    self.phase, self.error = 'blocked', '旧运行会话关闭尚未确认；资源所有权保留'
                    return
                self.resources = None
            if self.cancel.is_set():
                return
            self.generation += 1
            generation = self.generation
            self.projection = Projection(identity, redact=self.redactor.text)
            self.projection.seed(history)
            self.resources = self.resources_factory(self.config, **self.resource_options, resume=identity,
                notify=lambda event: self._event(event, generation), approval_responder=self.approvals.respond)
            await self.resources.start(cancel_event=self.cancel)
            if self.cancel.is_set():
                report = await self.resources.aclose(timeout=self.stop_timeout)
                if report.complete:
                    self.resources = None
                else:
                    self.phase, self.error = 'blocked', '准备取消后的资源关闭尚未确认'
                return
            self.registry = self.resources.registry
            self._notices = asyncio.create_task(self.resources.background.notices(lambda event: self._event(event, generation)))
            await self._drain()
            self._monitor = asyncio.create_task(self._monitor_activity(generation))
        except Exception:
            self.error = '会话准备失败，请检查存档占用、配置和本机服务后重试'
            if self.resources:
                report = await self.resources.aclose(timeout=self.stop_timeout)
                if report.complete:
                    self.resources = None
                else:
                    self.phase = 'blocked'
        finally:
            self._activating = False
            self._target_identity = None
            self._release()

    async def _monitor_activity(self, generation):
        previous = []
        while generation == self.generation and not self.closing:
            await self._refresh_teams()
            reasons = self.activity_reasons()
            if not self._claimed and reasons:
                self._claimed = True
                self.phase = 'background'
                self.state_version += 1
                self._job = asyncio.create_task(self._drain_owned())
            if reasons != previous:
                self.publish()
                previous = reasons
            await asyncio.sleep(.05)

    async def _drain_owned(self):
        """监控器只登记执行；接续和停止共用唯一可等待的任务所有者。"""
        try:
            await self._drain()
        except Exception:
            self.phase, self.error = 'blocked', '后台活动未确认完成；执行槽保留'
        finally:
            self._release()

    async def _refresh_teams(self):
        teams = self.session.teams if self.session else None
        self._team_reasons = []
        if teams and teams.handles and teams.scope:
            try:
                team = await asyncio.to_thread(teams.store.load, teams.scope.team_name)
                if any(member.member_id in teams.handles and member.state not in {'registered', 'idle', 'stopped'}
                       for member in team.members.values()):
                    self._team_reasons = ['外部团队成员活动']
            except (ValueError, OSError):
                self._team_reasons = ['团队状态待核查']

    async def _drain(self):
        while self.session and not self.cancel.is_set():
            await self._refresh_teams()
            parent = self.session.next_parent()
            if parent:
                self.phase = 'background'
                self.publish()
                await self._consume(self.session.resume_parent(parent, cancel_event=self.cancel))
                continue
            reasons = self.activity_reasons()
            if not reasons:
                return
            if '存档不可安全继续' in reasons or '团队停止待核查' in reasons or '团队状态待核查' in reasons:
                self.phase = 'blocked'
                self.error = '资源状态待核查；保留执行槽'
                return
            self.phase = 'maintenance' if all('维护' in reason or 'Hook' in reason for reason in reasons) else 'background'
            self.publish()
            await asyncio.sleep(.05)

    def _release(self):
        if self.phase not in {'blocked', 'cancelling', 'closing'}:
            self._claimed = False
            self.phase = 'idle' if self.session else 'unloaded'
            self.run_id = None
            self.state_version += 1
        self.publish()

    async def submit(self, identity, text, generation, version):
        self._check(generation, version, identity)
        if not isinstance(text, str) or not text.strip():
            raise WebError('invalid_input', '输入不能为空', 422)
        if len(text.encode('utf-8')) > 256 * 1024:
            raise WebError('input_too_large', '输入超过 256 KiB；草稿已保留', 413)
        self._claim('running')
        self.run_id = uuid4().hex
        self.projection.input(text, self.run_id)
        self._job = asyncio.create_task(self._run_input(text))
        return 202, {'operation_id': self.run_id, 'run_id': self.run_id}

    async def _consume(self, source):
        known = {message.id for message in self.session.history}
        try:
            async for event in source:
                self._event(event, self.generation)
                if event.kind in {'progress', 'finished', 'task_finished'}:
                    self._reconcile_messages(known)
        finally:
            await source.aclose()

    def _reconcile_messages(self, known):
        """提交后的展示使用原消息身份；刷新历史不会再次追加同一回复。"""
        candidates = [item for item in self.projection.messages
                      if item['id'].startswith(('input:', 'answer:'))]
        for message in self.session.history:
            if message.id in known or message.role not in {'user', 'assistant'}:
                continue
            text = self.redactor.text(message.content)
            match = next((item for item in candidates if item['role'] == message.role
                          and (item['text'] == text or item.get('truncated') and text.startswith(item['text']))), None)
            if match is not None:
                self.projection.reconcile_message(match, message.id)
                candidates.remove(match)

    async def _run_input(self, text):
        try:
            self.session.tasks.paused = False
            registry, warnings = self.session.refresh_skills()
            if registry is not None:
                self.registry = self.resources.registry = registry
            for warning in warnings:
                self.projection.notice(warning)
            parsed = parse_command(text)
            if parsed.kind == 'command' and parsed.name in {'clear', 'exit'}:
                self.projection.notice(f'提示> /{parsed.name} 是终端专属命令，在 Web 中不适用。')
                return
            result = await dispatch(parsed, self.registry, WebCommandContext(self))
            if result.kind == 'message':
                await self._consume(self.session.ask(result.text, cancel_event=self.cancel))
            elif result.kind == 'skill':
                await self._consume(self.session.run_skill(result.skill_name, result.text, cancel_event=self.cancel, user_text=text))
            elif result.kind == 'summary':
                self.phase = 'maintenance'
                await self._consume(self.session.compact(cancel_event=self.cancel))
            await self._drain()
        except asyncio.CancelledError:
            self.cancel.set()
            self.reason = 'cancelled'
        except Exception:
            self.error = '运行未完成；请查看已保存结果和任务详情'
            self.projection.notice(self.error)
            await self._drain()
        finally:
            self._release()

    async def control(self, identity, action, value, generation, version):
        self._check(generation, None if action == 'stop' else version, identity)
        if action == 'stop':
            return await self.stop(identity, generation)
        commands = {'plan': '/plan', 'execute': '/do', 'compact': '/compact', 'reset': '/reset'}
        if action == 'permission_mode' and value in {'strict', 'default', 'bypass'}:
            command = f'/permission mode {value}'
        elif action == 'revoke' and value in {'session', 'permanent'}:
            command = f'/permission revoke {value}'
        elif action in commands:
            command = commands[action]
        else:
            raise WebError('invalid_control', '控制参数不合法', 422)
        return await self.submit(identity, command, generation, version)

    async def stop(self, identity, generation):
        self._check(generation, identity=identity)
        if alive(self._stop_job):
            return 202, {'handled': True, 'operation_id': 'stop:' + str(self.generation)}
        self._claimed, self.phase = True, 'cancelling'
        self.state_version += 1
        self.cancel.set()
        self.approvals.invalidate_all()
        if self.session:
            self.session.tasks.paused = True
            for parent in self.session.tasks.parents.values():
                parent.wake_allowed = False
                parent.cancel.set()
        self._stop_job = asyncio.create_task(self._stop_activity())
        self._watchdog = asyncio.create_task(self._stop_deadline(self._stop_job))
        self.publish()
        return 202, {'operation_id': 'stop:' + str(self.generation)}

    async def _stop_deadline(self, job):
        await asyncio.wait((job,), timeout=self.stop_timeout)
        if not job.done():
            self.phase = 'blocked'
            self.error = '停止仍在清理，副作用状态尚未确认；执行槽保留'
            self.publish()

    async def _stop_activity(self):
        try:
            # 激活负责完整切换；先等唯一所有者，避免与它并发替换资源引用。
            if self._activating and alive(self._job):
                await asyncio.shield(self._job)
            if self.resources and self.resources._close_task is not None:
                # 切换失败的旧对象已经进入关闭链，不能通过 Stop 重新启用。
                while True:
                    report = await self.resources.aclose(timeout=self.stop_timeout)
                    if report.complete:
                        self.resources = None
                        self.generation += 1
                        self._claimed = False
                        self.phase, self.reason, self.error = 'unloaded', 'cancelled', ''
                        self.state_version += 1
                        return
                    if not report.pending:
                        raise RuntimeError('旧资源关闭失败')
                    self.phase, self.error = 'blocked', '旧资源仍在关闭；执行槽保留'
                    self.publish()
            session = self.session
            if session is None and alive(self._job):
                await asyncio.shield(self._job)
                session = self.session
            if session is None:
                self.phase, self.reason, self.error = 'unloaded', 'cancelled', ''
                self._claimed = False
                self.state_version += 1
                return
            await session.hooks.quiesce(cancel_event=self.cancel)
            await session.worktree_cleaner.pause()
            await session.teams.pause()
            await asyncio.gather(*(session.tasks.cancel_parent(identity) for identity in session.tasks.parents))
            if alive(self._job):
                await asyncio.shield(self._job)
            await asyncio.gather(*(record.handle for record in session.tasks.records.values() if record.handle), return_exceptions=True)
            await session.hooks.quiesce(cancel_event=self.cancel)
            if session.memory:
                await session.memory.aclose(wait_seconds=0)
            if session.teams.pause_unknown or session.agent.storage_blocked:
                raise RuntimeError('停止待核查')
            if not self.closing:
                session.tasks.paused = False
                session.hooks.resume_mutations()
                if session.memory:
                    session.memory._accepting = True
                session.worktree_cleaner.resume()
                self.phase, self.reason, self.error = 'idle', 'cancelled', ''
                self._claimed = False
                self.state_version += 1
                self.run_id = None
        except Exception:
            self.phase, self.error = 'blocked', '停止尚未确认；保留执行槽和资源所有权'
        finally:
            self.publish()

    async def aclose(self):
        if self.closing:
            return
        self.closing, self._claimed, self.phase = True, True, 'closing'
        self.ledger.closed = True
        self.cancel.set()
        self.approvals.invalidate_all()
        self.publish()
        self.hub.close()
        await self._stop_monitors()
        async def finish():
            if self.ledger.tasks:
                await asyncio.gather(*self.ledger.tasks, return_exceptions=True)
            if self.session:
                self.session.tasks.paused = True
                for parent in self.session.tasks.parents.values():
                    parent.wake_allowed = False
                    parent.cancel.set()
            for task in (self._job, self._stop_job):
                if alive(task):
                    await asyncio.shield(task)
            if self.resources:
                return await self.resources.aclose(timeout=self.stop_timeout)
        closing = asyncio.create_task(finish())
        done, _ = await asyncio.wait((closing,), timeout=self.stop_timeout)
        if not done or done and closing.result() is not None and not closing.result().complete:
            self.phase, self.error = 'blocked', '服务关闭达到期限，未完成资源状态待核查'
            diagnostic = self.root / '.mewcode' / 'web-shutdown.json'
            await asyncio.to_thread(self._write_diagnostic, diagnostic)
        if alive(self._watchdog):
            self._watchdog.cancel()
            await asyncio.gather(self._watchdog, return_exceptions=True)

    def begin_shutdown(self):
        """信号处理入口先结束长连接，随后由 lifespan 等待资源收尾。"""
        self._closing_requested = True
        self.ledger.closed = True
        self.cancel.set()
        self.approvals.invalidate_all()
        self.publish()
        self.hub.close()

    def _write_diagnostic(self, path):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.is_symlink():
                path.write_text(json.dumps({'server_instance_id': self.server_instance_id,
                    'generation': self.generation, 'message': self.error}, ensure_ascii=False), encoding='utf-8')
        except OSError:
            pass
