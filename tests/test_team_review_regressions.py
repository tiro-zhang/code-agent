"""独立审查发现的问题保留为真实资源回归。"""
from pathlib import Path

import pytest

from conftest import async_test
from test_team_security import active_team
from test_team_service import make_session
from mewcode.teams.backends import BackendStop
from mewcode.teams.models import TeamValidationError


@async_test
async def test_claim_switches_persistent_budget_without_replacing_active_reference(tmp_path):
    session,runtime=await active_team(tmp_path)
    runtime.budget.take()
    runtime.budget.take()
    old=runtime.budget
    runtime.busy=True
    task=await session.teams.task({'action':'create','title':'新领取','code':False})
    claimed=await runtime.session.teams.task({'action':'claim','task_id':task['task_id']})
    assert runtime.budget is old
    assert runtime.budget.root_run_id==claimed['claim_id'] and runtime.budget.used==0
    old.take()
    assert runtime.control.budget['used']==1
    assert runtime.control.budget['root_run_id']==claimed['claim_id']
    runtime.busy=False
    await session.aclose()


@async_test
async def test_resumed_inprocess_tools_respect_current_parent_ceiling(tmp_path):
    session,runtime=await active_team(tmp_path)
    identity=runtime.member.member_id
    await session.aclose()
    fresh=make_session(tmp_path)
    fresh.provider_factory=session.provider_factory
    await fresh.teams.control({'action':'resume','name':'alpha'})
    fresh.delegation_tools=lambda:frozenset({'read_file','team_task','team_message'})
    await fresh.teams.message({'action':'send','recipient':'alice','body':'明确继续只读检查'})
    restored=fresh.teams.runners[identity]
    assert restored.session.effective_tools()<=fresh.delegation_tools()
    assert 'write_file' not in restored.session.agent.effective_tools('execute')
    await fresh.aclose()


@async_test
async def test_invalid_member_name_does_not_probe_or_create_orphan_tree(tmp_path):
    session=make_session(tmp_path)
    await session.teams.control({'action':'create','name':'alpha'})
    await session.teams.control({'action':'goal','description':'输入校验'})
    with pytest.raises(TeamValidationError):
        await session.teams.member({'action':'spawn','name':'开发者','role':'general','backend':'auto'})
    assert not hasattr(session.teams,'tmux')
    assert not (tmp_path/'.mewcode/worktrees').exists()
    assert len(session.teams.store.load('alpha').members)==1
    await session.aclose()


@async_test
async def test_pause_closes_owned_empty_backend_after_members_stop(tmp_path):
    session=make_session(tmp_path)
    await session.teams.control({'action':'create','name':'alpha'})
    class Backend:
        probed=True
        closed=False
        async def close(self):
            self.closed=True
            self.probed=False
            return BackendStop(True,'stopped')
    backend=session.teams.tmux=Backend()
    await session.teams.pause()
    assert backend.closed
    assert session.teams.store.load('alpha').status=='paused'
    await session.aclose()
