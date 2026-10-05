"""同步副作用形成全批次顺序；无规则保留四路只读并发。"""

import asyncio
from conftest import async_test, collect
from mewcode.hooks.runtime import create_runtime
from mewcode.tools.scheduler import ToolScheduler
from mewcode.types import ToolCall
from test_hook_lifecycle import configure
from test_hook_actions import script
from test_tool_scheduler import executor, run, call
from test_tool_execution import wait_file


@async_test
async def test_after_formatting_precedes_next_read_in_same_readonly_group(tmp_path):
    configure(tmp_path, [{'event': 'tool.after', 'if': {'all': [{'field': 'tool.call_id', 'match': 'exact', 'value': 'first'}]},
        'action': {'type': 'command', 'command': script(tmp_path, "import pathlib;pathlib.Path('a').write_text('formatted')")}}])
    (tmp_path / 'a').write_text('before')
    ex = executor(tmp_path)
    hooks = create_runtime(tmp_path, ex.permissions, lambda: 'execute')
    scheduler = ToolScheduler(ex, hooks=hooks)
    await collect(run(scheduler, [ToolCall('first', 'read_file', '{"path":"a"}'), ToolCall('second', 'read_file', '{"path":"a"}')]))
    assert [item.data['content'] for item in scheduler.results] == ['before', 'formatted']
    assert scheduler.max_parallel == 1
    await hooks.close()


@async_test
async def test_empty_hook_runtime_keeps_four_readonly_workers_and_after_cancelled_queue(tmp_path):
    ex = executor(tmp_path)
    hooks = create_runtime(tmp_path, ex.permissions, lambda: 'execute')
    after = []
    dispatch = hooks.dispatch
    async def record(event, **options):
        if event.event == 'tool.after':
            after.append(event.data)
        return await dispatch(event, **options)
    hooks.dispatch = record
    cancel = asyncio.Event()
    scheduler = ToolScheduler(ex, hooks=hooks)
    task = asyncio.create_task(collect(run(scheduler, [call(i) for i in range(6)], cancel)))
    try:
        await asyncio.gather(*(wait_file(tmp_path / f'started-{i}') for i in range(4)))
        assert not (tmp_path / 'started-4').exists()
        cancel.set()
        await asyncio.wait_for(task, 5)
        assert len(after) == 6 and len({item['tool']['call_id'] for item in after}) == 6
        assert all(item['tool']['result']['error']['details']['not_started'] for item in after if item['tool']['call_id'] in {'4', '5'})
    finally:
        cancel.set()
        await task
        await hooks.close()
