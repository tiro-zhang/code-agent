"""角色快照、真实错误和父最终记忆归属。"""

import asyncio
import json
import pytest
from dataclasses import replace
from conftest import ScriptedProvider, async_test, collect
from mewcode.types import ToolCall, Message, TokenUsage
from test_agent_definitions import entry
from test_skills_session import session, response
from test_parent_tasks import RoutedProvider, delegate
from test_subagent_runtime import submit
from test_skills_persistence import CONFIG
from test_context_partition import history_for_task
from test_context_summary import response as summary_response


@async_test
async def test_queued_role_is_frozen_and_catalog_refresh_does_not_change_schema(tmp_path):
    path = entry(tmp_path / '.mewcode/agents/r.md', body='注册旧正文')
    provider = ScriptedProvider([response('子完成')])
    chat = session(tmp_path, provider)
    manager = chat.tasks
    manager.max_running = 1
    parent = manager.new_parent('父目标')
    gate = asyncio.Event()
    from mewcode.tasks.manager import TaskOutcome
    async def occupied(record):
        await gate.wait()
        return TaskOutcome('model_done', '占位完成')
    manager.submit(parent.task_id, occupied, type='defined', background=True)
    from mewcode.agents.runtime import freeze_child, ChildRuntime
    role = chat.roles.get('sample')
    snapshot = freeze_child(chat, {'type':'defined', 'prompt':'子目标'}, role=role)
    record = manager.submit(parent.task_id, lambda record: ChildRuntime(chat, record).run(),
                            type='defined', background=True, snapshot=snapshot)
    tools = chat.executor.registry.definitions()
    try:
        entry(path, body='热更新新正文')
        chat.refresh_skills()
        assert chat.roles.get('sample').body == '热更新新正文'
        assert chat.executor.registry.definitions() == tools
        assert record.state == 'queued'
        gate.set()
        await manager.wait_terminal(record.task_id)
        assert '注册旧正文' in provider.requests[0][1]['system_prompt']
        assert '热更新新正文' not in provider.requests[0][1]['system_prompt']
    finally:
        await chat.aclose()


@async_test
async def test_fixed_role_survives_child_compaction_without_parent_changes(tmp_path):
    config = CONFIG
    provider = ScriptedProvider([summary_response(), response('子最终')])
    chat = session(tmp_path, provider, config=config)
    chat.history[:] = [Message('user', '父保持标记')]
    from mewcode.agents.runtime import freeze_child, ChildRuntime
    snapshot = freeze_child(chat, {'type':'defined', 'prompt':'子目标'}, role=chat.roles.get('general'))
    manager = chat.tasks
    parent = manager.new_parent('父目标')
    async def run(record):
        runtime = ChildRuntime(chat, record)
        _, history = history_for_task()
        runtime.history[:] = history
        runtime.agent.context.estimator.observe(runtime.agent.context.estimator.snapshot(history, snapshot.system_prompt, snapshot.declared_tools),
            TokenUsage(total_input_tokens=120000))
        return await runtime.run()
    record = manager.submit(parent.task_id, run, type='defined', snapshot=snapshot)
    try:
        await manager.wait_terminal(record.task_id)
        assert record.state == 'completed', record.outcome
        assert provider.requests[0][1]['tool_choice'] == 'none'
        assert '## 固定角色' in provider.requests[-1][1]['system_prompt']
        assert chat.history == [Message('user', '父保持标记')]
        assert parent.budget.used == 0 and len(record.outcome.request_usage) == 2
    finally:
        await chat.aclose()


@async_test
async def test_memory_runs_only_after_root_final_and_uses_original_user(tmp_path):
    release = asyncio.Event()
    async def child():
        await release.wait()
        for event in response('子结论'):
            yield event
    provider = RoutedProvider([delegate(), response('中间'), response('最终')], [child])
    chat = session(tmp_path, provider)
    class Memory:
        def __init__(self):
            self.items = []
        def refresh(self):
            return ''
        def enqueue(self, item):
            self.items.append(item)
            return True
        async def aclose(self, **options):
            pass
    memory = chat.memory = Memory()
    try:
        await collect(chat.ask('原始用户目标'))
        record = next(iter(chat.tasks.records.values()))
        assert memory.items == []
        release.set()
        await chat.tasks.wait_terminal(record.task_id)
        await collect(chat.resume_parent(record.parent_task_id))
        await collect(chat.resume_parent(record.parent_task_id))
        assert len(memory.items) == 1
        assert memory.items[0]['task_id'] == record.parent_task_id
        assert memory.items[0]['user_message'].content == '原始用户目标'
        assert memory.items[0]['final_message'].content == '最终'
        assert sum(m.role == 'user' for m in chat.history) == 1
    finally:
        await chat.aclose()


@async_test
async def test_persistent_background_resume_commits_context_and_restores_without_jobs(tmp_path):
    release = asyncio.Event()
    async def child():
        await release.wait()
        for event in response('子结论'):
            yield event
    provider = RoutedProvider([delegate(), response('中间'), response('最终')], [child])
    chat = session(tmp_path, provider, config=CONFIG, persistent=True)
    identity = chat.session_id
    try:
        await collect(chat.ask('原始用户目标'))
        record = next(iter(chat.tasks.records.values()))
        release.set()
        await chat.tasks.wait_terminal(record.task_id)
        result = await collect(chat.resume_parent(record.parent_task_id))
        assert result[-1].reason == 'model_done'
        assert chat.history[-1].content == '最终'
    finally:
        await chat.aclose()
    restored = session(tmp_path, ScriptedProvider([]), config=CONFIG, resume=identity)
    try:
        assert restored.history[-1].content == '最终'
        assert any(message.context_kind == 'task_resume' for message in restored.history)
        assert restored.tasks.records == {} and restored.tasks.parents == {}
        assert restored.next_parent() is None
    finally:
        await restored.aclose()


@pytest.mark.parametrize('stop', ['cancel', 'budget'])
@async_test
async def test_pending_parent_terminal_is_journaled_once_at_control_boundary(tmp_path, stop):
    release = asyncio.Event()
    async def child():
        await release.wait()
        for event in response('子结论'):
            yield event
    chat = session(tmp_path, RoutedProvider([delegate(), response('中间')], [child]),
                   config=CONFIG, persistent=True, max_iterations=2)
    try:
        await collect(chat.ask('原目标'))
        record = next(iter(chat.tasks.records.values()))
        parent = chat.tasks.parent(record.parent_task_id)
        assert not parent.finished
        if stop == 'cancel':
            await chat.tasks_text('cancel-parent ' + parent.task_id)
            await chat.tasks_text('cancel-parent ' + parent.task_id)
        else:
            release.set()
            await chat.tasks.wait_terminal(record.task_id)
            await collect(chat.resume_parent(parent.task_id))
            await collect(chat.resume_parent(parent.task_id))
        await chat.aclose()
        rows = [json.loads(line) for line in chat.journal.path.read_text().splitlines()]
        final = [row for row in rows if row['kind'] == 'task_finished' and row['payload'].get('task_id') == parent.task_id]
        assert len(final) == 1
        assert parent.finished and parent.reason == ('cancelled' if stop == 'cancel' else 'max_iterations')
    finally:
        await chat.aclose()


@async_test
async def test_direct_isolated_skill_has_one_root_start_and_finish(tmp_path):
    from test_skills_catalog import entry as skill_entry
    skill_entry(tmp_path / '.mewcode/skills/iso.md', 'iso', extra='', body='独立职责')
    path = tmp_path / '.mewcode/skills/iso.md'
    path.write_text(path.read_text().replace('mode: shared', 'mode: isolated'))
    chat = session(tmp_path, ScriptedProvider([response('独立结论')]), config=CONFIG, persistent=True)
    class Memory:
        def refresh(self):
            return ''
        def enqueue(self, task):
            parent = chat._task_context
            assert parent.finished and parent.reason == 'model_done'
            assert task['question'] == parent.question
            return True
        async def aclose(self, **options):
            pass
    chat.memory = Memory()
    try:
        events = await collect(chat.run_skill('iso'))
        parent_id = chat._task_context.task_id
        rows = [json.loads(line) for line in chat.journal.path.read_text().splitlines()]
        for kind in ['task_started', 'task_finished']:
            assert sum(row['kind'] == kind and row['payload'].get('task_id') == parent_id for row in rows) == 1
        assert sum(event.kind == 'task_finished' for event in events) == 1
    finally:
        await chat.aclose()
