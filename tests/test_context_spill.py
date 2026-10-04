"""工具缓存的可恢复性、阈值与路径边界。"""
import json
import os

from mewcode.context.spill import ResultCache, spill_history
from mewcode.tools.base import ToolResult, ToolContext
from mewcode.tools.files import ReadFile
from mewcode.types import Message, ToolCall


def batch(lengths):
    calls = tuple(ToolCall(str(i), 'read_file', '{}') for i in range(len(lengths)))
    return [Message('assistant', tool_calls=calls), *(Message('tool', tool_call_id=c.id,
            tool_result=ToolResult.success({'text': '文' * n}, truncated=True)) for c, n in zip(calls, lengths))]


def test_spill_roundtrip_paging_state_and_reuse(tmp_path):
    cache = ResultCache(tmp_path)
    history = batch([9000]); original = history[1].tool_result
    changed, warnings = spill_history(history, cache)
    assert changed == 1 and not warnings
    stored = history[1]
    assert stored.cache_path and stored.tool_result.truncated
    assert cache.restore(stored.cache_path) == original
    assert original.data['text'] == '文' * 9000
    path = tmp_path / stored.cache_path
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700
    result = ReadFile().execute({'path': stored.cache_path, 'start_line': 2, 'max_lines': 2}, ToolContext(tmp_path))
    assert result.ok
    assert spill_history(history, cache) == (0, [])
    cache.close()
    assert not path.exists()


def test_batch_tie_spills_first_only(tmp_path):
    cache = ResultCache(tmp_path); history = batch([7000, 7000, 7000])
    assert spill_history(history, cache)[0] == 1
    assert [bool(m.cache_path) for m in history[1:]] == [True, False, False]
    cache.close()


def test_symlink_refusal_preserves_original_and_other_session(tmp_path):
    outside = tmp_path / 'outside'; outside.mkdir()
    (tmp_path / '.mewcode').symlink_to(outside, target_is_directory=True)
    history = batch([9000]); original = list(history)
    cache = ResultCache(tmp_path)
    changed, warnings = spill_history(history, cache)
    assert changed == 0 and warnings and history == original
    assert list(outside.iterdir()) == []
    cache.close()


def test_cleanup_keeps_other_sessions_and_never_follows_replaced_file(tmp_path):
    first, second = ResultCache(tmp_path), ResultCache(tmp_path)
    h1, h2 = batch([9000]), batch([9000])
    spill_history(h1, first); spill_history(h2, second)
    target = tmp_path / 'keep'; target.write_text('不能删除')
    path = tmp_path / h1[1].cache_path; path.unlink(); path.symlink_to(target)
    first.close()
    assert target.read_text() == '不能删除'
    assert second.restore(h2[1].cache_path).ok
    second.close()


def test_disk_failure_preserves_original_and_stops_further_attempts(tmp_path, monkeypatch):
    cache = ResultCache(tmp_path); history = batch([9000, 9000]); before = list(history)
    def fail(_):
        raise OSError('磁盘已满')
    monkeypatch.setattr('mewcode.context.spill.os.fsync', fail)
    assert spill_history(history, cache)[1]
    assert history == before and cache.count == 0
    assert spill_history(history, cache) == (0, [])
    cache.close()


def test_unshrinkable_error_does_not_repeat_writes(tmp_path):
    cache = ResultCache(tmp_path)
    history = [Message('assistant', tool_calls=(ToolCall('bad','read_file','{}'),)),
               Message('tool', tool_call_id='bad', tool_result=ToolResult.failure('bad', '文' * 21000))]
    spill_history(history, cache)
    files = cache.count
    spill_history(history, cache)
    assert cache.count == files == 1
    cache.close()


def test_spill_preserves_nested_states_and_full_original(tmp_path):
    cache = ResultCache(tmp_path)
    result = ToolResult.failure('cancelled','取消', details={'not_started':False,'side_effects_may_have_occurred':True},
                               data={'stdout':'中文\n'+('x'*40000),'nested':{'records':[1,{'v':'保留'}]},
                                     'permission_limited':True,'skipped_files':2}, truncated=True)
    history = batch([1]); history[1] = Message('tool',tool_call_id='0',tool_result=result)
    assert spill_history(history, cache)[0] == 1
    stored = history[1]
    assert stored.tool_result.error == result.error and stored.tool_result.data['state']['permission_limited']
    assert cache.restore(stored.cache_path) == result
    cache.close()


def test_cache_is_git_ignored_in_arbitrary_user_project(tmp_path):
    import subprocess
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    history = batch([9000]); cache = ResultCache(tmp_path)
    spill_history(history, cache)
    result = subprocess.run(['git','check-ignore',history[1].cache_path],cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode == 0
    cache.close()


from conftest import async_test


@async_test
async def test_cached_read_obeys_deny_and_search_never_includes_cache(tmp_path):
    from test_permission_execution import executor
    from test_permission_runtime import policy, rule
    from mewcode.tools.search import GlobFiles, SearchCode
    cache = ResultCache(tmp_path); history = batch([9000]); spill_history(history,cache)
    path = history[1].cache_path
    policy(tmp_path,[rule('deny',f'read_file({path})','exact')])
    result = await executor(tmp_path,mode='bypass').execute('read_file',json.dumps({'path':path}))
    assert result.error['code'] == 'permission_denied'
    # 即使候选由调用方显式提供，普通项目搜索也不扫描应用结果缓存。
    context = ToolContext(tmp_path,authorized_paths=(str(tmp_path/path),))
    assert GlobFiles().execute({'pattern':'**'},context).data['paths'] == []
    assert SearchCode().execute({'pattern':'文'},context).data['matches'] == []
    cache.close()
