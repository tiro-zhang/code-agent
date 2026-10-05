"""受管记录与真实跨进程排他租约。"""

from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import re
import os
from pathlib import Path
import stat
from uuid import uuid4

from ..tools.base import ToolError
from .paths import directory_fd, read_file
from .config import relative_path, WorktreeConfig


def private_directory(path):
    with directory_fd(path, create=True) as fd:
        info = os.fstat(fd)
        if info.st_uid != os.getuid():
            raise ValueError('目录不属于当前用户')
        os.fchmod(fd, 0o700)


def key(name):
    return hashlib.sha256(name.encode()).hexdigest()


def load_record(path):
    data = json.loads(read_file(path).decode('utf-8'))
    if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1:
        raise ValueError('未知受管记录')
    if type(data.get('ready')) is not bool or type(data.get('evidence_protected')) is not bool:
        raise ValueError('状态标记无效')
    for field in ('created', 'last_active'):
        value = data.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('活动时间无效')
    baseline = data.get('baseline')
    if not isinstance(baseline, dict) or len(baseline) > 512:
        raise ValueError('初始化摘要无效')
    for path, entry in baseline.items():
        relative_path(path)
        if not isinstance(entry, dict):
            raise ValueError('初始化文件摘要无效')
        if entry.get('kind') == 'file':
            if not re.fullmatch('[0-9a-f]{64}', str(entry.get('hash', ''))) or type(entry.get('mode')) is not int:
                raise ValueError('初始化文件摘要无效')
        elif entry.get('kind') == 'link':
            if entry.get('target') != str(Path(data['origin_root']) / path):
                raise ValueError('初始化链接目标无效')
        else:
            raise ValueError('初始化文件类型无效')
    regen = data.get('regenerable', [])
    if not isinstance(regen, list) or len(regen) > 512:
        raise ValueError('产物规则无效')
    for pattern in regen:
        if pattern not in WorktreeConfig().regenerable_paths:
            relative_path(pattern, pattern=True)
    return data


def save_record(path, data):
    """同目录原子发布，固定祖先且不跟随文件链接。"""
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
    temporary = '.record-' + uuid4().hex
    with directory_fd(path.parent) as parent:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(fd)
            os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(fd)
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


@dataclass(frozen=True)
class Worktree:
    name: str
    task_id: str
    origin_root: Path
    checkout_root: Path
    common_git_dir: Path
    worktree_root: Path
    workspace_root: Path
    branch: str
    base_commit: str
    git_dir: Path
    recovered: bool = False

    def metadata(self):
        return {field: str(getattr(self, field)) for field in
                ('name', 'task_id', 'origin_root', 'checkout_root', 'common_git_dir',
                 'worktree_root', 'workspace_root', 'branch', 'base_commit', 'git_dir')}


class Lease:
    """创建与运行共用精确目标的真实 flock；所有者自行收尾释放。"""

    def __init__(self, path, tree=None):
        self.path, self.tree = path, tree
        self.fd = None
        try:
            with directory_fd(path.parent) as parent:
                fd = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                    raise ValueError('锁文件归属不明')
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                os.close(fd)
                raise
            self.fd = fd
        except (OSError, ValueError):
            raise ToolError('worktree_busy', '工作树正在使用或租约无法安全取得', not_started=True) from None

    def check(self):
        if self.fd is None:
            raise ValueError('租约已释放')
        with directory_fd(self.path.parent) as parent:
            current = os.stat(self.path.name, dir_fd=parent, follow_symlinks=False)
        held = os.fstat(self.fd)
        if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
            raise ValueError('锁文件已被替换')

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
