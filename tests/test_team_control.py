"""计划决定、任务预算及已消费消息都依赖结构化身份。"""
import pytest

from mewcode.teams.control import MemberControl, PersistentBudget
from mewcode.tools.base import ToolError


def test_plan_decision_requires_matching_lead_and_version():
    control = MemberControl('team-a', 'member-a', 'lead-a', require_plan_approval=True)
    request = control.propose('task-a', 'claim-a', '先读文件')
    assert control.awaiting_plan
    with pytest.raises(ToolError):
        control.decide('other', request['plan_id'], 1, True)
    control.decide('lead-a', request['plan_id'], 1, True)
    assert not control.awaiting_plan
    control.propose('task-a', 'claim-a', '再改测试')
    with pytest.raises(ToolError):
        control.decide('lead-a', request['plan_id'], 1, True)
    assert control.awaiting_plan
    restored = MemberControl.from_dict(control.to_dict())
    assert restored.plan_version == 2 and restored.awaiting_plan


def test_budget_write_failure_never_allows_request():
    def failed(value):
        raise OSError('磁盘不可写')
    budget = PersistentBudget(3, 'run-a', commit=failed)
    with pytest.raises(OSError):
        budget.take()
    assert budget.used == 0


def test_budget_restore_does_not_refresh_limit():
    saved = []
    budget = PersistentBudget(2, 'run-a', commit=saved.append)
    budget.take()
    restored = PersistentBudget.from_dict(saved[-1], commit=saved.append)
    restored.take()
    assert restored.remaining == 0
    with pytest.raises(RuntimeError):
        restored.take()
