"""仅接受 Lead 私有登记和真实租约的完整成员进程入口。"""

import asyncio
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
from types import SimpleNamespace

from ..async_utils import protected
from ..worktrees.paths import directory_fd, freeze_repository
from .backends import BackendHandle, BackendLaunch, WorkerObservation
from .models import Member, Team, validate_id, validate_name
from .store import MAX_RECORD_BYTES, TeamStore


def _private_json(path: Path):
    """拒绝链接、特殊文件、多个硬链接和可被其他用户读取的启动凭证。"""
    with directory_fd(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_RECORD_BYTES):
                raise ValueError('成员控制记录必须是当前用户私有普通文件')
            with os.fdopen(os.dup(fd), 'rb') as stream:
                raw = stream.read(MAX_RECORD_BYTES + 1)
            if len(raw) > MAX_RECORD_BYTES:
                raise ValueError('成员控制记录容量超限')
        finally:
            os.close(fd)
    def mapping(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('成员控制记录含重复字段')
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8'), object_pairs_hook=mapping,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('非法 JSON 常量')))


def require_live_lead(store: TeamStore, name: str):
    """记录年龄和 PID 不能替代真实 Lead flock；此检查不会创建或夺取锁。"""
    path = store.team_path(name) / 'lead.lock'
    with directory_fd(path.parent) as parent:
        fd = os.open(path.name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise ValueError('Lead 锁归属无效')
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                raise ValueError('Lead 锁已替换')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            fcntl.flock(fd, fcntl.LOCK_UN)
            raise ValueError('没有活动 Lead 所有者，禁止启动或继续成员进程')
        finally:
            os.close(fd)


@dataclass(frozen=True)
class StartupRecord:
    store: TeamStore
    team: Team
    member: Member
    launch: BackendLaunch
    definition: dict
    handle: BackendHandle | None
    activated: bool
    policy: dict


def load_startup(path: Path, config_path: Path, *, require_starting: bool = True) -> StartupRecord:
    """从固定路径推导存储，再比对独立登记；argv 文件名本身不授予团队身份。"""
    path, config_path = Path(path), Path(config_path)
    if (not path.is_absolute() or not config_path.is_absolute() or path.name != 'startup.json'
            or len(path.parents) < 4 or path.parent.parent.name != 'members'
            or os.path.normpath(str(path)) != str(path)):
        raise ValueError('不是受控成员启动路径')
    identity, name = path.parent.name, path.parents[2].name
    validate_id(identity, 'member')
    validate_name(name)
    store = TeamStore(path.parents[3])
    if path != store.member_path(name, identity) / 'startup.json':
        raise ValueError('成员启动路径不一致')
    for directory in (store.root, store.team_path(name), path.parent.parent, path.parent):
        with directory_fd(directory) as fd:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('团队控制目录必须属于当前用户且为 0700')
    data = _private_json(path)
    if (type(data) is not dict or set(data) != {'version', 'team_name', 'store_root', 'launch', 'handle', 'activated'}
            or type(data['version']) is not int or data['version'] != 1 or type(data['activated']) is not bool
            or data['team_name'] != name or data['store_root'] != str(store.root)):
        raise ValueError('成员启动记录格式或存储归属无效')
    raw = data['launch']
    keys = {'team_id', 'member_id', 'generation', 'nonce', 'workspace_root', 'config_path', 'startup_path'}
    if type(raw) is not dict or set(raw) != keys or any(type(value) is not str for value in raw.values()):
        raise ValueError('成员启动身份格式无效')
    registered = _private_json(path.parent / 'registration.json')
    if (type(registered) is not dict or set(registered) != keys | {'version', 'policy'}
            or type(registered['version']) is not int or registered['version'] != 1
            or {key: registered[key] for key in keys} != raw):
        raise ValueError('成员启动凭证与 Lead 登记不一致')
    policy = registered['policy']
    if (type(policy) is not dict or set(policy) != {'permission_mode', 'user_permission_path', 'tools'}
            or type(policy['permission_mode']) is not str or policy['permission_mode'] not in {'bypass', 'default', 'strict'}
            or type(policy['user_permission_path']) is not str or not Path(policy['user_permission_path']).is_absolute()
            or os.path.normpath(policy['user_permission_path']) != policy['user_permission_path']
            or type(policy['tools']) is not list or any(type(tool) is not str for tool in policy['tools'])
            or len(set(policy['tools'])) != len(policy['tools'])):
        raise ValueError('本次成员启动权限上限格式无效')
    launch = BackendLaunch(raw['team_id'], raw['member_id'], raw['generation'], raw['nonce'],
                           Path(raw['workspace_root']), Path(raw['config_path']), Path(raw['startup_path']))
    if (launch.member_id != identity or launch.startup_path != path or launch.config_path != config_path
            or not re.fullmatch(r'[0-9a-f]{32}', launch.generation)
            or not re.fullmatch(r'[0-9a-f]{32}', launch.nonce)):
        raise ValueError('成员启动路径、代次或凭证无效')
    team = store.load(name)
    member = team.members.get(identity)
    if (team.team_id != launch.team_id or team.status != 'active' or member is None or not member.active
            or identity == team.lead_id or member.backend != 'tmux' or member.generation != launch.generation
            or member.workspace_root != str(launch.workspace_root)
            or not getattr(member, 'definition_fingerprint', '')
            or (require_starting and member.state != 'starting')):
        raise ValueError('成员未处于合法登记的 tmux 启动代次')
    require_live_lead(store, name)
    definition = _private_json(path.parent / 'definition.json')
    from .runtime import validate_definition
    validate_definition(definition, member)
    if set(policy['tools']) - set(definition['tools']):
        raise ValueError('本次成员启动不能扩大冻结工具上限')
    if (type(definition['repository']) is not dict or definition['repository'].get('common_git_dir') != team.repository
            or type(definition['worktree']) is not dict
            or definition['worktree'].get('workspace_root') != member.workspace_root
            or definition['worktree'].get('branch') != member.branch
            or definition['worktree'].get('task_id') != identity
            or str(freeze_repository(launch.workspace_root).common_git_dir) != team.repository):
        raise ValueError('成员工作目录或仓库归属无效')
    handle = None
    if data['handle'] is not None:
        value = data['handle']
        if type(value) is not dict or set(value) != {'socket', 'session', 'pane', 'pid', 'owner', 'channel'}:
            raise ValueError('窗格启动地址格式无效')
        handle = BackendHandle(launch, **value)
        owner = hashlib.sha256('\0'.join((launch.team_id, identity, launch.generation, launch.nonce)).encode()).hexdigest()
        if (handle.owner != owner or type(handle.pid) is not int or handle.pid <= 0
                or not re.fullmatch(r'%\d+', handle.pane) or not re.fullmatch(r'\$\d+', handle.session)
                or not re.fullmatch(r'mewcode-team-[0-9a-f]{32}', handle.channel)
                or not Path(handle.socket).is_absolute()):
            raise ValueError('窗格启动身份不完整')
    if data['activated'] and handle is None:
        raise ValueError('成员未取得窗格身份，不能激活')
    return StartupRecord(store, team, member, launch, definition, handle, data['activated'], policy)


def build_permissions(record: StartupRecord):
    """重新读取真实父项目规则和原用户规则；fork 保留拒绝上限及非交互约束。"""
    from ..permissions.runtime import PermissionManager
    path = Path(record.policy['user_permission_path'])
    modes = ('bypass', 'default', 'strict')
    mode = modes[max(modes.index(record.definition['parent_permission_mode']), modes.index(record.policy['permission_mode']))]
    parent = PermissionManager(Path(record.definition['repository']['origin_root']),
        mode=mode, user_path=path, noninteractive=True)
    parent.config.load()
    permissions = parent.fork(record.definition['permission_mode'], root=record.launch.workspace_root)
    permissions.config.load()
    return parent, permissions


async def worker(startup_path: Path, config_path: Path, *, stderr=None) -> int:
    """初始化独立服务；只有实际 pane 和 Lead 激活记录都成立才消费任务。"""
    from ..config import load_config
    from ..providers import make_provider
    from ..tools import default_registry
    from ..mcp.config import load_config as load_mcp_config
    from ..mcp.manager import MCPManager
    from .capabilities import TeamScope
    from .runtime import MemberRuntime
    from .service import TeamService
    from .backends import peer_backend, notify_peer

    stderr = stderr or sys.stderr
    runtime = provider = mcp = None
    signals = []
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for number in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(number, stopped.set)
            signals.append(number)
        except (NotImplementedError, RuntimeError, ValueError):
            pass
    record = None
    control = None
    failed = False
    try:
        record = load_startup(startup_path, config_path)
        deadline = loop.time() + 30
        while record.handle is None:
            if stopped.is_set() or loop.time() >= deadline:
                raise ValueError('等待受管窗格身份已停止或超时')
            await asyncio.sleep(.02)
            record = load_startup(startup_path, config_path)
        handle = record.handle
        if (handle.pid != os.getpid() or os.environ.get('TMUX_PANE') != handle.pane
                or os.environ.get('TMUX', '').rsplit(',', 2)[0] != handle.socket
                or Path.cwd().resolve() != record.launch.workspace_root):
            raise ValueError('实际进程、tmux 环境或工作目录与登记不符')

        async def observe(launch):
            try:
                value = record.store.read_json(record.team.name, f'members/{launch.member_id}/runtime.json')
                return WorkerObservation(**value)
            except (OSError, ValueError, TypeError):
                return None
        backend = await peer_backend(handle, observe=observe)
        config = load_config(config_path).for_agent(record.definition['model'])
        user_root = record.store.root.parent
        parent, permissions = build_permissions(record)
        provider = make_provider(config)
        registry = default_registry()
        config.validate_agent_tools(registry.names())
        mcp = MCPManager(load_mcp_config(record.launch.workspace_root), registry)
        owner_session = SimpleNamespace(user_root=user_root, team_scope=TeamScope(record.team.name, record.member.member_id, False),
            provider=None, config=config, mode='execute', _notify=None, warnings=[], tasks=SimpleNamespace(changed=asyncio.Event()))
        owner = TeamService(owner_session)
        owner.store = record.store
        async def notify(identity):
            if identity == record.member.member_id:
                if runtime is not None:
                    runtime.wake.set()
                return
            team = record.store.load(record.team.name)
            peer = team.members[identity]
            if peer.backend != 'tmux':
                return
            path = record.store.member_path(team.name, identity) / 'startup.json'
            # 先读固定登记中的配置路径，再进行完整私有路径与启动凭证校验。
            registered = _private_json(path.parent / 'registration.json')
            peer_record = load_startup(path, Path(registered['config_path']), require_starting=False)
            if peer_record.handle is None:
                raise ValueError('目标成员尚无可信窗格身份')
            await notify_peer(peer_record.handle, observe=observe)
        owner.notify_member = notify
        runtime = MemberRuntime(owner, record.member, record.definition, config, provider, permissions,
                                registry=registry, mcp=mcp, launch=record.launch)
        runtime.ceiling &= frozenset(record.policy['tools'])
        async def close_mcp():
            await mcp.close()
            if any(server.state != 'closed' or server.process_group_alive() for server in mcp.records.values()):
                raise ValueError('独立 MCP 服务停止未确认')
        runtime.before_shutdown = close_mcp
        async def prepare_services():
            await mcp.start(cancel_event=runtime.cancel)
            permissions.bind_mcp_tools(mcp.tools)
            parent.bind_mcp_tools(mcp.tools)
            if set(record.definition['tools']) - registry.names():
                raise ValueError('冻结角色包含当前无法初始化的工具')
            runtime.session.validate_skills()
        runtime.before_ready = prepare_services
        await runtime.setup()
        if not mcp._started:
            raise ValueError('成员运行器尚未接入完整服务就绪门禁')

        def request_shutdown():
            # 收尾只由主循环/serve 执行一次，避免激活前两条协程同时关闭 MCP 和租约。
            stopped.set()
            runtime.stop_requested = True
            runtime.cancel.set()
            runtime.wake.set()

        async def control_watch():
            try:
                while not runtime.stop_requested:
                    require_live_lead(record.store, record.team.name)
                    current = load_startup(startup_path, config_path, require_starting=False)
                    if current.launch != record.launch or current.handle != handle or current.policy != record.policy:
                        raise ValueError('成员激活记录身份已改变')
                    # 激活前尚未进入 serve；只识别停止协议，普通邮箱留给合法历史边界消费。
                    messages = await runtime.session.teams.inbox().read(record.member.member_id)
                    shutdown = any(message.type == 'shutdown_request' and message.sender_id == current.team.lead_id
                        and message.fields.get('generation') == record.launch.generation for message in messages)
                    if current.team.status != 'active' or shutdown:
                        request_shutdown()
                        return
                    if stopped.is_set():
                        request_shutdown()
                        return
                    await backend.wait_notification(handle, timeout=.25)
                    runtime.wake.set()
            except (OSError, ValueError):
                request_shutdown()
        control = asyncio.create_task(control_watch())
        deadline = loop.time() + 30
        while not load_startup(startup_path, config_path, require_starting=False).activated:
            if stopped.is_set() or runtime.stop_requested or loop.time() >= deadline:
                await runtime.stop()
                return 0
            await asyncio.sleep(.02)
        if runtime.stop_requested or stopped.is_set():
            await runtime.stop()
            return 0
        runtime.task = asyncio.create_task(runtime.serve())
        await runtime.task
        return 0
    except asyncio.CancelledError:
        stopped.set()
        if runtime is not None:
            await protected(runtime.stop())
        return 0
    except Exception as error:
        failed = True
        # 异常可能来自供应商或配置解析；不把错误正文、密钥或启动 nonce 写入 pane。
        stderr.write(f'团队成员启动或运行失败（{type(error).__name__}）；保留记录供 Lead 核查。\n')
        return 2
    finally:
        for task in (control,):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                await protected(asyncio.gather(task, return_exceptions=True))
        if runtime is not None:
            try:
                await protected(runtime.close())
            except Exception:
                failed = True
        else:
            if mcp is not None:
                await protected(mcp.close())
            if provider is not None:
                await protected(provider.aclose())
        if failed and record is not None:
            try:
                def mark_failed(team):
                    member = team.members.get(record.member.member_id)
                    if (team.team_id == record.launch.team_id and member is not None
                            and member.generation == record.launch.generation):
                        member.state = 'needs_review'
                await record.store.update_team(record.team.name, mark_failed)
            except (OSError, ValueError):
                pass
        for number in signals:
            loop.remove_signal_handler(number)
