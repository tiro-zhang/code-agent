"""团队规划消息只能唤醒既有只读成员，返回执行不隐式派活。"""
import asyncio

import pytest

from conftest import ScriptedProvider, async_test
from test_agent_loop import answer, calls, tool
from test_team_runtime_boundaries import create_team, spawn, wait_for
from test_team_worker import registration, worker_api
from test_team_service import make_session
from mewcode.teams.models import Team, TeamValidationError


@async_test
async def test_plan_message_wakes_existing_member_with_real_readonly_tool_guard(tmp_path):
    old = ScriptedProvider([])
    planned = ScriptedProvider([
        calls(tool('write', 'write_file', '{"path":"forbidden.txt","content":"禁止"}'),
              tool('shell', 'execute_command', '{"command":"touch shell.txt"}'),
              tool('claim', 'team_task', '{"action":"create","title":"禁止间接派活"}')),
        answer('只读规划完成'),
    ])
    session = await create_team(tmp_path, [old, planned])
    original = await spawn(session, 'alice')
    identity = original.member.member_id
    root = original.session.executor.context.root
    try:
        await session.set_mode('plan')
        delivered = await session.teams.message({'action':'send','recipient':'alice','body':'规划修改代码'})
        assert delivered['saved'] and delivered['notified']
        runtime = session.teams.runners[identity]
        await wait_for(lambda:len(planned.requests)==2 and not runtime.busy,runtime)
        assert runtime.session.mode == 'plan'
        assert not (root/'forbidden.txt').exists() and not (root/'shell.txt').exists()
        results = [m.tool_result for m in runtime.session.history if m.role=='tool']
        assert len(results)==3 and all(not result.ok and result.error['code']=='tool_not_allowed' for result in results)
        assert not await session.teams.board().list(session.team_scope.member_id)
        before = len(planned.requests)
        await session.set_mode('execute')
        await asyncio.sleep(.3)
        assert not session.teams.runners and len(planned.requests)==before
        assert session.teams.store.load('alpha').mode=='execute'
    finally:
        await session.aclose()


@async_test
async def test_tmux_startup_preserves_trusted_plan_mode(tmp_path):
    session,store,team,member,launch=await registration(tmp_path)
    try:
        await store.update_team(team.name,lambda value:setattr(value,'mode','plan'))
        record=worker_api().load_startup(launch.startup_path,launch.config_path)
        assert record.team.mode=='plan'
        from mewcode.teams.runtime import MemberRuntime
        from mewcode.teams.service import TeamService
        from mewcode.teams.capabilities import TeamScope
        session.team_scope=TeamScope(team.name,team.lead_id,True)
        owner=TeamService(session)
        owner.store=store
        parent,permissions=worker_api().build_permissions(record)
        runtime=MemberRuntime(owner,record.member,record.definition,session.config,ScriptedProvider([]),permissions,
                              registry=session.executor.registry,launch=record.launch)
        await runtime.setup()
        assert runtime.session.mode=='plan'
        result=await runtime.session.executor.execute('execute_command','{"command":"touch forbidden.txt"}',
            allowed_tools=runtime.session.effective_tools)
        assert not result.ok and result.error['code']=='tool_not_allowed'
        assert not (launch.workspace_root/'forbidden.txt').exists()
        await runtime.close()
    finally:
        store.release_lead(team.name)
        session.team_scope=None
        await session.aclose()


@async_test
async def test_trusted_team_mode_rejects_unknown_value(tmp_path):
    session=await create_team(tmp_path,[ScriptedProvider([])])
    try:
        value=session.teams.store.load('alpha').to_dict()
        old=dict(value)
        old.pop('mode',None)
        assert Team.from_dict(old).mode=='execute'
        value['mode']='bypass'
        with pytest.raises(TeamValidationError):
            Team.from_dict(value)
    finally:
        await session.aclose()


@async_test
async def test_approved_plan_clears_only_stale_waiting_notice(tmp_path):
    session=await create_team(tmp_path,[ScriptedProvider([])])
    runtime=await spawn(session,'alice',approval=True)
    try:
        await runtime.before_request()
        assert '计划尚未批准' in runtime.session.prompt_state.workspace_notice
        runtime.control.approved=True
        await runtime.before_request()
        assert '计划尚未批准' not in runtime.session.prompt_state.workspace_notice
        runtime.session.prompt_state.workspace_notice='本次任务持有冲突处理租约：/actual/integration'
        await runtime.before_request()
        assert '/actual/integration' in runtime.session.prompt_state.workspace_notice
    finally:
        await session.aclose()


@async_test
async def test_explicit_execute_input_after_plan_resume_recomputes_current_upper_mode(tmp_path):
    session=await create_team(tmp_path,[ScriptedProvider([])])
    runtime=await spawn(session,'alice')
    identity=runtime.member.member_id
    await session.set_mode('plan')
    await session.aclose()
    fresh=make_session(tmp_path)
    provider=ScriptedProvider([answer('明确继续')])
    fresh.provider_factory=lambda config:provider
    try:
        await fresh.teams.control({'action':'resume','name':'alpha'})
        assert not fresh.teams.runners and not provider.requests
        await fresh.teams.parent_for_input('明确恢复execute任务',asyncio.Event())
        await fresh.teams.message({'action':'send','recipient':'alice','body':'明确继续'})
        restored=fresh.teams.runners[identity]
        await wait_for(lambda:len(provider.requests)==1 and not restored.busy,restored)
        assert restored.session.mode=='execute'
        assert fresh.teams.store.load('alpha').mode=='execute'
    finally:
        await fresh.aclose()
