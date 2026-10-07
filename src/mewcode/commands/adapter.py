"""应用能力适配；命令定义无需依赖渲染框架。"""
from ..memory.store import MemoryStore
from ..sessions import scan_sessions
from ..terminal.text import terminal_text
from ..tools.base import ToolError


class SessionCommandContext:
    def __init__(self, registry, session, terminal, renderer, config):
        self.registry, self.session, self.terminal = registry, session, terminal
        self.renderer, self.config = renderer, config
        self.cancel_event = None

    @property
    def enhanced(self):
        return self.terminal.enhanced

    def show_message(self, text):
        self.renderer.line('本地> ' + terminal_text(text, self.config.api_key, multiline=True, limit=None))

    async def clear_screen(self):
        await self.terminal.clear_screen()

    async def set_mode(self, mode):
        await self.session.set_mode(mode, cancel_event=self.cancel_event)

    def refresh_status(self):
        self.terminal.sync_session(self.session)

    def status_text(self):
        return self.terminal.state.status_text(root=self.session.executor.context.root, model=self.config.model,
            mode=self.session.mode, permission_mode=self.session.permissions.mode) + '\n' + self.session.context_status()

    def permission_text(self, args):
        try:
            return self.session.permission_command('/permissions' + (' ' + args if args else ''))
        except ToolError as error:
            raise ValueError(str(error)) from error

    def skills_text(self, args):
        try:
            return self.session.skills_text(args)
        except ToolError as error:
            raise ValueError(str(error)) from error

    async def reset_session(self):
        await self.session.reset_async()
        self.terminal.state.reset_task()

    def agents_text(self, args):
        try:
            return self.session.agents_text(args)
        except ToolError as error:
            raise ValueError(str(error)) from error

    async def tasks_text(self, args):
        try:
            return await self.session.tasks_text(args)
        except ToolError as error:
            raise ValueError(str(error)) from error

    async def team_text(self, action, name=None):
        """本地命令仍经过运行身份和模式门禁，查看不创建执行对象。"""
        tool = 'team_member' if action == 'stop' else 'team'
        arguments = {'action': action}
        if name is not None:
            arguments['member' if action == 'stop' else 'name'] = name
        try:
            self.session.teams.guard(tool, arguments)
            result = await (self.session.teams.member(arguments) if action == 'stop'
                            else self.session.teams.control(arguments))
        except (ToolError, KeyError, RuntimeError) as error:
            raise ValueError(str(error)) from error
        if action == 'list':
            lines = ['团队名称 · 状态 · 活动目标 · 成员数']
            lines.extend(f'{team["name"]} · {team["status"]} · {team.get("active_goal_id") or "尚无"} · '
                         f'{len(team["members"])}' for team in result)
            if not result:
                lines.append('没有团队；/team create <name> 创建。')
            return '\n'.join(lines)
        if action == 'pause':
            if result['state'] == 'needs_review':
                lines = ['团队> 停止待核查，成员与成果保留；不能重复接管未知运行。']
                lines.extend(f'待核查成员> {member["member_id"]} · 代次 {member["generation"]} · '
                             f'backend {member["backend"]} · pane {member.get("pane") or "未确认"}'
                             for member in result.get('members', []))
                return '\n'.join(lines)
            return '团队> 已暂停，成员与成果保留；/team resume <name> 显式恢复。'
        if action == 'stop':
            return f'成员> {result["member_id"]} · {result["state"]}（已停止，成果保留）'
        team = result.get('team') if action == 'status' else result
        if team is None:
            return '团队> 当前会话未绑定团队；/team list 查看，/team create 或 resume 显式绑定。'
        lines = [f'团队> {team["name"]} · {team["status"]} · {team["team_id"]}',
                 f'仓库> {team["repository"]} · 项目根 {team["workspace_root"]}',
                 f'Lead> {team["lead_id"]}',
                 f'目标> {team.get("active_goal_id") or "尚无活动目标"}']
        if action == 'status':
            lines.append(f'coordinator> {"开启" if result["coordinator"] else "关闭"} · '
                         f'配置 {"开启" if result.get("coordinator_capability") else "关闭"} · '
                         f'环境开关 {"满足" if result.get("coordinator_environment") else "未满足"}')
        states = {'registered': '已登记', 'idle': '空闲', 'planning': '规划', 'awaiting_approval': '等待计划批准',
                  'running': '执行中', 'stopped': '已停止', 'blocked': '阻塞', 'needs_review': '待核查'}
        for member in team['members'].values():
            state = member['state']
            approval = ('已批准' if member.get('plan_approved') else '需要批准') if member['require_plan_approval'] else '无需计划批准'
            lines.append(f'成员> {member["name"]} [{member["member_id"]}] · backend {member["backend"]} · '
                         f'{state}（{states.get(state, state)}） · {approval}')
            lines.append(f'工作根> {member["workspace_root"]} · 分支 {member.get("branch") or "尚无"}')
            if action == 'status':
                plan = result.get('plans', {}).get(member['member_id'], {})
                if plan.get('plan_id'):
                    lines.append(f'计划> {member["member_id"]} · 任务 {plan.get("task_id") or "尚无"} · '
                                 f'{plan["plan_id"]} · 版本 {plan["plan_version"]} · '
                                 f'{"已批准" if plan.get("approved") else "尚未批准"}')
                    # 展示匹配的结构化决定记录，不从正文推断或修改实际批准。
                    decisions = [message for message in result.get('messages', [])
                        if message.get('type') == 'plan_decision' and message.get('sender_id') == team['lead_id']
                        and message.get('recipient_id') == member['member_id']
                        and all(message.get('fields', {}).get(key) == value for key, value in {
                            'task_id': member.get('task_id'), 'claim_id': member.get('claim_id'),
                            'plan_id': plan['plan_id'], 'plan_version': plan['plan_version']}.items())]
                    if decisions:
                        decision = decisions[-1]
                        lines.append(f'计划决定记录> {"批准" if decision["fields"]["approved"] else "拒绝"} · '
                                     f'理由 {decision.get("body") or "未提供理由"}（生效以控制状态为准）')
                    elif plan.get('reason'):
                        lines.append(f'计划理由> {plan["reason"]}')
                    else:
                        lines.append('计划理由> 没有匹配的结构化决定记录')
        if action == 'status':
            tasks = result.get('tasks', [])
            by_id = {task['task_id']: task for task in tasks}
            for task in tasks:
                blocked = [identity for identity in task.get('depends_on', [])
                    if identity not in by_id
                    or (by_id[identity].get('code') and
                        (by_id[identity]['state'] not in {'integrated', 'completed'}
                         or not by_id[identity].get('integrated_commit')))
                    or (not by_id[identity].get('code') and by_id[identity]['state'] != 'completed')]
                reason = task.get('reason') or ('前置成果未接纳／整合：' + ', '.join(blocked) if blocked else '无')
                label = '待验收' if task['state'] == 'submitted' else task['state']
                lines.append(f'任务> {task["task_id"]} · {task["title"]} · {label} · '
                             f'负责人 {task.get("owner_id") or "尚无"} · 阻塞／理由 {reason}')
            if not tasks:
                lines.append('任务> 尚无任务')
            messages = result.get('messages', [])
            for message in messages:
                wake = ('已确认通知' if message.get('notified') is True else
                        '唤醒失败' if message.get('notified') is False else '唤醒未确认')
                lines.append(f'邮箱> {message["message_id"]} · {message["sender_id"]} → {message["recipient_id"]} · '
                             f'{message["type"]} · {"已保存" if message.get("saved") else "保存未确认"} · '
                             f'{"已读" if message.get("read") else "未读"} · {wake} · '
                             f'{message.get("wake_error") or ""} · {message.get("summary") or ""}')
            if not messages:
                lines.append('邮箱> 尚无消息')
            integration = result.get('integration', {})
            lines.append(f'整合> {integration.get("state", "未知")} · head {integration.get("head") or "尚无"} · '
                         f'活动操作 {integration.get("active_operation") or "无"}')
            for operation in integration.get('operation_records', []):
                lines.append(f'整合操作> {operation["operation_id"]} · {operation["state"]} · '
                             f'结果 {operation.get("result_head") or "尚无"} · '
                             f'{operation.get("failure") or operation.get("rollback", {}).get("reason") or ""}')
        if action in {'create', 'resume'}:
            lines.append('团队保持空闲，等待明确目标或指派；查看不唤醒成员。')
        return '\n'.join(lines)

    def session_text(self, *, listing):
        if not listing:
            count = sum(message.role != 'context' for message in self.session.history)
            mode = '[PLAN]' if self.session.mode == 'plan' else '[DEFAULT]'
            return (f'会话> {self.session.session_id or "未启用存档"} · '
                    f'{"显式恢复" if self.session.resumed else "新建"} · {count} 条工作消息\n'
                    f'模式> {mode}\n项目> {self.session.executor.context.root}')
        rows = scan_sessions(self.session.executor.context.root)
        lines = ['会话 ID · 标题 · 消息数 · 最后活动 · 状态']
        for row in rows:
            status = '活动中' if row.active else '可恢复' if row.recoverable else '不可恢复'
            lines.append(f'{row.id} · {row.title} · {row.message_count} · {row.last_activity.isoformat()} · {status}')
            lines.extend('提示> ' + warning for warning in row.warnings)
        if not rows:
            lines.append('没有会话存档')
        lines.append('恢复请重启并使用 --resume ID|latest；当前会话保持。')
        return '\n'.join(lines)

    def memory_text(self, action, scope=None, identity=None):
        stores = {'project': MemoryStore(self.session.executor.context.root),
                  'user': MemoryStore(self.session.user_root, scope='user')}
        if action == 'status':
            pending = sum(value in {'queued', 'running'} for value in self.session.memory_tasks.values())
            return (f'记忆> 自动维护：{"开启" if self.session.memory else "关闭"} · 待处理 {pending} · {self.session.memory_status}\n'
                    + '\n'.join(f'{name}> {store.root}' for name, store in stores.items()))
        lines = []
        for name in ((scope,) if scope else stores):
            store = stores[name]
            snapshot = store.query()
            lines.extend('提示> ' + diagnostic for diagnostic in store.diagnostics)
            if action == 'show':
                note = next((note for note in snapshot.notes if note.id == identity), None)
                if note is None:
                    lines.append(f'提示> 找不到合法笔记：{name}/{identity}')
                else:
                    lines.append(f'笔记> {name}/{note.id}\n' + note.encode().decode('utf-8'))
            else:
                lines.append(f'{name}> {store.root}')
                lines.extend(f'{name}/{note.id} · {note.category} · {note.summary} · {note.updated_at}' for note in snapshot.notes)
                if not snapshot.notes:
                    lines.append('没有笔记')
        return '\n'.join(lines)
