"""实际会话上的项目门禁、独立任务所有权及取消边界。"""

import asyncio
from types import SimpleNamespace

import pytest

from conftest import ScriptedProvider, async_test
from test_app import config_file
from test_agent_loop import answer
from mewcode.config import load_config
from mewcode.web.errors import WebError


def manager(tmp_path, provider=None, **options):
    from mewcode.web.manager import WebManager
    provider = provider or ScriptedProvider([])
    options.setdefault('memory_enabled', False)
    return WebManager(load_config(config_file(tmp_path)), root=tmp_path,
        provider_factory=lambda config: provider,
        user_root=tmp_path / 'user', **options)


async def settled(web):
    async with asyncio.timeout(3):
        while web.snapshot()['runtime']['busy']:
            await asyncio.sleep(.01)


async def activate(web, identity):
    await web.activate(identity, web.generation, web.state_version)
    await settled(web)


@async_test
async def test_browsing_creating_and_activating_do_not_submit_to_model(tmp_path):
    provider = ScriptedProvider([])
    web = manager(tmp_path, provider)
    try:
        first = (await web.browser.create('openai', 'test-model'))['session_id']
        second = (await web.browser.create('openai', 'test-model'))['session_id']
        await web.browser.list_sessions()
        await web.browser.history(first)
        assert web.resources is None and not provider.requests
        await activate(web, first)
        old = web.resources
        assert web.session.session_id == first and not provider.requests
        await web.browser.history(second)
        assert web.resources is old
        await activate(web, second)
        assert old.provider.closed and web.session.session_id == second
    finally:
        await web.aclose()


@async_test
async def test_request_cancellation_does_not_cancel_accepted_task_and_busy_is_not_queued(tmp_path):
    release, entered = asyncio.Event(), asyncio.Event()
    async def slow():
        entered.set()
        await release.wait()
        for event in answer('完成'):
            yield event
    provider = ScriptedProvider([slow])
    web = manager(tmp_path, provider)
    try:
        identity = (await web.browser.create('openai', 'test-model'))['session_id']
        await activate(web, identity)
        status, receipt = await web.submit(identity, '任务\n', web.generation, web.state_version)
        assert status == 202 and receipt['run_id']
        await entered.wait()
        with pytest.raises(WebError, match='占用'):
            await web.submit(identity, '第二项', web.generation, web.state_version)
        assert len(provider.requests) == 1
        release.set()
        await settled(web)
        assert any(m['text'] == '完成' for m in web.snapshot()['projection']['messages'])
    finally:
        release.set()
        await web.aclose()


@async_test
async def test_stop_keeps_slot_until_stream_cleanup_really_finishes(tmp_path):
    entered, release, cleaning = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def slow():
        entered.set()
        try:
            await asyncio.Future()
            yield
        finally:
            cleaning.set()
            await release.wait()
    web = manager(tmp_path, ScriptedProvider([slow]), stop_timeout=.03)
    try:
        identity = (await web.browser.create('openai', 'test-model'))['session_id']
        await activate(web, identity)
        await web.submit(identity, '任务', web.generation, web.state_version)
        await entered.wait()
        await web.stop(identity, web.generation)
        await asyncio.wait_for(cleaning.wait(), 1)
        await asyncio.sleep(.05)
        assert web.snapshot()['runtime']['phase'] == 'blocked'
        with pytest.raises(WebError):
            await web.submit(identity, '新任务', web.generation, web.state_version)
        release.set()
        await settled(web)
        assert web.snapshot()['runtime']['phase'] == 'idle'
    finally:
        release.set()
        await web.aclose()


@async_test
async def test_live_handle_and_maintenance_are_busy_even_after_outcome(tmp_path):
    web = manager(tmp_path)
    handle = asyncio.create_task(asyncio.sleep(30))
    try:
        identity = (await web.browser.create('openai', 'test-model'))['session_id']
        await activate(web, identity)
        web.session.tasks.records['late'] = SimpleNamespace(state='cancelled', handle=handle)
        assert '子任务清理' in web.activity_reasons()
        with pytest.raises(WebError):
            await web.submit(identity, '任务', web.generation, web.state_version)
    finally:
        handle.cancel()
        await asyncio.gather(handle, return_exceptions=True)
        web.session.tasks.records.clear()
        await web.aclose()


@async_test
async def test_command_contract_is_shared_and_multiline_slash_is_message(tmp_path):
    provider = ScriptedProvider([answer('原文收到')])
    web = manager(tmp_path, provider)
    try:
        identity = (await web.browser.create('openai', 'test-model'))['session_id']
        await activate(web, identity)
        await web.submit(identity, '/permissions', web.generation, web.state_version)
        await settled(web)
        await web.submit(identity, '/exit', web.generation, web.state_version)
        await settled(web)
        assert not provider.requests
        assert any('不适用' in m['text'] for m in web.projection.messages)
        await web.submit(identity, '/plan\n文字\n', web.generation, web.state_version)
        await settled(web)
        assert web.session.mode == 'execute'
        assert any(m.content == '/plan\n文字\n' for m in provider.requests[0][0])
    finally:
        await web.aclose()


@async_test
async def test_saved_and_live_messages_have_same_identity_after_refresh(tmp_path):
    web = manager(tmp_path, ScriptedProvider([answer('不会重复的回复')]))
    try:
        identity = (await web.browser.create('openai', 'test-model'))['session_id']
        await activate(web, identity)
        await web.submit(identity, '不会重复的输入', web.generation, web.state_version)
        await settled(web)
        history = await web.browser.history(identity)
        for text in ('不会重复的输入', '不会重复的回复'):
            saved = [item for item in history['items'] if item['text'] == text and item['role'] in {'user', 'assistant'}]
            live = [item for item in web.projection.messages if item['text'] == text]
            assert len(live) == 1 and len(saved) == 1 and saved[0]['id'] == live[0]['id']
    finally:
        await web.aclose()


@async_test
async def test_main_finished_keeps_project_busy_and_background_resumes_original_parent_once(tmp_path):
    from test_parent_tasks import RoutedProvider, delegate, wait_until
    from test_skills_session import response
    release = asyncio.Event()
    async def child():
        await release.wait()
        for event in response('子结果'):
            yield event
    provider = RoutedProvider([delegate(), response('中间'), response('最终')], [child])
    web = manager(tmp_path, provider, permission_mode='bypass')
    try:
        identity = (await web.browser.create('openai', 'test-model'))['session_id']
        await activate(web, identity)
        await web.submit(identity, '后台目标', web.generation, web.state_version)
        await wait_until(lambda: len(provider.main.requests) == 2)
        assert web.snapshot()['runtime']['busy']
        with pytest.raises(WebError):
            await web.submit(identity, '越过子任务', web.generation, web.state_version)
        release.set()
        await settled(web)
        assert len(provider.main.requests) == 3
        parent = next(iter(web.session.tasks.parents.values()))
        assert parent.budget.used == 3 and parent.finished
        assert web.session.next_parent() is None
    finally:
        release.set()
        await web.aclose()


@async_test
async def test_stop_can_cancel_activation_before_runtime_is_constructed(tmp_path):
    web = manager(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = web.browser.history
    async def slow_history(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)
    try:
        identity = (await web.browser.create('openai', 'test-model'))['session_id']
        web.browser.history = slow_history
        await web.activate(identity, web.generation, web.state_version)
        await entered.wait()
        assert web.snapshot()['runtime']['session_id'] == identity
        await web.stop(identity, web.generation)
        release.set()
        await settled(web)
        assert web.resources is None
    finally:
        release.set()
        await web.aclose()


@async_test
async def test_stop_cannot_reactivate_resources_already_closing_during_switch(tmp_path):
    release, entered = asyncio.Event(), asyncio.Event()
    class ClosingProvider(ScriptedProvider):
        async def aclose(self):
            entered.set()
            await release.wait()
            self.closed = True
    web = manager(tmp_path, ClosingProvider([]), stop_timeout=.02)
    try:
        first = (await web.browser.create('openai', 'test-model'))['session_id']
        second = (await web.browser.create('openai', 'test-model'))['session_id']
        await activate(web, first)
        await web.activate(second, web.generation, web.state_version)
        await entered.wait()
        await asyncio.sleep(.04)
        assert web.phase == 'blocked'
        await web.stop(first, web.generation)
        await asyncio.sleep(.04)
        assert web.snapshot()['runtime']['busy'] and web.phase == 'blocked'
        release.set()
        await settled(web)
        assert web.resources is None
    finally:
        release.set()
        await web.aclose()


@async_test
async def test_monitor_resume_is_owned_until_stop_cleanup_finishes(tmp_path):
    from mewcode.tasks.manager import TaskRecord, TaskOutcome
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def slow():
        entered.set()
        try:
            await asyncio.Future()
            yield
        finally:
            cleaning.set()
            await release.wait()
    web = manager(tmp_path, ScriptedProvider([slow]), stop_timeout=.02)
    try:
        identity = (await web.browser.create('openai', 'test-model'))['session_id']
        await activate(web, identity)
        tasks = web.session.tasks
        parent = tasks.new_parent('原父目标', generation=web.session.generation)
        record = TaskRecord('late-child', parent.task_id, 'agent', 'execute', web.session.generation,
                            state='completed', outcome=TaskOutcome('model_done', '子结果'))
        record.done.set()
        tasks.records[record.task_id] = record
        parent.children.add(record.task_id)
        tasks.inbox.put(record.report())
        await asyncio.wait_for(entered.wait(), 2)
        await web.stop(identity, web.generation)
        await asyncio.wait_for(cleaning.wait(), 1)
        await asyncio.sleep(.04)
        assert web.snapshot()['runtime']['busy'] and not web._stop_job.done()
        release.set()
        await settled(web)
        assert not web.session._main_running
    finally:
        release.set()
        await web.aclose()


@async_test
async def test_actual_memory_maintenance_holds_slot_after_work_finished(tmp_path):
    from test_memory_manager import response as memory_response
    entered, release = asyncio.Event(), asyncio.Event()
    async def maintenance():
        entered.set()
        await release.wait()
        for event in memory_response([]):
            yield event
    provider = ScriptedProvider([answer('完成工作'), maintenance])
    web = manager(tmp_path, provider, memory_enabled=True)
    try:
        identity = (await web.browser.create('openai', 'test-model'))['session_id']
        await activate(web, identity)
        await web.submit(identity, '确认这个临时项目使用 uv', web.generation, web.state_version)
        await asyncio.wait_for(entered.wait(), 2)
        assert any(run['reason'] == 'model_done' for run in web.projection.runs)
        assert '记忆维护' in web.activity_reasons() and web.snapshot()['runtime']['busy']
        with pytest.raises(WebError):
            await web.submit(identity, '插入任务', web.generation, web.state_version)
        release.set()
        await settled(web)
        assert len(provider.requests) == 2
    finally:
        release.set()
        await web.aclose()


@async_test
async def test_stop_during_switch_waits_activation_owner_without_constructing_target(tmp_path):
    release, entered = asyncio.Event(), asyncio.Event()
    providers = []
    class ClosingProvider(ScriptedProvider):
        async def aclose(self):
            entered.set()
            await release.wait()
            self.closed = True
    web = manager(tmp_path, ClosingProvider([]), stop_timeout=2)
    original = web.resources_factory
    def resources(*args, **kwargs):
        item = original(*args, **kwargs)
        providers.append(item)
        return item
    web.resources_factory = resources
    try:
        first = (await web.browser.create('openai', 'test-model'))['session_id']
        second = (await web.browser.create('openai', 'test-model'))['session_id']
        await activate(web, first)
        await web.activate(second, web.generation, web.state_version)
        await entered.wait()
        await web.stop(second, web.generation)
        await asyncio.sleep(.01)
        release.set()
        await settled(web)
        assert len(providers) == 1 and web.resources is None
        from mewcode.sessions import Journal
        journal = Journal.resume(tmp_path, second, 'openai', 'test-model')
        journal.close()
    finally:
        release.set()
        await web.aclose()
