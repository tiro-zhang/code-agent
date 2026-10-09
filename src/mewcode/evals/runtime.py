"""收窄装配入口，执行仍交给真实会话与权限模块。"""
from dataclasses import asdict, replace
from pathlib import Path
import time
import yaml

from ..session import ChatSession
from ..tools import default_registry
from ..tools.base import ToolContext
from ..tools.executor import ToolExecutor
from ..tools.registry import ToolRegistry
from ..permissions.runtime import PermissionManager
from ..skills.catalog import SkillCatalog
from ..agents.definitions import RoleCatalog
from .suite import CORE_TOOLS, digest


class CoreEvalSession(ChatSession):
    """仅适配能力入口，不覆盖代理循环、模式控制或关闭。"""

    def effective_tools(self):
        return self.executor.registry.names(read_only=True) if self.mode == 'plan' else CORE_TOOLS


def make_session(provider, config, case, workspace, user_root):
    workspace, user_root = Path(workspace).resolve(), Path(user_root).resolve()
    user_root.mkdir(parents=True, exist_ok=True)
    if case.rules:
        policy = workspace/'.mewcode/permissions.yaml'
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text(yaml.safe_dump({'version':1, 'rules':list(case.rules)}, allow_unicode=True))

    async def respond(request, cancel):
        if cancel.is_set():
            return 'deny'
        for approval in case.approvals:
            if approval['tool'] != request.tool:
                continue
            if 'arguments' in approval:
                matched = approval['arguments'] == request.arguments
            else:
                target = Path(request.arguments.get('path', ''))
                target = target if target.is_absolute() else workspace/target
                matched = target.resolve() == (workspace/approval['path']).resolve()
            if matched:
                return approval['decision']
        return 'deny'

    permissions = PermissionManager(workspace, mode=case.permission_mode, responder=respond,
                                    user_path=user_root/'permissions.yaml')
    registry = default_registry()
    # 系统工具会穿过普通 allowed_tools，必须从注册表中移除。
    registry = ToolRegistry(registry.get(name) for name in sorted(CORE_TOOLS))
    executor = ToolExecutor(registry, ToolContext(workspace), permissions=permissions)
    core_config = replace(config, agent_plugin_dirs=(), agent_background_tools=None,
                          team_backend='inprocess', team_coordinator_enabled=False)
    session = CoreEvalSession(provider, executor=executor, config=core_config,
                              max_iterations=config.max_iterations, user_root=user_root,
                              skill_catalog=SkillCatalog(()), persistent=False, memory_enabled=False)
    session.roles = RoleCatalog(())
    session._role_options = {'user_root':user_root, 'plugin_dirs':()}
    return session


class ObservedProvider:
    """透明记录实际发出的提示与工具声明指纹，不保留请求正文。"""

    def __init__(self, provider, workspace, user_root):
        self.provider = provider
        self.config = getattr(provider, 'config', None)
        self.roots = (str(workspace), str(user_root))
        self.prompts, self.tools = set(), set()
        self.requests = []

    def normalize(self, value):
        if isinstance(value, str):
            for root, label in zip(self.roots, ('<workspace>', '<user>')):
                value = value.replace(root, label)
            return value
        if isinstance(value, dict):
            return {key:self.normalize(item) for key,item in value.items()}
        if isinstance(value, (tuple,list)):
            return [self.normalize(item) for item in value]
        return value

    async def stream(self, messages, *, tools=(), tool_choice='auto', system_prompt=''):
        self.prompts.add(digest(self.normalize(system_prompt)))
        self.tools.add(digest(self.normalize([asdict(tool) for tool in tools])))
        started = time.monotonic()
        source = self.provider.stream(messages, tools=tools, tool_choice=tool_choice, system_prompt=system_prompt)
        try:
            async for event in source:
                yield event
        finally:
            await source.aclose()
            self.requests.append(round(time.monotonic()-started,6))

    async def aclose(self):
        await self.provider.aclose()
