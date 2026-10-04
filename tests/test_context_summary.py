"""摘要必须完整、可追溯且事务性提交。"""
import asyncio
from dataclasses import replace
import pytest

from conftest import ScriptedProvider, async_test
from mewcode.context.manager import ContextManager
from mewcode.context.summary import HEADINGS, parse_summary
from mewcode.types import Message, ProviderEvent, TokenUsage, ToolCall
from test_context_partition import history_for_task


def summary_text(quote='[]', files='无'):
    sections = ['目标', quote, '已读取；尚未验证', '沿用原约束', files, '继续读取并验证']
    return '<draft>PRIVATE DRAFT</draft><summary>' + '\n\n'.join('## '+h+'\n'+v for h,v in zip(HEADINGS, sections)) + '</summary>'


def response(text=None):
    return [ProviderEvent('thinking_delta', 'PRIVATE THINKING'),
            ProviderEvent('usage', usage=TokenUsage(total_input_tokens=555, output_tokens=666, complete=True)),
            ProviderEvent('completed', message=Message('assistant', text or summary_text()))]


def test_summary_validates_source_quotes_and_discards_draft():
    raw = summary_text('[{"source":"u1","quote":"保持原文"}]')
    summary, quotes = parse_summary(raw, {'u1': ['必须保持原文！']}, {})
    assert 'PRIVATE' not in summary and quotes == {'u1': ['保持原文']}
    with pytest.raises(ValueError):
        parse_summary(raw, {'u1': ['可以改写']}, {})


@pytest.mark.parametrize('text', ['<draft>秘密</draft>', summary_text()*2,
    summary_text().replace('## 当前目标', '## 错误'), summary_text().replace('目标\n', '文'*3000+'\n', 1)])
def test_invalid_summary_rejected(text):
    with pytest.raises(ValueError):
        parse_summary(text, {}, {})


@async_test
async def test_summary_commit_is_atomic_and_preserves_raw_user(tmp_path):
    user, history = history_for_task()
    provider = ScriptedProvider([response()])
    manager = ContextManager(tmp_path, context_window=128000, max_output_tokens=8192, protocol='openai')
    result = await manager.compact(provider, history, user, (), '系统', Message('context', '完整环境'), asyncio.Event(), manual=True)
    assert result.success and result.called
    assert history[0].context_kind == 'summary' and history[1] is user
    assert history[-1].context_kind == 'boundary'
    assert 'PRIVATE' not in str(history)
    assert provider.requests[0][1]['tools'] == () and provider.requests[0][1]['tool_choice'] == 'none'
    assert result.usage.output_tokens == 666 and manager.failures == 0
    assert '禁止调用任何工具' in provider.requests[0][1]['system_prompt']


@async_test
async def test_failed_quote_rolls_back_and_counts_failure(tmp_path):
    user, history = history_for_task(); before = list(history)
    manager = ContextManager(tmp_path, context_window=128000, max_output_tokens=8192, protocol='openai')
    result = await manager.compact(ScriptedProvider([response(summary_text('[{"source":"fake","quote":"假的"}]'))]),
          history, user, (), '系统', Message('context', '环境'), asyncio.Event(), manual=True)
    assert not result.success and history == before and manager.failures == 1


@async_test
async def test_cancel_discards_draft_closes_stream_without_count(tmp_path):
    cancel = asyncio.Event()
    async def waiting():
        yield ProviderEvent('text_delta', '<draft>PRIVATE')
        cancel.set()
        await asyncio.sleep(60)
    user, history = history_for_task(); before = list(history)
    manager = ContextManager(tmp_path, context_window=128000, max_output_tokens=8192, protocol='openai')
    provider = ScriptedProvider([waiting])
    result = await manager.compact(provider, history, user, (), '系统', Message('context', '环境'), cancel, manual=True)
    assert result.cancelled and history == before and manager.failures == 0 and provider.closed_streams == 1


@async_test
async def test_tools_in_summary_never_execute_and_no_progress_is_failure(tmp_path):
    user, original = history_for_task()
    for events in [[ProviderEvent('completed', message=Message('assistant', summary_text(), (ToolCall('evil','execute_command','{}'),)))],
                   [ProviderEvent('text_delta', summary_text())],
                   response(summary_text().replace('无\n\n## 未完成事项', '.mewcode/context/fake/not-real.jsonl\n\n## 未完成事项'))]:
        history = list(original)
        manager = ContextManager(tmp_path, context_window=128000, max_output_tokens=8192, protocol='openai')
        result = await manager.compact(ScriptedProvider([events]), history, user, (), '系统', Message('context','环境'), asyncio.Event(), manual=True)
        assert result.called and not result.success and history == original and manager.failures == 1


def test_budget_equality_and_manual_margin():
    from pathlib import Path
    manager = ContextManager(Path.cwd(), context_window=128000, max_output_tokens=8192, protocol='openai')
    assert not manager.fits(106808) and manager.fits(106807)
    assert manager.fits(106808, manual=True)
    assert not manager.fits(116808, manual=True)


@async_test
async def test_second_summary_replaces_old_and_inherits_verified_quotes(tmp_path):
    user, history = history_for_task()
    quote = '[{"source":"'+history[0].id+'","quote":"很早的问题"}]'
    manager = ContextManager(tmp_path, context_window=128000, max_output_tokens=8192, protocol='anthropic')
    provider = ScriptedProvider([response(summary_text(quote)), response(summary_text(quote))])
    first = await manager.compact(provider, history, user, (), '系统', Message('context','环境'), asyncio.Event(), manual=True)
    assert first.success
    _, additional = history_for_task()
    history.extend(additional[3:])
    second = await manager.compact(provider, history, user, (), '系统', Message('context','环境'), asyncio.Event(), manual=True)
    assert second.success and sum(m.context_kind == 'summary' for m in history) == 1
    assert manager.version == 2


@async_test
async def test_cached_results_survive_failed_summary_and_missing_reference_rejected(tmp_path):
    from test_context_spill import batch
    user, history = history_for_task()
    history[2:2] = batch([9000])
    manager = ContextManager(tmp_path, context_window=128000, max_output_tokens=8192, protocol='openai')
    assert manager.spill(history)[0] == 1
    baseline = list(history)
    result = await manager.compact(ScriptedProvider([response()]), history, user, (), '系统', Message('context','环境'), asyncio.Event(), manual=True)
    assert not result.success and history == baseline
    path = next(m.cache_path for m in history if m.cache_path)
    assert manager.cache.restore(path).data['text'] == '文' * 9000
    result = await manager.compact(ScriptedProvider([response(summary_text(files=path))]), history, user, (), '系统', Message('context','环境'), asyncio.Event(), manual=True)
    assert result.success and path in manager.summary_files
    assert manager.cache.restore(path).ok
    manager.cache.close()


@async_test
async def test_missing_cache_blocks_before_llm_without_failure_count(tmp_path):
    from test_context_spill import batch
    user, history = history_for_task(); history[2:2] = batch([9000])
    manager = ContextManager(tmp_path, context_window=128000, max_output_tokens=8192, protocol='openai')
    manager.spill(history)
    path = next(m.cache_path for m in history if m.cache_path)
    (tmp_path/path).unlink()
    provider = ScriptedProvider([response(summary_text(files=path))])
    result = await manager.compact(provider, history, user, (), '系统', Message('context','环境'), asyncio.Event(), manual=True)
    assert not result.called and not provider.requests and manager.failures == 0


@async_test
async def test_many_old_results_use_bounded_index_instead_of_overflowing_summary(tmp_path):
    from mewcode.tools.base import ToolResult
    user, history = history_for_task()
    manager = ContextManager(tmp_path, context_window=128000, max_output_tokens=8192, protocol='openai')
    paths = {manager.cache.save(Message('tool',tool_call_id=str(i),tool_result=ToolResult.success({'text':'历史数据'})), 'read_file') for i in range(100)}
    manager.summary_files = paths
    async def indexed_response():
        import json
        data = json.loads(provider.requests[-1][0][0].content)
        assert len(data['required_files']) == 1
        for item in response(summary_text(files=data['required_files'][0])):
            yield item
    provider = ScriptedProvider([indexed_response])
    result = await manager.compact(provider, history, user, (), '系统', Message('context','环境'), asyncio.Event(), manual=True)
    assert result.success and manager.summary_files == paths
    index = manager.cache.restore(manager.cache.index_path)
    assert set(index.data['files']) == paths
    manager.cache.close()


@async_test
async def test_cache_disappears_during_model_call_blocks_without_model_failure(tmp_path):
    from test_context_spill import batch
    user, history = history_for_task(); history[2:2] = batch([9000])
    manager = ContextManager(tmp_path,context_window=128000,max_output_tokens=8192,protocol='openai')
    manager.spill(history)
    path = next(m.cache_path for m in history if m.cache_path)
    async def disappear():
        (tmp_path/path).unlink()
        for event in response(summary_text(files=path)): yield event
    before = tuple(history)
    result = await manager.compact(ScriptedProvider([disappear]),history,user,(),'系统',Message('context','环境'),asyncio.Event(),manual=True)
    assert result.called and result.blocked and not result.success
    assert manager.failures == 0 and tuple(history) == before
    manager.cache.close()
