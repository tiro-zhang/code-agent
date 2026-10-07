"""只读 Web 历史保留提交证据、稳定分页与结果访问边界。"""
import asyncio
import json
from dataclasses import replace

import pytest

from conftest import async_test
from mewcode.context.spill import ResultCache
from mewcode.sessions import Journal, SessionError, encode_message
from mewcode.sessions.browse import ArchiveBrowser
from mewcode.tools.base import ToolResult
from mewcode.types import Message, ToolCall


def commit(journal, *messages, **extra):
    return journal.append('history_commit', {'messages': [encode_message(m) for m in messages], **extra})


@async_test
async def test_create_and_browse_are_empty_and_read_only(tmp_path):
    browser = ArchiveBrowser(tmp_path)
    assert await browser.list_sessions() == {'items': [], 'next_cursor': None}
    assert not (tmp_path / '.mewcode').exists()
    identity = (await browser.create('openai', 'model'))['session_id']
    path = tmp_path / '.mewcode/sessions' / (identity + '.jsonl')
    before = path.read_bytes()
    listed = (await browser.list_sessions())['items']
    assert len(listed) == 1 and listed[0]['id'] == identity
    assert listed[0]['message_count'] == 0 and not listed[0]['active']
    assert listed[0]['recoverable']
    assert (await browser.history(identity))['items'] == []
    assert path.read_bytes() == before
    assert len(before.splitlines()) == 1


@async_test
async def test_compaction_and_reset_keep_old_display_without_checkpoint_duplicates(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    first, answer = Message('user', '最初问题'), Message('assistant', '原始答复')
    journal.append('task_started', {'task_id': 'task-a', 'input': '最初问题'})
    commit(journal, first, answer)
    journal.append('checkpoint', {'history': [encode_message(first), encode_message(answer)], 'state': {}})
    summary = Message('context', '压缩摘要', context_kind='summary')
    journal.append('history_checkpoint', {'history': [encode_message(summary)], 'state': {}})
    journal.append('history_checkpoint', {'history': [], 'state': {}, 'reset': True})
    final = Message('user', '重置后问题')
    commit(journal, final)
    journal.close()
    browser = ArchiveBrowser(tmp_path)
    page = await browser.history(journal.id)
    assert [item['text'] for item in page['items'] if item['role'] in {'user', 'assistant'}] == [
        '最初问题', '原始答复', '重置后问题']
    assert sum(item['id'] == first.id for item in page['items']) == 1
    assert any(item['kind'] == 'reset' for item in page['items'])
    assert any(item['kind'] == 'summary' and '压缩摘要' in item['text'] for item in page['items'])
    assert (await browser.list_sessions())['items'][0]['title'] == '最初问题'
    assert (await browser.list_sessions())['items'][0]['message_count'] == 3


@async_test
async def test_active_archive_and_stable_pagination_ignore_new_append(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    for number in range(55):
        commit(journal, Message('user', str(number)))
    browser = ArchiveBrowser(tmp_path)
    before = journal.path.read_bytes()
    info = (await browser.list_sessions())['items'][0]
    assert info['active'] and not info['recoverable']
    first = await browser.history(journal.id, limit=500)
    assert len(first['items']) == 50 and first['next_cursor']
    assert journal.path.read_bytes() == before
    commit(journal, Message('user', '新追加'))
    second = await browser.history(journal.id, cursor=first['next_cursor'])
    assert [item['text'] for item in second['items']] == ['50', '51', '52', '53', '54']
    assert second['next_cursor'] is None
    journal.close()


@async_test
async def test_partial_tail_and_unconfirmed_tool_result_are_visible_as_missing(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    user = Message('user', '已提交')
    commit(journal, user)
    assistant = Message('assistant', '', (ToolCall('a', 'read_file', '{}'), ToolCall('b', 'read_file', '{}')))
    journal.append('interaction_started', {'interaction_id': 'batch', 'messages': [encode_message(assistant)]})
    tool = Message('tool', tool_call_id='a', tool_result=ToolResult.success('确实读取'))
    journal.append('tool_result', {'interaction_id': 'batch', 'message': encode_message(tool)})
    journal.close()
    with journal.path.open('ab') as file:
        file.write(b'{"schema_version":1')
    before = journal.path.read_bytes()
    page = await ArchiveBrowser(tmp_path).history(journal.id)
    assert any(item['role'] == 'tool' and '确实读取' in json.dumps(item, ensure_ascii=False) for item in page['items'])
    assert any('不完整' in text or '未完整' in text for text in page['warnings'])
    assert any('尾' in text for text in page['warnings'])
    assert journal.path.read_bytes() == before


@async_test
async def test_large_message_is_chunked_and_secret_never_crosses_result_pages(tmp_path):
    secret = 'private-key-123456'
    body = '开头' + ('文' * 250000) + secret + ('x' * 250000) + '末尾'
    journal = Journal.create(tmp_path, 'openai', 'model')
    commit(journal, Message('assistant', body, provider_content=({'signature': 'never-public'},)))
    journal.close()
    browser = ArchiveBrowser(tmp_path, secret=secret)
    page = await browser.history(journal.id)
    assert len(json.dumps(page, ensure_ascii=False).encode()) <= 512 * 1024
    item = page['items'][0]
    assert item['truncated'] and item['result']['result_id']
    cursor, chunks = None, []
    while True:
        result = await browser.result(item['result']['result_id'], cursor=cursor)
        assert len(result['content'].encode()) <= 512 * 1024
        chunks.append(result['content'])
        cursor = result['next_cursor']
        if not cursor:
            break
    assert ''.join(chunks) == body.replace(secret, '[已隐藏]')
    assert 'never-public' not in json.dumps(page)


@async_test
async def test_cache_result_is_registered_to_message_and_not_an_arbitrary_path(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    cache = ResultCache(tmp_path, session_id=journal.id, persistent=True)
    assistant = Message('assistant', '', (ToolCall('a', 'read_file', '{}'),))
    tool = Message('tool', tool_call_id='a', tool_result=ToolResult.success({'output': '保留正文'}))
    path = cache.save(tool, 'read_file')
    reference = replace(tool, cache_path=path, tool_result=ToolResult.success({'preview': '短预览'}))
    commit(journal, assistant, reference)
    journal.close()
    cache.close()
    browser = ArchiveBrowser(tmp_path)
    history = await browser.history(journal.id)
    row = next(item for item in history['items'] if item['role'] == 'tool')
    result_id = row['result']['result_id']
    result = await browser.result(result_id)
    assert '保留正文' in result['content'] and result['session_id'] == journal.id
    with pytest.raises(SessionError):
        await browser.result(path)
    with pytest.raises(SessionError):
        await ArchiveBrowser(tmp_path).result(result_id)
    source = tmp_path / path
    source.unlink()
    source.symlink_to(journal.path)
    with pytest.raises(SessionError):
        await browser.result(result_id)


@async_test
async def test_cursor_is_bound_to_session_and_rejects_file_replacement(tmp_path):
    browser = ArchiveBrowser(tmp_path)
    first = Journal.create(tmp_path, 'openai', 'model')
    commit(first, Message('user', 'a'), Message('assistant', 'b'))
    first.close()
    second = await browser.create('openai', 'model')
    cursor = (await browser.history(first.id, limit=1))['next_cursor']
    with pytest.raises(SessionError):
        await browser.history(second['session_id'], cursor=cursor)
    with pytest.raises(SessionError):
        await browser.history(first.id, cursor=cursor + 'x')
    content = first.path.read_bytes()
    first.path.unlink()
    first.path.write_bytes(content)
    with pytest.raises(SessionError):
        await browser.history(first.id, cursor=cursor)


@async_test
async def test_root_and_archive_symlink_cannot_escape(tmp_path):
    browser = ArchiveBrowser(tmp_path)
    with pytest.raises(SessionError):
        await browser.history('../outside')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (tmp_path / '.mewcode').symlink_to(outside, target_is_directory=True)
    with pytest.raises(SessionError):
        await browser.list_sessions()
    assert list(outside.iterdir()) == []


@async_test
async def test_long_scan_yields_to_event_loop(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    commit(journal, *(Message('user', '内容' * 1000) for _ in range(100)))
    journal.close()
    completed = False
    ticks = 0
    async def heartbeat():
        nonlocal ticks
        while not completed:
            ticks += 1
            await asyncio.sleep(0)
    beat = asyncio.create_task(heartbeat())
    await ArchiveBrowser(tmp_path).history(journal.id)
    completed = True
    await beat
    assert ticks > 1


@async_test
async def test_orphan_results_and_forged_commits_do_not_become_verified_history(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    tool = Message('tool', tool_call_id='a', tool_result=ToolResult.success('孤立敏感正文'))
    journal.append('tool_result', {'interaction_id': 'absent', 'message': encode_message(tool)})
    assistant = Message('assistant', '', (ToolCall('a', 'read_file', '{}'),))
    journal.append('interaction_started', {'interaction_id': 'batch', 'messages': [encode_message(assistant)]})
    actual = Message('tool', tool_call_id='a', tool_result=ToolResult.success('真实结果'))
    journal.append('tool_result', {'interaction_id': 'batch', 'message': encode_message(actual)})
    forged = replace(actual, tool_result=ToolResult.success('伪造结果'))
    commit(journal, assistant, forged, interaction_id='batch')
    journal.close()
    page = await ArchiveBrowser(tmp_path).history(journal.id)
    text = json.dumps(page, ensure_ascii=False)
    assert '真实结果' in text and '伪造结果' not in text and '孤立敏感正文' not in text
    assert page['warnings']


@async_test
async def test_invalid_header_cannot_authorize_following_records(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    commit(journal, Message('user', '不可读'))
    journal.close()
    records = [json.loads(line) for line in journal.path.read_text().splitlines()]
    records[0]['payload']['created_at'] = 'invalid'
    journal.path.write_text(''.join(json.dumps(record) + '\n' for record in records))
    with pytest.raises(SessionError):
        await ArchiveBrowser(tmp_path).history(journal.id)


@async_test
async def test_cache_source_mismatch_and_result_cursor_swapping_are_rejected(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    cache = ResultCache(tmp_path, session_id=journal.id, persistent=True)
    a = Message('assistant', '', (ToolCall('call', 'read_file', '{}'),))
    source = Message('tool', tool_call_id='call', tool_result=ToolResult.success('超长' * 100000))
    path = cache.save(source, 'read_file')
    tool = replace(source, cache_path=path, tool_result=ToolResult.success({'preview': 'preview'}))
    commit(journal, a, tool)
    commit(journal, Message('assistant', '另一个正文' * 100000))
    cache.close()
    journal.close()
    browser = ArchiveBrowser(tmp_path)
    history = await browser.history(journal.id)
    first = next(row['result']['result_id'] for row in history['items'] if row['role'] == 'tool')
    second = history['items'][-1]['result']['result_id']
    page = await browser.result(first)
    assert page['next_cursor']
    with pytest.raises(SessionError):
        await browser.result(second, cursor=page['next_cursor'])
    lines = (tmp_path / path).read_text().splitlines()
    header = json.loads(lines[0])
    header['source'] = 'wrong-source'
    (tmp_path / path).write_text(json.dumps(header) + '\n' + '\n'.join(lines[1:]) + '\n')
    with pytest.raises(SessionError):
        await browser.result(first)


@async_test
async def test_list_sorts_by_latest_activity_and_snapshot_pagination(tmp_path):
    browser = ArchiveBrowser(tmp_path)
    first = Journal.create(tmp_path, 'openai', 'model')
    second = Journal.create(tmp_path, 'openai', 'model')
    second.close()
    first.append('task_started', {'input': '旧会话新活动'})
    first.close()
    page = await browser.list_sessions(limit=1)
    assert page['items'][0]['id'] == first.id
    third = await browser.create('openai', 'model')
    following = await browser.list_sessions(cursor=page['next_cursor'], limit=1)
    assert [item['id'] for item in following['items']] == [second.id]
    assert following['next_cursor'] is None
    assert (await browser.list_sessions())['items'][0]['id'] == third['session_id']


@async_test
async def test_history_body_and_call_preview_stay_bounded(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    messages = [Message('user', '文' * 3000) for _ in range(100)]
    commit(journal, *messages)
    journal.close()
    browser = ArchiveBrowser(tmp_path)
    cursor, identities = None, []
    while True:
        page = await browser.history(journal.id, cursor=cursor)
        assert len(json.dumps(page, ensure_ascii=False).encode()) <= 512 * 1024
        assert len(page['items']) <= 50
        identities.extend(item['id'] for item in page['items'])
        cursor = page['next_cursor']
        if not cursor:
            break
    assert len(identities) == len(set(identities)) == 100


@async_test
async def test_child_result_cannot_use_main_session_cache_directory(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    assistant = Message('assistant', '', (ToolCall('call', 'read_file', '{}'),))
    tool = Message('tool', tool_call_id='call', tool_result=ToolResult.success('子结果'))
    journal.append('child_event', {'parent_task_id': 'parent', 'run_id': 'child', 'skill': 'agent:test',
        'kind': 'history_commit', 'payload': {'messages': [encode_message(assistant), encode_message(tool)]},
        'cache_identity': 'child', 'workspace_root': str(tmp_path)})
    journal.close()
    history = await ArchiveBrowser(tmp_path).history(journal.id)
    assert all(item['run_id'] == 'child' for item in history['items'])
    assert len({item['id'] for item in history['items']}) == 2


@async_test
async def test_signed_cursor_rejects_in_place_changes_before_snapshot_boundary(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    commit(journal, Message('user', 'first'), Message('assistant', 'second'))
    journal.close()
    browser = ArchiveBrowser(tmp_path)
    cursor = (await browser.history(journal.id, limit=1))['next_cursor']
    content = journal.path.read_bytes().replace(b'first', b'other')
    journal.path.write_bytes(content)
    with pytest.raises(SessionError):
        await browser.history(journal.id, cursor=cursor)


@async_test
async def test_huge_record_reads_with_bounded_memory(tmp_path):
    import tracemalloc
    journal = Journal.create(tmp_path, 'openai', 'model')
    commit(journal, Message('assistant', 'A' * (12 * 1024 * 1024)))
    journal.close()
    browser = ArchiveBrowser(tmp_path)
    tracemalloc.start()
    try:
        page = await browser.history(journal.id)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert page['items'][0]['truncated']
    assert peak < 5 * 1024 * 1024


@async_test
async def test_task_boundaries_keep_saved_input_separate_from_context(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    journal.append('task_started', {'task_id': 't1', 'input': '/skill 写摘要'})
    commit(journal, Message('context', '展开的Skill内容', context_kind='skill_background'))
    journal.append('task_finished', {'task_id': 't1', 'status': 'cancelled'})
    journal.close()
    page = await ArchiveBrowser(tmp_path).history(journal.id)
    assert [row['kind'] for row in page['items']] == ['task_started', 'skill_background', 'task_finished']
    assert page['items'][0]['text'] == '/skill 写摘要'
    assert all(row['role'] == 'context' for row in page['items'])
