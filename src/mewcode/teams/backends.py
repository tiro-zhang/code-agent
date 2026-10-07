"""团队运行后端、可信 tmux 窗格归属及有界命令边界。"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import sys
import tempfile
from typing import Protocol
import uuid

from ..async_utils import protected


class BackendError(RuntimeError):
    def __init__(self, message: str, *, residual=None):
        super().__init__(message)
        self.residual = residual


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


async def run_command(argv: Sequence[str], *, cwd: Path | None = None,
                      env: Mapping[str, str] | None = None, timeout: float = 5,
                      output_limit: int = 65536) -> CommandResult:
    """只接受 argv；取消、超时及输出超限时收尾整个本地进程组。"""
    if not argv or any(not isinstance(value, str) or '\0' in value for value in argv):
        raise BackendError('命令参数无效')
    process = None
    reader = None
    completed = False
    cancelled = asyncio.Event()
    try:
        process = await protected(asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True), cancel_event=cancelled)
        if cancelled.is_set():
            raise asyncio.CancelledError
        async def read(stream):
            result = bytearray()
            while chunk := await stream.read(8192):
                result.extend(chunk)
                if len(result) > output_limit:
                    raise BackendError('命令输出超限')
            return result.decode('utf-8', errors='replace')
        async def collect():
            output, error = await asyncio.gather(read(process.stdout), read(process.stderr))
            await process.wait()
            return CommandResult(process.returncode, output, error)
        reader = asyncio.create_task(collect())
        result = await asyncio.wait_for(asyncio.shield(reader), timeout)
        completed = True
        return result
    except TimeoutError:
        raise BackendError('命令超时') from None
    except OSError:
        raise BackendError('命令无法启动') from None
    finally:
        if process is not None and not completed:
            # 主进程已退出也可能留下持有输出管道的子孙，仍向自建进程组发送信号。
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await protected(asyncio.wait_for(process.wait(), .2))
            except TimeoutError:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await protected(process.wait())
        if reader is not None and not reader.done():
            reader.cancel()
        if reader is not None:
            await protected(asyncio.gather(reader, return_exceptions=True))


@dataclass(frozen=True)
class BackendProbe:
    name: str
    available: bool
    reason: str = ''
    connection: str = ''


@dataclass(frozen=True)
class BackendLaunch:
    team_id: str
    member_id: str
    generation: str
    nonce: str
    workspace_root: Path
    config_path: Path
    startup_path: Path

    def __post_init__(self):
        for value in (self.team_id, self.member_id, self.generation, self.nonce):
            if not isinstance(value, str) or not value or len(value) > 256 or '\0' in value:
                raise BackendError('启动身份无效')
        for value in (self.workspace_root, self.config_path, self.startup_path):
            if not isinstance(value, Path) or not value.is_absolute():
                raise BackendError('成员启动必须使用绝对路径')


@dataclass(frozen=True)
class WorkerObservation:
    team_id: str
    member_id: str
    generation: str
    nonce: str
    pid: int
    lease_owned: bool
    state: str
    shutdown_confirmed: bool = False

    def matches(self, launch: BackendLaunch) -> bool:
        return (self.team_id, self.member_id, self.generation, self.nonce) == (
            launch.team_id, launch.member_id, launch.generation, launch.nonce)


@dataclass(frozen=True)
class BackendHandle:
    launch: BackendLaunch
    socket: str
    session: str
    pane: str
    pid: int
    owner: str
    channel: str

    def metadata(self) -> dict:
        """保存到可信启动记录，供 worker 等待通知和恢复时核验。"""
        return {'socket': self.socket, 'session': self.session, 'pane': self.pane,
                'pid': self.pid, 'owner': self.owner, 'channel': self.channel,
                'generation': self.launch.generation}


@dataclass(frozen=True)
class BackendInspection:
    state: str
    alive: bool
    owned: bool
    reason: str = ''


@dataclass(frozen=True)
class BackendStop:
    confirmed: bool
    state: str
    reason: str = ''


def process_alive(pid: int) -> bool:
    """停止记录与实际进程退出都要成立；PID 被复用时保守保留待核查。"""
    if type(pid) is not int or pid <= 0:
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class MemberBackend(Protocol):
    name: str
    async def probe(self) -> BackendProbe: ...
    async def start(self, launch: BackendLaunch, **kwargs): ...
    async def notify(self, handle): ...
    async def stop(self, handle, **kwargs) -> BackendStop: ...
    async def inspect(self, handle) -> BackendInspection: ...


@dataclass(frozen=True)
class BackendSelection:
    backend: MemberBackend
    fallback_reason: str = ''


async def select_backend(requested: str, tmux: MemberBackend,
                         inprocess: MemberBackend | None,
                         report: Callable[[str], object] | None = None) -> BackendSelection:
    """仅探测阶段允许 auto 回退；选定后 start/run 的失败直接交给调用者。"""
    if requested not in ('auto', 'tmux', 'inprocess'):
        raise BackendError('未知团队后端')
    backend = inprocess if requested == 'inprocess' else tmux
    if backend is None:
        raise BackendError('inprocess 后端未配置')
    probe = await backend.probe()
    if probe.available:
        if report is not None:
            report(f'团队使用 {backend.name} 后端。' + probe.connection)
        return BackendSelection(backend)
    if requested != 'auto':
        raise BackendError(f'{backend.name} 不可用：{probe.reason}')
    if inprocess is None or not (await inprocess.probe()).available:
        raise BackendError(f'tmux 不可用：{probe.reason}；inprocess 也不可用')
    if report is None:
        raise BackendError('auto 回退必须提供公开后端选择的报告接口')
    report(f'tmux 不可用：{probe.reason}；选择 inprocess 同进程独立协程。两者均不提供文件系统沙箱。')
    return BackendSelection(inprocess, probe.reason)


def worker_command(launch: BackendLaunch) -> tuple[str, ...]:
    """配置只传路径，凭据和启动凭证由完整成员实例自行读取。"""
    return (sys.executable, '-m', 'mewcode', '--config', str(launch.config_path),
            '--team-worker', str(launch.startup_path))


def shell_command(argv: Sequence[str]) -> str:
    """tmux 接受 shell 字符串，所有可信参数逐项安全引用。"""
    if not argv or any(not isinstance(value, str) or '\0' in value for value in argv):
        raise BackendError('成员命令参数无效')
    return 'exec ' + shlex.join(argv)


class TmuxBackend:
    name = 'tmux'

    def __init__(self, *, executable: str = 'tmux', runner=run_command,
                 environment: Mapping[str, str] | None = None,
                 observe: Callable[[BackendLaunch], Awaitable[WorkerObservation | None]] | None = None,
                 command: Callable[[BackendLaunch], Sequence[str]] = worker_command,
                 startup_timeout: float = 10, stop_timeout: float = 10):
        self.executable = executable
        self.runner = runner
        self.environment = dict(os.environ if environment is None else environment)
        self.observe = observe
        self.command = command
        self.startup_timeout = startup_timeout
        self.stop_timeout = stop_timeout
        self.socket = ''
        self.session = ''
        self.owns_server = False
        self.probed = False
        self._socket_directory = None
        self._handles = {}
        self._start_lock = asyncio.Lock()

    async def _tmux(self, *args, check=True):
        env = dict(self.environment)
        env.pop('TMUX', None)
        env.pop('TMUX_PANE', None)
        prefix = (self.executable, '-S', self.socket) if self.socket else (self.executable,)
        result = await self.runner((*prefix, *args), env=env)
        if check and result.returncode:
            # 不传播任意 stderr，避免 worker 或配置内容进入 UI / 日志。
            raise BackendError('tmux 命令失败')
        return result

    async def probe(self) -> BackendProbe:
        """验证命令、socket 和受管会话能力；不会建立任何成员窗格。"""
        if self.probed:
            return BackendProbe(self.name, True, connection=self.connection)
        executable = shutil.which(self.executable)
        if executable is None:
            return BackendProbe(self.name, False, '找不到 tmux 命令')
        self.executable = executable
        try:
            await self._tmux('-V')
            tmux = self.environment.get('TMUX')
            if tmux:
                parts = tmux.rsplit(',', 2)
                pane = self.environment.get('TMUX_PANE', '')
                if len(parts) != 3 or not re.fullmatch(r'%\d+', pane):
                    raise BackendError('无法验证已有 TMUX 目标')
                info = os.stat(parts[0], follow_symlinks=False)
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                    raise BackendError('无法验证 tmux socket 归属')
                self.socket = parts[0]
                result = await self._tmux('display-message', '-p', '-t', pane,
                                          '#{pane_id}\t#{session_id}')
                actual, session = result.stdout.strip().split('\t')
                if actual != pane or not re.fullmatch(r'\$\d+', session):
                    raise BackendError('无法验证已有 tmux 会话')
                self.session = session
                await self._tmux('show-options', '-s')
            else:
                self._socket_directory = Path(tempfile.mkdtemp(prefix='mewcode-team-tmux-'))
                self.socket = str(self._socket_directory / 'server.sock')
                self.owns_server = True
                await self._tmux('-f', '/dev/null', 'start-server', ';',
                                  'set-option', '-g', 'exit-empty', 'off', ';', 'show-options', '-s')
                info = os.stat(self.socket, follow_symlinks=False)
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                    raise BackendError('自建 tmux socket 未就绪')
                await self._tmux('show-options', '-s')
            self.probed = True
            return BackendProbe(self.name, True, connection=self.connection)
        except asyncio.CancelledError:
            await protected(self.close())
            raise
        except (BackendError, OSError, ValueError):
            await self.close()
            return BackendProbe(self.name, False, '无法验证 tmux server/socket 或受管会话能力')

    @property
    def connection(self) -> str:
        target = ('-t', self.session) if self.session else ()
        return '查看成员：' + shlex.join((self.executable, '-S', self.socket, 'attach-session', *target))

    async def _pane(self, handle: BackendHandle):
        if handle.socket != self.socket:
            raise BackendError('窗格 socket 归属不符', residual=handle)
        # display-message 对不存在的 -t 可能落到当前窗格，枚举后精确匹配稳定 ID。
        result = await self._tmux('list-panes', '-a', '-F',
                                  '#{pane_id}\t#{session_id}\t#{pane_pid}\t#{pane_dead}\t#{@mewcode-team-owner}', check=False)
        if result.returncode:
            return None
        candidates = [line.split('\t') for line in result.stdout.splitlines()
                      if line.split('\t', 1)[0] == handle.pane]
        if not candidates:
            return None
        if len(candidates) != 1:
            raise BackendError('窗格身份重复', residual=handle)
        fields = candidates[0]
        if len(fields) != 5 or fields[0] != handle.pane or fields[1] != handle.session or fields[4] != handle.owner:
            raise BackendError('窗格归属不符', residual=handle)
        try:
            pid = int(fields[2])
        except ValueError:
            raise BackendError('窗格进程身份无效', residual=handle) from None
        if handle.pid and pid != handle.pid:
            raise BackendError('窗格进程归属不符', residual=handle)
        return pid, fields[3] != '1'

    async def _observation(self, handle):
        value = await self.observe(handle.launch) if self.observe is not None else None
        if value is not None and (not value.matches(handle.launch) or value.pid != handle.pid):
            return None
        return value

    async def start(self, launch: BackendLaunch, *, on_started=None) -> BackendHandle:
        """调用者必须先发布权威启动记录；握手未就绪不运行替代后端。"""
        if not self.probed:
            probe = await self.probe()
            if not probe.available:
                raise BackendError(probe.reason)
        if self.observe is None:
            raise BackendError('缺少可信成员就绪观察接口')
        async with self._start_lock:
            key = (launch.team_id, launch.member_id)
            if key in self._handles:
                raise BackendError('成员已有后端所有者')
            handle = None
            cancelled = asyncio.Event()
            creation_attempted = False
            try:
                owner = hashlib.sha256('\0'.join((launch.team_id, launch.member_id, launch.generation, launch.nonce)).encode()).hexdigest()
                channel = 'mewcode-team-' + uuid.uuid4().hex
                argv = self.command(launch)
                command = shell_command(argv)
                # 只给本次窗格传主动开关，旧 server 的值不能替代当前启动环境。
                coordinator = 'MEWCODE_COORDINATOR'
                if coordinator in self.environment:
                    coordinator += '=' + self.environment['MEWCODE_COORDINATOR']
                else:
                    # tmux 的无值 -e 不能覆盖 session 里的旧值；子进程精确删除这一项。
                    command = shell_command(('/usr/bin/env', '-u', coordinator, *argv))
                    coordinator += '='
                creation_attempted = True
                if self.session:
                    result = await protected(self._tmux('new-window', '-d', '-P', '-F', '#{pane_id}\t#{session_id}',
                                              '-t', self.session, '-c', str(launch.workspace_root), '-e', coordinator, command), cancel_event=cancelled)
                else:
                    result = await protected(self._tmux('new-session', '-d', '-P', '-F', '#{pane_id}\t#{session_id}',
                                              '-s', 'mewcode-' + uuid.uuid4().hex, '-c', str(launch.workspace_root), '-e', coordinator, command), cancel_event=cancelled)
                fields = result.stdout.strip().split('\t')
                if len(fields) != 2:
                    raise BackendError('tmux 创建未返回可验证的窗格身份；残留待核查')
                pane, session = fields
                if not re.fullmatch(r'%\d+', pane) or not re.fullmatch(r'\$\d+', session):
                    raise BackendError('tmux 创建返回非法窗格身份')
                handle = BackendHandle(launch, self.socket, session, pane, 0, owner, channel)
                await protected(self._tmux('set-option', '-p', '-t', pane, '@mewcode-team-owner', owner), cancel_event=cancelled)
                info = await protected(self._pane(handle), cancel_event=cancelled)
                if info is None or not info[1]:
                    raise BackendError('成员窗格未就绪')
                handle = BackendHandle(launch, self.socket, session, pane, info[0], owner, channel)
                self.session = session
                self._handles[key] = handle
                if cancelled.is_set():
                    raise asyncio.CancelledError
                if on_started is not None:
                    await on_started(handle)
                deadline = asyncio.get_running_loop().time() + self.startup_timeout
                while asyncio.get_running_loop().time() < deadline:
                    info = await self._pane(handle)
                    value = await self._observation(handle)
                    if info is None or not info[1]:
                        break
                    if value is not None and value.lease_owned is True and value.state in ('idle', 'planning', 'awaiting_approval', 'running'):
                        return handle
                    await asyncio.sleep(.02)
                raise BackendError('成员就绪握手失败')
            except BaseException as error:
                residual = handle
                if handle is None and creation_attempted:
                    # 命令失败或回执损坏不证明副作用未发生；空 pane 明示身份未知，禁止清空 server。
                    residual = BackendHandle(launch, self.socket, self.session, '', 0, owner, channel)
                if handle is not None:
                    try:
                        info = await protected(self._pane(handle))
                        if info is not None:
                            await protected(self._tmux('kill-pane', '-t', handle.pane))
                        deadline = asyncio.get_running_loop().time() + .2
                        while handle.pid and process_alive(handle.pid) and asyncio.get_running_loop().time() < deadline:
                            await protected(asyncio.sleep(.01))
                        if await protected(self._pane(handle)) is None and handle.pid and not process_alive(handle.pid):
                            residual = None
                    except BackendError:
                        pass
                if residual is None:
                    self._handles.pop(key, None)
                    if self.owns_server and not self._handles:
                        self.session = ''
                else:
                    self._handles[key] = residual
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise BackendError(str(error), residual=residual) from None

    async def inspect(self, handle: BackendHandle) -> BackendInspection:
        try:
            info = await self._pane(handle)
            value = await self._observation(handle)
            if info is None or not info[1]:
                stopped = value and value.shutdown_confirmed is True and value.lease_owned is False and value.state == 'stopped' and not process_alive(handle.pid)
                return BackendInspection('stopped' if stopped else 'needs_review', False, value is not None, '成员窗格已退出')
            if value is None or value.lease_owned is not True:
                return BackendInspection('needs_review', True, False, '成员运行代次或租约无法验证')
            return BackendInspection(value.state, True, True)
        except BackendError as error:
            return BackendInspection('needs_review', True, False, str(error))

    async def notify(self, handle: BackendHandle) -> None:
        state = await self.inspect(handle)
        if not state.alive or not state.owned:
            raise BackendError('成员通知失败：窗格或运行归属无法验证', residual=handle)
        await self._tmux('wait-for', '-S', handle.channel)

    async def stop(self, handle: BackendHandle, *, request_stop=None) -> BackendStop:
        """成员确认本地工具和自有服务收尾且退出后，才能解除后端所有权。"""
        try:
            if request_stop is not None:
                await request_stop(handle.launch)
            state = await self.inspect(handle)
            if state.alive and state.owned:
                await self.notify(handle)
            deadline = asyncio.get_running_loop().time() + self.stop_timeout
            while asyncio.get_running_loop().time() < deadline:
                info = await self._pane(handle)
                value = await self._observation(handle)
                if value is not None and value.shutdown_confirmed is True and value.lease_owned is False and value.state == 'stopped' and (info is None or not info[1]) and not process_alive(handle.pid):
                    if info is not None:
                        await self._tmux('kill-pane', '-t', handle.pane)
                    self._handles.pop((handle.launch.team_id, handle.launch.member_id), None)
                    if self.owns_server and not self._handles:
                        self.session = ''
                    return BackendStop(True, 'stopped')
                await asyncio.sleep(.02)
            return BackendStop(False, 'needs_review', '成员进程或本地工具停止未确认；保留实际窗格归属')
        except BackendError as error:
            return BackendStop(False, 'needs_review', str(error))

    async def wait_notification(self, handle: BackendHandle, *, timeout: float = 1) -> bool:
        """worker 控制唤醒；超时也须重新检查邮箱，信号不是消息事实源。"""
        try:
            info = await self._pane(handle)
            if info is None or not info[1]:
                return False
            env = dict(self.environment)
            env.pop('TMUX', None)
            result = await self.runner((self.executable, '-S', handle.socket, 'wait-for', handle.channel), env=env, timeout=timeout)
            return result.returncode == 0
        except BackendError:
            return False

    async def close(self) -> BackendStop:
        """只清理自建空 server；活动成员需要逐个执行可信停止协议。"""
        if self._handles:
            return BackendStop(False, 'needs_review', '仍有未确认停止的成员后端')
        if self.owns_server and self.socket:
            try:
                await self._tmux('kill-server', check=False)
            except BackendError:
                return BackendStop(False, 'needs_review', '自有 tmux server 清理未确认')
        if self._socket_directory is not None:
            try:
                self._socket_directory.rmdir()
            except OSError:
                pass
        self.probed = False
        self.session = ''
        return BackendStop(True, 'stopped')


async def peer_backend(handle: BackendHandle, *, observe=None, runner=run_command,
                       executable: str = 'tmux') -> TmuxBackend:
    """从可信存档地址建立直连；仍以实际 socket、窗格和进程身份验证归属。"""
    try:
        info = os.stat(handle.socket, follow_symlinks=False)
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise BackendError('peer socket 归属无法验证', residual=handle)
        executable = shutil.which(executable)
        if executable is None:
            raise BackendError('peer tmux 命令不存在', residual=handle)
        expected = hashlib.sha256('\0'.join((handle.launch.team_id, handle.launch.member_id,
                                             handle.launch.generation, handle.launch.nonce)).encode()).hexdigest()
        if (handle.owner != expected or type(handle.pid) is not int or handle.pid <= 0
                or not re.fullmatch(r'%\d+', handle.pane) or not re.fullmatch(r'\$\d+', handle.session)
                or not re.fullmatch(r'mewcode-team-[0-9a-f]{32}', handle.channel)):
            raise BackendError('peer 启动身份无法验证', residual=handle)
        backend = TmuxBackend(executable=executable, runner=runner, observe=observe)
        backend.socket, backend.session = handle.socket, handle.session
        pane = await backend._pane(handle)
        current = os.stat(handle.socket, follow_symlinks=False)
        if (pane is None or not pane[1]
                or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
            raise BackendError('peer 窗格或 socket 已失效', residual=handle)
        backend.probed = True
        return backend
    except OSError:
        raise BackendError('peer socket 无法安全访问', residual=handle) from None


async def notify_peer(handle: BackendHandle, *, observe, runner=run_command,
                      executable: str = 'tmux') -> None:
    """点对点通知不经过 Lead；正文和邮箱路径不会进入 tmux 命令。"""
    backend = await peer_backend(handle, observe=observe, runner=runner, executable=executable)
    await backend.notify(handle)
