"""跨执行段的父身份、收件箱和控制边界。"""

import asyncio
import pytest
from conftest import ScriptedProvider, async_test, collect
from mewcode.types import Message, ToolCall, ProviderError, TokenUsage
from test_skills_session import session, response


class RoutedProvider:
    config = ScriptedProvider.config

    def __init__(self, main, child):
        self.main, self.child = ScriptedProvider(main), ScriptedProvider(child)
        self.closed = False

    async def stream(self, messages, **options):
        route = self.child if '## 固定角色' in options['system_prompt'] else self.main
        async for event in route.stream(messages, **options):
            yield event

    async def aclose(self):
        self.closed = True


def delegate(background=True):
    return response(calls=(ToolCall('delegate', 'agent', '{"type":"defined","role":"general","prompt":"子目标","background":' + str(background).lower() + '}'),))


async def wait_until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(.005)
    await asyncio.wait_for(wait(), 2)


@async_test
async def test_results_wait_during_new_root_and_resume_fifo_without_second_tool_result(tmp_path):
    release = asyncio.Event()
    async def child():
        await release.wait()
        for event in response('有来源子结论'):
            yield event
    provider = RoutedProvider([delegate(), response('中间答复'), response('新用户答复'), response('旧父最终答复')], [child])
    chat = session(tmp_path, provider)
    try:
        await collect(chat.ask('原始目标'))
        record = next(iter(chat.tasks.records.values()))
        parent = chat.tasks.parent(record.parent_task_id)
        assert not parent.finished
        release.set()
        await chat.tasks.wait_terminal(record.task_id)
        await collect(chat.ask('优先处理新用户'))
        assert '有来源子结论' not in provider.main.requests[-1][0][-1].content
        assert chat.next_parent() == parent.task_id
        events = await collect(chat.resume_parent(parent.task_id))
        assert '有来源子结论' in provider.main.requests[-1][0][-1].content
        assert parent.finished and parent.budget.used == 3
        assert len([m for m in chat.history if m.tool_call_id == 'delegate']) == 1
        assert len([e for e in events if e.kind == 'task_finished']) == 1
        assert chat.next_parent() is None
    finally:
        await chat.aclose()


@async_test
async def test_resume_failure_consumes_sent_results_without_fake_success_or_retry(tmp_path):
    provider = RoutedProvider([delegate(), response('中间'), [ProviderError('失败')]], [response('子结论')])
    chat = session(tmp_path, provider)
    try:
        await collect(chat.ask('原目标'))
        record = next(iter(chat.tasks.records.values()))
        await chat.tasks.wait_terminal(record.task_id)
        before = tuple(chat.history)
        events = await collect(chat.resume_parent(record.parent_task_id))
        assert events[-1].reason == 'stream_error'
        assert tuple(chat.history) == before
        assert not chat.tasks.inbox.peek(record.parent_task_id)
        assert chat.next_parent() is None
    finally:
        await chat.aclose()


@async_test
async def test_cancel_before_resume_request_does_not_consume_inbox(tmp_path):
    provider = RoutedProvider([delegate(), response('中间')], [response('子结论')])
    chat = session(tmp_path, provider)
    try:
        await collect(chat.ask('原目标'))
        record = next(iter(chat.tasks.records.values()))
        await chat.tasks.wait_terminal(record.task_id)
        cancel = asyncio.Event()
        source = chat.resume_parent(record.parent_task_id, cancel_event=cancel)
        async for event in source:
            if event.kind == 'progress':
                assert chat.tasks.inbox.peek(record.parent_task_id)
                cancel.set()
        assert chat.tasks.inbox.peek(record.parent_task_id)
        assert len(provider.main.requests) == 2
        assert chat.next_parent() is None
    finally:
        await chat.aclose()


@pytest.mark.parametrize('control', ['plan', 'reset', 'close'])
@async_test
async def test_controls_stop_children_before_publishing_new_state(tmp_path, control):
    started, closed = asyncio.Event(), asyncio.Event()
    async def child():
        started.set()
        try:
            await asyncio.Event().wait()
            yield
        finally:
            closed.set()
    provider = RoutedProvider([delegate(), response('中间')], [child])
    chat = session(tmp_path, provider)
    try:
        await collect(chat.ask('原目标'))
        await asyncio.wait_for(started.wait(), 2)
        record = next(iter(chat.tasks.records.values()))
        parent = chat.tasks.parent(record.parent_task_id)
        if control == 'plan':
            await chat.set_mode('plan')
            assert chat.mode == 'plan'
            count = len(provider.main.requests)
            await chat.set_mode('execute')
            assert len(provider.main.requests) == count
        elif control == 'reset':
            generation = chat.generation
            await chat.reset_async()
            assert chat.generation == generation + 1 and chat.history == []
        else:
            await chat.aclose()
        assert closed.is_set() and record.state == 'cancelled'
        assert not parent.wake_allowed and chat.next_parent() is None
        assert chat.tasks.get(record.task_id) is record
        assert not provider.closed
    finally:
        await chat.aclose()


@async_test
async def test_active_parent_cancel_stops_foreground_child(tmp_path):
    async def child():
        await asyncio.Event().wait()
        yield
    provider = RoutedProvider([delegate(False)], [child])
    chat = session(tmp_path, provider)
    cancel = asyncio.Event()
    work = asyncio.create_task(collect(chat.ask('原目标', cancel_event=cancel)))
    try:
        await wait_until(lambda: bool(chat.tasks.foreground_task_id))
        cancel.set()
        events = await asyncio.wait_for(work, 3)
        assert events[-1].reason == 'cancelled'
        assert all(record.state == 'cancelled' for record in chat.tasks.records.values())
        assert chat.next_parent() is None
    finally:
        await chat.aclose()


@async_test
async def test_exhausted_root_has_local_child_result_without_new_request(tmp_path):
    provider = RoutedProvider([delegate(), response('中间')], [response('子结论')])
    chat = session(tmp_path, provider, max_iterations=2)
    try:
        await collect(chat.ask('原目标'))
        record = next(iter(chat.tasks.records.values()))
        await chat.tasks.wait_terminal(record.task_id)
        events = await collect(chat.resume_parent(record.parent_task_id))
        assert events[-1].reason == 'max_iterations'
        assert len(provider.main.requests) == 2
        assert '子结论' in await chat.tasks_text('show ' + record.task_id)
        assert chat.next_parent() is None
    finally:
        await chat.aclose()


@async_test
async def test_plan_cancels_execute_children_even_when_parent_budget_exhausted(tmp_path):
    closed = asyncio.Event()
    async def child():
        try:
            await asyncio.Event().wait()
            yield
        finally:
            closed.set()
    provider = RoutedProvider([delegate()], [child])
    chat = session(tmp_path, provider, max_iterations=1)
    try:
        events = await collect(chat.ask('原目标'))
        record = next(iter(chat.tasks.records.values()))
        parent = chat.tasks.parent(record.parent_task_id)
        assert events[-1].reason == 'max_iterations' and not parent.wake_allowed
        await chat.set_mode('plan')
        assert chat.mode == 'plan' and record.state == 'cancelled' and closed.is_set()
    finally:
        await chat.aclose()


@async_test
async def test_parent_report_aggregates_request_ledgers_once_and_preserves_final_reason(tmp_path):
    import json
    from mewcode.tasks.manager import TaskOutcome
    chat = session(tmp_path, ScriptedProvider([response('自然完成')]))
    try:
        await collect(chat.ask('目标'))
        parent = chat._task_context
        parent.budget.usage[:] = [TokenUsage(10, 2, True), TokenUsage(20, 3, True)]
        # 子请求独立归属，不把子汇总再加一次。
        parent.wake_allowed = True
        async def child(record):
            return TaskOutcome('model_done', '完成', usage=TokenUsage(99, 99, True),
                               request_usage=(TokenUsage(7, 4, True),))
        record = chat.tasks.submit(parent.task_id, child, type='defined', background=True)
        await chat.tasks.wait_terminal(record.task_id)
        report = json.loads(await chat.tasks_text('show ' + parent.task_id))
        assert report['finished'] and report['reason'] == 'model_done'
        assert report['own_usage']['input_tokens'] == 30
        assert report['children_usage']['input_tokens'] == 7
        assert report['total_usage']['input_tokens'] == 37
        assert report['total_usage']['output_tokens'] == 9
        assert len(report['request_usage']) == 2
        await chat.tasks.cancel_parent(parent.task_id)
        assert parent.reason == 'model_done'
    finally:
        await chat.aclose()


@async_test
async def test_same_parent_ready_results_merge_in_one_remaining_request(tmp_path):
    release = asyncio.Event()
    async def first():
        await release.wait()
        for event in response('第一个子结论'):
            yield event
    async def second():
        await release.wait()
        for event in response('第二个子结论'):
            yield event
    calls = tuple(ToolCall(str(i), 'agent', '{"type":"defined","role":"general","prompt":"子目标","background":true}') for i in range(2))
    provider = RoutedProvider([response(calls=calls), response('中间'), response('合并最终')], [first, second])
    chat = session(tmp_path, provider, max_iterations=3)
    try:
        await collect(chat.ask('原目标'))
        parent = chat._task_context
        assert len(parent.children) == 2 and parent.budget.remaining == 1
        release.set()
        await asyncio.gather(*(chat.tasks.wait_terminal(child) for child in parent.children))
        assert len(chat.tasks.inbox.peek(parent.task_id)) == 2
        events = await collect(chat.resume_parent(parent.task_id))
        assert len(provider.main.requests) == 3
        sent = provider.main.requests[-1][0][-1].content
        assert '第一个子结论' in sent and '第二个子结论' in sent
        assert parent.budget.used == 3 and parent.finished
        assert not chat.tasks.inbox.peek(parent.task_id)
        assert sum(event.kind == 'task_finished' for event in events) == 1
        assert len([message for message in chat.history if message.role == 'tool']) == 2
    finally:
        await chat.aclose()
