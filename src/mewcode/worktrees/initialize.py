"""从明确清单补齐环境，记录可检查的初始化基线。"""

import fnmatch
import hashlib
import os
from pathlib import Path
import stat

from ..tools.base import ToolError
from .paths import directory_fd, read_file

FILE_LIMIT = 1024 * 1024
TOTAL_LIMIT = 16 * FILE_LIMIT
ENUM_LIMIT = 512


def fingerprint(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return {'kind': 'link', 'target': os.readlink(path)}
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('成果类型不是普通文件或登记链接')
    # 初始化文件有界；变大本身就使摘要不同，不能无界读入内存。
    raw = read_file(path, limit=FILE_LIMIT)
    return {'kind': 'file', 'hash': hashlib.sha256(raw).hexdigest(), 'mode': stat.S_IMODE(info.st_mode)}


def source_path(root, relative):
    path = root / relative
    with directory_fd(path.parent):
        pass
    return path


def matches(path, pattern):
    return fnmatch.fnmatchcase(path, pattern) or Path(path).match(pattern)


def enumerate_files(root, *, limit=ENUM_LIMIT):
    """不进入链接或 Git 目录；保守超限，避免扫描未知大目录。"""
    pending = [root]
    seen = 0
    while pending:
        directory = pending.pop()
        with directory_fd(directory) as fd:
            entries = []
            with os.scandir(fd) as iterator:
                for entry in iterator:
                    if entry.name == '.git':
                        continue
                    seen += 1
                    if seen > limit:
                        raise ValueError('文件枚举超限')
                    entries.append(entry.name)
            for name in sorted(entries):
                path = directory / name
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                else:
                    yield path, info


async def initialize(manager, tree, record, cancel_event):
    cfg = manager.config
    baseline, skipped = {}, []
    total, count = 0, 0

    async def copy(relative, required, *, ignored=False):
        nonlocal total, count
        manager.check_mutations(cancel_event)
        if relative.startswith(('.git/', '.mewcode/context/', '.mewcode/sessions/', '.mewcode/memory/', '.mewcode/worktrees/', '.mewcode/worktree-state/')) or relative == '.mewcode/permissions.local.yaml':
            raise ValueError('初始化不能搬迁运行记录和批准')
        try:
            source = source_path(tree.origin_root, relative)
            raw = read_file(source, limit=FILE_LIMIT)
            info = source.stat(follow_symlinks=False)
        except FileNotFoundError:
            if required:
                raise ValueError('必需配置缺失')
            skipped.append(relative)
            return
        if not ignored:
            # 普通配置也不能把有版本的源码改动带入子工作副本。
            tracked = await manager.git(tree.origin_root, 'ls-files', '--', relative, cancel_event=cancel_event)
            if tracked.strip():
                raise ValueError('配置复制项必须是非追踪本地文件')
        total += len(raw)
        count += 1
        if total > TOTAL_LIMIT or count > ENUM_LIMIT:
            raise ValueError('复制预算超限')
        target = tree.workspace_root / relative
        with directory_fd(target.parent, create=True) as parent:
            fd = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                os.fchmod(fd, stat.S_IMODE(info.st_mode))
                with os.fdopen(fd, 'wb', closefd=False) as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(fd)
            finally:
                os.close(fd)
        baseline[relative] = fingerprint(target)
        record['baseline'] = baseline.copy()
        manager.save(tree.name, record)

    try:
        for item in cfg.copy_files:
            await copy(item.path, item.required)
        if cfg.copy_ignored_files:
            # Git 提供忽略文件集合，限制输出和数量，不扫描大型依赖内容。
            ignored = await manager.git(tree.origin_root, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z', cancel_event=cancel_event, output_limit=65536)
            files = [name for name in ignored.split('\0') if name]
            if len(files) > ENUM_LIMIT:
                raise ValueError('忽略文件枚举超限')
            for item in cfg.copy_ignored_files:
                selected = [name for name in files if matches(name, item.path)]
                if item.required and not selected:
                    raise ValueError('必需忽略文件缺失')
                for name in selected:
                    if name in baseline:
                        raise ValueError('初始化清单目标重复')
                    await copy(name, True, ignored=True)
        for item in cfg.link_directories:
            manager.check_mutations(cancel_event)
            source = tree.origin_root / item.path
            try:
                with directory_fd(source):
                    pass
            except FileNotFoundError:
                if item.required:
                    raise ValueError('必需依赖目录缺失')
                skipped.append(item.path)
                continue
            target = tree.workspace_root / item.path
            with directory_fd(target.parent, create=True) as parent:
                os.symlink(str(source), target.name, dir_fd=parent)
            baseline[item.path] = fingerprint(target)
            record['baseline'] = baseline.copy()
            manager.save(tree.name, record)
        if cfg.hooks_path is not None:
            manager.check_mutations(cancel_event)
            hooks = tree.worktree_root / cfg.hooks_path
            with directory_fd(hooks):
                pass
            # 开启专属配置前拒绝需要迁移的共享 core.worktree／core.bare。
            for setting in ('core.worktree', 'core.bare'):
                value = await manager.git(tree.checkout_root, 'config', '--local', '--get', setting, acceptable=(0, 1), cancel_event=cancel_event)
                if value.strip() and not (setting == 'core.bare' and value.strip() == 'false'):
                    raise ValueError('现有 Git 配置不支持安全启用 worktreeConfig')
            # --worktree 能力通过只读命令检测，不猜版本号。
            help_text = await manager.git(tree.checkout_root, 'config', 'set', '-h', acceptable=(0, 129),
                                          include_stderr=True, cancel_event=cancel_event)
            if '--worktree' not in help_text and '--[no-]worktree' not in help_text:
                help_text = await manager.git(tree.checkout_root, 'config', '-h', acceptable=(0, 129),
                                              include_stderr=True, cancel_event=cancel_event)
                if '--worktree' not in help_text and '--[no-]worktree' not in help_text:
                    raise ValueError('Git 未提供工作树专属配置能力')
            await manager.git(tree.checkout_root, 'config', 'extensions.worktreeConfig', 'true', cancel_event=cancel_event)
            await manager.git(tree.worktree_root, 'config', '--worktree', 'core.hooksPath', str(hooks), cancel_event=cancel_event)
        record.update(baseline=baseline, skipped=skipped, regenerable=list(cfg.regenerable_paths))
    except (OSError, ValueError) as error:
        raise ToolError('worktree_initialization_error', '工作树环境初始化失败：' + str(error),
                        stage='initialization', side_effects='possible', not_started=False) from None
