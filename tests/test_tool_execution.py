import asyncio
from conftest import async_test, permission_bypass
"""通过真实进程验证 shell、超时、取消和搜索，不只检查异常名称。"""
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import pytest

class BlockingTool:
    name = 'block'
    description = '用于验证终止'
    input_schema = {'type': 'object'}

    def execute(self, arguments, context):
        time.sleep(20)

class FailingTool(BlockingTool):
    name = 'fail'

    def execute(self, arguments, context):
        raise RuntimeError('api-secret must never appear')

@pytest.fixture
def runner(tmp_path):
    from mewcode.tools import default_registry
    from mewcode.tools.base import ToolContext
    from mewcode.tools.executor import ToolExecutor
    return ToolExecutor(default_registry(), ToolContext(tmp_path), permissions=permission_bypass(tmp_path))

@async_test
async def test_shell_pipeline_redirection_exit_and_cwd(runner, tmp_path):
    result = await runner.execute('execute_command', json.dumps({'command': 'printf cat | tr a o > out; cat out; printf err >&2; exit 7'}))
    assert not result.ok and result.error['code'] == 'command_failed'
    assert result.data == {'exit_code': 7, 'stdout': 'cot', 'stderr': 'err'}
    assert (tmp_path / 'out').read_text() == 'cot'
    await runner.execute('execute_command', '{"command":"cd /; export MEW_TMP=changed"}')
    result = await runner.execute('execute_command', '{"command":"pwd; printenv MEW_TMP || printf unset"}')
    assert result.data['stdout'] == str(tmp_path.resolve()) + '\nunset'

@async_test
async def test_large_command_output_is_bounded_and_does_not_deadlock(runner):
    command = 'yes x | head -c 200000; yes e | head -c 200000 >&2'
    result = await runner.execute('execute_command', json.dumps({'command': command}))
    assert result.ok and result.truncated
    assert len(result.data['stdout'].encode()) <= 32768
    assert len(result.data['stderr'].encode()) <= 32768

@async_test
async def test_timeout_kills_descendants_and_keeps_partial_output(runner, tmp_path):
    command = 'printf started; (sleep 3; printf leaked > should-not-exist) & wait'
    result = await runner.execute('execute_command', json.dumps({'command': command, 'timeout_seconds': 1}))
    assert result.error['code'] == 'timeout'
    assert result.data['stdout'] == 'started'
    assert result.error['details']['side_effects_may_have_occurred']
    await asyncio.sleep(2.2)
    assert not (tmp_path / 'should-not-exist').exists()

@async_test
async def test_non_command_tool_timeout_and_exception_are_contained(tmp_path):
    from mewcode.tools.base import ToolContext
    from mewcode.tools.executor import ToolExecutor
    from mewcode.tools.registry import ToolRegistry
    runner = ToolExecutor(ToolRegistry([BlockingTool(), FailingTool()]), ToolContext(tmp_path), timeout=1,
                          permissions=permission_bypass(tmp_path))
    assert (await runner.execute('block', '{}')).error['code'] == 'timeout'
    result = await runner.execute('fail', '{}')
    assert result.error['code'] == 'execution_error'
    assert 'api-secret' not in result.to_json()

@async_test
async def test_cancel_returns_result_and_stops_process(runner, tmp_path):
    cancel = asyncio.Event()
    task = asyncio.create_task(runner.execute("execute_command", '{"command":"touch started; sleep 3; touch cancelled-leak"}', cancel_event=cancel))
    await wait_file(tmp_path / "started")
    cancel.set()
    result = await task
    assert result.error["code"] == "cancelled"
    await asyncio.sleep(3.1)
    assert not (tmp_path / "cancelled-leak").exists()

@async_test
async def test_glob_and_search_honor_ignores_order_and_result_bounds(runner, tmp_path):
    (tmp_path / '.gitignore').write_text('ignored.py\n')
    (tmp_path / 'b.py').write_text('def 猫():\n    pass\n')
    (tmp_path / 'a.py').write_text('def dog():\n    pass\n')
    (tmp_path / 'ignored.py').write_text('def ignored(): pass')
    (tmp_path / '.hidden.py').write_text('def hidden(): pass')
    (tmp_path / 'binary.py').write_bytes(b'def\x00abc')
    result = await runner.execute('glob_files', '{"pattern":"**/*.py","max_results":2}')
    assert result.data['paths'] == ['a.py', 'b.py'] and result.truncated
    result = await runner.execute('search_code', '{"pattern":"^def","glob":"*.py"}')
    assert [(m['path'], m['line']) for m in result.data['matches']] == [('a.py', 1), ('b.py', 1)]
    assert '猫' in result.data['matches'][1]['text']
    assert (await runner.execute('search_code', '{"pattern":"not_found"}')).data['matches'] == []
    assert (await runner.execute('search_code', '{"pattern":"["}')).error['code'] == 'invalid_pattern'
    assert (await runner.execute('search_code', '{"pattern":"-x"}')).ok
    assert (await runner.execute('glob_files', '{"pattern":"*.no"}')).data['paths'] == []

@async_test
async def test_search_skips_outside_links_and_reports_missing_rg(runner, tmp_path, monkeypatch):
    outside = tmp_path.with_name(tmp_path.name + '-outside')
    outside.mkdir()
    (outside / 'external.py').write_text('secret-marker')
    (tmp_path / 'escape').symlink_to(outside, target_is_directory=True)
    (tmp_path / 'linked.py').symlink_to(outside / 'external.py')
    assert (await runner.execute('search_code', '{"pattern":"secret-marker"}')).data['matches'] == []
    assert (await runner.execute('glob_files', '{"pattern":"*.py"}')).data['paths'] == []
    monkeypatch.setenv('PATH', str(tmp_path))
    assert (await runner.execute('search_code', '{"pattern":"x"}')).error['code'] == 'dependency_missing'

@async_test
async def test_executor_rejects_invalid_input_before_process_side_effect(runner, tmp_path):
    result = await runner.execute('execute_command', '{"command":"touch bad","timeout_seconds":0}')
    assert result.error['code'] == 'invalid_arguments' and (not (tmp_path / 'bad').exists())
    assert (await runner.execute('unknown', '{}')).error['code'] == 'unknown_tool'

@async_test
async def test_search_truncates_large_lines_and_sorts_relative_paths(runner, tmp_path):
    (tmp_path / 'a').mkdir()
    for name in ['a/z.py', 'a+.py']:
        (tmp_path / name).write_text('marker\n')
    glob = await runner.execute('glob_files', '{"pattern":"**/*.py"}')
    assert glob.data['paths'] == sorted(glob.data['paths'])
    result = await runner.execute('search_code', '{"pattern":"marker","glob":"**/*.py"}')
    assert [m['path'] for m in result.data['matches']] == sorted((m['path'] for m in result.data['matches']))
    (tmp_path / 'long.txt').write_text('猫' * 100000)
    result = await runner.execute('search_code', '{"pattern":"猫","glob":"*.txt"}')
    assert result.ok and result.truncated
    assert sum((len(m['text'].encode()) for m in result.data['matches'])) <= 65536

@async_test
async def test_search_drops_matches_from_later_detected_binary_files(runner, tmp_path):
    (tmp_path / 'binary').write_bytes(b'marker\n' + b'x\n' * 100000 + b'\x00\n')
    (tmp_path / 'text').write_text('marker\n')
    result = await runner.execute('search_code', '{"pattern":"marker","max_results":1}')
    assert [m['path'] for m in result.data['matches']] == ['text']

@pytest.mark.parametrize('timeout', [False, True])
@async_test
async def test_invalid_utf8_expansion_marks_truncation_in_normal_and_partial_output(runner, timeout):
    import shlex
    script = 'import os,time; os.write(1,bytes([255])*20000)' + ('; time.sleep(5)' if timeout else '')
    result = await runner.execute('execute_command', json.dumps({'command': shlex.join([sys.executable, '-c', script]), 'timeout_seconds': 1 if timeout else 30}))
    assert result.truncated
    assert len(result.data['stdout'].encode()) <= 32768
    assert result.error['code'] == 'timeout' if timeout else result.ok


async def wait_file(path):
    async def wait():
        while not path.exists():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), 5)


@async_test
async def test_executor_wait_keeps_event_loop_responsive(runner, tmp_path):
    operation = asyncio.create_task(runner.execute("execute_command", '{"command":"touch started; sleep 20"}'))
    await wait_file(tmp_path / "started")
    observed = asyncio.Event()
    async def observer():
        await asyncio.sleep(0)
        observed.set()
    await asyncio.create_task(observer())
    assert observed.is_set() and not operation.done()
    operation.cancel()
    result = await operation
    assert result.error["code"] == "cancelled"


def slow_start_worker(connection, registry, context, name, raw):
    time.sleep(0.5)
    from mewcode.tools.executor import _worker
    _worker(connection, registry, context, name, raw)


@async_test
async def test_cancel_before_worker_ready_never_kills_parent_group(runner, tmp_path, monkeypatch):
    monkeypatch.setattr("mewcode.tools.executor._worker", slow_start_worker)
    cancel = asyncio.Event()
    task = asyncio.create_task(runner.execute("execute_command", '{"command":"touch leaked"}', cancel_event=cancel))
    await asyncio.sleep(0.05)
    cancel.set()
    result = await task
    assert result.error["code"] == "cancelled"
    assert not (tmp_path / "leaked").exists()
    import multiprocessing
    assert multiprocessing.active_children() == []


@async_test
async def test_result_is_preserved_when_cancel_arrives_during_cleanup(runner, monkeypatch):
    from mewcode.tools import executor as module
    cleanup = module._cleanup
    cleaning, release = threading.Event(), threading.Event()
    def held_cleanup(process, grouped):
        cleaning.set()
        release.wait(5)
        return cleanup(process, grouped)
    monkeypatch.setattr(module, "_cleanup", held_cleanup)
    cancel = asyncio.Event()
    task = asyncio.create_task(runner.execute("execute_command", '{"command":"printf actual"}', cancel_event=cancel))
    assert await asyncio.to_thread(cleaning.wait, 5)
    cancel.set()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release.set()
    result = await task
    assert result.ok and result.data["stdout"] == "actual"
    import multiprocessing
    assert multiprocessing.active_children() == []


@async_test
async def test_mode_guard_does_not_start_worker(runner, tmp_path):
    result = await runner.execute("execute_command", '{"command":"touch forbidden"}',
                                  allowed_tools=runner.registry.names(read_only=True))
    assert result.error["code"] == "tool_not_allowed"
    assert not (tmp_path / "forbidden").exists()


@async_test
async def test_timeout_does_not_cancel_another_active_tool(tmp_path):
    from mewcode.tools import default_registry
    from mewcode.tools.base import ToolContext
    from mewcode.tools.executor import ToolExecutor
    registry = default_registry()
    registry.register(BlockingTool())
    runner = ToolExecutor(registry, ToolContext(tmp_path), timeout=1, permissions=permission_bypass(tmp_path))
    stuck = asyncio.create_task(runner.execute("block", "{}"))
    healthy = asyncio.create_task(runner.execute("execute_command", '{"command":"touch active; sleep 1.5; touch finished", "timeout_seconds":5}'))
    await wait_file(tmp_path / "active")
    assert (await stuck).error["code"] == "timeout"
    assert (await healthy).ok
    assert (tmp_path / "finished").exists()
