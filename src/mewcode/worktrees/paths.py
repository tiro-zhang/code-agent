"""原始名称、真实目录与冻结仓库身份的边界。"""

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import subprocess

from ..tools.base import ToolError


def git_environment():
    """目录必须来自显式 cwd，不能被继承的 Git 定位变量重定向。"""
    return {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}


def git_read(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(['git', *args], cwd=root, env=git_environment(),
                                capture_output=True, timeout=10, check=True)
        return result.stdout.decode('utf-8').strip()
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise ToolError('worktree_repository_error', '无法核对 Git 仓库身份或提交', not_started=True) from None


@dataclass(frozen=True)
class RepositorySnapshot:
    origin_root: Path
    checkout_root: Path
    common_git_dir: Path
    project_relative: Path
    base_commit: str


def freeze_repository(root: Path) -> RepositorySnapshot:
    """接受任务时仅查询仓库，允许父目录未提交修改。"""
    try:
        origin = Path(root).resolve(strict=True)
        if git_read(origin, 'rev-parse', '--is-inside-work-tree') != 'true':
            raise ValueError('不支持裸仓库')
        if git_read(origin, 'rev-parse', '--show-superproject-working-tree'):
            raise ValueError('不支持子模块')
        checkout = Path(git_read(origin, 'rev-parse', '--show-toplevel')).resolve(strict=True)
        common = Path(git_read(origin, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve(strict=True)
        relative = origin.relative_to(checkout)
        commit = git_read(origin, 'rev-parse', '--verify', 'HEAD^{commit}')
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
            raise ValueError('提交身份无效')
        if relative != Path('.') and git_read(origin, 'cat-file', '-t', f'{commit}:{relative.as_posix()}') != 'tree':
            raise ValueError('基线不存在项目子目录')
        if any(line.startswith('160000 ') for line in git_read(origin, 'ls-tree', '-r', commit).splitlines()):
            raise ValueError('第一版不支持包含子模块的布局')
        return RepositorySnapshot(origin, checkout, common, relative, commit)
    except (OSError, ValueError, ToolError):
        raise ToolError('worktree_repository_error', '工作目录必须属于有提交且布局受支持的 Git 仓库', not_started=True) from None


def validate_name(name: str) -> tuple[str, ...]:
    if not isinstance(name, str) or not name or len(name) > 128:
        raise ToolError('invalid_worktree_name', '工作树名称为空或超长', not_started=True)
    parts = tuple(name.split('/'))
    if len(parts) > 4 or any(re.fullmatch(r'[A-Za-z0-9_-]{1,64}', part) is None for part in parts):
        raise ToolError('invalid_worktree_name', '工作树名称字符、路径段或深度无效', not_started=True)
    return parts


@contextmanager
def directory_fd(path: Path, *, create=False):
    """逐级打开而非跟随链接；持有描述符以便后续操作固定祖先。"""
    absolute = Path(os.path.abspath(path))
    fd = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in absolute.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        yield fd
    finally:
        os.close(fd)


def read_file(path: Path, *, limit=1024 * 1024) -> bytes:
    with directory_fd(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError('不是有界普通文件')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise ValueError('读取超限')
            return raw
        finally:
            os.close(fd)


def managed_target(container: Path, name: str) -> Path:
    parts = validate_name(name)
    container = Path(os.path.abspath(container))
    target = container.joinpath(*parts)
    try:
        cursor = Path(container.anchor)
        for segment in (*container.parts[1:], *parts):
            cursor /= segment
            if cursor.is_symlink():
                raise ValueError('祖先是链接')
            if cursor.exists() and not cursor.is_dir():
                raise ValueError('祖先不是目录')
            if cursor != target and cursor.is_relative_to(container) and (cursor / '.git').exists():
                raise ValueError('工作树不能嵌套')
        if target.exists() and not (target / '.git').exists():
            pending = [target]
            visited = 0
            while pending:
                current = pending.pop()
                visited += 1
                if visited > 512:
                    raise ValueError('分组目录检查超限')
                if (current / '.git').exists():
                    raise ValueError('目标包含工作树')
                pending.extend(p for p in current.iterdir() if p.is_dir() and not p.is_symlink())
        return target
    except (OSError, ValueError):
        raise ToolError('worktree_path_error', '受管工作树路径越界、嵌套或无法安全检查', not_started=True) from None
