"""匹配计划批准与兄弟成员资源的实际生命周期边界。"""

import asyncio
import json
import os
from pathlib import Path
import shlex
import sys

import pytest
import yaml

from conftest import ScriptedProvider, async_test
from test_agent_loop import answer, calls, tool
from test_mcp_manager import config as mcp_config, stdio
from test_team_runtime_boundaries import (
    assign, create_team, dispatch, gated_response, spawn, wait_for,
)
from mewcode.hooks.runtime import create_runtime
from mewcode.mcp.manager import MCPManager


@pytest.mark.parametrize('effect,code', [('deny', 'permission_denied'), ('ask', 'approval_required')])
@async_test
async def test_matching_plan_approval_and_actual_start_do_not_grant_command_permission(tmp_path, effect, code):
    entered, release = asyncio.Event(), asyncio.Event()
    responses = [calls(tool('plan', 'team_message', json.dumps({
        'action': 'send', 'recipient': 'lead', 'type': 'plan_request', 'body': '开工后核查命令许可'})))]
    provider = ScriptedProvider(responses)
    session = await create_team(tmp_path, [provider])
    try:
        runtime = await spawn(session, 'alice', approval=True)
        task = await assign(session, runtime)
        await dispatch(session, runtime, task)
        await wait_for(lambda: not runtime.busy and runtime.control.plan_version == 1, runtime)
        assert runtime.control.awaiting_plan
        responses.extend([
            calls(tool('actual-start', 'team_task', json.dumps({
                'action': 'start', 'task_id': task['task_id'], 'claim_id': task['claim_id']}))),
            gated_response(entered, release, calls(tool('restricted-command', 'execute_command',
                '{"command":"touch forbidden.txt"}'))), answer('当前许可阻止命令'),
        ])
        await session.teams.message({'action': 'send', 'recipient': runtime.member.member_id,
            'type': 'plan_decision', 'body': '只批准匹配计划，不批准普通工具', 'fields': {
                **{key: task[key] for key in ('goal_id', 'task_id', 'claim_id')},
                'plan_id': runtime.control.plan_id, 'plan_version': 1, 'approved': True}})
        async with asyncio.timeout(8):
            await entered.wait()
        started = await session.teams.board().get(session.team_scope.member_id, task['task_id'])
        assert started.state == 'running' and started.synced_commit
        assert runtime.control.approved and not runtime.control.awaiting_plan
        assert not runtime.permissions.grants.session
        root = Path(runtime.member.workspace_root)
        policy = root / '.mewcode' / 'permissions.yaml'
        policy.parent.mkdir(exist_ok=True)
        policy.write_text(yaml.safe_dump({'rules': [{'effect': effect,
            'rule': 'execute_command(touch forbidden.txt)', 'match': 'exact'}]}))
        # 收紧当前真实策略，不更改计划和任务状态；成员保持非交互许可合同。
        runtime.permissions.mode = 'default'
        assert runtime.permissions.noninteractive
        release.set()
        await wait_for(lambda: len(provider.requests) == 4 and not runtime.busy, runtime)
        results = {message.tool_call_id: message.tool_result for message in runtime.session.history
                   if message.role == 'tool'}
        assert results['actual-start'].ok
        denied = results['restricted-command']
        assert not denied.ok and denied.error['code'] == code
        assert denied.error['details']['not_started'] is True
        assert not (root / 'forbidden.txt').exists() and not (tmp_path / 'forbidden.txt').exists()
        assert runtime.control.approved and not runtime.permissions.grants.session
    finally:
        release.set()
        await session.aclose()


async def install_hook(runtime):
    """真实命令持有门闩，证明 A 收尾期间 B 的后台动作仍有效。"""
    root = Path(runtime.member.workspace_root)
    (root / '.mewcode').mkdir(exist_ok=True)
    (root / 'hook_probe.py').write_text(
        "import json,os,pathlib,time\n"
        "root=pathlib.Path.cwd()\n"
        "(root/'hook-started.json').write_text(json.dumps({'pid':os.getpid(),'cwd':str(root)}))\n"
        "while not (root/'release-hook').exists(): time.sleep(.01)\n"
        "with (root/'hook-finished').open('a') as file: file.write('已完成\\n')\n")
    (root / '.mewcode' / 'hooks.yaml').write_text(yaml.safe_dump({'version': 1, 'hooks': [{
        'event': 'message.before_request', 'async': True,
        'action': {'type': 'command', 'command': shlex.join([sys.executable, 'hook_probe.py'])}}]}))
    await runtime.session.hooks.close()
    hooks = create_runtime(root, runtime.permissions, runtime.hook_mode)
    assert len(hooks.snapshot.rules) == 1 and not hooks.snapshot.diagnostics
    runtime.session.hooks = runtime.session.agent.hooks = hooks
    return root


@async_test
async def test_stopping_parallel_member_preserves_sibling_actual_mcp_and_hook(tmp_path):
    entered = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    responses = [[], []]
    providers = [ScriptedProvider(value) for value in responses]
    session = await create_team(tmp_path, providers, maximum=2)
    manager = MCPManager(mcp_config(tmp_path, {'shared': stdio(tmp_path, 'shared')}), session.executor.registry)
    try:
        await manager.start()
        session.executor.mcp = manager
        session.permissions.bind_mcp_tools(manager.tools)
        external = next(item for item in manager.tools if item.original_name == 'echo')
        alice, bob = await spawn(session, 'alice'), await spawn(session, 'bob')
        roots = [await install_hook(runtime) for runtime in (alice, bob)]
        for index, runtime in enumerate((alice, bob)):
            # Hook 探针是本地运行设施；此处核查非代码协议任务，避免把探针当未知源码同步。
            task = await session.teams.task({'action': 'create', 'title': '核查真实共享资源', 'code': False})
            task = await session.teams.task({'action': 'reassign', 'task_id': task['task_id'],
                'member_id': runtime.member.member_id})
            responses[index].extend([
                calls(tool('actual-start', 'team_task', json.dumps({
                    'action': 'start', 'task_id': task['task_id'], 'claim_id': task['claim_id']}))),
                gated_response(entered[index], release[index], calls(tool('shared-mcp', external.name,
                    json.dumps({'text': f'成员{index}的真实MCP'})))), answer('MCP 与 Hook 仍然有效'),
            ])
            await dispatch(session, runtime, task)
        async with asyncio.timeout(8):
            await asyncio.gather(*(event.wait() for event in entered))
        await wait_for(lambda: all((root / 'hook-started.json').exists() for root in roots))
        assert alice.busy and bob.busy and not bob.cancel.is_set()
        probes = [json.loads((root / 'hook-started.json').read_text()) for root in roots]
        assert [probe['cwd'] for probe in probes] == [str(root) for root in roots]
        assert alice.session.hooks.background_count and bob.session.hooks.background_count
        mcp_events = [json.loads(line) for line in (tmp_path / 'shared.jsonl').read_text().splitlines()]
        mcp_pid = next(event['pid'] for event in mcp_events if event['method'] == 'PROCESS_START')
        await session.teams.member({'action': 'stop', 'member': alice.member.member_id})
        assert providers[0].closed and not providers[1].closed and not bob.cancel.is_set()
        assert alice.session.hooks.closed and not bob.session.hooks.closed
        assert not (roots[0] / 'hook-finished').exists()
        with pytest.raises(ProcessLookupError):
            os.kill(probes[0]['pid'], 0)
        os.kill(probes[1]['pid'], 0)
        os.kill(mcp_pid, 0)
        assert manager.records['shared'].state == 'ready'
        assert bob.session.executor.mcp is manager
        assert bob.session.executor.context.root == roots[1]
        (roots[1] / 'release-hook').touch()
        await wait_for(lambda: (roots[1] / 'hook-finished').exists(), bob)
        release[1].set()
        await wait_for(lambda: len(providers[1].requests) == 3 and not bob.busy, bob)
        result = next(message.tool_result for message in bob.session.history
                      if message.role == 'tool' and message.tool_call_id == 'shared-mcp')
        assert result.ok and result.data['content'][0]['text'] == '成员1的真实MCP'
        assert not bob.cancel.is_set() and not providers[1].closed
        assert not bob.session.hooks.closed and manager.records['shared'].state == 'ready'
        events = [json.loads(line) for line in (tmp_path / 'shared.jsonl').read_text().splitlines()]
        assert sum(event['method'] == 'PROCESS_START' for event in events) == 1
        assert sum(event['method'] == 'tools/call' for event in events) == 1
        assert not any(message.tool_call_id == 'shared-mcp' for message in alice.session.history)
    finally:
        for root in locals().get('roots', ()):
            (root / 'release-hook').touch()
        for event in release:
            event.set()
        await session.aclose()
        await manager.close()
    assert manager.records['shared'].state == 'closed'
    assert not manager.records['shared'].thread.is_alive()
