"""成员存档与真实工作根独立，恢复沿用原有日志协议。"""
import pytest

from mewcode.sessions.store import Journal, SessionError
from mewcode.sessions.codec import encode_message
from mewcode.context.spill import ResultCache
from mewcode.types import Message
from mewcode.tools.base import ToolResult


def test_external_journal_and_cache_roundtrip(tmp_path):
    root = tmp_path / 'worktree'
    storage = tmp_path / 'private-member'
    root.mkdir()
    storage.mkdir()
    journal = Journal.create(root, 'openai', 'test', storage_root=storage)
    cache = ResultCache(root, session_id=journal.id, persistent=True, storage_root=storage)
    result = Message('tool', tool_call_id='call', tool_result=ToolResult.success({'data':'结果'}))
    path = cache.save(result, 'read_file')
    assert journal.root == root
    assert journal.path.is_relative_to(storage)
    assert cache.directory.is_relative_to(storage)
    assert not (root / '.mewcode').exists()
    journal.append('history_checkpoint', {'history':[encode_message(Message('user','持续上下文'))],
                   'state': {'cache_paths':[path]}})
    identity = journal.id
    cache.close()
    journal.close()
    restored = Journal.resume(root, identity, 'openai', 'test', storage_root=storage)
    restored_cache = ResultCache(root, session_id=identity, persistent=True, storage_root=storage)
    restored_cache.reopen(restored.projection.cache_paths)
    assert restored.projection.history[0].content == '持续上下文'
    assert restored_cache.restore(path).data == {'data':'结果'}
    restored_cache.close()
    restored.close()


def test_external_resume_cannot_change_root_or_protocol(tmp_path):
    root, other, storage = (tmp_path / x for x in ('one','two','member'))
    for path in (root,other,storage):
        path.mkdir()
    journal = Journal.create(root, 'openai', 'test', storage_root=storage)
    identity = journal.id
    journal.close()
    with pytest.raises(SessionError):
        Journal.resume(other, identity, 'openai', 'test', storage_root=storage)
    with pytest.raises(SessionError):
        Journal.resume(root, identity, 'anthropic', 'test', storage_root=storage)
