"""私有 JSONL 的单写者日志、只读扫描及按归属清理。"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat
from uuid import uuid4

from .codec import json_value
from .projection import Projection, build_projection, timestamp, validate_payload


ID_PATTERN = re.compile(r'\d{8}-\d{6}-[0-9a-f]{4}')
TTL = timedelta(days=30)
CRITICAL_KINDS = {'session_created', 'session_resumed', 'interaction_started', 'tool_result',
                  'history_commit', 'history_checkpoint', 'checkpoint', 'task_finished', 'run_finished'}


class SessionError(OSError):
    """可安全展示的会话存储或恢复错误。"""


@dataclass
class SessionInfo:
    id: str
    title: str = '未命名会话'
    message_count: int = 0
    last_activity: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=timezone.utc))
    active: bool = False
    recoverable: bool = False
    warnings: list[str] = field(default_factory=list)


def _root(root) -> Path:
    result = Path(root).resolve(strict=True)
    if not result.is_dir():
        raise SessionError('项目根不是目录')
    return result


def _directories(root: Path, *, create=False, leaf='sessions') -> list[int]:
    descriptors = []
    try:
        descriptors.append(os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        for name in ('.mewcode', leaf):
            if create:
                try:
                    os.mkdir(name, 0o700, dir_fd=descriptors[-1])
                except FileExistsError:
                    pass
            descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=descriptors[-1])
            descriptors.append(descriptor)
            if os.fstat(descriptor).st_uid != os.getuid():
                raise SessionError('会话私有目录所有者不一致')
            if create:
                os.fchmod(descriptor, 0o700)
        if create:
            _ignore(descriptors[-1])
        _check_directories(root, descriptors, leaf)
        return descriptors
    except BaseException:
        for descriptor in descriptors:
            os.close(descriptor)
        raise


def _ignore(descriptor):
    try:
        fd = os.open('.gitignore', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=descriptor)
    except FileExistsError:
        fd = os.open('.gitignore', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(fd, 'rb') as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode) or file.read(16) != b'*\n':
                raise SessionError('会话忽略规则不安全')
    else:
        with os.fdopen(fd, 'wb') as file:
            file.write(b'*\n')
            file.flush()
            os.fsync(file.fileno())


def _check_directories(root: Path, descriptors: list[int], leaf='sessions'):
    current = root
    for name, fd in zip(('.mewcode', leaf), descriptors[1:]):
        current /= name
        actual, owned = current.lstat(), os.fstat(fd)
        if not stat.S_ISDIR(actual.st_mode) or (actual.st_dev, actual.st_ino) != (owned.st_dev, owned.st_ino):
            raise SessionError('会话目录真实路径发生变化')


def _close_directories(descriptors):
    for fd in reversed(descriptors):
        os.close(fd)


def _regular(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise SessionError('会话存档不是当前用户的普通文件')


def _locked(fd) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    fcntl.flock(fd, fcntl.LOCK_UN)
    return False


def _read(fd, identity: str, root: Path):
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while chunk := os.read(fd, 65536):
        chunks.append(chunk)
    raw = b''.join(chunks)
    records, warnings, ids, seqs = [], [], {}, set()
    maximum = 0
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line.decode('utf-8'), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            if isinstance(record, dict) and type(record.get('seq')) is int:
                maximum = max(maximum, record['seq'])
            if (not isinstance(record, dict) or record.get('schema_version') != 1
                    or type(record.get('schema_version')) is not int
                    or type(record.get('seq')) is not int or record['seq'] <= 0
                    or not isinstance(record.get('record_id'), str) or not record['record_id']
                    or not isinstance(record.get('kind'), str)):
                raise ValueError('记录版本、序号或身份无效')
            timestamp(record.get('timestamp'))
            record['payload'] = validate_payload(record['kind'], record.get('payload'), identity, record['seq'])
            if record['record_id'] in ids:
                if ids[record['record_id']] == record:
                    continue
                raise ValueError('记录身份重复冲突')
            if record['seq'] in seqs or (records and record['seq'] <= records[-1]['seq']):
                raise ValueError('记录顺序号重复或倒退')
            ids[record['record_id']] = record
            seqs.add(record['seq'])
            records.append(record)
        except (ValueError, TypeError, UnicodeError, KeyError) as error:
            warnings.append(f'会话第 {line_number} 行已跳过：{error}')
    if not records or records[0]['kind'] != 'session_created':
        raise SessionError('会话缺少有效身份记录')
    header = records[0]['payload']
    if (header.get('session_id') != identity or header.get('project_root') != str(root)
            or not isinstance(header.get('protocol'), str) or not header['protocol']
            or not isinstance(header.get('model'), str) or not header['model']):
        raise SessionError('会话身份或真实项目根不匹配')
    try:
        timestamp(header.get('created_at'))
    except ValueError as error:
        raise SessionError('会话创建时间无效') from error
    if any(record['kind'] == 'session_created' and record['payload'] != header for record in records[1:]):
        raise SessionError('会话身份记录冲突')
    return records, warnings, maximum, raw


class Journal:
    """持有排他文件锁；projection 是本次启动时的恢复快照。"""

    def __init__(self, root: Path, identity: str, fd: int, directories: list[int], *, storage_root=None):
        self.root, self.id, self._fd, self._directories = root, identity, fd, directories
        self.storage_root = storage_root or root
        self.path = self.storage_root / '.mewcode' / 'sessions' / f'{identity}.jsonl'
        self.seq = 0
        self.warnings: list[str] = []
        self.projection = Projection()
        self._failed = False
        self._resume_payload: dict | None = None
        self._resume_confirmed = False

    @classmethod
    def create(cls, root, protocol: str, model: str, *, storage_root=None):
        directories, fd = [], None
        try:
            root = _root(root)
            storage = _root(storage_root) if storage_root is not None else root
            if not isinstance(protocol, str) or not protocol or not isinstance(model, str) or not model:
                raise SessionError('协议或模型身份无效')
            directories = _directories(storage, create=True)
            prefix = datetime.now().strftime('%Y%m%d-%H%M%S')
            for _ in range(1000):
                identity = f'{prefix}-{secrets.token_hex(2)}'
                try:
                    fd = os.open(f'{identity}.jsonl', os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directories[-1])
                except FileExistsError:
                    continue
                break
            else:
                raise SessionError('无法分配唯一会话身份')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            journal = cls(root, identity, fd, directories, storage_root=storage)
            now = datetime.now(timezone.utc).isoformat()
            journal.append('session_created', {'session_id': identity, 'project_root': str(root),
                                              'protocol': protocol, 'model': model, 'created_at': now})
            return journal
        except (OSError, ValueError, SessionError) as error:
            if fd is not None:
                os.close(fd)
            _close_directories(directories)
            if isinstance(error, SessionError):
                raise
            raise SessionError(f'无法创建会话存档：{error}') from error

    @classmethod
    def resume(cls, root, identity: str, protocol: str, model: str, *, record_activity: bool = True, storage_root=None):
        directories, fd = [], None
        try:
            root = _root(root)
            storage = _root(storage_root) if storage_root is not None else root
            if not isinstance(protocol, str) or not protocol or not isinstance(model, str) or not model:
                raise SessionError('协议或模型身份无效')
            if identity == 'latest':
                if storage != root:
                    raise SessionError('成员恢复必须指定精确会话 ID')
                now = datetime.now(timezone.utc)
                candidates = [item for item in scan_sessions(root) if item.recoverable and not item.active
                              and timedelta() <= now - item.last_activity <= TTL]
                if not candidates:
                    raise SessionError('当前项目无可恢复的非活动会话')
                identity = candidates[0].id
            if not isinstance(identity, str) or not ID_PATTERN.fullmatch(identity):
                raise SessionError('会话 ID 格式无效')
            directories = _directories(storage)
            fd = os.open(f'{identity}.jsonl', os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directories[-1])
            _regular(fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise SessionError('会话仍处于活动状态，无法同时恢复') from error
            records, warnings, maximum, raw = _read(fd, identity, root)
            projection = build_projection(records, warnings)
            header = records[0]['payload']
            if header['protocol'] != protocol:
                raise SessionError('当前协议与会话存档协议不兼容')
            saved_model = header['model']
            for record in records:
                if record['kind'] == 'session_resumed':
                    if record['payload']['protocol'] != header['protocol']:
                        raise SessionError('存档恢复活动协议与身份不兼容')
                    saved_model = record['payload']['model']
            if saved_model != model and any(message.provider_content for message in projection.history):
                raise SessionError('供应商续接内容与当前模型不兼容')
            journal = cls(root, identity, fd, directories, storage_root=storage)
            journal.seq, journal.warnings, journal.projection = maximum, warnings, projection
            journal._resume_payload = {'session_id': identity, 'protocol': protocol, 'model': model}
            os.fchmod(fd, 0o600)
            if raw and not raw.endswith(b'\n'):
                if os.write(fd, b'\n') != 1:
                    raise OSError('残缺末行分隔写入失败')
                os.fsync(fd)
            if record_activity:
                journal.confirm_resume()
            return journal
        except (OSError, ValueError, SessionError) as error:
            if fd is not None:
                os.close(fd)
            _close_directories(directories)
            if isinstance(error, SessionError):
                raise
            raise SessionError(f'无法恢复会话存档：{error}') from error

    def confirm_resume(self) -> None:
        """当前缓存与预算已确认可继续时才记录活动；重复确认不重复追加。"""
        if self._resume_payload is not None and not self._resume_confirmed:
            self.append('session_resumed', self._resume_payload)
            self._resume_confirmed = True

    def append(self, kind: str, payload: dict, critical: bool = True) -> dict:
        if self._fd is None or self._failed:
            raise SessionError('会话存档已关闭或此前写入失败')
        try:
            _check_directories(self.storage_root, self._directories)
            actual, owned = self.path.lstat(), os.fstat(self._fd)
            if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (owned.st_dev, owned.st_ino):
                raise SessionError('会话存档真实路径发生变化')
            payload = json_value(payload)
            if kind in {'history_checkpoint', 'checkpoint'}:
                payload.setdefault('covers_seq', self.seq)
            payload = validate_payload(kind, payload, self.id, self.seq + 1)
            record = {'schema_version': 1, 'seq': self.seq + 1, 'record_id': uuid4().hex,
                      'timestamp': datetime.now(timezone.utc).isoformat(), 'kind': kind, 'payload': payload}
            encoded = (json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')
            position = 0
            while position < len(encoded):
                written = os.write(self._fd, encoded[position:])
                if written <= 0:
                    raise OSError('会话追加写入不完整')
                position += written
            if critical or kind in CRITICAL_KINDS:
                os.fsync(self._fd)
            self.seq += 1
            return record
        except (OSError, ValueError, SessionError) as error:
            self._failed = True
            raise SessionError(f'会话持久化失败：{error}') from error

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
            _close_directories(self._directories)
            self._directories = []


def scan_sessions(root) -> list[SessionInfo]:
    """只扫描 JSONL；缺失目录不创建目录或供应商。"""
    directories = []
    try:
        root = _root(root)
        try:
            directories = _directories(root)
        except FileNotFoundError:
            return []
        result = []
        for name in os.listdir(directories[-1]):
            if not name.endswith('.jsonl') or not ID_PATTERN.fullmatch(name[:-6]):
                continue
            info = SessionInfo(name[:-6])
            fd = None
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directories[-1])
                _regular(fd)
                info.active = _locked(fd)
                records, info.warnings, _, _ = _read(fd, info.id, root)
                projection = build_projection(records, info.warnings)
                info.last_activity = projection.last_activity
                task = next((r for r in records if r['kind'] == 'task_started'), None)
                if task:
                    info.title = next((line.strip()[:60] for line in task['payload']['input'].splitlines()
                                       if line.strip()), '未命名会话')
                committed = {}
                for record in records:
                    if record['kind'] == 'history_commit':
                        for message in record['payload']['messages']:
                            if message['role'] != 'context':
                                committed.setdefault(message['id'], message)
                for message in projection.history:
                    if message.role != 'context':
                        committed.setdefault(message.id, None)
                info.message_count = len(committed)
                info.recoverable = not info.active
            except (OSError, ValueError, SessionError) as error:
                info.warnings.append(str(error))
            finally:
                if fd is not None:
                    os.close(fd)
            result.append(info)
        return sorted(result, key=lambda info: (-info.last_activity.timestamp(), info.id))
    except (OSError, ValueError) as error:
        raise SessionError(f'会话扫描失败：{error}') from error
    finally:
        _close_directories(directories)


def _delete_cache(root: Path, identity: str, paths: set[str], warnings: list[str]) -> bool:
    directories, session_fd = [], None
    try:
        try:
            directories = _directories(root, leaf='context')
            session_fd = os.open(identity, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=directories[-1])
        except FileNotFoundError:
            return True
        if os.fstat(session_fd).st_uid != os.getuid():
            raise SessionError('结果目录所有者不一致')
        names = {Path(path).name for path in paths}
        present = set(os.listdir(session_fd))
        unknown = present - names
        if unknown:
            warnings.append(f'会话 {identity} 结果目录有未知文件，保留存档以供下次清理：{", ".join(sorted(unknown))}')
        failed = bool(unknown)
        for name in sorted(present & names):
            try:
                value = os.stat(name, dir_fd=session_fd, follow_symlinks=False)
                if not stat.S_ISREG(value.st_mode) or value.st_uid != os.getuid():
                    raise SessionError('登记结果不是当前用户的普通文件')
                _check_directories(root, directories, 'context')
                actual, owned = (root / '.mewcode/context' / identity).lstat(), os.fstat(session_fd)
                if not stat.S_ISDIR(actual.st_mode) or (actual.st_dev, actual.st_ino) != (owned.st_dev, owned.st_ino):
                    raise SessionError('结果会话目录真实路径发生变化')
                os.unlink(name, dir_fd=session_fd)
            except (OSError, SessionError) as error:
                failed = True
                warnings.append(f'会话 {identity} 结果清理失败：{name}：{error}')
        if not failed:
            _check_directories(root, directories, 'context')
            actual, owned = (root / '.mewcode/context' / identity).lstat(), os.fstat(session_fd)
            if not stat.S_ISDIR(actual.st_mode) or (actual.st_dev, actual.st_ino) != (owned.st_dev, owned.st_ino):
                raise SessionError('结果会话目录真实路径发生变化')
            os.rmdir(identity, dir_fd=directories[-1])
        return not failed
    except (OSError, SessionError) as error:
        warnings.append(f'会话 {identity} 结果清理失败：{error}')
        return False
    finally:
        if session_fd is not None:
            os.close(session_fd)
        _close_directories(directories)


def cleanup_expired(root, now: datetime | None = None) -> list[str]:
    """启动时清理超过三十天的非活动记录；未知文件保留且可重试。"""
    warnings, directories = [], []
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise SessionError('清理时间必须带时区')
    try:
        root = _root(root)
        infos = scan_sessions(root)
        context_directories = []
        try:
            context_directories = _directories(root, leaf='context')
            known = {info.id for info in infos}
            for name in sorted(os.listdir(context_directories[-1])):
                if name != '.gitignore' and name not in known:
                    warnings.append(f'缓存目录 {name} 无存档归属，未自动清理；请人工确认旧缓存。')
        except FileNotFoundError:
            pass
        except (OSError, SessionError) as error:
            warnings.append(f'缓存归属扫描失败：{error}')
        finally:
            _close_directories(context_directories)
        try:
            directories = _directories(root)
        except FileNotFoundError:
            return warnings
        for info in infos:
            if info.active:
                warnings.append(f'会话 {info.id} 正在活动，跳过清理')
                continue
            if not info.recoverable:
                warnings.append(f'会话 {info.id} 身份无效，跳过清理')
                continue
            if info.last_activity > now:
                warnings.append(f'会话 {info.id} 活动时间在未来，跳过清理')
                continue
            if now - info.last_activity <= TTL:
                continue
            fd = None
            try:
                name = f'{info.id}.jsonl'
                fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directories[-1])
                _regular(fd)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    warnings.append(f'会话 {info.id} 正在活动，跳过清理')
                    continue
                records, diagnostics, _, _ = _read(fd, info.id, root)
                projection = build_projection(records, diagnostics)
                if projection.last_activity > now or now - projection.last_activity <= TTL:
                    continue
                warnings.extend(f'会话 {info.id}：{message}' for message in diagnostics)
                if not _delete_cache(root, info.id, projection.cache_paths, warnings):
                    continue
                _check_directories(root, directories)
                actual, owned = os.stat(name, dir_fd=directories[-1], follow_symlinks=False), os.fstat(fd)
                if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (owned.st_dev, owned.st_ino):
                    raise SessionError('待清理存档真实路径发生变化')
                os.unlink(name, dir_fd=directories[-1])
            except (OSError, ValueError, SessionError) as error:
                warnings.append(f'会话 {info.id} 清理失败：{error}')
            finally:
                if fd is not None:
                    os.close(fd)
        return warnings
    except (OSError, ValueError, SessionError) as error:
        warnings.append(f'会话过期清理失败：{error}')
        return warnings
    finally:
        _close_directories(directories)
