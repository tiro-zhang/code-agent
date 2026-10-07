"""用户域团队快照与固定 inode 的可取消 flock 事务。"""
import asyncio
from contextlib import asynccontextmanager, contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import time
from uuid import uuid4

from ..worktrees.paths import directory_fd, freeze_repository, git_read
from .models import Team, Member, Goal, TeamValidationError, new_id, now, validate_id, validate_name


MAX_RECORD_BYTES = 8 * 1024 * 1024


class TeamStoreError(OSError):
    """存储失败；busy 表示调用者可重试。"""


def _regular(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise TeamStoreError('记录必须为当前用户独占的普通文件')


class TeamLease:
    """长锁与短锁共用固定文件；退出只关闭，不删除。"""
    def __init__(self, path):
        self.path, self.fd = Path(path), None
        try:
            with directory_fd(self.path.parent) as parent:
                fd = os.open(self.path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=parent)
            try:
                _regular(fd)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.fchmod(fd, 0o600)
            except BaseException:
                os.close(fd)
                raise
            self.fd = fd
        except BlockingIOError:
            raise TeamStoreError('busy：锁正在使用') from None
        except (OSError, ValueError) as error:
            raise TeamStoreError(f'锁无法安全取得：{error}') from error

    def check(self):
        if self.fd is None:
            raise TeamStoreError('租约已关闭')
        with directory_fd(self.path.parent) as parent:
            current = os.stat(self.path.name, dir_fd=parent, follow_symlinks=False)
        owned = os.fstat(self.fd)
        if (current.st_dev, current.st_ino) != (owned.st_dev, owned.st_ino):
            raise TeamStoreError('固定锁已被替换')

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class TeamStore:
    def __init__(self, user_root=None, *, lock_timeout=5.0):
        self.root = Path(os.path.abspath(user_root or Path.home() / '.mewcode' / 'teams'))
        self.lock_timeout = lock_timeout
        self._leads = {}

    def team_path(self, name):
        validate_name(name)
        return self.root / name

    def member_path(self, name, member_id):
        validate_id(member_id, 'member')
        return self.team_path(name) / 'members' / member_id

    def _path(self, name, relative):
        relative = Path(relative)
        if relative.is_absolute() or not relative.parts or any(part in {'.', '..'} for part in relative.parts):
            raise TeamStoreError('记录路径必须为受控相对路径')
        return self.team_path(name) / relative

    def private_directory(self, path):
        try:
            with directory_fd(path, create=True) as fd:
                if os.fstat(fd).st_uid != os.getuid():
                    raise TeamStoreError('私有目录所有者不一致')
                os.fchmod(fd, 0o700)
        except (OSError, ValueError) as error:
            raise TeamStoreError(f'无法创建私有目录：{error}') from error

    def read_json(self, name, relative):
        path = self._path(name, relative)
        try:
            with directory_fd(path.parent) as parent:
                fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                try:
                    _regular(fd)
                    if os.fstat(fd).st_size > MAX_RECORD_BYTES:
                        raise TeamStoreError('记录容量超限')
                    with os.fdopen(fd, 'rb', closefd=False) as stream:
                        raw = stream.read(MAX_RECORD_BYTES + 1)
                    if len(raw) > MAX_RECORD_BYTES:
                        raise TeamStoreError('记录容量超限')
                finally:
                    os.close(fd)
            return json.loads(raw.decode('utf-8'), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (OSError, ValueError, UnicodeError) as error:
            raise TeamStoreError(f'无法安全读取 {relative}：{error}') from error

    def write_json(self, name, relative, data):
        path = self._path(name, relative)
        temporary = '.team-' + uuid4().hex
        try:
            raw = json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True).encode('utf-8')
            if len(raw) > MAX_RECORD_BYTES:
                raise TeamStoreError('记录容量超限')
            with directory_fd(path.parent) as parent:
                try:
                    existing = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                except FileNotFoundError:
                    pass
                else:
                    try:
                        _regular(existing)
                    finally:
                        os.close(existing)
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
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
        except (OSError, ValueError, TypeError) as error:
            raise TeamStoreError(f'无法安全发布 {relative}：{error}') from error

    @asynccontextmanager
    async def lock(self, name, relative='state.lock'):
        path, deadline, delay = self._path(name, relative), time.monotonic() + self.lock_timeout, .02
        lease = None
        while lease is None:
            try:
                lease = TeamLease(path)
            except TeamStoreError as error:
                if 'busy' not in str(error) or time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(min(delay, max(0, deadline - time.monotonic())))
                delay = min(.2, delay * 1.5)
        try:
            lease.check()
            yield lease
            lease.check()
        finally:
            lease.close()

    def load(self, name):
        try:
            team = Team.from_dict(self.read_json(name, 'team.json'))
            if team.name != name:
                raise TeamValidationError('团队目录与身份不符')
            return team
        except TeamValidationError as error:
            raise TeamStoreError(f'团队记录损坏：{error}') from error

    def list(self):
        try:
            with directory_fd(self.root) as fd:
                names = os.listdir(fd)
        except FileNotFoundError:
            return []
        return [self.load(name) for name in sorted(names) if not name.startswith('.')]

    async def create(self, name, repository, *, target_branch=None):
        validate_name(name)
        snapshot = freeze_repository(Path(repository))
        branch = target_branch or git_read(snapshot.origin_root, 'symbolic-ref', '--quiet', '--short', 'HEAD')
        git_read(snapshot.origin_root, 'check-ref-format', '--branch', branch)
        if git_read(snapshot.origin_root, 'rev-parse', '--verify', branch + '^{commit}') != snapshot.base_commit:
            raise TeamStoreError('目标分支必须位于登记基线')
        self.private_directory(self.root)
        path = self.team_path(name)
        try:
            with directory_fd(self.root) as fd:
                os.mkdir(name, mode=0o700, dir_fd=fd)
        except FileExistsError:
            raise TeamStoreError('同名团队已存在，不覆盖') from None
        lead = Member(new_id('member'), 'lead', 'lead', str(snapshot.origin_root), branch=branch, state='idle')
        team = Team(new_id('team'), name, str(snapshot.common_git_dir), str(snapshot.origin_root),
                    lead.member_id, snapshot.base_commit, branch, members={lead.member_id: lead})
        lease = TeamLease(path / 'lead.lock')
        try:
            for child in ('inboxes', 'locks', 'members', 'integration'):
                self.private_directory(path / child)
            self.private_directory(self.member_path(name, lead.member_id))
            self.private_directory(self.member_path(name, lead.member_id) / 'cache')
            self.write_json(name, 'tasks.json', {'version': 1, 'team_id': team.team_id, 'tasks': {}})
            self.write_json(name, f'inboxes/{lead.member_id}.json',
                            {'version': 1, 'team_id': team.team_id, 'member_id': lead.member_id, 'messages': []})
            self.write_json(name, 'team.json', team.to_dict())
        except BaseException:
            lease.close()
            raise
        self._leads[name] = lease
        return team

    def require_lead(self, name):
        if name not in self._leads:
            raise TeamStoreError('当前进程没有 Lead 所有权')
        self._leads[name].check()

    def release_lead(self, name):
        lease = self._leads.pop(name, None)
        if lease:
            lease.close()

    def member_lease(self, name, member_id):
        team = self.load(name)
        if team.status != 'active' or member_id not in team.members or not team.members[member_id].active:
            raise TeamStoreError('未登记成员')
        return TeamLease(self._path(name, f'locks/member-{member_id}.lock'))

    def recovery_data(self, name, member, projection):
        """恢复记录只绑定当前权威身份及原始意图，不保存模型正文。"""
        team = self.load(name)
        from ..sessions.store import ID_PATTERN
        if not re.fullmatch(r'[0-9a-f]{32}', member.generation) or not ID_PATTERN.fullmatch(member.session_ref):
            raise TeamStoreError('待核查代次或会话身份无效')
        current = team.members[member.member_id]
        if (current.generation, current.session_ref, current.task_id, current.claim_id) != (
                member.generation, member.session_ref, member.task_id, member.claim_id):
            raise TeamStoreError('待核查成员身份已改变')
        interactions = [{'interaction_id': item['interaction_id'], 'intent_seq': item['intent_seq'],
                         'missing_call_ids': item['missing_call_ids']}
            for item in projection.unconfirmed_interactions
            if (item['task_id'], item['claim_id']) == (member.task_id, member.claim_id)
            and (item['team_id'], item['member_id'], item['lead_id']) == (team.team_id, member.member_id, team.lead_id)]
        if not interactions:
            raise TeamStoreError('成员权威存档没有匹配的未确认意图')
        return {'version': 1, 'team_id': team.team_id, 'member_id': member.member_id,
                'generation': member.generation, 'session_id': member.session_ref,
                'task_id': member.task_id, 'claim_id': member.claim_id, 'interactions': interactions}

    @contextmanager
    def reviewed_member(self, name, member):
        """恢复标记不能代替停止证据；同时核验真实成员及 Journal 排他锁。"""
        from ..sessions.store import _root, _read, ID_PATTERN
        from ..sessions.projection import build_projection
        if not re.fullmatch(r'[0-9a-f]{32}', member.generation) or not ID_PATTERN.fullmatch(member.session_ref):
            raise TeamStoreError('待核查代次或会话身份无效')
        lease = TeamLease(self._path(name, f'locks/member-{member.member_id}.lock'))
        fd = None
        try:
            path = self.member_path(name, member.member_id) / '.mewcode/sessions' / (member.session_ref + '.jsonl')
            with directory_fd(path.parent) as parent:
                fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            _regular(fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            records, warnings, _, _ = _read(fd, member.session_ref, _root(member.workspace_root))
            projection = build_projection(records, warnings)
            actual = self.read_json(name, f'members/{member.member_id}/recovery.json')
            expected = self.recovery_data(name, member, projection)
            # 严格 JSON 比较区分 true/1、1.0/1，并拒绝未知字段及多余事实。
            if json.dumps(actual, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
                raise TeamStoreError('待核查恢复记录与成员权威存档不一致')
            lease.check()
            yield projection
            lease.check()
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise TeamStoreError(f'无法确认待核查成员已停止：{error}') from error
        finally:
            if fd is not None:
                os.close(fd)
            lease.close()

    async def update_team(self, name, mutator, *, expected_revision=None):
        async with self.lock(name):
            team = self.load(name)
            if expected_revision is not None and team.revision != expected_revision:
                raise TeamStoreError('revision conflict：团队版本冲突')
            mutator(team)
            team.revision += 1
            team.updated_at = now()
            Team.from_dict(team.to_dict())
            self.write_json(name, 'team.json', team.to_dict())
            return team

    async def register_member(self, name, member):
        self.require_lead(name)
        Member.from_dict(member.to_dict())
        async with self.lock(name):
            team = self.load(name)
            if team.status != 'active' or member.member_id in team.members or any(m.name == member.name for m in team.members.values()):
                raise TeamStoreError('成员身份冲突或团队不可派生')
            self.private_directory(self.member_path(name, member.member_id))
            self.private_directory(self.member_path(name, member.member_id) / 'cache')
            self.write_json(name, f'inboxes/{member.member_id}.json',
                            {'version': 1, 'team_id': team.team_id, 'member_id': member.member_id, 'messages': []})
            team.members[member.member_id] = member
            team.revision += 1
            team.updated_at = now()
            self.write_json(name, 'team.json', team.to_dict())
            return member

    async def start_goal(self, name, description, *, budget_limit=20):
        self.require_lead(name)
        def mutate(team):
            if team.status != 'active' or (team.active_goal_id and team.goals[team.active_goal_id].state == 'active'):
                raise TeamStoreError('只能串行建立活动目标')
            snapshot = freeze_repository(Path(team.workspace_root))
            if str(snapshot.common_git_dir) != team.repository:
                raise TeamStoreError('创建目标时仓库身份已变化')
            if git_read(snapshot.origin_root, 'rev-parse', '--verify', team.target_branch + '^{commit}') != snapshot.base_commit:
                raise TeamStoreError('当前工作根 HEAD 不在目标分支基线')
            goal = Goal(new_id('goal'), description, snapshot.base_commit, team.target_branch,
                        f'mewcode/team/{team.team_id}/{uuid4().hex}', budget_limit=budget_limit)
            team.goals[goal.goal_id] = goal
            team.active_goal_id = goal.goal_id
        team = await self.update_team(name, mutate)
        return team.goals[team.active_goal_id]

    async def pause(self, name, *, unknown_members=()):
        self.require_lead(name)
        unknown = set(unknown_members)
        def mutate(team):
            if unknown - set(team.members):
                raise TeamStoreError('未知成员身份')
            team.status = 'needs_review' if unknown or team.status == 'needs_review' else 'paused'
            for member in team.members.values():
                if member.member_id in unknown:
                    member.state = 'needs_review'
                elif member.state == 'needs_review':
                    with self.reviewed_member(name, member):
                        pass
                elif member.state not in {'registered', 'idle', 'stopped', 'blocked', 'failed'}:
                    raise TeamStoreError('运行尚未确认停止，不能保存正常暂停')
        team = await self.update_team(name, mutate)
        self.release_lead(name)
        return team

    async def resume(self, name, repository):
        snapshot = freeze_repository(Path(repository))
        lease = TeamLease(self.team_path(name) / 'lead.lock')
        try:
            async with self.lock(name):
                team = self.load(name)
                if team.repository != str(snapshot.common_git_dir):
                    raise TeamStoreError('不能从其他仓库恢复团队')
                uncertain = {'starting', 'planning', 'awaiting_approval', 'running', 'checkpoint', 'stopping'}
                if team.status == 'needs_review' or any(member.state in uncertain for member in team.members.values()):
                    raise TeamStoreError('上次运行尚待核查，拒绝重复接管')
                for member in team.members.values():
                    if member.state == 'needs_review':
                        with self.reviewed_member(name, member):
                            pass
                    if member.member_id != team.lead_id and member.active:
                        probe = TeamLease(self._path(name, f'locks/member-{member.member_id}.lock'))
                        probe.close()
                    with directory_fd(self.member_path(name, member.member_id)):
                        pass
                    if member.active and member.resume_allowed and member.state == 'stopped':
                        member.state = 'idle'
                team.status = 'active'
                team.revision += 1
                team.updated_at = now()
                self.write_json(name, 'team.json', team.to_dict())
        except BaseException:
            lease.close()
            raise
        self._leads[name] = lease
        return team
