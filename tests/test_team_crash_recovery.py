"""真实子进程死亡后的缺失结果恢复，不抢占存活成员的固定租约。"""
import asyncio
from pathlib import Path
import signal
import sys

import pytest

from conftest import ScriptedProvider, async_test
from test_agent_loop import answer
from test_team_lead_resume import reopen
from test_team_security import active_team
from mewcode.sessions import Journal, encode_message
from mewcode.teams.store import TeamLease
from mewcode.tools.base import ToolError
from mewcode.types import Message


# 子进程持有真实成员租约与真实 Journal；通知父进程时，意图已经 fsync。
# 可选执行实际本地写入后仍不记录结果，复现无法确认副作用的崩溃窗口。
JOURNAL_WRITER = r'''
import json
from pathlib import Path
import sys
from mewcode.sessions import Journal, encode_message
from mewcode.teams.store import TeamLease
from mewcode.tools.base import ToolContext
from mewcode.tools.files import WriteFile
from mewcode.types import Message, ToolCall

store_root, identity, root, storage, journal_id, protocol, model, effect = sys.argv[1:]
lease = TeamLease(Path(store_root) / 'alpha' / 'locks' / ('member-' + identity + '.lock'))
journal = Journal.resume(Path(root), journal_id, protocol, model,
    storage_root=Path(storage), record_activity=False)
arguments = {'path': 'crash-side-effect.txt', 'content': '实际操作已完成，但结果尚未入档\n'}
call = ToolCall('crash-write-call', 'write_file', json.dumps(arguments, ensure_ascii=False))
journal.append('interaction_started', {'interaction_id': 'crash-interaction',
    'messages': [encode_message(Message('user', '继续当前领取任务')),
                 encode_message(Message('assistant', tool_calls=(call,)))]})
if effect == 'yes':
    WriteFile().execute(arguments, ToolContext(Path(root)))
print('intent-durable', flush=True)
sys.stdin.read()
'''


async def frozen_running_task(tmp_path, *, accepted=False):
    """保留真实开工与预算身份，先正常释放原协程的上下文所有权。"""
    session, runtime = await active_team(tmp_path)
    created = await session.teams.task({'action': 'create', 'title': '恢复不可确认工具操作', 'code': False})
    assigned = await session.teams.task({'action': 'reassign', 'task_id': created['task_id'],
                                       'member_id': runtime.member.member_id})
    runtime.reconcile_control()
    runtime.restore_budget()
    await runtime.start_task(assigned['task_id'], assigned['claim_id'])
    runtime.budget.take()
    await session.teams.board().use_budget(runtime.member.member_id, assigned['task_id'], assigned['claim_id'])
    runtime.session.history.extend([Message('user', '已经确认的旧请求'), Message('assistant', '已经保存的旧结论')])
    runtime.session._checkpoint(runtime.session.history, runtime.session.context.state())
    runtime.save_control()
    if accepted:
        submitted = await session.teams.board().submit(runtime.member.member_id, assigned['task_id'], assigned['claim_id'],
            {'summary': '已独立验收的结果', 'verified': True, 'evidence': ['实际非代码成果']})
        await session.teams.board().accept(session.team_scope.member_id, assigned['task_id'], submitted.revision,
                                          reason='Lead 已验收')
    await session.teams.stop_member(runtime.member.member_id, resumable=True)
    member = session.teams.store.load('alpha').members[runtime.member.member_id]
    await session.aclose()
    return session, runtime, member, assigned


async def journal_writer(session, member, *, effect=False):
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', JOURNAL_WRITER,
        str(session.teams.store.root), member.member_id, member.workspace_root,
        str(session.teams.store.member_path('alpha', member.member_id)), member.session_ref,
        session.config.protocol, session.config.model, 'yes' if effect else 'no',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        line = await asyncio.wait_for(process.stdout.readline(), 5)
        assert line == b'intent-durable\n', (await process.stderr.read()).decode()
        return process
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise


async def kill_writer(process):
    if process.returncode is None:
        process.send_signal(signal.SIGKILL)
    await asyncio.wait_for(process.wait(), 5)
    assert process.returncode == -signal.SIGKILL


@pytest.mark.parametrize('effect', [False, True])
@async_test
async def test_explicit_lead_resume_isolates_incomplete_member_task_without_replay(tmp_path, effect):
    previous, old_runtime, member, assigned = await frozen_running_task(tmp_path)
    process = await journal_writer(previous, member, effect=effect)
    await kill_writer(process)
    journal = Journal.resume(Path(member.workspace_root), member.session_ref,
        previous.config.protocol, previous.config.model,
        storage_root=previous.teams.store.member_path('alpha', member.member_id), record_activity=False)
    try:
        assert any('副作用' in warning for warning in journal.warnings)
        assert any(message.content == '已经保存的旧结论' for message in journal.projection.history)
        assert not any(call.id == 'crash-write-call' for message in journal.projection.history for call in message.tool_calls)
        assert journal.projection.unconfirmed_interactions[0]['claim_id'] == assigned['claim_id']
        if effect:
            # 保存合法截断历史不能使之前的未知操作变成已确认结果。
            journal.append('history_checkpoint', {'history': [encode_message(message) for message in journal.projection.history],
                                                  'state': journal.projection.state})
    finally:
        journal.close()
    resumed = reopen(previous)
    member_provider = ScriptedProvider([answer('不应执行旧领取任务')])
    resumed.provider_factory = lambda config: member_provider
    try:
        await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
        assert not resumed.provider.requests and not member_provider.requests and not resumed.teams.runners
        team = resumed.teams.store.load('alpha')
        restored = await resumed.teams.board().get(team.lead_id, assigned['task_id'])
        assert restored.state in {'blocked', 'needs_review'}
        assert '副作用' in restored.reason
        assert restored.claim_id == assigned['claim_id'] and restored.owner_id == member.member_id
        assert restored.budget_used == 1 and restored.result == {}
        assert team.members[member.member_id].generation == member.generation
        assert team.members[member.member_id].state == 'needs_review'
        assert team.members[member.member_id].workspace_root == member.workspace_root
        assert (Path(member.workspace_root) / 'crash-side-effect.txt').exists() is effect
        with pytest.raises((OSError, ValueError, ToolError)):
            await resumed.teams.board().use_budget(member.member_id, assigned['task_id'], assigned['claim_id'])
    finally:
        await resumed.aclose()


@async_test
async def test_later_text_cannot_continue_incomplete_claim_until_explicit_reassignment(tmp_path):
    previous, old_runtime, member, assigned = await frozen_running_task(tmp_path)
    process = await journal_writer(previous, member, effect=True)
    await kill_writer(process)
    resumed = reopen(previous)
    member_provider = ScriptedProvider([answer('普通消息不得重新开工')])
    resumed.provider_factory = lambda config: member_provider
    try:
        await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
        try:
            await resumed.teams.message({'action': 'send', 'recipient': member.member_id, 'body': '继续此前工作'})
        except (ValueError, ToolError):
            pass
        # 消息通知会完成实际运行器 setup；给异步接收者一次处理边界。
        await asyncio.sleep(.15)
        assert not member_provider.requests
        saved = await resumed.teams.board().get(resumed.team_scope.member_id, assigned['task_id'])
        assert saved.state in {'blocked', 'needs_review'} and saved.claim_id == assigned['claim_id']
        assert (Path(member.workspace_root) / 'crash-side-effect.txt').read_text() == '实际操作已完成，但结果尚未入档\n'
        reassigned = await resumed.teams.task({'action': 'reassign', 'task_id': assigned['task_id'],
                                             'member_id': member.member_id})
        assert reassigned['claim_id'] != assigned['claim_id'] and reassigned['state'] == 'claimed'
        assert not member_provider.requests
        assert any(entry.get('claim_id') == assigned['claim_id'] for entry in reassigned['history'])
        await resumed.teams.message({'action': 'send', 'recipient': member.member_id, 'body': '已核查副作用，明确新领取继续',
            'type': 'task_assignment', 'fields': {key: reassigned[key] for key in ('goal_id', 'task_id', 'claim_id')}})
        async with asyncio.timeout(3):
            while not member_provider.requests or resumed.teams.runners[member.member_id].busy:
                await asyncio.sleep(.01)
        assert len(member_provider.requests) == 1
        recovered_history = member_provider.requests[0][0]
        assert any(message.content == '已经保存的旧结论' for message in recovered_history)
        assert not any(call.id == 'crash-write-call' for message in recovered_history for call in message.tool_calls)
        assert (Path(member.workspace_root) / 'crash-side-effect.txt').read_text() == '实际操作已完成，但结果尚未入档\n'
    finally:
        await resumed.aclose()


@async_test
async def test_lazy_member_setup_blocks_newly_discovered_incomplete_interaction(tmp_path):
    previous, old_runtime, member, assigned = await frozen_running_task(tmp_path)
    resumed = reopen(previous)
    member_provider = ScriptedProvider([answer('新发现的未知操作不应继续')])
    resumed.provider_factory = lambda config: member_provider
    try:
        await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
        # Lead 完成无模型恢复之后，真实成员进程才进入意图已存的崩溃窗口。
        process = await journal_writer(previous, member, effect=True)
        await kill_writer(process)
        await resumed.teams.message({'action': 'send', 'recipient': member.member_id, 'body': '普通后续通知'})
        await asyncio.sleep(.15)
        assert not member_provider.requests
        task = await resumed.teams.board().get(resumed.team_scope.member_id, assigned['task_id'])
        assert task.state == 'blocked' and '副作用' in task.reason
        runtime = resumed.teams.runners[member.member_id]
        assert runtime.control.claim_id == assigned['claim_id']
        assert resumed.teams.store.load('alpha').members[member.member_id].state == 'needs_review'
        assert (Path(member.workspace_root) / 'crash-side-effect.txt').exists()
        await resumed.teams.message({'action': 'send', 'recipient': member.member_id, 'body': '迟到的原领取指派',
            'type': 'task_assignment', 'fields': {key: assigned[key] for key in ('goal_id', 'task_id', 'claim_id')}})
        await asyncio.sleep(.15)
        assert not member_provider.requests and not runtime._closed
        assert resumed.teams.store.load('alpha').members[member.member_id].state == 'needs_review'
    finally:
        await resumed.aclose()


@async_test
async def test_repeated_recovery_audits_incomplete_successful_task_without_downgrade_or_duplicate(tmp_path):
    previous, old_runtime, member, assigned = await frozen_running_task(tmp_path, accepted=True)
    process = await journal_writer(previous, member)
    await kill_writer(process)
    resumed = reopen(previous)
    await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
    saved = await resumed.teams.board().get(resumed.team_scope.member_id, assigned['task_id'])
    assert saved.state in {'accepted', 'integrated', 'completed'} and saved.result['summary'] == '已独立验收的结果' and saved.budget_used == 1
    assert sum(entry.get('reported_state') == 'blocked' for entry in saved.history) == 1
    await resumed.aclose()
    fresh = reopen(resumed)
    try:
        await fresh.teams.control({'action': 'resume', 'name': 'alpha'})
        again = await fresh.teams.board().get(fresh.team_scope.member_id, assigned['task_id'])
        assert again.to_dict() == saved.to_dict()
        assert fresh.teams.store.load('alpha').members[member.member_id].state == 'needs_review'
        assert not fresh.provider.requests and not fresh.teams.runners
    finally:
        await fresh.aclose()


@pytest.mark.parametrize('change', ['generation', 'session_id', 'claim_id', 'interactions', 'version', 'extra'])
@async_test
async def test_modified_private_recovery_record_does_not_authorize_resume(tmp_path, change):
    previous, old_runtime, member, assigned = await frozen_running_task(tmp_path)
    process = await journal_writer(previous, member)
    await kill_writer(process)
    resumed = reopen(previous)
    await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
    await resumed.aclose()
    store = resumed.teams.store
    relative = f'members/{member.member_id}/recovery.json'
    record = store.read_json('alpha', relative)
    if change == 'version':
        record[change] = True
    elif change == 'interactions':
        record[change][0]['interaction_id'] = '伪造的操作身份'
    elif change == 'extra':
        record['claimed_safe'] = True
    else:
        record[change] = '伪造的成员绑定'
    store.write_json('alpha', relative, record)
    before = store.load('alpha').to_dict()
    fresh = reopen(resumed)
    try:
        with pytest.raises(OSError):
            await fresh.teams.control({'action': 'resume', 'name': 'alpha'})
        assert store.load('alpha').to_dict() == before
        assert not fresh.provider.requests and not fresh.teams.runners
    finally:
        await fresh.aclose()


@async_test
async def test_forged_review_marker_without_actual_journal_intent_cannot_classify_unknown_exit(tmp_path):
    previous, old_runtime, member, assigned = await frozen_running_task(tmp_path)
    store = previous.teams.store
    await store.update_team('alpha', lambda team: setattr(team.members[member.member_id], 'state', 'needs_review'))
    team = store.load('alpha')
    store.write_json('alpha', f'members/{member.member_id}/recovery.json', {
        'version': 1, 'team_id': team.team_id, 'member_id': member.member_id, 'generation': member.generation,
        'session_id': member.session_ref, 'task_id': member.task_id, 'claim_id': member.claim_id,
        'interactions': [{'interaction_id': '不存在的意图', 'intent_seq': 1, 'missing_call_ids': ['fake']}],
    })
    fresh = reopen(previous)
    try:
        with pytest.raises(OSError):
            await fresh.teams.control({'action': 'resume', 'name': 'alpha'})
        assert not fresh.provider.requests and not fresh.teams.runners
        assert store.load('alpha').members[member.member_id].state == 'needs_review'
    finally:
        await fresh.aclose()


@async_test
async def test_valid_review_record_does_not_override_live_journal_owner(tmp_path):
    previous, old_runtime, member, assigned = await frozen_running_task(tmp_path)
    writer = await journal_writer(previous, member)
    await kill_writer(writer)
    resumed = reopen(previous)
    await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
    await resumed.aclose()
    holder_code = r'''
from pathlib import Path
import sys
from mewcode.sessions import Journal
root, identity, protocol, model, storage = sys.argv[1:]
journal = Journal.resume(Path(root), identity, protocol, model, storage_root=Path(storage), record_activity=False)
print('journal-owned', flush=True)
sys.stdin.read()
'''
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', holder_code,
        member.workspace_root, member.session_ref, previous.config.protocol, previous.config.model,
        str(previous.teams.store.member_path('alpha', member.member_id)),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    fresh = reopen(resumed)
    try:
        assert await asyncio.wait_for(process.stdout.readline(), 5) == b'journal-owned\n'
        # 成员锁确已空闲，失败证据必须来自仍存活的独立 Journal 所有者。
        lease = TeamLease(previous.teams.store.team_path('alpha') / f'locks/member-{member.member_id}.lock')
        lease.close()
        before = previous.teams.store.load('alpha').to_dict()
        with pytest.raises(OSError):
            await fresh.teams.control({'action': 'resume', 'name': 'alpha'})
        assert process.returncode is None and previous.teams.store.load('alpha').to_dict() == before
        assert not fresh.provider.requests and not fresh.teams.runners
    finally:
        await kill_writer(process)
        await fresh.aclose()


@async_test
async def test_new_goal_keeps_member_context_review_until_explicit_new_claim(tmp_path):
    previous, old_runtime, member, assigned = await frozen_running_task(tmp_path)
    process = await journal_writer(previous, member, effect=True)
    await kill_writer(process)
    resumed = reopen(previous)
    try:
        await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
        await resumed.teams.cancel_goal()
        goal = await resumed.teams.control({'action': 'goal', 'description': '明确新的目标'})
        waiting = resumed.teams.store.load('alpha').members[member.member_id]
        assert waiting.state == 'needs_review' and waiting.claim_id == assigned['claim_id']
        assert not resumed.provider.requests and not resumed.teams.runners
        task = await resumed.teams.task({'action': 'create', 'title': '新目标独立任务', 'code': False})
        reassigned = await resumed.teams.task({'action': 'reassign', 'task_id': task['task_id'], 'member_id': member.member_id})
        assert reassigned['goal_id'] == goal['goal_id'] and reassigned['claim_id'] != assigned['claim_id']
        assert resumed.teams.store.load('alpha').members[member.member_id].state == 'idle'
        old = await resumed.teams.board().get(resumed.team_scope.member_id, assigned['task_id'])
        assert old.state == 'blocked' and old.budget_used == 1 and old.claim_id == assigned['claim_id']
        assert (Path(member.workspace_root) / 'crash-side-effect.txt').exists()
    finally:
        await resumed.aclose()


@pytest.mark.parametrize(('state', 'crashed'), [('stopped', False), ('running', False), ('running', True)])
@async_test
async def test_live_or_unknown_generation_cannot_be_taken_over_after_lead_disconnect(tmp_path, state, crashed):
    previous, old_runtime, member, assigned = await frozen_running_task(tmp_path)
    process = await journal_writer(previous, member)
    if crashed:
        await kill_writer(process)
    await previous.teams.store.update_team('alpha', lambda team: setattr(team.members[member.member_id], 'state', state))
    before = previous.teams.store.load('alpha').to_dict()
    path = previous.teams.store.member_path('alpha', member.member_id) / '.mewcode/sessions' / (member.session_ref + '.jsonl')
    original_bytes = path.read_bytes()
    resumed = reopen(previous)
    try:
        assert (process.returncode is None) is not crashed
        with pytest.raises((OSError, ValueError, ToolError)):
            await resumed.teams.control({'action': 'resume', 'name': 'alpha'})
        assert (process.returncode is None) is not crashed
        assert previous.teams.store.load('alpha').to_dict() == before
        assert path.read_bytes() == original_bytes
        assert not resumed.provider.requests and not resumed.teams.runners
    finally:
        await kill_writer(process)
        await resumed.aclose()
