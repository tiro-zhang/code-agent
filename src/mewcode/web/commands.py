"""共享命令能力的网页呈现适配。"""

from ..commands.service import SessionCommandService


class WebCommandContext(SessionCommandService):
    enhanced = False

    def __init__(self, manager):
        super().__init__(manager.session, manager.config)
        self.manager = manager
        self.registry = manager.resources.registry
        self.cancel_event = manager.cancel

    def show_message(self, text):
        self.manager.projection.notice(text, self.manager.run_id or '')
        self.manager.publish()

    async def clear_screen(self):
        self.show_message('提示> /clear 是终端专属命令，在 Web 中不适用。')

    def refresh_status(self):
        self.manager.publish()
