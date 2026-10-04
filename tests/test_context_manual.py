"""手动压缩与计划、模式和最近任务记录相互独立。"""
import asyncio
from conftest import async_test, collect
from mewcode.terminal.commands import parse_command, command_completions, help_text
from test_plan_mode import session
from test_agent_loop import answer
from test_context_summary import response
from test_context_partition import history_for_task


def test_compact_command_help_completion_and_escape():
    assert parse_command('/compact').name == 'compact'
    assert parse_command('/compact extra').text == 'extra'
    assert parse_command('//compact').text == '/compact'
    assert parse_command('/compact\n正文').kind == 'message'
    assert command_completions('/comp') == ['/compact']
    assert '/compact' in help_text(enhanced=True)


@async_test
async def test_manual_recovers_circuit_preserves_plan_and_request_period(tmp_path):
    chat, provider = session(tmp_path, [answer('完整计划'), response(), answer('执行完毕')])
    chat.enter_plan()
    await collect(chat.ask('  当前目标\n保留空白！  '))
    task = next(m for m in chat.history if m.role == 'user')
    _, earlier = history_for_task()
    chat.history[:0] = earlier
    chat.context.failures = 3
    sequence = chat.prompt_state.request_sequence
    events = await collect(chat.compact())
    assert events[-1].phase == 'success' and chat.context.failures == 0
    assert chat.mode == 'plan'
    assert task in chat.history and chat.prompt_state.request_sequence == sequence
    assert len(provider.requests) == 2
    assert not any(e.kind in {'text_delta','thinking_delta','finished'} for e in events)
    chat.enter_execute()
    assert len(provider.requests) == 2
    await collect(chat.ask("按计划执行"))
    assert len(provider.requests) == 3
    assert any(m.content == "完整计划" for m in provider.requests[-1][0])


@async_test
async def test_manual_empty_and_failed_probe_do_not_reset_circuit(tmp_path):
    chat, provider = session(tmp_path, [response('坏摘要')])
    chat.context.failures = 3
    await collect(chat.compact())
    assert not provider.requests and chat.context.failures == 3
    _, chat.history = history_for_task()
    await collect(chat.compact())
    assert len(provider.requests) == 1 and chat.context.failures == 4


def test_plain_compact_preserves_recent_task_status_and_noop_usage(tmp_path):
    from conftest import ScriptedProvider
    from test_app import invoke
    provider = ScriptedProvider([answer('回答')])
    _, shown = invoke(tmp_path, provider, '问题\n/compact\n/status\n/exit\n')
    assert len(provider.requests) == 1
    assert '没有可摘要' in shown and '最近任务>' in shown and 'context_blocked' not in shown
    assert 'model_done' in shown and '上下文> 输入估算' in shown


def test_enhanced_manual_events_keep_recent_work_record():
    from io import StringIO
    from mewcode.app import _Renderer
    from mewcode.terminal.controller import TerminalController
    from mewcode.permissions.terminal import InputReader
    from mewcode.types import AgentEvent, TokenUsage
    terminal = TerminalController(InputReader(StringIO()), StringIO(), secret='',root='.',on_interrupt=lambda:None)
    terminal.state.update(AgentEvent('progress',run_id='work',iteration=1))
    terminal.state.update(AgentEvent('finished',run_id='work',iteration=1,reason='model_done',usage=TokenUsage(10,20)))
    renderer = _Renderer(terminal.stream,'')
    terminal.show(renderer, AgentEvent('usage',run_id='manual',purpose='summary',usage=TokenUsage(30,40)),maintenance=True)
    assert terminal.state.run_id == 'work' and terminal.state._total_usage == TokenUsage(10,20)
    assert '摘要 Token' in terminal.output.getvalue()


@async_test
async def test_enhanced_summary_phase_supports_cancel_and_ignores_typeahead():
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from mewcode.terminal.input import EnhancedTerminal
    cancel = asyncio.Event()
    with create_pipe_input() as pipe:
        terminal = EnhancedTerminal(pipe,DummyOutput(),on_interrupt=cancel.set)
        await terminal.start()
        try:
            terminal.set_phase('summary')
            assert '压缩' in terminal._status_text()
            pipe.send_text('不应成为下一任务\x03')
            await asyncio.wait_for(cancel.wait(),1)
            assert not terminal.chat.text
        finally:
            await terminal.close()
