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

    def reset_session(self):
        self.session.reset()
        self.terminal.state.reset_task()

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
