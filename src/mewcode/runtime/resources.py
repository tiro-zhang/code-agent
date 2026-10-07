"""运行资源的公共所有者；不导入终端、信号处理或 Web 框架。"""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from ..async_utils import protected
from ..commands.builtins import build_registry
from ..mcp.config import load_config as load_mcp_config
from ..mcp.manager import MCPManager
from ..permissions.runtime import PermissionManager
from ..providers import make_provider
from ..session import ChatSession
from ..skills.catalog import discover_skills
from ..tools import default_registry
from ..tools.base import ToolContext
from ..tools.executor import ToolExecutor
from ..types import AgentEvent
from .events import BackgroundPump, notification_event


@dataclass(frozen=True)
class CloseReport:
    """尚未确认结束时保留所有权；超时不是释放执行槽的证据。"""
    complete: bool
    errors: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()


class RuntimeResources:
    """按命令校验、构造、MCP、恢复、Hook 顺序准备一个运行会话。"""

    def __init__(self, config, *, root, provider_factory=None, notify=None,
                 approval_responder=None, permission_mode='default', persistent=True,
                 resume=None, team=None, memory_enabled=True, user_root=None,
                 config_path=None, registry_factory=None, session_factory=None,
                 mcp_factory=None, mcp_config_loader=None, mcp_notify=None):
        self.config = config
        self.root = Path(root).expanduser().resolve()
        self.provider_factory = provider_factory or make_provider
        self.notify, self.approval_responder = notify, approval_responder
        self.permission_mode, self.persistent = permission_mode, persistent
        self.resume, self.team = resume, team
        self.memory_enabled, self.user_root = memory_enabled, user_root
        self.config_path = Path(config_path).expanduser().resolve() if config_path is not None else None
        self.registry_factory = registry_factory or build_registry
        self.session_factory = session_factory or ChatSession
        self.mcp_factory = mcp_factory or MCPManager
        self.mcp_config_loader = mcp_config_loader or load_mcp_config
        self.mcp_notify = mcp_notify
        self.provider = self.session = self.mcp = self.registry = self.catalog = self.background = None
        self._partial_session = None
        self._close_task = None
        self._closing_stage = ''
        self._close_errors = []
        self._prepared = False

    def validate(self):
        """纯定义校验先于 Provider、存档和交互资源创建。"""
        if self._close_task is not None:
            raise RuntimeError('运行资源已开始关闭')
        if self.resume is not None and self.team is not None:
            raise ValueError('--resume 与 --team 不能同时使用。')
        if not self.root.is_dir():
            raise ValueError('项目根目录不存在')
        if self.registry is None:
            self.catalog = discover_skills(self.root, user_root=self.user_root)
            self.registry = self.registry_factory(self.catalog)
        return self.registry

    def create_provider(self):
        """校验后创建唯一客户端，终端可在输入初始化前取得所有权。"""
        self.validate()
        if self.provider is None:
            self.provider = self.provider_factory(self.config)
        return self.provider

    def construct(self):
        """显式项目根通过工具上下文传入，不修改进程 cwd。"""
        self.create_provider()
        if self.session is not None:
            return self.session
        permissions = PermissionManager(self.root, mode=self.permission_mode,
                                        responder=self.approval_responder)
        executor = ToolExecutor(default_registry(), ToolContext(self.root), permissions=permissions)
        options = dict(executor=executor,
            max_iterations=self.config.max_iterations, permission_mode=self.permission_mode,
            config=self.config, persistent=self.persistent, resume=self.resume,
            memory_enabled=self.memory_enabled, user_root=self.user_root,
            notify=self._notification, skill_catalog=self.catalog,
            provider_factory=self.provider_factory)
        if self.session_factory is ChatSession:
            # 构造函数创建存档后仍可能失败；先取得对象所有权才能回收半构造句柄。
            self._partial_session = ChatSession.__new__(ChatSession)
            ChatSession.__init__(self._partial_session, self.provider, **options)
            self.session, self._partial_session = self._partial_session, None
        else:
            self.session = self.session_factory(self.provider, **options)
        self.session.config_path = self.config_path
        self.session.permissions.config.load()
        self.background = BackgroundPump(self.session)
        return self.session

    def _notification(self, notification):
        if self.notify is not None:
            self.notify(notification_event(notification))

    def _mcp_diagnostic(self, diagnostic):
        if self.mcp_notify is not None:
            self.mcp_notify(diagnostic)
        elif self.notify is not None:
            self.notify(AgentEvent('display_line', purpose='mcp', phase=diagnostic.stage,
                text=f'MCP> {diagnostic.server or "全部 Server"} · {diagnostic.stage} · {diagnostic.message}'))

    async def resume_team(self):
        if self.team is not None:
            arguments = {'action': 'resume', 'name': self.team}
            self.session.teams.guard('team', arguments)
            await self.session.teams.control(arguments)

    async def bind_mcp(self, *, cancel_event=None):
        if self.mcp is None:
            snapshot = self.mcp_config_loader(self.root)
            self.mcp = self.mcp_factory(snapshot, self.session.executor.registry,
                                        notify=self._mcp_diagnostic)
            self.session.executor.mcp = self.mcp
        await self.mcp.start(cancel_event=cancel_event)
        if cancel_event is None or not cancel_event.is_set():
            self.session.permissions.bind_mcp_tools(self.mcp.tools)
        return self.mcp

    async def prepare(self, *, cancel_event=None):
        if self._prepared:
            return
        self.session.validate_skills()
        await self.session.prepare_restore(cancel_event=cancel_event)
        await self.session.start(cancel_event=cancel_event)
        self._prepared = True

    async def start(self, *, cancel_event=None):
        """无界面调用的完整启动；部分失败也由本对象负责清理。"""
        try:
            self.construct()
            await self.resume_team()
            await self.bind_mcp(cancel_event=cancel_event)
            if cancel_event is None or not cancel_event.is_set():
                await self.prepare(cancel_event=cancel_event)
            return self
        except BaseException:
            await self.aclose(cancel_event=cancel_event)
            raise

    async def _close_partial_session(self):
        """尚未启动的部分会话只关闭已经成功赋值的资源。"""
        session = self._partial_session
        try:
            for name, method in (('teams', 'pause'), ('worktree_cleaner', 'close'),
                                 ('tasks', 'aclose'), ('hooks', 'close'), ('memory', 'aclose')):
                owner = getattr(session, name, None)
                if owner is not None:
                    await getattr(owner, method)()
        finally:
            session._close_handles()
            self._partial_session = None

    async def _close_all(self):
        if self._partial_session is not None:
            self._closing_stage = 'session'
            try:
                await self._close_partial_session()
            except (Exception, asyncio.CancelledError):
                self._close_errors.append('session 初始化资源关闭失败，状态待核查')
        for name, owner, method in (('session', self.session, 'aclose'),
                                    ('mcp', self.mcp, 'close'),
                                    ('provider', self.provider, 'aclose')):
            if owner is None:
                continue
            self._closing_stage = name
            try:
                await getattr(owner, method)()
                if name == 'mcp' and any(item.stage == '关闭'
                                         for item in getattr(owner, 'diagnostics', ())):
                    self._close_errors.append('mcp 关闭未确认，资源状态待核查')
            except (Exception, asyncio.CancelledError):
                # 底层异常可能含配置凭据；公开关闭报告只保留资源身份。
                self._close_errors.append(f'{name} 关闭失败，资源状态待核查')
        self._closing_stage = ''
        return CloseReport(not self._close_errors, tuple(self._close_errors))

    async def aclose(self, *, cancel_event=None, timeout=10):
        """重复取消不打断关闭；有界等待超时后仍持有原关闭任务。"""
        if timeout <= 0:
            raise ValueError('关闭期限必须大于零')
        if self._close_task is None:
            if self.session is not None:
                self.session.tasks.paused = True
                for parent in self.session.tasks.parents.values():
                    parent.wake_allowed = False
                    parent.cancel.set()
            self._close_task = asyncio.create_task(self._close_all())
        async def wait():
            done, _ = await asyncio.wait((self._close_task,), timeout=timeout)
            if done:
                return self._close_task.result()
            return CloseReport(False, tuple(self._close_errors),
                               (self._closing_stage or 'resources',))
        return await protected(wait(), cancel_event=cancel_event)
