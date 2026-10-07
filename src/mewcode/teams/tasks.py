"""共享任务的 DAG、领取与成果发布；事务内重读权威记录。"""
from pathlib import Path
import re
from ..tools.base import ToolError
from ..worktrees.paths import freeze_repository, git_read
from .models import Task, new_id, now, TeamValidationError
from .store import TeamStoreError


MAX_TASKS = 4096


class TaskError(TeamStoreError):
    """任务冲突、权限或状态门槛失败。"""


class TaskBoard:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def _load(self):
        team = self.store.load(self.name)
        raw = self.store.read_json(self.name, 'tasks.json')
        if (type(raw) is not dict or set(raw) != {'version', 'team_id', 'tasks'}
                or type(raw['version']) is not int or raw['version'] != 1 or raw['team_id'] != team.team_id
                or type(raw['tasks']) is not dict or len(raw['tasks']) > MAX_TASKS):
            raise TaskError('任务清单版本、归属或容量无效')
        try:
            tasks = {identity: Task.from_dict(data) for identity, data in raw['tasks'].items()}
            if any(identity != task.task_id or task.team_id != team.team_id or task.goal_id not in team.goals
                   for identity, task in tasks.items()):
                raise TaskError('任务归属无效')
            self._dag(tasks)
            return team, tasks
        except TeamValidationError as error:
            raise TaskError(f'任务清单损坏：{error}') from error

    def _actor(self, team, actor, lead=False, mutation=False):
        if actor not in team.members or not team.members[actor].active:
            raise TaskError('调用者不是当前团队参与者')
        if lead and actor != team.lead_id:
            raise TaskError('只有 Lead 可执行此动作')
        if mutation and team.status != 'active':
            raise TaskError('团队暂停或待核查，禁止任务变更')

    def _goal(self, team, task):
        if team.active_goal_id != task.goal_id or team.goals[task.goal_id].state != 'active':
            raise TaskError('任务不属于当前活动目标')

    def _revision(self, task, expected):
        if type(expected) is not int or task.revision != expected:
            raise TaskError('revision conflict：任务版本冲突')

    def _task(self, tasks, identity):
        if identity not in tasks:
            raise TaskError('任务不存在')
        return tasks[identity]

    def _dag(self, tasks):
        # 迭代拓扑校验支持完整容量内的长链，不依赖 Python 递归深度。
        counts = {identity: len(task.depends_on) for identity, task in tasks.items()}
        followers = {identity: [] for identity in tasks}
        for identity, task in tasks.items():
            if len(task.depends_on) != len(set(task.depends_on)):
                raise TaskError('重复依赖')
            for dependency in task.depends_on:
                if dependency == identity or dependency not in tasks or tasks[dependency].goal_id != task.goal_id:
                    raise TaskError('依赖缺失、自依赖或跨目标')
                followers[dependency].append(identity)
        ready = [identity for identity, count in counts.items() if not count]
        visited = 0
        while ready:
            identity = ready.pop()
            visited += 1
            for follower in followers[identity]:
                counts[follower] -= 1
                if not counts[follower]:
                    ready.append(follower)
        if visited != len(tasks):
            raise TaskError('任务依赖形成环')

    def _save(self, team, tasks, changed=None):
        self._dag(tasks)
        if len(tasks) > MAX_TASKS:
            raise TaskError('任务容量超限')
        if changed:
            changed.revision += 1
            changed.updated_at = now()
            Task.from_dict(changed.to_dict())
        self.store.write_json(self.name, 'tasks.json', {'version': 1, 'team_id': team.team_id,
                              'tasks': {identity: task.to_dict() for identity, task in tasks.items()}})

    async def create(self, actor_id, goal_id, title, description='', *, depends_on=None, code=True, budget_limit=20):
        async with self.store.lock(self.name, 'tasks.lock'):
            team, tasks = self._load()
            self._actor(team, actor_id, mutation=True)
            task = Task(new_id('task'), team.team_id, goal_id, title, description,
                        depends_on=list(depends_on or []), code=code, budget_limit=budget_limit)
            self._goal(team, task)
            tasks[task.task_id] = task
            self._save(team, tasks)
            return task

    async def list(self, actor_id, *, goal_id=None):
        async with self.store.lock(self.name, 'tasks.lock'):
            team, tasks = self._load()
            self._actor(team, actor_id)
            return [task for task in tasks.values() if goal_id is None or task.goal_id == goal_id]

    async def get(self, actor_id, task_id):
        async with self.store.lock(self.name, 'tasks.lock'):
            team, tasks = self._load()
            self._actor(team, actor_id)
            return self._task(tasks, task_id)

    async def update(self, actor_id, task_id, expected_revision, **changes):
        if not changes or set(changes) - {'title', 'description', 'depends_on'}:
            raise TaskError('可编辑字段仅限标题、描述及依赖')
        async with self.store.lock(self.name, 'tasks.lock'):
            team, tasks = self._load()
            self._actor(team, actor_id, mutation=True)
            task = self._task(tasks, task_id)
            self._goal(team, task)
            self._revision(task, expected_revision)
            if task.state != 'pending' and 'depends_on' in changes:
                raise TaskError('活动任务禁止修改依赖')
            if task.owner_id and actor_id not in {task.owner_id, team.lead_id}:
                raise TaskError('已领取任务仅负责人或 Lead 可编辑')
            for key, value in changes.items():
                setattr(task, key, value)
            self._save(team, tasks, task)
            return task

    async def delete(self, actor_id, task_id, expected_revision):
        async with self.store.lock(self.name, 'tasks.lock'):
            team, tasks = self._load()
            self._actor(team, actor_id, mutation=True)
            task = self._task(tasks, task_id)
            self._revision(task, expected_revision)
            if task.state != 'pending' or task.history or task.result or any(task_id in item.depends_on for item in tasks.values()):
                raise TaskError('活动、历史成果或被依赖任务不可删除')
            del tasks[task_id]
            self._save(team, tasks)

    def _dependencies(self, task, tasks):
        for identity in task.depends_on:
            dependency = tasks[identity]
            if dependency.code:
                if dependency.state not in {'integrated', 'completed'} or not dependency.integrated_commit:
                    raise TaskError('前置代码尚未验收整合')
            elif dependency.state != 'completed':
                raise TaskError('前置成果尚未验收')

    async def claim(self, actor_id, task_id):
        async with self.store.lock(self.name, 'tasks.lock'):
            team, tasks = self._load()
            self._actor(team, actor_id, mutation=True)
            task = self._task(tasks, task_id)
            self._goal(team, task)
            if task.owner_id or task.state != 'pending':
                raise TaskError('任务已领取或不可领取')
            self._dependencies(task, tasks)
            task.owner_id, task.claim_id, task.state = actor_id, new_id('claim'), 'claimed'
            self._save(team, tasks, task)
            return task

    def _owner(self, task, actor_id, claim_id):
        if task.owner_id != actor_id or task.claim_id != claim_id:
            raise TaskError('当前领取身份不匹配')

    async def start(self, actor_id, task_id, claim_id, *, synced_commit=None):
        async with self.store.lock(self.name, 'tasks.lock'):
            team, tasks = self._load()
            self._actor(team, actor_id, mutation=True)
            task = self._task(tasks, task_id)
            self._goal(team, task)
            self._owner(task, actor_id, claim_id)
            self._dependencies(task, tasks)
            if task.state != 'claimed':
                raise TaskError('任务尚不可进入执行')
            if task.code and not _commit(synced_commit):
                raise TaskError('代码任务尚未确认实际同步提交')
            if task.code:
                root = Path(team.members[actor_id].workspace_root)
                try:
                    snapshot = freeze_repository(root)
                    if str(snapshot.common_git_dir) != team.repository or snapshot.base_commit != synced_commit:
                        raise TaskError('目录实际 HEAD 与声明同步提交不一致')
                    if git_read(root, 'status', '--porcelain'):
                        raise TaskError('成员目录有未知修改，拒绝执行同步')
                    for identity in task.depends_on:
                        dependency = tasks[identity]
                        if dependency.code:
                            git_read(root, 'merge-base', '--is-ancestor', dependency.integrated_commit, synced_commit)
                except (ToolError, TaskError) as error:
                    task.state, task.reason = 'blocked', str(error)
                    self._save(team, tasks, task)
                    raise TaskError('无法确认成员实际仓库或依赖代码同步') from error
            task.synced_commit, task.state = synced_commit, 'running'
            self._save(team, tasks, task)
            return task

    async def submit(self, actor_id, task_id, claim_id, result):
        if (type(result) is not dict or set(result) - {'summary', 'verified', 'evidence', 'branch', 'commit', 'run_id'}
                or type(result.get('summary')) is not str or type(result.get('verified')) is not bool
                or type(result.get('evidence')) is not list or not result['evidence']):
            raise TaskError('成果必须包含摘要、真实验证标记和证据')
        async with self.store.lock(self.name, 'tasks.lock'):
            team, tasks = self._load()
            self._actor(team, actor_id, mutation=True)
            task = self._task(tasks, task_id)
            if task.owner_id != actor_id or task.claim_id != claim_id:
                if not any(entry.get('owner_id') == actor_id and entry.get('claim_id') == claim_id for entry in task.history):
                    raise TaskError('未知领取身份不能提交')
                task.history.append({'owner_id': actor_id, 'claim_id': claim_id, 'result': result, 'late': True, 'timestamp': now()})
                self._save(team, tasks, task)
                return task
            self._goal(team, task)
            if task.state not in {'claimed', 'running', 'submitted', 'blocked', 'failed'}:
                raise TaskError('任务状态不能提交成果')
            if task.code and (not _commit(result.get('commit')) or not result.get('branch')):
                raise TaskError('代码成果必须先提交并提供不可变提交和分支')
            if task.code:
                member = team.members[actor_id]
                if member.branch and result['branch'] != member.branch:
                    raise TaskError('成果分支不属于当前成员登记')
                root = Path(member.workspace_root)
                try:
                    snapshot = freeze_repository(root)
                    commit = git_read(root, 'rev-parse', '--verify', result['commit'] + '^{commit}')
                    if str(snapshot.common_git_dir) != team.repository or commit != result['commit']:
                        raise TaskError('成果提交不属于固定仓库')
                    git_read(root, 'merge-base', '--is-ancestor', commit, result['branch'])
                except ToolError as error:
                    raise TaskError('成果提交不存在或不属于成员分支') from error
            task.result, task.state = result, 'submitted'
            self._save(team, tasks, task)
            return task

    async def accept(self, actor_id, task_id, expected_revision, *, reason):
        async with self.store.lock(self.name, 'tasks.lock'):
            team, tasks = self._load()
            self._actor(team, actor_id, lead=True, mutation=True)
            task = self._task(tasks, task_id)
            self._goal(team, task)
            self._revision(task, expected_revision)
            if task.state != 'submitted' or task.result.get('verified') is not True or not reason.strip():
                raise TaskError('成果尚未通过证据验收')
            task.state = 'accepted' if task.code else 'completed'
            task.history.append({'action': 'accept', 'reason': reason, 'result': dict(task.result), 'timestamp': now()})
            self._save(team, tasks, task)
            return task

    async def reject(self, actor_id, task_id, expected_revision, *, reason):
        return await self._lead_state(actor_id, task_id, expected_revision, 'blocked', reason=reason,
                                      allowed={'submitted', 'accepted'})

    async def _lead_state(self, actor_id, task_id, expected_revision, state, *, reason='', allowed=(), commit=None):
        async with self.store.lock(self.name, 'tasks.lock'):
            team, tasks = self._load()
            self._actor(team, actor_id, lead=True, mutation=True)
            task = self._task(tasks, task_id)
            self._goal(team, task)
            self._revision(task, expected_revision)
            if task.state not in allowed:
                raise TaskError('任务状态转换无效')
            task.state, task.reason = state, reason
            if commit is not None:
                if not _commit(commit):
                    raise TaskError('整合提交无效')
                task.integrated_commit = commit
            self._save(team, tasks, task)
            return task

    async def integrate(self, actor_id, task_id, expected_revision, *, commit):
        return await self._lead_state(actor_id, task_id, expected_revision, 'integrated', allowed={'accepted'}, commit=commit)

    async def complete(self, actor_id, task_id, expected_revision):
        return await self._lead_state(actor_id, task_id, expected_revision, 'completed', allowed={'integrated'})

    async def reassign(self, actor_id, task_id, member_id, *, old_run_stopped, expected_revision=None):
        if old_run_stopped is not True:
            raise TaskError('旧运行尚未确认收尾')
        async with self.store.lock(self.name, 'tasks.lock'):
            team, tasks = self._load()
            self._actor(team, actor_id, lead=True, mutation=True)
            self._actor(team, member_id)
            task = self._task(tasks, task_id)
            self._goal(team, task)
            if expected_revision is not None:
                self._revision(task, expected_revision)
            if task.state in {'accepted', 'integrated', 'completed'}:
                raise TaskError('已验收成果不能重新指派')
            task.history.append({'owner_id': task.owner_id, 'claim_id': task.claim_id,
                                 'result': dict(task.result), 'reason': task.reason, 'timestamp': now()})
            task.owner_id, task.claim_id, task.state = member_id, new_id('claim'), 'claimed'
            task.result, task.synced_commit, task.reason, task.budget_used = {}, None, '', 0
            self._save(team, tasks, task)
            return task

    async def use_budget(self, actor_id, task_id, claim_id, *, amount=1):
        if type(amount) is not int or amount < 1:
            raise TaskError('预算消耗必须为正整数')
        async with self.store.lock(self.name, 'tasks.lock'):
            team, tasks = self._load()
            self._actor(team, actor_id, mutation=True)
            task = self._task(tasks, task_id)
            self._goal(team, task)
            self._owner(task, actor_id, claim_id)
            if task.state not in {'claimed', 'running', 'submitted', 'accepted', 'integrated', 'completed'} or task.budget_used + amount > task.budget_limit:
                raise TaskError('任务预算耗尽或必须由 Lead 重新指派')
            task.budget_used += amount
            self._save(team, tasks, task)
            return task

    async def report(self, actor_id, task_id, claim_id, *, state, reason, budget_used=None):
        if state not in {'failed', 'blocked', 'needs_review'} or not reason.strip():
            raise TaskError('失败或阻塞必须说明原因')
        async with self.store.lock(self.name, 'tasks.lock'):
            team, tasks = self._load()
            self._actor(team, actor_id, mutation=True)
            task = self._task(tasks, task_id)
            self._owner(task, actor_id, claim_id)
            if budget_used is not None:
                if type(budget_used) is not int or budget_used < task.budget_used:
                    raise TaskError('预算不可回退或刷新')
            if task.state in {'accepted', 'integrated', 'completed'}:
                # Lead 验收与整合不可被成员后续通信失败覆盖；真实失败仍持久审计。
                task.history.append({'owner_id': actor_id, 'claim_id': claim_id, 'late': True,
                                     'reported_state': state, 'reason': reason,
                                     'budget_used': budget_used, 'timestamp': now()})
                self._save(team, tasks, task)
                return task
            if budget_used is not None:
                task.budget_used = budget_used
            task.state, task.reason = state, reason
            self._save(team, tasks, task)
            return task


def _commit(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value) is not None
