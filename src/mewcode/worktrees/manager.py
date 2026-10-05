"""受管工作树的创建、恢复、进入、退出及保护删除。"""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import signal
import stat
import time

from ..async_utils import protected
from ..tools.base import ToolError
from .config import WorktreeConfig
from .initialize import enumerate_files, fingerprint, initialize, matches
from .paths import directory_fd, git_environment, managed_target, read_file
from .records import Lease, Worktree, key, load_record, private_directory, save_record


class WorktreeManager:
    def __init__(self, repository, config=None, *, current_mode=None):
        self.repository = repository
        self.config = config or WorktreeConfig()
        self.current_mode = current_mode or (lambda: 'execute')
        self.container = repository.checkout_root / '.mewcode/worktrees'
        self.state_root = repository.checkout_root / '.mewcode/worktree-state'
        self.closed = False

    def check_mutations(self, cancel_event=None):
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError
        if self.closed or self.current_mode() == 'plan':
            raise ToolError('worktree_readonly', '当前模式禁止创建、初始化或删除工作树', not_started=True)

    async def git(self, cwd, *args, cancel_event=None, acceptable=(0,), output_limit=1024*1024, include_stderr=False):
        """固定 cwd，取消时终止进程组并等待真实退出。"""
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError
        process = None
        reader = waiter = None
        try:
            with directory_fd(cwd):
                pass
            process = await asyncio.create_subprocess_exec('git', *args, cwd=cwd, env=git_environment(),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True,
                limit=65536)
            async def collect():
                async def bounded(stream):
                    output = bytearray()
                    while chunk := await stream.read(65536):
                        output.extend(chunk)
                        if len(output) > output_limit:
                            raise ValueError('Git 输出超限')
                    return bytes(output)
                out, err = await asyncio.gather(bounded(process.stdout), bounded(process.stderr))
                await process.wait()
                return out + err if include_stderr else out
            reader = asyncio.create_task(collect())
            waiter = asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
            waiting = (reader, waiter) if waiter else (reader,)
            done, _ = await asyncio.wait(waiting, timeout=30, return_when=asyncio.FIRST_COMPLETED)
            if reader not in done or (cancel_event is not None and cancel_event.is_set()):
                raise asyncio.CancelledError if cancel_event is not None and cancel_event.is_set() else TimeoutError()
            raw = reader.result()
            if process.returncode not in acceptable:
                raise ValueError('Git 命令失败')
            return raw.decode('utf-8')
        except (OSError, ValueError, TimeoutError, UnicodeError):
            raise ToolError('worktree_git_error', 'Git 工作树命令失败、超时或输出超限',
                            stage='git', side_effects='possible' if process else 'none', not_started=process is None) from None
        finally:
            if process is not None and process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await protected(asyncio.wait_for(process.wait(), 2), cancel_event=cancel_event)
                except TimeoutError:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await protected(process.wait(), cancel_event=cancel_event)
            for pending in (reader, waiter):
                if pending is not None and not pending.done():
                    pending.cancel()
            await protected(asyncio.gather(*(p for p in (reader, waiter) if p is not None), return_exceptions=True), cancel_event=cancel_event)

    def record_path(self, name):
        return self.state_root / (key(name) + '.json')

    def lock_path(self, name):
        return self.state_root / (key(name) + '.lock')

    def save(self, name, record):
        save_record(self.record_path(name), record)

    def recover(self, name, *, task_id=None):
        """完整快速路径仅使用读取，不碰 Git、锁或活动状态。"""
        target = managed_target(self.container, name)
        try:
            record = load_record(self.record_path(name))
            repo = self.repository
            expected = {'name': name, 'origin_root': str(repo.origin_root), 'checkout_root': str(repo.checkout_root),
                        'common_git_dir': str(repo.common_git_dir), 'worktree_root': str(target),
                        'workspace_root': str(target / repo.project_relative), 'base_commit': repo.base_commit}
            if any(record.get(k) != v for k, v in expected.items()) or record.get('ready') is not True:
                raise ValueError('记录不完整或仓库归属不同')
            if task_id is not None and record.get('task_id') != task_id:
                raise ValueError('来源任务不一致')
            branch = record['branch']
            if branch != 'mewcode/worktree/' + key(name):
                raise ValueError('分支身份不一致')
            with directory_fd(target / repo.project_relative):
                pass
            pointer = read_file(target / '.git', limit=4096).decode().strip()
            if not pointer.startswith('gitdir: '):
                raise ValueError('Git 指针无效')
            gitdir = Path(pointer[8:])
            if not gitdir.is_absolute():
                gitdir = target / gitdir
            gitdir = Path(os.path.abspath(gitdir))
            if str(gitdir) != record['git_dir'] or gitdir.parent != repo.common_git_dir / 'worktrees':
                raise ValueError('Git 管理目录不属于当前仓库')
            with directory_fd(gitdir):
                pass
            common = read_file(gitdir/'commondir', limit=4096).decode().strip()
            if Path(os.path.abspath(gitdir/common)) != repo.common_git_dir:
                raise ValueError('共享目录不同')
            if read_file(gitdir/'gitdir', limit=4096).decode().strip() != str(target / '.git'):
                raise ValueError('反向关联不同')
            if read_file(gitdir/'HEAD', limit=4096).decode().strip() != 'ref: refs/heads/' + branch:
                raise ValueError('预期分支不同')
            return Worktree(name, record['task_id'], repo.origin_root, repo.checkout_root, repo.common_git_dir,
                            target, target/repo.project_relative, branch, repo.base_commit, gitdir, True)
        except (OSError, ValueError, KeyError, TypeError, UnicodeError):
            raise ToolError('worktree_state_error', '已有目录未知、初始化不完整或 Git 关联损坏；未修复',
                            stage='recovery', not_started=True) from None

    async def create(self, name, *, task_id, cancel_event=None):
        target = managed_target(self.container, name)
        if target.exists():
            return self.recover(name, task_id=task_id)
        self.check_mutations(cancel_event)
        repo = self.repository
        tracked = await self.git(repo.checkout_root, 'ls-files', '--', '.mewcode/worktrees', '.mewcode/worktree-state', cancel_event=cancel_event)
        if tracked.strip():
            raise ToolError('worktree_path_error', '管理路径已被 Git 追踪，拒绝创建', not_started=True)
        private_directory(self.state_root)
        lease = Lease(self.lock_path(name))
        record = None
        try:
            lease.check()
            target = managed_target(self.container, name)
            if target.exists():
                raise ToolError('worktree_busy', '创建期间同名目录已出现', not_started=True)
            if self.record_path(name).exists():
                raise ToolError('worktree_state_error', '同名残留记录需人工检查，未接管', not_started=True)
            self.check_mutations(cancel_event)
            private_directory(self.container)
            # 私有忽略安排不编辑项目源码。公共 exclude 对各工作树生效。
            exclude = repo.common_git_dir / 'info/exclude'
            with directory_fd(exclude.parent, create=True) as parent:
                fd = os.open(exclude.name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                try:
                    os.write(fd, b'\n/.mewcode/worktrees/\n/.mewcode/worktree-state/\n')
                finally:
                    os.close(fd)
            with directory_fd(target.parent, create=True):
                pass
            branch = 'mewcode/worktree/' + key(name)
            await self.git(repo.checkout_root, 'check-ref-format', '--branch', branch, cancel_event=cancel_event)
            record = {'version': 1, 'ready': False, 'name': name, 'task_id': task_id,
                      'origin_root': str(repo.origin_root), 'checkout_root': str(repo.checkout_root),
                      'common_git_dir': str(repo.common_git_dir), 'worktree_root': str(target),
                      'workspace_root': str(target/repo.project_relative), 'branch': branch,
                      'base_commit': repo.base_commit, 'created': time.time(), 'last_active': time.time(),
                      'evidence_protected': False, 'baseline': {}, 'stage': 'creating'}
            self.save(name, record)
            await self.git(repo.checkout_root, 'worktree', 'add', '-b', branch, '--', str(target), repo.base_commit, cancel_event=cancel_event)
            private_directory(target)
            pointer = read_file(target/'.git', limit=4096).decode().strip()
            gitdir = Path(pointer.removeprefix('gitdir: ')).resolve(strict=True)
            tree = Worktree(name, task_id, repo.origin_root, repo.checkout_root, repo.common_git_dir, target,
                            target/repo.project_relative, branch, repo.base_commit, gitdir)
            record.update(git_dir=str(gitdir), stage='initializing')
            self.save(name, record)
            await initialize(self, tree, record, cancel_event)
            self.check_mutations(cancel_event)
            lease.check()
            managed_target(self.container, name)
            with directory_fd(tree.workspace_root):
                pass
            record.update(ready=True, stage='ready', last_active=time.time())
            self.save(name, record)
            return tree
        except BaseException as error:
            if record is not None:
                record.update(ready=False, stage='incomplete', failure=type(error).__name__)
                try:
                    self.save(name, record)
                except (OSError, ValueError):
                    pass
            raise
        finally:
            lease.close()

    def enter(self, tree):
        lease = Lease(self.lock_path(tree.name), tree)
        try:
            lease.check()
            self.recover(tree.name, task_id=tree.task_id)
            record = load_record(self.record_path(tree.name))
            record.update(evidence_protected=True, stage='running', last_active=time.time())
            self.save(tree.name, record)
            return lease
        except BaseException:
            lease.close()
            raise

    async def exit(self, lease, *, evidence_safe=False):
        tree = lease.tree
        try:
            lease.check()
            record = load_record(self.record_path(tree.name))
            record.update(evidence_protected=not evidence_safe, stage='stopped', last_active=time.time())
            self.save(tree.name, record)
        finally:
            lease.close()
        return await self.delete(tree)

    async def _protected_reason(self, tree, record):
        if record.get('evidence_protected') is not False:
            return '证据尚未归档或仍有引用'
        if (await self.git(tree.worktree_root, 'rev-parse', '--verify', 'HEAD')).strip() != tree.base_commit:
            return '存在提交成果或分支已改变'
        if (await self.git(tree.worktree_root, 'diff', '--name-only', 'HEAD')).strip():
            return '存在暂存或未暂存修改'
        baseline = record['baseline']
        for relative, expected in baseline.items():
            if fingerprint(tree.workspace_root/relative) != expected:
                return '初始化配置或链接已改变'
        tracked = set((await self.git(tree.worktree_root, 'ls-files', '-z')).split('\0'))
        # 枚举上限是保守拒绝条件；不能把无法核对的大目录当作空目录。
        for path, info in enumerate_files(tree.worktree_root, limit=10000):
            relative = path.relative_to(tree.worktree_root).as_posix()
            if relative in tracked:
                continue
            project_relative = path.relative_to(tree.workspace_root).as_posix() if path.is_relative_to(tree.workspace_root) else None
            if project_relative in baseline:
                continue
            if project_relative and any(matches(project_relative, p) for p in record.get('regenerable', ())):
                if stat.S_ISREG(info.st_mode):
                    continue
            return '存在未追踪或未知忽略成果'
        return None

    async def scan(self, *, now=None):
        from .cleanup import scan
        return await scan(self, now=now)

    async def delete(self, tree, *, expired_before=None):
        report = {'state': 'retained', 'branch_state': 'retained', 'reason': '', **tree.metadata()}
        lease = None
        try:
            self.check_mutations()
            lease = Lease(self.lock_path(tree.name))
            lease.check()
            self.recover(tree.name, task_id=tree.task_id)
            record = load_record(self.record_path(tree.name))
            if expired_before is not None and (record['last_active'] >= expired_before
                    or record['created'] > record['last_active']):
                report['reason'] = '活动时间更新或异常，未清理'
                return report
            reason = await self._protected_reason(tree, record)
            if reason:
                report['reason'] = reason
                return report
            # 锁内在副作用前复查。仅移除刚核对的初始化／可再生非追踪文件。
            self.check_mutations()
            lease.check()
            self.recover(tree.name, task_id=tree.task_id)
            reason = await self._protected_reason(tree, record)
            if reason:
                report['reason'] = reason
                return report
            tracked = set((await self.git(tree.worktree_root, 'ls-files', '-z')).split('\0'))
            disposable = []
            for path, info in enumerate_files(tree.worktree_root, limit=10000):
                if path.relative_to(tree.worktree_root).as_posix() in tracked:
                    continue
                relative = path.relative_to(tree.workspace_root).as_posix() if path.is_relative_to(tree.workspace_root) else None
                expected = record['baseline'].get(relative)
                if expected is not None:
                    if fingerprint(path) != expected:
                        raise ValueError('初始化内容在删除前改变')
                elif relative is None or not stat.S_ISREG(info.st_mode) or not any(matches(relative, p) for p in record.get('regenerable', ())):
                    raise ValueError('删除前出现未知成果')
                disposable.append((path, info.st_dev, info.st_ino))
            for path, dev, ino in disposable:
                self.check_mutations()
                lease.check()
                with directory_fd(path.parent) as parent:
                    info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                    if (info.st_dev, info.st_ino) != (dev, ino):
                        raise ValueError('待删除文件已被替换')
                    os.unlink(path.name, dir_fd=parent)
            self.check_mutations()
            lease.check()
            managed_target(self.container, tree.name)
            await self.git(tree.checkout_root, 'worktree', 'remove', '--', str(tree.worktree_root))
            report.update(state='removed', reason='无文件或提交成果且证据已归档')
            # 精确旧提交比较后删除应用 ref，不依赖父当前分支的合并关系。
            try:
                await self.git(tree.checkout_root, 'update-ref', '-d', 'refs/heads/'+tree.branch, tree.base_commit)
                report['branch_state'] = 'removed'
            except ToolError:
                report['reason'] = '目录已删除，分支删除失败'
            record.update(ready=False, stage='removed', deletion=report)
            self.save(tree.name, record)
        except (OSError, ValueError, KeyError, ToolError) as error:
            report['reason'] = getattr(error, 'message', '状态不明或保护检查失败，保留工作树')
        finally:
            if lease:
                lease.close()
        return report
