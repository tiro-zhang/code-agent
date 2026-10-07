"""界面无关通知与后台父任务就绪观察；结果仍由会话确认消费。"""

import asyncio
from inspect import isawaitable

from ..types import AgentEvent


def notification_event(notification):
    """维护通知保持用途和来源，不混入当前工作任务的用量。"""
    kind = notification.get('kind')
    event_kind = ('usage' if kind in {'usage', 'restore_usage'} else
                  'team_update' if kind == 'team_update' else 'memory_update')
    return AgentEvent(event_kind,
        purpose=notification.get('purpose', 'team' if kind == 'team_update' else 'memory'),
        text=notification.get('text', ''), usage=notification.get('usage'),
        phase=notification.get('status', ''),
        run_id=notification.get('run_id', notification.get('task_id', '')),
        parent_run_id=notification.get('parent_run_id', notification.get('parent_task_id', '')))


def task_event(report):
    """后台提示只传递状态与身份，完整结果继续按原结果通道读取。"""
    return AgentEvent('task_status', run_id=report['task_id'],
        parent_run_id=report['parent_task_id'], phase=report['state'],
        text=report['display_mode'])


class BackgroundPump:
    """单消费者观察任务／团队唤醒，任何界面都不在这里消费结果。"""

    def __init__(self, session):
        self.session = session
        self._waiting = False
        self._notifying = False

    async def wait_ready(self):
        """返回原父身份；调用方完成接续或状态操作后才能再次等待。"""
        if self._waiting:
            raise RuntimeError('后台就绪只能由一个事件泵观察')
        self._waiting = True
        try:
            while True:
                self.session.tasks.changed.clear()
                parent = self.session.next_parent()
                if parent is not None:
                    return parent
                await self.session.tasks.changed.wait()
        finally:
            self._waiting = False

    async def notices(self, notify):
        """任务通知使用独立队列，不干扰父任务收件箱与聊天输入。"""
        if self._notifying:
            raise RuntimeError('后台通知只能由一个事件泵消费')
        self._notifying = True
        try:
            while True:
                event = task_event(await self.session.tasks.events.get())
                result = notify(event)
                if isawaitable(result):
                    await result
        finally:
            self._notifying = False
