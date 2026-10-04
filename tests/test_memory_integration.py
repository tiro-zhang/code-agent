"""持久化边界与工作上下文更新的集成行为。"""
import asyncio

import pytest

from conftest import ScriptedProvider, async_test
from test_agent_loop import calls
from mewcode.agent import Agent
from mewcode.context.manager import ContextManager
from mewcode.context.spill import ResultCache
from mewcode.prompts import PromptState
from mewcode.tools import default_registry
from mewcode.tools.base import ToolContext
from mewcode.tools.executor import ToolExecutor
from mewcode.types import Message, ToolCall
from test_agent_loop import answer


def test_memory_refresh_forces_full_without_restarting_sequence(tmp_path):
    state = PromptState(tmp_path, custom_instructions='手写约束', memory='旧知识')
    first = state.begin_request()
    state.commit(first)
    state.update_memory('新知识')
    assert state.request_sequence == 1
    assert '新知识' in state.peek_request().content
    assert '手写约束' in state.peek_request().content
    assert '旧知识' in first.content
    state.commit(state.begin_request())
    state.update_memory('新知识')
    assert not state.full_pending


def test_loaded_memory_including_prompt_heading_stays_bounded(tmp_path):
    from mewcode.memory.store import Note, render_index
    from test_memory_store import note_value
    notes = [Note.from_dict(note_value(f'{i:032x}', summary='项目事实'), scope='project') for i in range(250)]
    memory = render_index(notes, combined=True)
    state = PromptState(tmp_path, memory=memory)
    injected = state.supplements[-1]
    assert len(injected.splitlines()) <= 200
    assert len((injected + '\n').encode('utf-8')) <= 25000


def test_checkpoint_failure_preserves_original_tool_results(tmp_path):
    from test_context_spill import batch
    manager = ContextManager(tmp_path, context_window=128000, max_output_tokens=8192, protocol='openai')
    history = batch([9000])
    original = tuple(history)
    def failed(candidate, state):
        raise OSError('磁盘满')
    manager.checkpoint = failed
    with pytest.raises(OSError):
        manager.spill(history)
    assert tuple(history) == original
    manager.cache.close()


def test_archive_cache_release_and_explicit_reopen(tmp_path):
    from test_context_spill import batch
    cache = ResultCache(tmp_path, session_id='20261004-123456-abcd', persistent=True)
    path = cache.save(batch([9000])[1], 'read_file')
    cache.close()
    assert (tmp_path / path).exists()
    restored = ResultCache(tmp_path, session_id=cache.session_id, persistent=True)
    restored.reopen([path])
    assert restored.restore(path).data == batch([9000])[1].tool_result.data
    restored.close()


@pytest.mark.parametrize('header', ['[]', 'null'])
def test_corrupt_cache_header_is_controlled_error(tmp_path, header):
    from test_context_spill import batch
    cache = ResultCache(tmp_path)
    path = cache.save(batch([9000])[1], 'read_file')
    file = tmp_path/path
    lines = file.read_text().splitlines()
    file.write_text(header + '\n' + '\n'.join(lines[1:]) + '\n')
    with pytest.raises(ValueError):
        cache.restore(path)
    cache.close()


@async_test
async def test_journal_failure_before_dispatch_prevents_side_effect(tmp_path):
    class BrokenJournal:
        def append(self, kind, payload, **kwargs):
            if kind == 'interaction_started':
                raise OSError('磁盘满')
    provider = ScriptedProvider([calls(ToolCall('w', 'write_file', '{"path":"created.txt","content":"data"}'))])
    executor = ToolExecutor(default_registry(), ToolContext(tmp_path))
    agent = Agent(provider, executor, journal=BrokenJournal())
    history = []
    events = [event async for event in agent.run('写文件', history=history, mode='execute')]
    assert events[-1].reason == 'context_blocked'
    assert not (tmp_path / 'created.txt').exists()
    assert history == []
    second = [e async for e in agent.run('继续', history=history, mode='execute')]
    assert second[-1].reason == 'context_blocked'
    assert len(provider.requests) == 1


@async_test
async def test_result_storage_failure_retains_effect_and_stops_next_write(tmp_path):
    from conftest import permission_bypass
    from mewcode.sessions import Journal
    journal = Journal.create(tmp_path, 'openai', 'test')
    original_append = journal.append
    def append(kind, payload, **options):
        if kind == 'tool_result':
            raise OSError('磁盘满')
        return original_append(kind, payload, **options)
    journal.append = append
    provider = ScriptedProvider([calls(
        ToolCall('one', 'write_file', '{"path":"one","content":"已执行"}'),
        ToolCall('two', 'write_file', '{"path":"two","content":"不要启动"}'))])
    executor = ToolExecutor(default_registry(), ToolContext(tmp_path), permissions=permission_bypass(tmp_path))
    history = []
    events = [e async for e in Agent(provider, executor, journal=journal).run('创建', history=history, mode='execute')]
    assert (tmp_path / 'one').read_text() == '已执行'
    assert not (tmp_path / 'two').exists()
    assert events[-1].reason == 'context_blocked'
    results = [m.tool_result for m in history if m.role == 'tool']
    assert results[0].ok and results[1].error['details']['not_started']
    journal.close()


@async_test
async def test_summary_checkpoint_failure_preserves_history_and_failure_counter(tmp_path):
    from test_context_summary import history_for_task, summary_text, response
    user, history = history_for_task()
    before = tuple(history)
    manager = ContextManager(tmp_path, context_window=128000, max_output_tokens=8192, protocol='openai')
    manager.failures = 2
    def failed(candidate, state):
        raise OSError('磁盘满')
    manager.checkpoint = failed
    result = await manager.compact(ScriptedProvider([response(summary_text())]), history, user, (), '系统',
                                   Message('context', '环境'), asyncio.Event(), manual=True)
    assert result.blocked and not result.success
    assert tuple(history) == before and manager.failures == 2 and manager.version == 0


@async_test
async def test_session_restore_has_history_and_do_requires_explicit_task(tmp_path):
    from mewcode.session import ChatSession
    from types import SimpleNamespace
    config = SimpleNamespace(context_window=128000, max_output_tokens=8192, protocol='openai', model='test')
    executor = ToolExecutor(default_registry(), ToolContext(tmp_path))
    first = ChatSession(ScriptedProvider([answer('目标、步骤、验证')]), executor=executor,
                        config=config, persistent=True, memory_enabled=False, user_root=tmp_path/'user')
    first.enter_plan()
    events = [e async for e in first.ask('读取方案')]
    assert events[-1].reason == 'model_done' and first.mode == 'plan'
    identity = first.session_id
    old = tuple(first.history)
    await first.aclose()
    provider = ScriptedProvider([answer('新计划')])
    second = ChatSession(provider, executor=executor, config=config, resume=identity,
                         memory_enabled=False, user_root=tmp_path/'user')
    assert second.mode == 'plan'
    assert tuple(second.history) == old
    await second.prepare_restore()
    assert provider.requests == []
    second.enter_execute()
    assert provider.requests == [] and tuple(second.history) == old
    assert second.mode == 'execute'
    _ = [event async for event in second.ask('明确的新任务')]
    assert len(provider.requests) == 1
    await second.aclose()


def test_list_sessions_never_opens_config_or_provider(tmp_path, monkeypatch):
    from io import StringIO
    from mewcode.app import run
    monkeypatch.chdir(tmp_path)
    output = StringIO()
    def forbidden(config):
        raise AssertionError('列表不能启动供应商')
    assert run('不存在的配置', list_sessions=True, provider_factory=forbidden, stdout=output) == 0
    assert '没有会话' in output.getvalue()


def test_cli_session_arguments_are_mutually_exclusive():
    from mewcode.cli import main
    with pytest.raises(SystemExit) as caught:
        main(['--config', 'x', '--resume', 'latest', '--list-sessions'])
    assert caught.value.code == 2


def restore_fixture(root, history, *, failures=0):
    """用真实存档提交历史，恢复测试不依赖进程内会话状态。"""
    from mewcode.sessions import Journal, encode_message
    journal = Journal.create(root, 'openai', 'test')
    journal.append('history_checkpoint', {'history': [encode_message(m) for m in history],
        'state': {'version': 0, 'failures': failures, 'circuit_open': failures >= 3,
                  'quotes': {}, 'summary_files': [], 'cache_paths': []}})
    identity = journal.id
    journal.close()
    return identity


def test_restore_startup_shows_work_message_count_without_reprinting_history(tmp_path, monkeypatch):
    from io import StringIO
    from mewcode.app import run
    from test_app import config_file
    monkeypatch.chdir(tmp_path)
    identity = restore_fixture(tmp_path, [Message('user', '私有旧任务'),
        Message('context', '应用提醒', context_kind='runtime'), Message('assistant', '私有旧回答')])
    provider = ScriptedProvider([])
    output, errors = StringIO(), StringIO()
    code = run(config_file(tmp_path), stdin=StringIO('/exit\n'), stdout=output, stderr=errors,
        provider_factory=lambda config: provider, resume=identity,
        memory_enabled=False, user_root=tmp_path/'user')
    assert code == 0 and errors.getvalue() == ''
    assert f'{identity} · 已恢复 · 2 条工作消息 · 等待新输入' in output.getvalue()
    assert '私有旧任务' not in output.getvalue() and '私有旧回答' not in output.getvalue()
    assert provider.requests == [] and provider.closed


def long_restore_history():
    return [message for i in range(16) for message in (
        Message('user', f'历史背景第{i}段'), Message('assistant', '历史背景资料。' * 260))]


@async_test
async def test_restore_summary_has_one_request_and_next_work_has_full_budget(tmp_path):
    from types import SimpleNamespace
    from mewcode.session import ChatSession
    from test_context_summary import response
    config = SimpleNamespace(context_window=45000, max_output_tokens=8192, protocol='openai', model='test')
    identity = restore_fixture(tmp_path, long_restore_history(), failures=2)
    provider = ScriptedProvider([response(), answer('新工作完成')])
    session = ChatSession(provider, executor=ToolExecutor(default_registry(), ToolContext(tmp_path)),
        config=config, max_iterations=1, resume=identity, memory_enabled=False, user_root=tmp_path/'user')
    try:
        await session.prepare_restore()
        assert len(provider.requests) == 1 and provider.requests[0][1]['tools'] == ()
        assert session.history[0].context_kind == 'summary'
        assert session.context.failures == 0 and session.context.version == 1
        assert session.prompt_state.request_sequence == 0
        assert session.restore_usage.output_tokens == 666
        assert session.context.manual_usage is None
        events = [e async for e in session.ask('新的完整预算任务')]
        assert events[-1].reason == 'model_done' and events[-1].iteration == 1
        assert len(provider.requests) == 2 and session.prompt_state.request_sequence == 1
    finally:
        await session.aclose()


@pytest.mark.parametrize('cancelled', [False, True])
@async_test
async def test_failed_restore_keeps_history_activity_and_persistent_failure_state(tmp_path, cancelled):
    from types import SimpleNamespace
    from mewcode.session import ChatSession
    from mewcode.sessions import Journal
    from mewcode.types import ProviderEvent
    config = SimpleNamespace(context_window=45000, max_output_tokens=8192, protocol='openai', model='test')
    history = long_restore_history()
    identity = restore_fixture(tmp_path, history, failures=2)
    cancel = asyncio.Event()
    async def waiting():
        yield ProviderEvent('text_delta', '<draft>尚未完成')
        cancel.set()
        await asyncio.sleep(60)
    provider = ScriptedProvider([waiting if cancelled else answer('非法摘要')])
    session = ChatSession(provider, executor=ToolExecutor(default_registry(), ToolContext(tmp_path)),
        config=config, resume=identity, memory_enabled=False, user_root=tmp_path/'user')
    old_activity = session.journal.projection.last_activity
    try:
        with pytest.raises(ValueError, match='恢复无法继续'):
            await session.prepare_restore(cancel_event=cancel)
        assert session.history == history and len(provider.requests) == 1
        assert provider.closed_streams == 1 and session.prompt_state.request_sequence == 0
    finally:
        await session.aclose()
    journal = Journal.resume(tmp_path, identity, 'openai', 'test', record_activity=False)
    try:
        assert journal.projection.last_activity == old_activity
        assert journal.projection.state['failures'] == (2 if cancelled else 3)
        assert journal.projection.state['circuit_open'] is (not cancelled)
        assert journal.projection.history == history
    finally:
        journal.close()


@async_test
async def test_uncompressible_restore_does_not_make_model_request(tmp_path):
    from types import SimpleNamespace
    from mewcode.session import ChatSession
    config = SimpleNamespace(context_window=45000, max_output_tokens=8192, protocol='openai', model='test')
    identity = restore_fixture(tmp_path, [Message('user', '当前输入' * 9000)])
    provider = ScriptedProvider([])
    session = ChatSession(provider, executor=ToolExecutor(default_registry(), ToolContext(tmp_path)),
        config=config, resume=identity, memory_enabled=False, user_root=tmp_path/'user')
    try:
        with pytest.raises(ValueError, match='没有可摘要'):
            await session.prepare_restore()
        assert provider.requests == []
    finally:
        await session.aclose()


@pytest.mark.parametrize('extra_seconds', [0, 1])
def test_restore_gap_boundary_is_strict_and_uses_trusted_context(tmp_path, monkeypatch, extra_seconds):
    import json
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    import mewcode.session as module
    identity = restore_fixture(tmp_path, [Message('user', '旧任务'), Message('assistant', '旧答复')])
    path = tmp_path/'.mewcode/sessions'/f'{identity}.jsonl'
    records = [json.loads(line) for line in path.read_text().splitlines()]
    now = datetime.now(timezone.utc)
    last = now - timedelta(hours=24, seconds=extra_seconds)
    for record in records:
        record['timestamp'] = last.isoformat()
        if record['kind'] == 'session_created':
            record['payload']['created_at'] = last.isoformat()
    path.write_text('\n'.join(json.dumps(r) for r in records) + '\n')
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(module, 'datetime', Clock)
    config = SimpleNamespace(context_window=128000, max_output_tokens=8192, protocol='openai', model='test')
    session = module.ChatSession(ScriptedProvider([]), executor=ToolExecutor(default_registry(), ToolContext(tmp_path)),
        config=config, resume=identity, memory_enabled=False, user_root=tmp_path/'user')
    try:
        reminders = [m for m in session.history if m.context_kind == 'resume']
        assert len(reminders) == extra_seconds
        if reminders:
            assert reminders[0] is session.history[-1] and reminders[0].role == 'context'
            assert '重新读取' in reminders[0].content
    finally:
        session.close()


@async_test
async def test_completed_tool_task_restores_final_answer(tmp_path):
    from conftest import permission_bypass
    from mewcode.sessions import Journal
    (tmp_path / 'README.md').write_text('真实项目')
    journal = Journal.create(tmp_path, 'openai', 'test')
    identity = journal.id
    provider = ScriptedProvider([calls(ToolCall('read', 'read_file', '{"path":"README.md"}')), answer('完整最终答复')])
    executor = ToolExecutor(default_registry(), ToolContext(tmp_path), permissions=permission_bypass(tmp_path))
    history = []
    events = [e async for e in Agent(provider, executor, journal=journal).run('读项目', history=history, mode='execute')]
    assert events[-1].reason == 'model_done'
    journal.close()
    restored = Journal.resume(tmp_path, identity, 'openai', 'test')
    assert restored.projection.history[-1].content == '完整最终答复'
    assert tuple(restored.projection.history) == tuple(history)
    assert not restored.warnings
    restored.close()


@async_test
async def test_restore_rebudgets_after_spill_before_trying_summary(tmp_path):
    from types import SimpleNamespace
    from mewcode.session import ChatSession
    from test_context_spill import batch
    config = SimpleNamespace(context_window=35000, max_output_tokens=8192, protocol='openai', model='test')
    provider = ScriptedProvider([])
    session = ChatSession(provider, executor=ToolExecutor(default_registry(), ToolContext(tmp_path)),
                          config=config, user_root=tmp_path/'user')
    session.history[:] = [Message('user', '最近任务'), *batch([20000])]
    session.resumed = True
    await session.prepare_restore()
    assert provider.requests == []
    assert any(m.cache_path for m in session.history)
    assert session.context.fits(session.context.last_estimate)
    session.close()


@async_test
async def test_real_journal_fsync_failure_returns_blocked_after_actual_write(tmp_path, monkeypatch):
    from conftest import permission_bypass
    from mewcode.sessions import Journal
    import os
    journal = Journal.create(tmp_path, 'openai', 'test')
    append = journal.append
    def record(kind, payload, **options):
        if kind == 'tool_result':
            with monkeypatch.context() as patch:
                def failed(fd):
                    raise OSError('磁盘满')
                patch.setattr(os, 'fsync', failed)
                return append(kind, payload, **options)
        return append(kind, payload, **options)
    journal.append = record
    provider = ScriptedProvider([calls(
        ToolCall('one', 'write_file', '{"path":"one","content":"实际修改"}'),
        ToolCall('two', 'write_file', '{"path":"two","content":"不可启动"}'))])
    executor = ToolExecutor(default_registry(), ToolContext(tmp_path), permissions=permission_bypass(tmp_path))
    history = []
    async def run():
        return [e async for e in Agent(provider, executor, journal=journal).run('创建', history=history, mode='execute')]
    events = await asyncio.wait_for(run(), 3)
    assert events[-1].reason == 'context_blocked'
    assert (tmp_path/'one').read_text() == '实际修改' and not (tmp_path/'two').exists()
    assert [m for m in history if m.role == 'tool'][0].tool_result.ok
    journal.close()


@async_test
async def test_background_memory_is_in_next_request_with_separate_budget(tmp_path):
    import json
    from types import SimpleNamespace
    from mewcode.session import ChatSession
    from mewcode.memory.manager import MEMORY_PROMPT
    from mewcode.types import ProviderEvent, TokenUsage
    class Provider(ScriptedProvider):
        def __init__(self):
            super().__init__([answer('已确认'), answer('完成下一问')])
            self.memory_requests = []
        async def stream(self, messages, **options):
            if options['system_prompt'] == MEMORY_PROMPT:
                self.memory_requests.append((tuple(messages), options))
                task = json.loads(messages[0].content)['task']
                operation = {'action':'add', 'scope':'project', 'category':'project_knowledge',
                             'summary':'项目安装使用 uv sync', 'content':'安装使用 uv sync。',
                             'sources':[{'session_id':task['session_id'], 'task_id':task['task_id'],
                                         'message_id':task['user']['id']}]}
                yield ProviderEvent('completed', message=Message('assistant', json.dumps({'operations':[operation]}, ensure_ascii=False)))
                yield ProviderEvent('usage', usage=TokenUsage(input_tokens=100, output_tokens=25, complete=True))
            else:
                async for item in super().stream(messages, **options):
                    yield item
    provider = Provider()
    config = SimpleNamespace(context_window=128000, max_output_tokens=8192, protocol='openai', model='test')
    session = ChatSession(provider, executor=ToolExecutor(default_registry(), ToolContext(tmp_path)), max_iterations=1,
                          config=config, persistent=True, user_root=tmp_path/'user')
    first = [e async for e in session.ask('项目安装使用 uv sync，请记住。')]
    old_context = next(m for m in session.history if m.role == 'context')
    # 笔记名称由应用生成；通过索引中的实际事实等待外部可见提交。
    async def wait_index():
        while '项目安装使用 uv sync' not in session.memory.refresh():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(wait_index(), 2)
    assert session.prompt_state.request_sequence == 1
    second = [e async for e in session.ask('下一问')]
    assert second[-1].reason == 'model_done' and second[-1].iteration == 1
    assert session.prompt_state.request_sequence == 2
    assert '项目安装使用 uv sync' in provider.requests[1][0][-1].content
    assert '项目安装使用 uv sync' not in old_context.content
    assert first[-1].usage.input_tokens is None
    status = session.context_status()
    assert '记忆维护实际用量' in status and '100' in status
    await session.aclose()
    assert all(options['tool_choice'] == 'none' and not options['tools'] for _, options in provider.memory_requests)
