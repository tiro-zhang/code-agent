"""团队目标的串行 Git 整合、成员租约及保守故障对账。"""

import asyncio
from contextlib import contextmanager
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from ..async_utils import protected
from ..tools.base import ToolError
from ..worktrees.manager import WorktreeManager
from ..worktrees.paths import directory_fd, freeze_repository, git_environment, read_file
from ..worktrees.records import Lease, private_directory, save_record


class IntegrationService:
    """Git 服务只接收可信已验收记录；角色和任务授权由团队入口负责。"""

    def __init__(self, repo_root, state_path, goal_id, base_commit, target_branch, *, current_mode=None, authorize=None):
        if not isinstance(goal_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', goal_id):
            raise ToolError('integration_identity', '目标身份无效', not_started=True)
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', str(base_commit)):
            raise ToolError('integration_identity', '必须使用完整不可变提交', not_started=True)
        self.repository = freeze_repository(Path(repo_root))
        self.manager = WorktreeManager(self.repository, current_mode=current_mode)
        self.current_mode = current_mode or (lambda: 'execute')
        self.authorize = authorize
        self._cleanup_approved = {}
        self.goal_id, self.base_commit, self.target_branch = goal_id, base_commit, target_branch
        self.root = Path(os.path.abspath(state_path)) / goal_id
        self.record_path = self.root / 'goal.json'
        self.worktree_root = self.root / 'worktree'
        digest = hashlib.sha256(str(self.root).encode()).hexdigest()[:24]
        self.branch = f'mewcode/team/{digest}/{goal_id}'
        self.lock_path = self.root / 'integration.lock'
        self.workspace_lock = self.root / 'workspace.lock'

    def _error(self, message, code='integration_safety'):
        return ToolError(code, message, not_started=True)

    def _check_mode(self):
        if self.current_mode() == 'plan':
            raise self._error('规划模式禁止 Git 整合与同步', 'integration_readonly')

    @contextmanager
    def _locked(self, *, workspace=True):
        private_directory(self.root)
        state_lease = Lease(self.lock_path)
        tree_lease = None
        try:
            state_lease.check()
            if workspace:
                tree_lease = Lease(self.workspace_lock)
                tree_lease.check()
            yield
            state_lease.check()
        finally:
            if tree_lease is not None:
                tree_lease.close()
            state_lease.close()

    async def _authorize_git(self, root, *args):
        if self.authorize is not None:
            try:
                await self.authorize(Path(root), ('-c', 'core.hooksPath=/dev/null', *args))
            except ToolError as error:
                error.git_authorization_rejected = True
                raise

    def _discard_prepared(self, previous, operation, *, sync=False, receipt=None):
        """执行前策略复查拒绝时恢复原快照，不伪造一次成功回滚。"""
        save_record(self.record_path, previous)
        path = self.root / (('sync-' if sync else '') + operation['operation_id'] + '.json')
        path.unlink()
        if receipt is not None:
            receipt.unlink()
        self._cleanup_approved.pop(operation['operation_id'], None)

    def _mutates(self, args):
        offset = 0
        while offset < len(args) and args[offset] == '-c':
            offset += 2
        command = args[offset:]
        return bool(command and (command[0] in {'merge', 'reset', 'update-ref', 'read-tree', 'write-tree'}
                    or (command[0] == 'worktree' and len(command) > 1 and command[1] != 'list')))

    def _cleanup_allowed(self, root, args, operation):
        if operation is None:
            return False
        approved = self._cleanup_approved.get(operation['operation_id'])
        return (approved == (Path(root), operation['pre_head']) and
                args in {('merge', '--abort'), ('reset', '--hard', operation['pre_head'])})

    async def _git(self, root, *args, acceptable=(0,), cleanup=None):
        if self._mutates(args) and not self._cleanup_allowed(root, args, cleanup):
            self._check_mode()
            await self._authorize_git(root, *args)
            self._check_mode()
        return (await self.manager.git(Path(root), '-c', 'core.hooksPath=/dev/null', *args,
                                       acceptable=acceptable)).strip()

    def _identity(self):
        return {'common_git_dir': str(self.repository.common_git_dir),
                'checkout_root': str(self.repository.checkout_root), 'goal_id': self.goal_id,
                'base_commit': self.base_commit, 'target_branch': self.target_branch,
                'branch': self.branch, 'worktree_root': str(self.worktree_root)}

    def _load(self):
        try:
            state = json.loads(read_file(self.record_path))
            if not isinstance(state, dict) or type(state.get('version')) is not int or state['version'] != 1 or any(state.get(k) != v for k, v in self._identity().items()):
                raise ValueError('目标记录身份不一致')
            if not isinstance(state.get('operations'), list) or len(state['operations']) > 4096:
                raise ValueError('操作列表无效或超限')
            if not isinstance(state.get('sync_operations', []), list) or len(state.get('sync_operations', [])) > 4096:
                raise ValueError('同步列表无效或超限')
            if state.get('state') not in {'initializing', 'ready', 'integrating', 'syncing', 'conflict', 'needs_review', 'published'} or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', str(state.get('head'))):
                raise ValueError('目标状态或提交无效')
            return state
        except (OSError, ValueError, TypeError):
            raise self._error('目标记录损坏、缺失或仓库归属不一致') from None

    def _save(self, state):
        state['updated_at'] = time.time()
        save_record(self.record_path, state)

    def _save_operation(self, operation):
        save_record(self.root / (operation['operation_id'] + '.json'), operation)

    def _save_sync(self, operation):
        save_record(self.root / ('sync-' + operation['operation_id'] + '.json'), operation)

    def _operation(self, state, operation_id):
        if not isinstance(operation_id, str) or operation_id not in state['operations'] or not re.fullmatch(r'[0-9a-f]{32}', operation_id):
            raise self._error('未知整合操作')
        record = json.loads(read_file(self.root / (operation_id + '.json')))
        if not isinstance(record, dict) or record.get('version') != 1 or record.get('goal_id') != self.goal_id or record.get('operation_id') != operation_id:
            raise self._error('操作身份无效')
        if record.get('kind') not in {'integrate', 'finalize'} or record.get('state') not in {
            'prepared', 'git_applied', 'conflict', 'rolled_back', 'needs_review', 'published', 'validation_failed', 'publishing'
        } or any(not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', str(record.get(field))) for field in ('pre_head', 'input_commit')):
            raise self._error('操作记录格式损坏')
        return record

    async def _same_repository(self, root):
        snapshot = freeze_repository(Path(root))
        if snapshot.common_git_dir != self.repository.common_git_dir:
            raise self._error('成果来自其他仓库')
        return snapshot.checkout_root

    async def _managed(self):
        await self._same_repository(self.worktree_root)
        branch = await self._git(self.worktree_root, 'symbolic-ref', '--short', 'HEAD')
        if branch != self.branch:
            raise self._error('整合目录分支已被外部改变')
        return await self._git(self.worktree_root, 'rev-parse', 'HEAD')

    async def _clean(self, root):
        if await self._git(root, 'status', '--porcelain', '--untracked-files=all'):
            raise self._error('目录存在未知未提交修改，禁止覆盖')
        # MERGE_HEAD 即使没有冲突文件也表示操作尚未结束。
        merge_path = Path(await self._git(root, 'rev-parse', '--path-format=absolute', '--git-path', 'MERGE_HEAD'))
        if merge_path.exists():
            raise self._error('目录已有未完成 Git 合并')

    def _available(self, state):
        if state['state'] != 'ready' or state.get('active_operation'):
            raise self._error('整合尚未结束或需要人工核查', 'integration_blocked')

    async def _protect_incoming(self, root, before, after):
        """Git 默认可覆盖忽略文件；新增路径也必须核查未知本地内容。"""
        raw = await self._git(root, 'diff', '--name-only', '--diff-filter=A', '-z', before, after)
        for name in raw.split('\0'):
            if not name:
                continue
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts:
                raise self._error('Git 成果路径无效')
            target = Path(root) / relative
            if os.path.lexists(target):
                raise self._error('新增成果路径存在未知本地文件，禁止覆盖')
            parent = target.parent
            while parent != Path(root):
                if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                    raise self._error('新增成果路径祖先存在未知本地内容')
                parent = parent.parent

    async def initialize(self):
        self._check_mode()
        # 首次授权必须早于创建状态根、固定锁及初始化记录。
        if not self.record_path.exists():
            await self._authorize_git(self.repository.checkout_root, 'worktree', 'add', '-b', self.branch,
                                      str(self.worktree_root), self.base_commit)
        with self._locked():
            await self._same_repository(self.repository.checkout_root)
            await self._git(self.repository.checkout_root, 'check-ref-format', '--branch', self.target_branch)
            resolved = await self._git(self.repository.checkout_root, 'rev-parse', '--verify', self.base_commit + '^{commit}')
            if resolved != self.base_commit:
                raise self._error('基线提交无效')
            if self.record_path.exists():
                state = self._load()
                if state['state'] == 'initializing':
                    raise self._error('目标初始化曾中断，需要核查')
                await self._managed()
                return state
            target = await self._git(self.repository.checkout_root, 'rev-parse', '--verify', 'refs/heads/' + self.target_branch)
            if target != self.base_commit:
                raise self._error('用户目标分支已经偏离基线')
            state = {'version': 1, **self._identity(), 'state': 'initializing', 'operations': [],
                     'active_operation': None, 'head': self.base_commit, 'created_at': time.time(),
                     'sync_operations': [], 'active_sync': None,
                     'evidence_protected': True, 'team_owner': str(self.root.parent), 'goal_owner': self.goal_id}
            self._save(state)
            try:
                await self._git(self.repository.checkout_root, 'worktree', 'add', '-b', self.branch,
                                str(self.worktree_root), self.base_commit)
            except ToolError as error:
                if getattr(error, 'git_authorization_rejected', False):
                    self.record_path.unlink()
                raise
            state['state'] = 'ready'
            self._save(state)
            return state

    async def status(self):
        with self._locked(workspace=False):
            state = self._load()
            return {**state, 'operation_records': [self._operation(state, op) for op in state['operations']]}

    def _receipt_path(self, task_id, member_branch, commit):
        if not isinstance(task_id, str) or not task_id or len(task_id) > 128:
            raise self._error('成果任务身份无效')
        digest = hashlib.sha256(json.dumps([task_id, member_branch, commit]).encode()).hexdigest()
        return self.root / ('input-' + digest + '.json')

    def _input_receipt(self, task_id, member_root, member_branch, commit):
        path = self._receipt_path(task_id, member_branch, commit)
        try:
            receipt = json.loads(read_file(path))
        except FileNotFoundError:
            return None
        expected = {**self._identity(), 'task_id': task_id, 'member_root': str(Path(member_root).resolve()),
                    'member_branch': member_branch, 'input_commit': commit, 'accepted': True}
        if not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in expected.items()):
            raise self._error('冻结成果凭据损坏或归属不一致')
        return receipt

    async def register_input(self, task_id, member_root, member_branch, commit, *, accepted):
        """Lead 接纳时冻结归属证据；分支之后移动不改变已接纳输入。"""
        self._check_mode()
        with self._locked(workspace=False):
            self._load()
            existing = self._input_receipt(task_id, member_root, member_branch, commit)
            root = await self._validate_input(member_root, member_branch, commit, accepted, frozen=existing is not None)
            if existing is not None:
                return existing
            receipt = {'version': 1, **self._identity(), 'task_id': task_id, 'member_root': str(Path(member_root).resolve()),
                       'member_branch': member_branch, 'input_commit': commit, 'accepted': True,
                       'registered_head': await self._git(root, 'rev-parse', 'HEAD'), 'registered_at': time.time()}
            save_record(self._receipt_path(task_id, member_branch, commit), receipt)
            return receipt

    async def _validate_input(self, member_root, member_branch, commit, accepted, *, frozen=False):
        if accepted is not True:
            raise self._error('仅可整合 Lead 已接纳的成果')
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', str(commit)):
            raise self._error('成果必须是完整不可变提交')
        root = await self._same_repository(member_root)
        actual_branch = await self._git(root, 'symbolic-ref', '--short', 'HEAD')
        if actual_branch != member_branch:
            raise self._error('成果目录与登记成员分支不一致')
        await self._git(root, 'check-ref-format', '--branch', member_branch)
        await self._git(root, 'rev-parse', '--verify', 'refs/heads/' + member_branch)
        identity = await self._git(root, 'rev-parse', '--verify', commit + '^{commit}')
        if identity != commit:
            raise self._error('成果提交身份无效')
        # 分支可以继续前进；已验收提交仍必须属于登记分支及目标基线。
        await self._git(root, 'merge-base', '--is-ancestor', self.base_commit, commit)
        if not frozen:
            await self._git(root, 'merge-base', '--is-ancestor', commit, 'refs/heads/' + member_branch)
        return root

    def _start(self, state, kind, pre_head, **fields):
        if len(state['operations']) >= 4096:
            raise self._error('目标整合操作容量已满')
        operation = {'version': 1, 'operation_id': uuid4().hex, 'goal_id': self.goal_id,
                     'kind': kind, 'state': 'prepared', 'pre_head': pre_head, 'result_head': None,
                     'created_at': time.time(), 'validation': None, 'rollback': None,
                     'side_effects': {'git': 'not_started', 'hooks': 'disabled_for_managed_git',
                                      'shell': 'not_rolled_back', 'remote': 'not_performed'}, **fields}
        # 登记引用先于 Git；文件缺失会进入 needs_review，不推断为未执行。
        state['operations'].append(operation['operation_id'])
        state['active_operation'] = operation['operation_id']
        state['state'] = 'integrating'
        self._save(state)
        self._save_operation(operation)
        return operation

    async def _validation(self, validate, root, head):
        if validate is None:
            raise self._error('缺少约定验证程序')
        try:
            result = validate(Path(root), head)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            raise self._error('约定验证程序失败，当前成果尚未发布') from None
        if not isinstance(result, dict) or type(result.get('ok')) is not bool:
            raise self._error('验证必须返回含 ok 布尔字段的证据')
        # 持久证据必须有界并可编码，不能接受无法保存的验证结果。
        if len(json.dumps(result, ensure_ascii=False).encode()) > 256 * 1024:
            raise self._error('验证证据超限')
        return result

    async def _working_evidence(self, root):
        """撤销前核对冲突副作用范围，未知后续编辑不能被 abort 丢弃。"""
        digest = hashlib.sha256()
        for args in (('diff', '--binary', 'HEAD'), ('diff', '--cached', '--binary', 'HEAD'),
                     ('status', '--porcelain', '--untracked-files=all')):
            digest.update((await self._git(root, *args)).encode())
        return digest.hexdigest()

    async def _publish(self, state, operation, validate):
        head = await self._managed()
        await self._clean(self.worktree_root)
        operation['validation'] = await self._validation(validate, self.worktree_root, head)
        self._check_mode()
        if await self._managed() != head:
            raise self._error('验证期间整合分支被外部改变')
        await self._clean(self.worktree_root)
        if not operation['validation']['ok']:
            self._save_operation(operation)
            return await self._rollback(state, operation, cleanup=True)
        operation.update(state='published', result_head=head, published_at=time.time())
        self._save_operation(operation)
        state.update(state='ready', head=head, active_operation=None)
        self._save(state)
        self._cleanup_approved.pop(operation['operation_id'], None)
        return operation

    async def integrate(self, task_id, member_root, member_branch, commit, *, accepted, validate, member_id=None):
        self._check_mode()
        with self._locked():
            state = self._load()
            self._available(state)
            previous = json.loads(json.dumps(state))
            receipt = self._input_receipt(task_id, member_root, member_branch, commit)
            created_receipt = receipt is None
            root = await self._validate_input(member_root, member_branch, commit, accepted, frozen=receipt is not None)
            await self._authorize_git(self.worktree_root, '-c', 'core.hooksPath=/dev/null',
                                      'merge', '--no-edit', '--no-ff', commit)
            if receipt is None:
                receipt = {'version': 1, **self._identity(), 'task_id': task_id, 'member_root': str(Path(member_root).resolve()),
                           'member_branch': member_branch, 'input_commit': commit, 'accepted': True,
                           'registered_head': await self._git(root, 'rev-parse', 'HEAD'), 'registered_at': time.time()}
                save_record(self._receipt_path(task_id, member_branch, commit), receipt)
            head = await self._managed()
            if head != state['head']:
                raise self._error('整合分支已被外部改变')
            await self._clean(self.worktree_root)
            await self._protect_incoming(self.worktree_root, head, commit)
            operation = self._start(state, 'integrate', head, task_id=task_id, member_id=member_id,
                                    member_root=str(Path(member_root).resolve()), member_branch=member_branch,
                                    input_commit=commit, conflict_owner=None)
            self._cleanup_approved[operation['operation_id']] = (self.worktree_root, head)
            try:
                operation['side_effects']['git'] = 'possible'
                self._save_operation(operation)
                await self._git(self.worktree_root, '-c', 'core.hooksPath=/dev/null', 'merge', '--no-edit', '--no-ff', commit)
            except ToolError as error:
                if getattr(error, 'git_authorization_rejected', False):
                    self._discard_prepared(previous, operation,
                        receipt=self._receipt_path(task_id, member_branch, commit) if created_receipt else None)
                    raise
                conflicts = await self._git(self.worktree_root, 'diff', '--name-only', '--diff-filter=U')
                if conflicts:
                    operation.update(state='conflict', conflict_files=conflicts.splitlines())
                    operation['working_evidence'] = await self._working_evidence(self.worktree_root)
                    self._save_operation(operation)
                    state['state'] = 'conflict'
                    self._save(state)
                    return operation
                return await self._rollback(state, operation, cleanup=True)
            except asyncio.CancelledError:
                await protected(self._rollback(state, operation, cleanup=True))
                raise
            operation.update(state='git_applied', result_head=await self._managed())
            self._save_operation(operation)
            try:
                return await self._publish(state, operation, validate)
            except asyncio.CancelledError:
                await protected(self._rollback(state, operation, cleanup=True))
                raise
            except (ToolError, OSError, ValueError, TypeError) as error:
                operation['failure'] = str(error)
                return await self._rollback(state, operation, cleanup=True)

    async def sync_member(self, member_root, member_branch, required_head):
        self._check_mode()
        with self._locked():
            state = self._load()
            self._available(state)
            previous = json.loads(json.dumps(state))
            root = await self._same_repository(member_root)
            if await self._git(root, 'symbolic-ref', '--short', 'HEAD') != member_branch:
                raise self._error('同步目录不是登记成员分支')
            if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', str(required_head)):
                raise self._error('同步需要不可变团队提交')
            published = [self._operation(state, identity) for identity in state['operations']]
            # 复用成员进入新目标时，冻结基线本身就是可信输入；其他提交仍须已有发布证据。
            if required_head != self.base_commit and not any(
                    op['kind'] == 'integrate' and op['state'] == 'published' and op['result_head'] == required_head
                    for op in published):
                raise self._error('所需提交尚未发布')
            await self._clean(root)
            before = await self._git(root, 'rev-parse', 'HEAD')
            await self._protect_incoming(root, before, required_head)
            await self._authorize_git(root, '-c', 'core.hooksPath=/dev/null', 'merge', '--no-edit', required_head)
            operation = {'version': 1, 'operation_id': uuid4().hex, 'goal_id': self.goal_id,
                         'kind': 'sync', 'state': 'prepared', 'pre_head': before,
                         'input_commit': required_head, 'member_root': str(root), 'member_branch': member_branch}
            syncs = state.setdefault('sync_operations', [])
            if len(syncs) >= 4096:
                raise self._error('目标同步操作容量已满')
            syncs.append(operation['operation_id'])
            state.update(state='syncing', active_sync=operation['operation_id'])
            self._save(state)
            self._save_sync(operation)
            self._cleanup_approved[operation['operation_id']] = (root, before)
            try:
                await self._git(root, '-c', 'core.hooksPath=/dev/null', 'merge', '--no-edit', required_head)
                await self._clean(root)
                operation.update(state='synced', result_head=await self._git(root, 'rev-parse', 'HEAD'))
                await self._git(root, 'merge-base', '--is-ancestor', required_head, operation['result_head'])
            except (ToolError, asyncio.CancelledError) as error:
                if getattr(error, 'git_authorization_rejected', False):
                    self._discard_prepared(previous, operation, sync=True)
                    raise
                # 成员目录只尝试 Git abort，绝不硬重置未知成果。
                try:
                    await protected(self._git(root, 'merge', '--abort', cleanup=operation))
                    if await self._git(root, 'rev-parse', 'HEAD') != before:
                        raise self._error('同步中断后成员分支已改变')
                    await self._clean(root)
                    operation['state'] = 'blocked'
                except (ToolError, OSError):
                    operation['state'] = 'needs_review'
                self._save_sync(operation)
                state.update(state='ready' if operation['state'] == 'blocked' else 'needs_review', active_sync=None)
                self._save(state)
                self._cleanup_approved.pop(operation['operation_id'], None)
                raise
            self._save_sync(operation)
            state.update(state='ready', active_sync=None)
            self._save(state)
            self._cleanup_approved.pop(operation['operation_id'], None)
            return operation

    async def handoff_conflict(self, operation_id, member_id):
        self._check_mode()
        if not isinstance(member_id, str) or not member_id or len(member_id) > 128:
            raise self._error('冲突处理成员身份无效')
        with self._locked():
            state = self._load()
            operation = self._operation(state, operation_id)
            if state.get('active_operation') != operation_id or operation['state'] != 'conflict' or operation.get('conflict_owner'):
                raise self._error('冲突已有所有者或不再活动')
            if await self._managed() != operation['pre_head']:
                raise self._error('冲突检查点已改变')
            token = uuid4().hex
            operation.update(conflict_owner=member_id, conflict_token=token)
            operation['conflict_side_effects'] = {'git': 'member_owned', 'hooks': 'not_rolled_back',
                                                  'shell': 'not_rolled_back', 'remote': 'not_rolled_back'}
            self._save_operation(operation)
            return {'operation_id': operation_id, 'member_id': member_id, 'token': token,
                    'workspace_root': str(self.worktree_root), 'conflict_files': operation['conflict_files']}

    def acquire_conflict_lease(self, operation_id, member_id, token):
        """成员运行器持有该 flock 直到所有文件／shell 动作真正停止。"""
        self._check_mode()
        with self._locked(workspace=False):
            state = self._load()
            operation = self._operation(state, operation_id)
            self._check_conflict_owner(state, operation, member_id, token)
            return Lease(self.workspace_lock)

    def _check_conflict_owner(self, state, operation, member_id, token):
        if state.get('active_operation') != operation['operation_id'] or operation.get('conflict_owner') != member_id or not token or operation.get('conflict_token') != token:
            raise self._error('冲突处理租约身份不匹配')

    async def finish_conflict(self, operation_id, member_id, token, *, validate):
        self._check_mode()
        with self._locked():
            state = self._load()
            operation = self._operation(state, operation_id)
            self._check_conflict_owner(state, operation, member_id, token)
            head = await self._managed()
            await self._clean(self.worktree_root)
            await self._git(self.worktree_root, 'merge-base', '--is-ancestor', operation['pre_head'], head)
            await self._git(self.worktree_root, 'merge-base', '--is-ancestor', operation['input_commit'], head)
            await self._authorize_git(self.worktree_root, 'reset', '--hard', operation['pre_head'])
            self._cleanup_approved[operation['operation_id']] = (self.worktree_root, operation['pre_head'])
            operation.update(state='git_applied', result_head=head, conflict_owner=None, conflict_token=None)
            self._save_operation(operation)
            try:
                return await self._publish(state, operation, validate)
            except asyncio.CancelledError:
                await protected(self._rollback(state, operation, cleanup=True))
                raise
            except (ToolError, OSError, ValueError, TypeError) as error:
                operation['failure'] = str(error)
                return await self._rollback(state, operation, cleanup=True)

    async def release_conflict(self, operation_id, member_id, token):
        """只有实际成员动作已收尾且排他锁可取得时才收回写入所有权。"""
        self._check_mode()
        with self._locked():
            state = self._load()
            operation = self._operation(state, operation_id)
            self._check_conflict_owner(state, operation, member_id, token)
            operation.update(conflict_owner=None, conflict_token=None)
            operation['working_evidence'] = await self._working_evidence(self.worktree_root)
            self._save_operation(operation)
            return operation

    async def _rollback(self, state, operation, *, cleanup=False):
        try:
            if operation.get('conflict_owner'):
                raise self._error('冲突写入仍由成员持有，不能撤销')
            head = await self._managed()
            merge_path = Path(await self._git(self.worktree_root, 'rev-parse', '--path-format=absolute', '--git-path', 'MERGE_HEAD'))
            if merge_path.exists():
                if head != operation['pre_head']:
                    raise self._error('冲突 HEAD 已偏离检查点')
                if operation.get('working_evidence') and await self._working_evidence(self.worktree_root) != operation['working_evidence']:
                    raise self._error('冲突目录在记录之后出现未知编辑，禁止撤销')
                await self._git(self.worktree_root, 'merge', '--abort', cleanup=operation if cleanup else None)
            else:
                await self._clean(self.worktree_root)
                if head != operation['pre_head']:
                    if head != operation.get('result_head'):
                        raise self._error('分支不是本操作结果，不能重置')
                    await self._protect_incoming(self.worktree_root, head, operation['pre_head'])
                    await self._git(self.worktree_root, 'reset', '--hard', operation['pre_head'],
                                    cleanup=operation if cleanup else None)
            if await self._managed() != operation['pre_head']:
                raise self._error('撤销后 HEAD 核查失败')
            await self._clean(self.worktree_root)
            operation.update(state='rolled_back', rollback={'ok': True, 'head': operation['pre_head']})
            self._save_operation(operation)
            state.update(state='ready', active_operation=None, head=operation['pre_head'])
            self._save(state)
        except (ToolError, OSError, ValueError) as error:
            if getattr(error, 'git_authorization_rejected', False) and not cleanup:
                raise
            operation.update(state='needs_review', rollback={'ok': False, 'reason': str(error)})
            self._save_operation(operation)
            state['state'] = 'needs_review'
            self._save(state)
        self._cleanup_approved.pop(operation['operation_id'], None)
        return operation

    async def rollback(self, operation_id):
        self._check_mode()
        with self._locked():
            state = self._load()
            operation = self._operation(state, operation_id)
            if state.get('active_operation') != operation_id or operation.get('conflict_owner'):
                raise self._error('操作已发布、非当前操作或仍由冲突成员持有')
            if operation['kind'] != 'integrate':
                raise self._error('用户分支发布不允许受管目录硬重置')
            merge_path = Path(await self._git(self.worktree_root, 'rev-parse', '--path-format=absolute', '--git-path', 'MERGE_HEAD'))
            args = ('merge', '--abort') if merge_path.exists() else ('reset', '--hard', operation['pre_head'])
            await self._authorize_git(self.worktree_root, *args)
            return await self._rollback(state, operation)

    async def _target_checkout(self):
        raw = await self._git(self.repository.checkout_root, 'worktree', 'list', '--porcelain', '-z')
        roots = []
        for group in raw.split('\0\0'):
            fields = group.split('\0')
            root = next((item[9:] for item in fields if item.startswith('worktree ')), None)
            branch = next((item[7:] for item in fields if item.startswith('branch ')), None)
            if branch == 'refs/heads/' + self.target_branch and root:
                roots.append(Path(root))
        if len(roots) > 1:
            raise self._error('目标分支在多个工作区检出，无法安全发布')
        return roots[0] if roots else None

    async def _target_safe(self):
        await self._same_repository(self.repository.checkout_root)
        if await self._git(self.repository.checkout_root, 'rev-parse', '--verify', 'refs/heads/' + self.target_branch) != self.base_commit:
            raise self._error('目标分支外部更新，需重新核查基线')
        checkout = await self._target_checkout()
        if checkout is not None:
            await self._same_repository(checkout)
            await self._clean(checkout)
            if await self._git(checkout, 'symbolic-ref', '--short', 'HEAD') != self.target_branch:
                raise self._error('目标目录检出身份发生变化')
        return checkout

    async def _publish_target(self, checkout, candidate):
        self._check_mode()
        ref = 'refs/heads/' + self.target_branch
        if checkout is None:
            await self._git(self.repository.checkout_root, 'update-ref', ref, candidate, self.base_commit)
            return
        # prepare 真正锁定目标 ref，更新工作目录／索引期间外部 Git 无法移动该 ref。
        with directory_fd(self.repository.checkout_root):
            pass
        await self._authorize_git(self.repository.checkout_root, 'update-ref', '--stdin')
        self._check_mode()
        process = await asyncio.create_subprocess_exec('git', '-c', 'core.hooksPath=/dev/null', 'update-ref', '--stdin',
            cwd=self.repository.checkout_root, env=git_environment(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        index_updated = committing = False
        try:
            process.stdin.write(f'start\nupdate {ref} {candidate} {self.base_commit}\nprepare\n'.encode())
            await process.stdin.drain()
            if await asyncio.wait_for(process.stdout.readline(), 10) != b'start: ok\n' or await asyncio.wait_for(process.stdout.readline(), 10) != b'prepare: ok\n':
                raise self._error('目标分支比较更新失败')
            if await self._target_safe() != checkout:
                raise self._error('目标工作区在发布前发生变化')
            await self._protect_incoming(checkout, self.base_commit, candidate)
            await self._git(checkout, 'read-tree', '-u', '-m', self.base_commit, candidate)
            index_updated = True
            # ref 事务提交前再核查最新策略，stdin 不能成为绕过路径。
            await self._authorize_git(self.repository.checkout_root, 'update-ref', '--stdin')
            self._check_mode()
            committing = True
            process.stdin.write(b'commit\n')
            await process.stdin.drain()
            if await asyncio.wait_for(process.stdout.readline(), 10) != b'commit: ok\n':
                raise self._error('目标 ref 事务提交失败')
            process.stdin.close()
            await asyncio.wait_for(process.wait(), 10)
            if process.returncode != 0:
                raise self._error('目标 ref 事务结果不明')
        except ToolError as error:
            if getattr(error, 'git_authorization_rejected', False) and index_updated and not committing:
                # 仅撤销本次尚未提交 ref 的 read-tree；任何未知后续编辑都保留待核查。
                async def restore_index():
                    if (await self._git(checkout, 'diff', '--name-only', candidate, '--') or
                            await self._git(checkout, 'diff', '--cached', '--name-only', candidate, '--') or
                            await self._git(self.repository.checkout_root, 'rev-parse', ref) != self.base_commit):
                        raise self._error('发布目录出现未知后续编辑，不能清理本次索引更新')
                    await self._protect_incoming(checkout, candidate, self.base_commit)
                    await self.manager.git(checkout, '-c', 'core.hooksPath=/dev/null', 'read-tree', '-u', '-m',
                                           candidate, self.base_commit)
                await protected(restore_index())
            raise
        finally:
            if process.returncode is None:
                process.stdin.close()
                try:
                    await protected(asyncio.wait_for(process.wait(), 2))
                except TimeoutError:
                    process.kill()
                    await protected(process.wait())

    async def finalize(self, required_tasks, *, validate):
        self._check_mode()
        with self._locked():
            state = self._load()
            self._available(state)
            previous = json.loads(json.dumps(state))
            if not isinstance(required_tasks, list) or not required_tasks:
                raise self._error('必须明确列出当前目标全部必需任务')
            seen = set()
            for task in required_tasks:
                if not isinstance(task, dict) or task.get('accepted') is not True or not task.get('task_id') or task['task_id'] in seen:
                    raise self._error('必需任务尚未全部接纳或身份重复')
                seen.add(task['task_id'])
                if task.get('code') is True:
                    op = self._operation(state, task.get('operation_id'))
                    if op['kind'] != 'integrate' or op['state'] != 'published' or op['task_id'] != task['task_id'] or not op.get('validation', {}).get('ok'):
                        raise self._error('必需代码成果尚未完成验证整合')
                elif task.get('code') is not False or not task.get('evidence'):
                    raise self._error('非代码任务缺少明确验收证据')
            if await self._managed() != state['head']:
                raise self._error('整合分支外部更新使验证失效')
            await self._clean(self.worktree_root)
            checkout = await self._target_safe()
            operation_id = uuid4().hex
            candidate_root = self.root / ('candidate-' + operation_id)
            await self._authorize_git(self.repository.checkout_root, 'worktree', 'add', '--detach',
                                      str(candidate_root), self.base_commit)
            await self._authorize_git(candidate_root, '-c', 'core.hooksPath=/dev/null', 'merge', '--ff-only', state['head'])
            if checkout is not None:
                await self._authorize_git(self.repository.checkout_root, 'update-ref', '--stdin')
                await self._authorize_git(checkout, 'read-tree', '-u', '-m', self.base_commit, state['head'])
                await self._authorize_git(checkout, 'write-tree')
            else:
                await self._authorize_git(self.repository.checkout_root, 'update-ref',
                                          'refs/heads/' + self.target_branch, state['head'], self.base_commit)
            operation = self._start(state, 'finalize', self.base_commit, input_commit=state['head'],
                                    required_tasks=required_tasks, target_checkout=str(checkout) if checkout else None,
                                    operation_id=operation_id)
            operation['candidate_root'] = str(candidate_root)
            self._save_operation(operation)
            candidate_created = False
            try:
                await self._git(self.repository.checkout_root, 'worktree', 'add', '--detach', str(candidate_root), self.base_commit)
                candidate_created = True
                await self._git(candidate_root, '-c', 'core.hooksPath=/dev/null', 'merge', '--ff-only', state['head'])
                candidate = await self._git(candidate_root, 'rev-parse', 'HEAD')
                operation['result_head'] = candidate
                operation['validation'] = await self._validation(validate, candidate_root, candidate)
                await self._clean(candidate_root)
                if await self._git(candidate_root, 'rev-parse', 'HEAD') != candidate:
                    raise self._error('验证期间候选提交被改变')
                if not operation['validation']['ok']:
                    operation['state'] = 'validation_failed'
                    self._save_operation(operation)
                    state.update(state='ready', active_operation=None)
                    self._save(state)
                    return operation
                if await self._managed() != operation['input_commit']:
                    raise self._error('候选验证期间团队分支改变')
                self._check_mode()
                checkout = await self._target_safe()
                operation['state'] = 'publishing'
                operation['side_effects']['git'] = 'possible'
                self._save_operation(operation)
                await self._publish_target(checkout, candidate)
                if await self._git(self.repository.checkout_root, 'rev-parse', 'refs/heads/' + self.target_branch) != candidate:
                    raise self._error('发布后目标分支发生变化')
                if checkout is not None:
                    await self._clean(checkout)
                    if await self._git(checkout, 'write-tree') != await self._git(checkout, 'rev-parse', candidate + '^{tree}'):
                        raise self._error('发布后索引与候选不一致')
                operation.update(state='published', published_at=time.time())
                self._save_operation(operation)
                state.update(state='published', active_operation=None, final_operation=operation['operation_id'])
                self._save(state)
                return operation
            except asyncio.CancelledError:
                operation.update(state='needs_review', failure='最终发布取消，需核查真实用户目录和 ref')
                self._save_operation(operation)
                state['state'] = 'needs_review'
                self._save(state)
                raise
            except (ToolError, OSError, ValueError, TypeError) as error:
                if getattr(error, 'git_authorization_rejected', False) and not candidate_created:
                    self._discard_prepared(previous, operation)
                    raise
                # ref 已提交或副作用不确定时不自动撤销；候选和此前成果保留。
                operation.update(state='needs_review', failure=str(error))
                self._save_operation(operation)
                state['state'] = 'needs_review'
                self._save(state)
                return operation

    async def recover(self):
        """只对账，无 merge/reset；任何未确认副作用都阻塞自动调度。"""
        with self._locked():
            state = self._load()
            actual = await self._managed()
            records = []
            uncertain = actual != state['head']
            sync_records = []
            for identity in state.get('sync_operations', []):
                try:
                    if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
                        raise ValueError('同步操作身份无效')
                    operation = json.loads(read_file(self.root / ('sync-' + identity + '.json')))
                    if not isinstance(operation, dict) or operation.get('goal_id') != self.goal_id or operation.get('operation_id') != identity or operation.get('kind') != 'sync':
                        raise ValueError('同步记录身份损坏')
                    if operation.get('state') not in {'synced', 'blocked'}:
                        root = await self._same_repository(operation['member_root'])
                        operation['actual_head'] = await self._git(root, 'rev-parse', 'HEAD')
                        operation['actual_status'] = await self._git(root, 'status', '--porcelain')
                        operation['state'] = 'needs_review'
                        operation['recovery'] = '只核查成员真实 Git 状态，不重跑同步'
                        self._save_sync(operation)
                        uncertain = True
                    elif state.get('active_sync') == identity:
                        root = await self._same_repository(operation['member_root'])
                        await self._clean(root)
                        expected = operation['result_head'] if operation['state'] == 'synced' else operation['pre_head']
                        if await self._git(root, 'rev-parse', 'HEAD') != expected:
                            raise ValueError('同步完成后的成员分支已移动')
                        if operation['state'] == 'synced':
                            await self._git(root, 'merge-base', '--is-ancestor', operation['input_commit'], expected)
                        state.update(state='ready', active_sync=None)
                        self._save(state)
                except (ToolError, OSError, ValueError, KeyError, TypeError):
                    operation = {'operation_id': identity, 'state': 'needs_review', 'reason': '同步证据缺失、损坏或成员目录已改变'}
                    uncertain = True
                sync_records.append(operation)
            for identity in state['operations']:
                try:
                    operation = self._operation(state, identity)
                except (ToolError, OSError, ValueError):
                    records.append({'operation_id': identity, 'state': 'needs_review', 'reason': '记录缺失或损坏'})
                    uncertain = True
                    continue
                if operation['state'] not in {'published', 'rolled_back', 'validation_failed'}:
                    operation['actual_head'] = actual
                    operation['actual_target_head'] = await self._git(self.repository.checkout_root, 'rev-parse', 'refs/heads/' + self.target_branch)
                    operation['actual_status'] = await self._git(self.worktree_root, 'status', '--porcelain')
                    operation['state'] = 'needs_review'
                    operation['recovery'] = '只核查实际 Git 结果；未重放 Git 或撤销用户操作'
                    self._save_operation(operation)
                    uncertain = True
                records.append(operation)
            # 操作证据已经发布而快照写入中断，只修复快照，不执行任何 Git 写入。
            active = next((op for op in records if op['operation_id'] == state.get('active_operation')), None)
            if active is not None and active['state'] == 'published' and active.get('validation', {}).get('ok'):
                try:
                    await self._clean(self.worktree_root)
                    if active['kind'] == 'integrate' and active['result_head'] == actual and active['pre_head'] == state['head']:
                        await self._git(self.worktree_root, 'merge-base', '--is-ancestor', active['input_commit'], actual)
                        state.update(state='ready', head=actual, active_operation=None)
                        uncertain = False
                        self._save(state)
                    elif active['kind'] == 'finalize' and actual == state['head']:
                        target = await self._git(self.repository.checkout_root, 'rev-parse', 'refs/heads/' + self.target_branch)
                        if target == active['result_head']:
                            checkout = await self._target_checkout()
                            if checkout is not None:
                                await self._clean(checkout)
                            state.update(state='published', active_operation=None, final_operation=active['operation_id'])
                            uncertain = False
                            self._save(state)
                except (ToolError, OSError, ValueError):
                    uncertain = True
            if uncertain or state.get('active_operation') or state.get('active_sync'):
                state['state'] = 'needs_review'
                self._save(state)
            return {**state, 'operations': records, 'sync_operations': sync_records}
