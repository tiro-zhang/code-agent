"""团队协议工具使用真实配置和完整参数授权，不扩大角色门禁。"""

import json
from pathlib import Path

import pytest

from conftest import ScriptedProvider, async_test
from mewcode.permissions.config import PermissionConfigError
from mewcode.tools.base import ToolError
from test_agent_loop import answer, calls, tool
from test_permission_runtime import manager, policy, rule
from test_team_runtime_boundaries import create_team, dispatch, spawn, wait_for


TEAM_TOOLS = ('team', 'team_member', 'team_task', 'team_message', 'team_integrate')


@pytest.mark.parametrize('name', TEAM_TOOLS)
@async_test
async def test_team_default_without_rule_and_strict_with_allow_still_require_approval(tmp_path, name):
    runtime = manager(tmp_path, noninteractive=True)
    with pytest.raises(ToolError) as caught:
        await runtime.authorize(name, {'action': 'read'})
    assert caught.value.code == 'approval_required'
    policy(tmp_path, [rule('allow', f'{name}(*)')])
    runtime.mode = 'strict'
    with pytest.raises(ToolError) as caught:
        await runtime.authorize(name, {'action': 'read'})
    assert caught.value.code == 'approval_required'


@pytest.mark.parametrize('name', TEAM_TOOLS)
@async_test
async def test_team_exact_rules_bind_complete_canonical_arguments(tmp_path, name):
    args = {'action': 'send', 'body': '检查 src/a.py', 'recipient': 'lead'}
    policy(tmp_path, [rule('allow', f'{name}({json.dumps(args, ensure_ascii=False)})', 'exact')])
    runtime = manager(tmp_path, noninteractive=True)
    assert (await runtime.authorize(name, dict(reversed(tuple(args.items()))))).arguments == args
    with pytest.raises(ToolError) as caught:
        await runtime.authorize(name, {**args, 'body': '改变正文'})
    assert caught.value.code == 'approval_required'


@pytest.mark.parametrize('name', TEAM_TOOLS)
@async_test
async def test_team_glob_deny_overrides_allow_and_handles_slash(tmp_path, name):
    args = {'action': 'send', 'body': 'src/a.py', 'recipient': 'lead'}
    policy(tmp_path, [rule('allow', f'{name}(*)')])
    runtime = manager(tmp_path, noninteractive=True)
    assert (await runtime.authorize(name, args)).arguments == args
    policy(tmp_path, [rule('allow', f'{name}(*)'),
                     rule('deny', f'{name}({json.dumps(args)})', 'exact')])
    with pytest.raises(ToolError) as caught:
        await runtime.authorize(name, args)
    assert caught.value.code == 'permission_denied' and caught.value.details['not_started']


@pytest.mark.parametrize('name', TEAM_TOOLS)
@async_test
async def test_team_session_and_permanent_approval_bind_parameters(tmp_path, name):
    choices = iter(('session', 'permanent'))
    async def respond(request, cancel):
        return next(choices)
    runtime = manager(tmp_path, responder=respond)
    args = {'action': 'read'}
    await runtime.authorize(name, args)
    await runtime.authorize(name, args)
    await runtime.authorize(name, {'action': 'list'})
    fresh = manager(tmp_path, noninteractive=True)
    await fresh.authorize(name, {'action': 'list'})
    assert len(runtime.grants.session) == 1
    with pytest.raises(ToolError) as caught:
        await fresh.authorize(name, args)
    assert caught.value.code == 'approval_required'


@pytest.mark.parametrize('name', TEAM_TOOLS)
@pytest.mark.parametrize('pattern', ('send', '[]', '{"action":NaN}'))
def test_team_exact_rule_requires_finite_json_object(tmp_path, name, pattern):
    policy(tmp_path, [rule('allow', f'{name}({pattern})', 'exact')])
    with pytest.raises(PermissionConfigError):
        manager(tmp_path).config.load()


@pytest.mark.parametrize('effect', ('allow', 'deny'))
@async_test
async def test_default_member_actual_plan_message_and_start_use_configured_permission(tmp_path, effect):
    responses = [calls(tool('plan', 'team_message', json.dumps({
        'action': 'send', 'recipient': 'lead', 'type': 'plan_request', 'body': '检查 src/a.py'})))]
    provider = ScriptedProvider(responses)
    session = await create_team(tmp_path, [provider])
    try:
        policy(tmp_path, [rule('allow', 'Bash(git *)'), rule('allow', 'team_message(*)'),
                          rule('allow', 'team_task(*)')])
        session.permissions.mode = 'default'
        runtime = await spawn(session, 'alice', approval=True)
        assert runtime.permissions.mode == 'default'
        task = await session.teams.task({'action': 'create', 'title': '规划许可', 'code': False})
        task = await session.teams.task({'action': 'reassign', 'task_id': task['task_id'],
                                         'member_id': runtime.member.member_id})
        start = {'action': 'start', 'task_id': task['task_id'], 'claim_id': task['claim_id']}
        rules = [rule('allow', 'team_message(*)'), rule('allow', 'team_task(*)')]
        if effect == 'deny':
            rules.append(rule('deny', f'team_task({json.dumps(start)})', 'exact'))
        for root in (tmp_path, Path(runtime.member.workspace_root)):
            policy(root, rules)
        assert runtime.permissions.noninteractive
        await dispatch(session, runtime, task)
        await wait_for(lambda: len(provider.requests) == 1 and not runtime.busy, runtime)
        assert runtime.control.awaiting_plan and runtime.control.plan_version == 1
        assert next(m.tool_result for m in runtime.session.history if m.tool_call_id == 'plan').ok
        responses.extend([calls(tool('start', 'team_task', json.dumps(start))),
            calls(tool('message', 'team_message', '{"action":"send","recipient":"lead","body":"开工结果"}')),
            answer('许可已核查')])
        decision = await session.executor.execute('team_message', json.dumps({
            'action': 'send', 'recipient': runtime.member.member_id, 'type': 'plan_decision',
            'body': '批准匹配计划', 'fields': {**{k: task[k] for k in ('goal_id','task_id','claim_id')},
                'plan_id': runtime.control.plan_id, 'plan_version': 1, 'approved': True}}))
        assert decision.ok
        await wait_for(lambda: len(provider.requests) == 4 and not runtime.busy, runtime)
        results = {m.tool_call_id: m.tool_result for m in runtime.session.history if m.role == 'tool'}
        assert results['message'].ok
        current = await session.teams.board().get(session.team_scope.member_id, task['task_id'])
        if effect == 'allow':
            assert results['start'].ok and current.state == 'running'
        else:
            assert not results['start'].ok and results['start'].error['code'] == 'permission_denied'
            assert results['start'].error['details']['not_started'] and current.state == 'claimed'
        assert not runtime.permissions.grants.session
        assert not (Path(runtime.member.workspace_root) / 'src/a.py').exists()
        forbidden = await runtime.session.executor.execute('team_task', json.dumps({
            'action': 'accept', 'task_id': task['task_id'], 'expected_revision': current.revision,
            'reason': '越权接纳'}))
        assert not forbidden.ok and forbidden.error['code'] == 'tool_not_allowed'
        runtime.session.enter_plan()
        forbidden = await runtime.session.executor.execute('team_task', json.dumps(start))
        assert not forbidden.ok and forbidden.error['code'] == 'tool_not_allowed'
        assert len(await session.teams.board().list(session.team_scope.member_id)) == 1
    finally:
        await session.aclose()
