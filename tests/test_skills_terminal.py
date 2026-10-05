"""子事件归属和重置展示不干扰顶层任务。"""

import io

from mewcode.app import _Renderer
from mewcode.permissions.terminal import InputReader
from mewcode.terminal.controller import TerminalController
from mewcode.types import AgentEvent, TokenUsage


def test_child_finished_does_not_finish_parent_and_usage_once(tmp_path):
    out = io.StringIO()
    terminal = TerminalController(InputReader(io.StringIO()), out, secret='secret', root=tmp_path,
                                  on_interrupt=lambda: None, allow_enhanced=False)
    renderer = _Renderer(out, 'secret')
    terminal.show(renderer, AgentEvent('progress', run_id='parent', iteration=1))
    child_usage = AgentEvent('usage', run_id='child', parent_run_id='parent', skill_name='test',
                             iteration=2, usage=TokenUsage(input_tokens=10, output_tokens=3))
    terminal.show(renderer, AgentEvent('skill_event', run_id='parent', child_event=child_usage))
    terminal.show(renderer, AgentEvent('skill_event', run_id='parent', child_event=AgentEvent(
        'finished', run_id='child', parent_run_id='parent', skill_name='test', iteration=2,
        reason='model_done', usage=TokenUsage(input_tokens=10, output_tokens=3))))
    assert terminal.state.run_id == 'parent'
    assert not terminal.state._finished
    assert terminal.state._usage[2].input_tokens == 10
    assert 'test' in out.getvalue() and 'child' in out.getvalue()
    terminal.show(renderer, AgentEvent('finished', run_id='parent', iteration=2, reason='model_done',
                                       usage=TokenUsage(input_tokens=15, output_tokens=5)))
    assert terminal.state._total_usage.input_tokens == 15
    terminal.state.reset_task()
    assert terminal.state.run_id is None and not terminal.state._usage
