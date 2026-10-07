"""公共生命周期在真实会话上保持项目边界、关闭所有权与后台消费合同。"""

import asyncio
from dataclasses import replace

import pytest

from conftest import ScriptedProvider, async_test, collect
from mewcode.config import load_config
from test_app import config_file


def runtime(tmp_path, provider=None, **options):
    from mewcode.runtime import RuntimeResources
    provider = provider or ScriptedProvider([])
    return RuntimeResources(load_config(config_file(tmp_path)), root=tmp_path,
        provider_factory=lambda config: provider, memory_enabled=False,
        user_root=tmp_path / 'user', **options)


@async_test
async def test_explicit_root_owns_journal_tools_and_approval_without_chdir(tmp_path):
    from pathlib import Path
    from mewcode.types import ToolCall
    from test_agent_loop import answer, calls
    original = Path.cwd()
    approvals = []
    async def approve(request, cancel):
        approvals.append(request)
        return 'once'
    provider = ScriptedProvider([calls(ToolCall('write', 'write_file', '{"path":"output","content":"完成"}')),
                                 answer('完成')])
    resources = runtime(tmp_path, provider, approval_responder=approve)
    await resources.start()
    identity = resources.session.session_id
    try:
        assert resources.session.executor.context.root == tmp_path.resolve()
        assert resources.session.executor.mcp is resources.mcp
        assert resources.registry.find('permissions').name == 'permission'
        await collect(resources.session.ask('执行明确任务'))
        assert (tmp_path / 'output').read_text() == '完成'
        assert len(approvals) == 1
        assert Path.cwd() == original
    finally:
        report = await resources.aclose()
    assert report.complete and not report.pending and provider.closed
    from mewcode.sessions import Journal
    journal = Journal.resume(tmp_path, identity, 'openai', 'test-model')
    journal.close()


@async_test
async def test_invalid_definitions_fail_before_provider_or_archive(tmp_path):
    from mewcode.commands import CommandRegistry
    from mewcode.commands.builtins import builtin_definitions
    definitions = builtin_definitions()
    def broken(catalog):
        return CommandRegistry((*definitions, replace(definitions[0], name='hidden', aliases=('HELP',))))
    resources = runtime(tmp_path, registry_factory=broken)
    with pytest.raises(ValueError, match='冲突'):
        await resources.start()
    assert resources.provider is None and resources.session is None
    assert not (tmp_path / '.mewcode' / 'sessions').exists()
    assert (await resources.aclose()).complete


@async_test
async def test_mcp_is_bound_before_restore_and_hook_start(tmp_path, monkeypatch):
    from mewcode.session import ChatSession
    order = []
    class MCP:
        tools = ()
        def __init__(self, snapshot, registry, *, notify):
            self.registry = registry
        async def start(self, *, cancel_event):
            order.append('mcp')
        async def close(self):
            order.append('mcp.close')
    restore, start = ChatSession.prepare_restore, ChatSession.start
    async def prepare(self, **options):
        assert isinstance(self.executor.mcp, MCP)
        order.append('restore')
        await restore(self, **options)
    async def startup(self, **options):
        order.append('hook')
        await start(self, **options)
    monkeypatch.setattr(ChatSession, 'prepare_restore', prepare)
    monkeypatch.setattr(ChatSession, 'start', startup)
    resources = runtime(tmp_path, mcp_factory=MCP)
    await resources.start()
    assert order == ['mcp', 'restore', 'hook']
    assert (await resources.aclose()).complete


@async_test
async def test_start_failure_closes_provider_and_releases_archive(tmp_path, monkeypatch):
    from mewcode.session import ChatSession
    from mewcode.sessions import Journal
    identity = []
    async def fail(self, **options):
        identity.append(self.session_id)
        raise OSError('恢复失败')
    monkeypatch.setattr(ChatSession, 'prepare_restore', fail)
    resources = runtime(tmp_path)
    with pytest.raises(OSError, match='恢复失败'):
        await resources.start()
    assert resources.provider.closed and (await resources.aclose()).complete
    journal = Journal.resume(tmp_path, identity[0], 'openai', 'test-model')
    journal.close()


@async_test
async def test_repeated_cancel_preserves_close_order_and_single_owner(tmp_path):
    resources = runtime(tmp_path)
    await resources.start()
    entered, release = asyncio.Event(), asyncio.Event()
    order = []
    original = resources.session.aclose
    async def close_session():
        order.append('session')
        entered.set()
        await release.wait()
        await original()
    async def close_mcp():
        from mewcode.sessions import Journal
        journal = Journal.resume(tmp_path, resources.session.session_id, 'openai', 'test-model')
        journal.close()
        order.append('mcp')
    async def close_provider():
        order.append('provider')
    resources.session.aclose = close_session
    resources.mcp.close = close_mcp
    resources.provider.aclose = close_provider
    cancellation = asyncio.Event()
    closing = asyncio.create_task(resources.aclose(cancel_event=cancellation))
    await entered.wait()
    closing.cancel()
    await asyncio.sleep(0)
    closing.cancel()
    release.set()
    report = await closing
    assert report.complete and cancellation.is_set()
    assert (await resources.aclose()).complete
    assert order == ['session', 'mcp', 'provider']


@async_test
async def test_timeout_retains_close_task_until_real_session_shutdown(tmp_path):
    resources = runtime(tmp_path)
    await resources.start()
    release = asyncio.Event()
    original = resources.session.aclose
    async def close_session():
        await release.wait()
        await original()
    resources.session.aclose = close_session
    report = await resources.aclose(timeout=.01)
    assert not report.complete and 'session' in report.pending
    assert not resources.provider.closed
    release.set()
    assert (await resources.aclose()).complete and resources.provider.closed


@async_test
async def test_close_failure_still_releases_later_owners_and_is_not_success(tmp_path):
    resources = runtime(tmp_path)
    await resources.start()
    original = resources.session.aclose
    async def fail():
        await original()
        raise OSError('包含敏感信息的底层错误')
    resources.session.aclose = fail
    report = await resources.aclose()
    assert not report.complete and report.errors and resources.provider.closed
    assert '包含敏感信息' not in str(report)


@async_test
async def test_background_pump_resumes_original_budget_and_consumes_result_once(tmp_path):
    from mewcode.runtime import BackgroundPump
    from test_parent_tasks import RoutedProvider, delegate
    from test_skills_session import session, response
    provider = RoutedProvider([delegate(), response('中间'), response('最终')], [response('子结论')])
    chat = session(tmp_path, provider)
    try:
        await collect(chat.ask('目标'))
        record = next(iter(chat.tasks.records.values()))
        await chat.tasks.wait_terminal(record.task_id)
        pump = BackgroundPump(chat)
        parent = await asyncio.wait_for(pump.wait_ready(), 1)
        assert parent == record.parent_task_id
        budget = chat.tasks.parent(parent).budget
        events = await collect(chat.resume_parent(parent))
        assert chat.tasks.parent(parent).budget is budget and budget.used == 3
        assert any(event.kind == 'task_finished' and event.run_id == parent for event in events)
        assert not chat.tasks.inbox.peek(parent) and chat.next_parent() is None
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(pump.wait_ready(), .02)
        assert len(provider.main.requests) == 3
    finally:
        await chat.aclose()


def test_notifications_keep_origin_and_maintenance_usage():
    from mewcode.runtime import notification_event, task_event
    from mewcode.types import TokenUsage
    usage = TokenUsage(10, 2, True)
    event = notification_event({'kind': 'restore_usage', 'purpose': 'restore', 'usage': usage,
        'run_id': 'maintenance-1', 'parent_run_id': 'parent-1'})
    assert event.kind == 'usage' and event.purpose == 'restore' and event.usage is usage
    assert event.run_id == 'maintenance-1' and event.parent_run_id == 'parent-1'
    event = notification_event({'kind': 'team_update', 'text': '成员完成', 'status': 'completed'})
    assert event.kind == 'team_update' and event.purpose == 'team' and event.phase == 'completed'
    event = task_event({'task_id': 'child', 'parent_task_id': 'parent', 'display_mode': 'background', 'state': 'completed'})
    assert event.run_id == 'child' and event.parent_run_id == 'parent' and event.phase == 'completed'


@async_test
async def test_mcp_unconfirmed_cleanup_is_reported_as_incomplete(tmp_path):
    from mewcode.mcp.config import Diagnostic
    resources = runtime(tmp_path)
    await resources.start()
    async def unconfirmed():
        resources.mcp.diagnostics.append(Diagnostic('local', '关闭', '清理失败或超过期限；未确认资源已回收'))
    resources.mcp.close = unconfirmed
    report = await resources.aclose()
    assert not report.complete and any('mcp' in error for error in report.errors)
    assert resources.provider.closed


@async_test
async def test_constructor_failure_after_journal_creation_releases_partial_handles(tmp_path, monkeypatch):
    from mewcode.sessions import Journal
    from mewcode.hooks import runtime as hooks_runtime
    def broken(*args):
        raise OSError('Hook 初始化失败')
    monkeypatch.setattr(hooks_runtime, 'create_runtime', broken)
    resources = runtime(tmp_path)
    with pytest.raises(OSError, match='Hook 初始化失败'):
        await resources.start()
    assert resources.provider.closed
    archive = next((tmp_path / '.mewcode/sessions').glob('*.jsonl'))
    journal = Journal.resume(tmp_path, archive.stem, 'openai', 'test-model')
    journal.close()
