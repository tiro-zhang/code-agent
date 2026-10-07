"""成员 Hook 副作用遵守计划审批、开始任务与角色能力边界。"""
import shlex
import sys

import pytest

from conftest import async_test
from test_team_security import active_team
from mewcode.hooks.models import HookAction, HookConfigSnapshot, HookRule


@pytest.mark.parametrize('stage', ['unassigned', 'claimed', 'awaiting', 'approved'])
@async_test
async def test_member_hooks_cannot_write_before_task_start(tmp_path, stage):
    session, runtime = await active_team(tmp_path, approval=stage in {'awaiting', 'approved'})
    try:
        if stage != 'unassigned':
            task = await session.teams.task({'action': 'create', 'title': '有明确开工门槛'})
            task = await session.teams.task({'action': 'reassign', 'task_id': task['task_id'],
                                            'member_id': runtime.member.member_id})
            runtime.reconcile_control()
            runtime.restore_budget()
            if stage in {'awaiting', 'approved'}:
                fields = await runtime.propose('读取后按批准计划修改')
                if stage == 'approved':
                    await session.teams.message({'action': 'send', 'recipient': 'alice',
                        'type': 'plan_decision', 'fields': {**fields, 'approved': True}, 'body': '批准'})
                    await runtime.receive()
        root = runtime.session.executor.context.root
        command = f'{shlex.quote(sys.executable)} -c ' + shlex.quote("from pathlib import Path;Path('hook-write.txt').touch()")
        hooks = runtime.session.hooks
        hooks.snapshot = HookConfigSnapshot(root, (HookRule('turn.start', HookAction('command', command=command)),))
        await hooks.emit('turn.start', mode='execute')
        assert not (root/'hook-write.txt').exists()
    finally:
        await session.aclose()


@async_test
async def test_started_member_hook_checks_current_role_ceiling(tmp_path):
    session, runtime = await active_team(tmp_path)
    try:
        task = await session.teams.task({'action': 'create', 'title': '受管命令'})
        task = await session.teams.task({'action': 'reassign', 'task_id': task['task_id'], 'member_id': runtime.member.member_id})
        runtime.reconcile_control()
        runtime.restore_budget()
        await runtime.start_task(task['task_id'], task['claim_id'])
        root = runtime.session.executor.context.root
        command = f'{shlex.quote(sys.executable)} -c ' + shlex.quote("from pathlib import Path;Path('hook-write.txt').touch()")
        hooks = runtime.session.hooks
        hooks.snapshot = HookConfigSnapshot(root, (HookRule('turn.start', HookAction('command', command=command)),))
        await hooks.emit('turn.start')
        assert (root/'hook-write.txt').exists()
        (root/'hook-write.txt').unlink()
        runtime.ceiling = runtime.ceiling - {'execute_command'}
        await hooks.emit('turn.start', mode='execute')
        assert not (root/'hook-write.txt').exists()
    finally:
        await session.aclose()
