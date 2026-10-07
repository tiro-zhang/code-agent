"""会话拥有的有界扫描：归属、过期无租约引用、成果保护三层过滤。"""

import asyncio
import itertools
import math
import os
from pathlib import Path
import re
import time

from ..async_utils import protected
from ..tools.base import ToolError
from .config import load_config
from .paths import RepositorySnapshot, directory_fd, freeze_repository
from .records import key, load_record


async def scan(manager, *, now=None):
    from .manager import WorktreeManager
    now = time.time() if now is None else now
    if type(now) not in (int, float) or not math.isfinite(now) or now <= 0:
        return []
    if manager.closed or manager.current_mode() == 'plan':
        return []
    cutoff, reports = now - manager.config.ttl_days * 86400, []
    try:
        with directory_fd(manager.state_root) as fd, os.scandir(fd) as entries:
            names = [entry.name for entry in itertools.islice(entries, 1024)
                     if re.fullmatch('[0-9a-f]{64}\\.json', entry.name)]
    except (OSError, ValueError):
        return []
    for name in names:
        if manager.closed or manager.current_mode() == 'plan':
            break
        try:
            record = load_record(manager.state_root/name)
            repo = manager.repository
            origin = Path(record['origin_root'])
            if (record['checkout_root'] != str(repo.checkout_root)
                    or record['common_git_dir'] != str(repo.common_git_dir)
                    or not origin.is_absolute() or not origin.is_relative_to(repo.checkout_root)
                    or origin != origin.resolve() or key(record['name'])+'.json' != name
                    or not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', record['base_commit'])
                    or not 0 < record['created'] <= record['last_active'] <= now):
                raise ValueError('记录归属或时间异常')
            if record.get('team_pin') or record['ready'] is not True or record['evidence_protected'] is not False or record['last_active'] >= cutoff:
                continue
            frozen = RepositorySnapshot(origin, repo.checkout_root, repo.common_git_dir,
                                        origin.relative_to(repo.checkout_root), record['base_commit'])
            candidate = WorktreeManager(frozen, manager.config, current_mode=manager.current_mode)
            tree = candidate.recover(record['name'], task_id=record['task_id'])
            reports.append(await candidate.delete(tree, expired_before=cutoff))
        except (OSError, ValueError, KeyError, TypeError, ToolError):
            # 未知归属与检测故障均保持原样，也不阻断其他准确记录。
            reports.append({'state': 'skipped', 'record': name,
                            'reason': '受管记录损坏、归属未知或安全检查失败，目录保持原样'})
            continue
    return reports


class WorktreeCleaner:
    def __init__(self, root, current_mode, warnings):
        self.root, self.current_mode, self.warnings = Path(root).resolve(), current_mode, warnings
        self.manager = self.task = self._scan = self._initializing = None
        self.closed = self.paused = False
        self._wake = asyncio.Event()
        self._scan_lock = asyncio.Lock()

    def start(self):
        if self.task is None and not self.closed:
            self.task = asyncio.create_task(self._run())

    def mode(self):
        return 'plan' if self.closed or self.paused else self.current_mode()

    def _initialize(self):
        from .manager import WorktreeManager
        try:
            repo = freeze_repository(self.root)
        except ToolError:
            return None
        return WorktreeManager(repo, load_config(self.root), current_mode=self.mode)

    async def ready(self):
        if self._initializing is None:
            self._initializing = asyncio.create_task(asyncio.to_thread(self._initialize))
        try:
            self.manager = await protected(self._initializing)
        except (ToolError, OSError, ValueError):
            if '工作树清理配置不可用，已跳过' not in self.warnings:
                self.warnings.append('工作树清理配置不可用，已跳过')

    async def scan_once(self):
        await self.ready()
        async with self._scan_lock:
            if self.manager is None or self.mode() == 'plan':
                return []
            self._scan = asyncio.create_task(self.manager.scan())
            try:
                reports = await asyncio.shield(self._scan)
                if any(report['state'] == 'skipped' for report in reports):
                    warning = '工作树扫描跳过不明记录；请核查私有 worktree-state，目录未删除'
                    if warning not in self.warnings:
                        self.warnings.append(warning)
                return reports
            except asyncio.CancelledError:
                return []
            except Exception:
                self.warnings.append('工作树定期扫描失败，本轮未完成；会话可继续')
                return []
            finally:
                if self._scan is not None and not self._scan.done():
                    self._scan.cancel()
                await protected(asyncio.gather(self._scan, return_exceptions=True))
                self._scan = None

    async def _run(self):
        await self.ready()
        if self.manager is None:
            return
        while not self.closed:
            self._wake.clear()
            await self.scan_once()
            if self.closed:
                return
            try:
                await asyncio.wait_for(self._wake.wait(), self.manager.config.interval_seconds)
            except TimeoutError:
                pass

    def suspend(self):
        self.paused = True
        if self._scan is not None:
            self._scan.cancel()

    async def pause(self):
        self.suspend()
        if self._scan is not None:
            await protected(asyncio.gather(self._scan, return_exceptions=True))

    def resume(self):
        if not self.closed:
            self.paused = False
            self._wake.set()

    def stop(self):
        self.closed = True
        self.suspend()
        self._wake.set()

    async def close(self):
        self.stop()
        await self.pause()
        self._wake.set()
        if self.task is not None:
            await protected(asyncio.gather(self.task, return_exceptions=True))
