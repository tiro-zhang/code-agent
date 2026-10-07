"""终端命令外观；业务校验和查询由公共服务承担。"""

from .service import SessionCommandService
from ..terminal.text import terminal_text


class SessionCommandContext(SessionCommandService):
    def __init__(self, registry, session, terminal, renderer, config):
        super().__init__(session, config)
        self.registry, self.terminal, self.renderer = registry, terminal, renderer

    @property
    def enhanced(self):
        return self.terminal.enhanced

    def show_message(self, text):
        self.renderer.line('本地> ' + terminal_text(text, self.config.api_key, multiline=True, limit=None))

    async def clear_screen(self):
        await self.terminal.clear_screen()

    def refresh_status(self):
        self.terminal.sync_session(self.session)

    def status_text(self):
        return self.terminal.state.status_text(root=self.session.executor.context.root, model=self.config.model,
            mode=self.session.mode, permission_mode=self.session.permissions.mode) + '\n' + self.session.context_status()

    async def reset_session(self):
        await super().reset_session()
        self.terminal.state.reset_task()
