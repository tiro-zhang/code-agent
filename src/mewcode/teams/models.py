"""团队持久化记录；未知字段、身份类型及预算均严格校验。"""
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
import re
from types import UnionType
from typing import get_args, get_origin, get_type_hints
from uuid import uuid4


PROTOCOL_FIELDS = {
    'text': set(),
    'task_assignment': {'goal_id', 'task_id', 'claim_id'},
    'plan_request': {'goal_id', 'task_id', 'claim_id', 'plan_id', 'plan_version'},
    'plan_decision': {'goal_id', 'task_id', 'claim_id', 'plan_id', 'plan_version', 'approved'},
    'idle': {'goal_id', 'run_id'},
    'result_submitted': {'goal_id', 'task_id', 'claim_id', 'run_id'},
    'shutdown_request': {'generation'},
    'shutdown_ack': {'generation', 'stopped'},
}


class TeamValidationError(ValueError):
    """记录不满足当前版本的存储合同。"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(kind: str) -> str:
    return kind + '-' + uuid4().hex


def validate_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', value):
        raise TeamValidationError('名称必须匹配 [a-z][a-z0-9_-]{0,63}')
    return value


def validate_id(value: str, kind: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(kind + r'-[0-9a-f]{32}', value):
        raise TeamValidationError(f'{kind} 身份无效')


def _typed(value, hint):
    origin, args = get_origin(hint), get_args(hint)
    if origin is UnionType:
        return any(_typed(value, option) for option in args)
    if origin is list:
        return type(value) is list and all(_typed(item, args[0]) for item in value)
    if origin is dict:
        return type(value) is dict and all(_typed(k, args[0]) and _typed(v, args[1]) for k, v in value.items())
    if hint is object:
        return True
    return type(value) is hint if hint in (int, bool, str, float, type(None)) else isinstance(value, hint)


class Record:
    def __post_init__(self):
        for key, hint in get_type_hints(type(self)).items():
            if not _typed(getattr(self, key), hint):
                raise TeamValidationError(f'{key} 类型无效')
        if self.version != 1:
            raise TeamValidationError('记录版本不兼容')
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name.endswith('_id') and value is not None and item.name not in {'broadcast_id', 'call_id', 'plan_id'}:
                kind = {'owner_id': 'member', 'sender_id': 'member', 'recipient_id': 'member', 'lead_id': 'member',
                        'active_goal_id': 'goal'}.get(item.name, item.name[:-3])
                validate_id(value, kind)
            if item.name == 'budget_limit' and value <= 0:
                raise TeamValidationError('budget_limit 必须为正整数')
            if item.name in {'revision', 'budget_used', 'plan_version'} and value < 0:
                raise TeamValidationError(f'{item.name} 不得为负')
            if item.name in {'created_at', 'updated_at', 'timestamp'}:
                try:
                    parsed = datetime.fromisoformat(value)
                    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
                        raise ValueError()
                except (ValueError, AttributeError):
                    raise TeamValidationError('时间必须为 UTC') from None
        if hasattr(self, 'name'):
            validate_name(self.name)
        for key in ('baseline_commit', 'integrated_commit', 'synced_commit', 'result_commit'):
            value = getattr(self, key, None)
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value)):
                raise TeamValidationError(f'{key} 必须为完整不可变提交')

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        if type(data) is not dict or 'version' not in data or set(data) - {item.name for item in fields(cls)}:
            raise TeamValidationError('记录包含未知字段')
        data = dict(data)
        if cls is Team:
            if type(data.get('members')) is not dict or type(data.get('goals', {})) is not dict:
                raise TeamValidationError('团队成员或目标必须为映射')
            data['members'] = {key: Member.from_dict(value) for key, value in data.get('members', {}).items()}
            data['goals'] = {key: Goal.from_dict(value) for key, value in data.get('goals', {}).items()}
        try:
            return cls(**data)
        except (TypeError, KeyError) as error:
            raise TeamValidationError('记录缺少字段或类型无效') from error


@dataclass
class Member(Record):
    member_id: str
    name: str
    role: str
    workspace_root: str
    version: int = 1
    role_body: str = ''
    role_fingerprint: str = ''
    definition_fingerprint: str = ''
    branch: str = ''
    backend: str = 'inprocess'
    require_plan_approval: bool = False
    session_ref: str = ''
    state: str = 'registered'
    generation: str = ''
    active: bool = True
    resume_allowed: bool = True
    revision: int = 0
    plan_id: str | None = None
    plan_version: int = 0
    plan_approved: bool = False
    task_id: str | None = None
    claim_id: str | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.backend not in {'inprocess', 'tmux'} or self.state not in {
            'registered', 'starting', 'idle', 'planning', 'awaiting_approval', 'running',
            'checkpoint', 'stopping', 'stopped', 'failed', 'blocked', 'needs_review'}:
            raise TeamValidationError('成员后端或状态无效')
        if not Path(self.workspace_root).is_absolute() or not self.role:
            raise TeamValidationError('成员工作根或角色无效')
        if self.definition_fingerprint and not re.fullmatch(r'[0-9a-f]{64}', self.definition_fingerprint):
            raise TeamValidationError('冻结成员定义指纹无效')


@dataclass
class Goal(Record):
    goal_id: str
    description: str
    baseline_commit: str
    target_branch: str
    integration_branch: str
    version: int = 1
    budget_limit: int = 20
    budget_used: int = 0
    state: str = 'active'
    created_at: str = field(default_factory=now)

    def __post_init__(self):
        super().__post_init__()
        if self.state not in {'active', 'cancelled', 'completed', 'needs_review'} or self.budget_used > self.budget_limit:
            raise TeamValidationError('目标状态或预算无效')


@dataclass
class Team(Record):
    team_id: str
    name: str
    repository: str
    workspace_root: str
    lead_id: str
    baseline_commit: str
    target_branch: str
    version: int = 1
    status: str = 'active'
    mode: str = 'execute'
    members: dict[str, Member] = field(default_factory=dict)
    goals: dict[str, Goal] = field(default_factory=dict)
    active_goal_id: str | None = None
    revision: int = 0
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    def __post_init__(self):
        super().__post_init__()
        if self.status not in {'active', 'paused', 'needs_review'}:
            raise TeamValidationError('团队状态无效')
        if self.mode not in {'execute', 'plan'}:
            raise TeamValidationError('团队模式无效')
        if not Path(self.repository).is_absolute() or not Path(self.workspace_root).is_absolute():
            raise TeamValidationError('仓库身份必须为绝对路径')
        if len({member.name for member in self.members.values()}) != len(self.members):
            raise TeamValidationError('成员名称重复')
        if any(key != member.member_id for key, member in self.members.items()):
            raise TeamValidationError('花名册身份不一致')
        if self.lead_id not in self.members or any(key != goal.goal_id for key, goal in self.goals.items()):
            raise TeamValidationError('Lead 或目标归属无效')
        if self.active_goal_id is not None and self.active_goal_id not in self.goals:
            raise TeamValidationError('活动目标不存在')


@dataclass
class Task(Record):
    task_id: str
    team_id: str
    goal_id: str
    title: str
    description: str = ''
    version: int = 1
    state: str = 'pending'
    owner_id: str | None = None
    claim_id: str | None = None
    depends_on: list[str] = field(default_factory=list)
    revision: int = 0
    code: bool = True
    result: dict[str, object] = field(default_factory=dict)
    history: list[dict[str, object]] = field(default_factory=list)
    integrated_commit: str | None = None
    synced_commit: str | None = None
    budget_limit: int = 20
    budget_used: int = 0
    reason: str = ''
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    def __post_init__(self):
        super().__post_init__()
        if self.state not in {'pending', 'claimed', 'running', 'submitted', 'accepted', 'integrated',
                              'completed', 'blocked', 'failed', 'needs_review'} or not self.title.strip():
            raise TeamValidationError('任务状态或标题无效')
        for identity in self.depends_on:
            validate_id(identity, 'task')
        if self.budget_used > self.budget_limit:
            raise TeamValidationError('任务预算超限')


@dataclass
class Run(Record):
    run_id: str
    member_id: str
    goal_id: str
    task_id: str
    claim_id: str
    version: int = 1
    backend_generation: str = ''
    budget_limit: int = 20
    budget_used: int = 0
    stop_reason: str = ''
    state: str = 'running'

    def __post_init__(self):
        super().__post_init__()
        if self.budget_used > self.budget_limit or self.state not in {'running', 'stopping', 'stopped', 'completed', 'failed', 'blocked', 'needs_review'}:
            raise TeamValidationError('运行状态或预算无效')


@dataclass
class Message(Record):
    message_id: str
    team_id: str
    sender_id: str
    recipient_id: str
    body: str
    summary: str
    type: str = 'text'
    version: int = 1
    protocol_version: int = 1
    timestamp: str = field(default_factory=now)
    read: bool = False
    fields: dict[str, object] = field(default_factory=dict)
    call_id: str | None = None
    broadcast_id: str | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.protocol_version != 1 or self.type not in {'text', 'task_assignment', 'plan_request',
            'plan_decision', 'idle', 'result_submitted', 'shutdown_request', 'shutdown_ack'}:
            raise TeamValidationError('消息协议无效')
        if set(self.fields) != PROTOCOL_FIELDS[self.type]:
            raise TeamValidationError('协议消息专用字段无效')
        for key, kind in {'goal_id': 'goal', 'task_id': 'task', 'claim_id': 'claim', 'run_id': 'run'}.items():
            if key in self.fields:
                validate_id(self.fields[key], kind)
        if 'plan_version' in self.fields and (type(self.fields['plan_version']) is not int or self.fields['plan_version'] < 1):
            raise TeamValidationError('计划版本无效')
        if 'approved' in self.fields and type(self.fields['approved']) is not bool:
            raise TeamValidationError('计划决定类型无效')
        if 'stopped' in self.fields and self.fields['stopped'] is not True:
            raise TeamValidationError('停止确认字段无效')
        if len(self.body.encode()) > 65536 or len(self.summary) > 256:
            raise TeamValidationError('消息正文或摘要超限')


@dataclass
class IntegrationOperation(Record):
    operation_id: str
    team_id: str
    goal_id: str
    pre_head: str
    inputs: list[str]
    version: int = 1
    state: str = 'prepared'
    result_commit: str | None = None
    evidence: dict[str, object] = field(default_factory=dict)
    rollback_result: str = ''


    def __post_init__(self):
        super().__post_init__()
        if self.state not in {'prepared', 'merging', 'conflict', 'validated', 'published', 'rolled_back', 'failed', 'needs_review'}:
            raise TeamValidationError('整合操作状态无效')
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', self.pre_head):
            raise TeamValidationError('整合检查点提交无效')
        if any(not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit) for commit in self.inputs):
            raise TeamValidationError('整合输入提交无效')
