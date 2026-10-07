"""持久计划门禁与请求预算，不从聊天正文恢复权限。"""
from dataclasses import asdict, dataclass, field, fields
from uuid import uuid4

from ..skills.budget import TaskBudget
from ..tools.base import ToolError


@dataclass
class MemberControl:
    team_id: str
    member_id: str
    lead_id: str
    require_plan_approval: bool = False
    version: int = 1
    task_id: str | None = None
    claim_id: str | None = None
    plan_id: str | None = None
    plan_version: int = 0
    approved: bool = False
    consumed_ids: list[str] = field(default_factory=list)
    budget: dict | None = None

    @property
    def awaiting_plan(self):
        return self.require_plan_approval and not self.approved

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {f.name for f in fields(cls)}:
            raise ValueError('成员控制状态字段无效')
        if type(value['version']) is not int or value['version'] != 1:
            raise ValueError('成员控制状态版本无效')
        for key in ('team_id', 'member_id', 'lead_id'):
            if not isinstance(value[key], str) or not value[key]:
                raise ValueError('成员控制身份无效')
        for key in ('require_plan_approval', 'approved'):
            if type(value[key]) is not bool:
                raise ValueError('成员控制布尔状态无效')
        if type(value['plan_version']) is not int or value['plan_version'] < 0:
            raise ValueError('成员计划版本无效')
        for key in ('task_id', 'claim_id', 'plan_id'):
            if value[key] is not None and (not isinstance(value[key], str) or not value[key]):
                raise ValueError('成员关联身份无效')
        ids = value['consumed_ids']
        if (not isinstance(ids, list) or any(not isinstance(i, str) or not i for i in ids)
                or len(ids) != len(set(ids))):
            raise ValueError('邮箱消费身份无效')
        if value['budget'] is not None:
            PersistentBudget.from_dict(value['budget'])
        return cls(**value)

    def bind_task(self, task_id, claim_id):
        if (task_id, claim_id) != (self.task_id, self.claim_id):
            self.task_id, self.claim_id = task_id, claim_id
            self.plan_id, self.plan_version, self.approved = None, 0, False
            self.budget = None

    def propose(self, task_id, claim_id, body):
        if not isinstance(body, str) or not body.strip():
            raise ToolError('invalid_arguments', '计划正文不能为空', not_started=True)
        self.bind_task(task_id, claim_id)
        self.plan_id = self.plan_id or 'plan-' + uuid4().hex
        self.plan_version += 1
        self.approved = False
        return {'task_id':task_id, 'claim_id':claim_id, 'plan_id':self.plan_id,
                'plan_version':self.plan_version}

    def decide(self, sender_id, plan_id, plan_version, approved):
        if (sender_id != self.lead_id or plan_id != self.plan_id or
                type(plan_version) is not int or plan_version != self.plan_version
                or not self.task_id or not self.claim_id or type(approved) is not bool):
            raise ToolError('team_plan_mismatch', '决定必须来自 Lead 并匹配当前计划版本', not_started=True)
        self.approved = approved


class PersistentBudget(TaskBudget):
    def __init__(self, limit, root_run_id, *, used=0, commit=None):
        if (type(limit) is not int or limit <= 0 or type(used) is not int
                or not 0 <= used <= limit or not isinstance(root_run_id, str) or not root_run_id):
            raise ValueError('持久任务预算无效')
        super().__init__(limit, root_run_id, used)
        self.commit = commit

    def to_dict(self, *, used=None):
        return {'limit':self.limit, 'root_run_id':self.root_run_id, 'used':self.used if used is None else used}

    @classmethod
    def from_dict(cls, value, *, commit=None):
        if not isinstance(value, dict) or set(value) != {'limit', 'root_run_id', 'used'}:
            raise ValueError('持久任务预算字段无效')
        return cls(value['limit'], value['root_run_id'], used=value['used'], commit=commit)

    def take(self):
        if self.remaining <= 0:
            raise RuntimeError('任务请求预算已耗尽')
        if self.commit:
            self.commit(self.to_dict(used=self.used + 1))
        self.used += 1
