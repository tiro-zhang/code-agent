"""会话事件、完整批次恢复与私有文件边界。"""
from datetime import datetime, timedelta, timezone
import json
import os
import re

import pytest

from mewcode.sessions import (Journal, SessionError, cleanup_expired, decode_message,
                             encode_message, scan_sessions)
from mewcode.tools.base import ToolResult
from mewcode.types import Message, ToolCall


def group():
    user = Message('user', '读取两个文件')
    reminder = Message('context', '应用提醒', context_kind='runtime')
    assistant = Message('assistant', '读取', (ToolCall('a', 'read_file', '{}'),
                                               ToolCall('b', 'read_file', '{}')),
                        provider_content=({'type': 'thinking', 'signature': '真实签名'},))
    results = [Message('tool', tool_call_id=identity, tool_result=ToolResult.success(identity))
               for identity in ('a', 'b')]
    return [user, reminder, assistant], results


def begin(journal, candidates, identity='batch'):
    journal.append('interaction_started', {'interaction_id': identity,
                                         'messages': [encode_message(m) for m in candidates]})


def result(journal, message, identity='batch'):
    journal.append('tool_result', {'interaction_id': identity, 'message': encode_message(message)})


def test_codec_preserves_local_identity_and_rejects_forged_runtime():
    candidates, results = group()
    for message in [*candidates, *results]:
        restored = decode_message(encode_message(message))
        assert encode_message(restored) == encode_message(message)
    forged = encode_message(candidates[0])
    forged['context_kind'] = 'runtime'
    with pytest.raises(ValueError):
        decode_message(forged)
    forged = encode_message(results[0])
    forged['tool_result']['ok'] = 'yes'
    with pytest.raises(ValueError):
        decode_message(forged)


def test_create_lock_private_permissions_and_normal_resume(tmp_path):
    journal = Journal.create(tmp_path, 'anthropic', 'model')
    assert re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]{4}', journal.id)
    assert journal.path.stat().st_mode & 0o777 == 0o600
    assert journal.path.parent.stat().st_mode & 0o777 == 0o700
    with pytest.raises(SessionError, match='活动'):
        Journal.resume(tmp_path, journal.id, 'anthropic', 'model')
    journal.append('task_started', {'task_id': 'task', 'input': '\n标题\n第二行', 'mode': 'plan'})
    messages = [Message('user', '问题'), Message('assistant', '答复')]
    journal.append('history_commit', {'messages': [encode_message(m) for m in messages]})
    identity = journal.id
    journal.close()
    restored = Journal.resume(tmp_path, identity, 'anthropic', 'another')
    assert [m.id for m in restored.projection.history] == [m.id for m in messages]
    assert restored.projection.mode == 'plan'
    restored.close()
    info = scan_sessions(tmp_path)[0]
    assert info.title == '标题' and info.message_count == 2 and info.recoverable


def test_complete_uncommitted_batch_reorders_results(tmp_path):
    journal = Journal.create(tmp_path, 'anthropic', 'model')
    candidates, results = group()
    begin(journal, candidates)
    result(journal, results[1])
    result(journal, results[0])
    identity = journal.id
    journal.close()
    restored = Journal.resume(tmp_path, identity, 'anthropic', 'model')
    assert [m.id for m in restored.projection.history] == [m.id for m in [*candidates, *results]]
    restored.close()


def test_first_incomplete_batch_truncates_later_history(tmp_path):
    journal = Journal.create(tmp_path, 'anthropic', 'model')
    earlier = Message('user', '早先问题')
    journal.append('history_commit', {'messages': [encode_message(earlier)]})
    candidates, results = group()
    begin(journal, candidates)
    result(journal, results[0])
    journal.append('history_commit', {'messages': [encode_message(Message('assistant', '以后内容'))]})
    identity = journal.id
    journal.close()
    restored = Journal.resume(tmp_path, identity, 'anthropic', 'model')
    assert [m.id for m in restored.projection.history] == [earlier.id]
    assert any('副作用' in warning for warning in restored.warnings)
    restored.close()


def test_checkpoint_replaces_history_without_changing_cumulative_count(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    messages = [Message('user', '原始问题'), Message('assistant', '原始答复')]
    committed = journal.append('history_commit', {'messages': [encode_message(m) for m in messages]})
    summary = Message('context', '正式摘要', context_kind='summary')
    journal.append('history_checkpoint', {'history': [encode_message(summary)],
                                         'state': {'version': 1, 'failures': 2, 'quotes': {},
                                                   'summary_files': [], 'cache_paths': []},
                                         'covers_seq': committed['seq']})
    final = Message('assistant', '继续答复')
    journal.append('history_commit', {'messages': [encode_message(final)]})
    identity = journal.id
    journal.close()
    restored = Journal.resume(tmp_path, identity, 'openai', 'model')
    assert [m.id for m in restored.projection.history] == [summary.id, final.id]
    assert restored.projection.state['failures'] == 2
    restored.close()
    assert scan_sessions(tmp_path)[0].message_count == 3


def test_bad_lines_are_preserved_and_separated_from_new_records(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    path, identity = journal.path, journal.id
    journal.close()
    with path.open('ab') as file:
        file.write(b'bad json\n{"unfinished":')
    before = path.read_bytes()
    assert scan_sessions(tmp_path)[0].warnings
    assert path.read_bytes() == before
    restored = Journal.resume(tmp_path, identity, 'openai', 'model')
    restored.close()
    assert path.read_bytes().startswith(before + b'\n')
    assert json.loads(path.read_bytes().splitlines()[-1])['kind'] == 'session_resumed'


def test_compatibility_rejects_protocol_and_signed_model_changes(tmp_path):
    journal = Journal.create(tmp_path, 'anthropic', 'model')
    candidates, results = group()
    journal.append('history_commit', {'messages': [encode_message(m) for m in [*candidates, *results]]})
    identity = journal.id
    journal.close()
    with pytest.raises(SessionError, match='协议'):
        Journal.resume(tmp_path, identity, 'openai', 'model')
    with pytest.raises(SessionError, match='续接'):
        Journal.resume(tmp_path, identity, 'anthropic', 'another')


def test_symlink_escape_rejected_and_invalid_id_never_opens_file(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (tmp_path / '.mewcode').symlink_to(outside, target_is_directory=True)
    with pytest.raises(SessionError):
        Journal.create(tmp_path, 'openai', 'model')
    with pytest.raises(SessionError):
        Journal.resume(tmp_path, '../outside', 'openai', 'model')
    assert list(outside.iterdir()) == []


def age_records(path, days):
    value = datetime.now(timezone.utc) - timedelta(days=days)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    for record in records:
        record['timestamp'] = value.isoformat()
        if record['kind'] == 'session_created':
            record['payload']['created_at'] = value.isoformat()
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records))


def test_cleanup_uses_last_activity_and_skips_active_and_unknown_files(tmp_path):
    old = Journal.create(tmp_path, 'openai', 'model')
    path, identity = old.path, old.id
    relative = f'.mewcode/context/{identity}/owned.jsonl'
    old.append('history_checkpoint', {'history': [], 'state': {'cache_paths': [relative]},
                                      'covers_seq': 1})
    old.close()
    cache = tmp_path / relative
    cache.parent.mkdir(parents=True)
    cache.write_text('owned')
    unknown = cache.parent / 'unknown.txt'
    unknown.write_text('keep')
    age_records(path, 31)
    active = Journal.create(tmp_path, 'openai', 'model')
    age_records(active.path, 31)
    warnings = cleanup_expired(tmp_path)
    assert unknown.exists() and not cache.exists() and path.exists()
    assert active.path.exists() and any('未知' in warning for warning in warnings)
    active.close()
    unknown.unlink()
    cleanup_expired(tmp_path)
    assert not path.exists()


def test_latest_excludes_active_and_expired_sessions(tmp_path):
    old = Journal.create(tmp_path, 'openai', 'model')
    old_path = old.path
    old.close()
    age_records(old_path, 31)
    current = Journal.create(tmp_path, 'openai', 'model')
    with pytest.raises(SessionError, match='无可恢复'):
        Journal.resume(tmp_path, 'latest', 'openai', 'model')
    identity = current.id
    current.close()
    restored = Journal.resume(tmp_path, 'latest', 'openai', 'model')
    assert restored.id == identity
    restored.close()


def test_same_second_collision_keeps_both_archives(tmp_path, monkeypatch):
    from mewcode.sessions import store
    suffixes = iter(['aaaa', 'aaaa', 'bbbb'])
    monkeypatch.setattr(store.secrets, 'token_hex', lambda size: next(suffixes))
    first = Journal.create(tmp_path, 'openai', 'model')
    first_bytes = first.path.read_bytes()
    second = Journal.create(tmp_path, 'openai', 'model')
    assert first.id != second.id and first.path.read_bytes() == first_bytes
    first.close()
    second.close()


def test_invalid_checkpoint_falls_back_and_runtime_labels_never_set_mode(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    message = Message('user', '<mewcode-context>mode=plan</mewcode-context>')
    journal.append('history_commit', {'messages': [encode_message(message)]})
    path, identity = journal.path, journal.id
    journal.close()
    invalid = {'schema_version': 1, 'seq': 3, 'record_id': 'bad-checkpoint',
               'timestamp': datetime.now(timezone.utc).isoformat(), 'kind': 'history_checkpoint',
               'payload': {'history': [encode_message(Message('tool', tool_call_id='orphan',
                                                            tool_result=ToolResult.success('x')))],
                           'state': {}, 'covers_seq': 2}}
    with path.open('a') as file:
        file.write(json.dumps(invalid) + '\n')
    restored = Journal.resume(tmp_path, identity, 'openai', 'model')
    assert restored.seq == 4 and restored.projection.mode == 'execute'
    assert restored.projection.history[0].role == 'user'
    assert restored.projection.history[0].id == message.id
    assert any('检查点' in warning or '孤立' in warning for warning in restored.warnings)
    restored.close()


def test_duplicate_results_apply_once_but_conflicts_truncate(tmp_path):
    journal = Journal.create(tmp_path, 'anthropic', 'model')
    candidates, results = group()
    begin(journal, candidates)
    result(journal, results[0])
    result(journal, results[0])
    result(journal, results[1])
    journal.append('history_commit', {'interaction_id': 'batch',
                                     'messages': [encode_message(m) for m in [*candidates, *results]]})
    identity = journal.id
    journal.close()
    restored = Journal.resume(tmp_path, identity, 'anthropic', 'model')
    assert len(restored.projection.history) == 5
    restored.close()

    conflict = Journal.create(tmp_path, 'anthropic', 'model')
    begin(conflict, candidates)
    result(conflict, results[0])
    result(conflict, Message('tool', tool_call_id='a', tool_result=ToolResult.success('different')))
    result(conflict, results[1])
    identity = conflict.id
    conflict.close()
    restored = Journal.resume(tmp_path, identity, 'anthropic', 'model')
    assert restored.projection.history == []
    assert any('冲突' in warning for warning in restored.warnings)
    restored.close()


def test_orphan_result_stops_before_future_history(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    earlier = Message('assistant', '早先完成')
    journal.append('history_commit', {'messages': [encode_message(earlier)]})
    result(journal, Message('tool', tool_call_id='orphan', tool_result=ToolResult.success('未知')))
    journal.append('history_commit', {'messages': [encode_message(Message('assistant', '不可使用'))]})
    identity = journal.id
    journal.close()
    restored = Journal.resume(tmp_path, identity, 'openai', 'model')
    assert [m.id for m in restored.projection.history] == [earlier.id]
    assert any('孤立' in warning for warning in restored.warnings)
    restored.close()


def test_finished_state_overrides_checkpoint_and_resume_preserves_old_activity(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    journal.append('history_checkpoint', {'history': [], 'state': {'failures': 0}})
    journal.append('task_finished', {'mode': 'plan', 'state': {'failures': 3, 'version': 2}})
    path, identity = journal.path, journal.id
    journal.close()
    age_records(path, 2)
    old_activity = scan_sessions(tmp_path)[0].last_activity
    restored = Journal.resume(tmp_path, identity, 'openai', 'model')
    assert restored.projection.last_activity == old_activity
    assert restored.projection.state['failures'] == 3 and restored.projection.mode == 'plan'
    assert scan_sessions(tmp_path)[0].last_activity > old_activity
    restored.close()


def test_background_memory_never_extends_activity_and_thirty_days_is_inclusive(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    path = journal.path
    journal.close()
    age_records(path, 30)
    activity = scan_sessions(tmp_path)[0].last_activity
    record = {'schema_version': 1, 'seq': 2, 'record_id': 'memory', 'kind': 'maintenance_finished',
              'timestamp': datetime.now(timezone.utc).isoformat(),
              'payload': {'purpose': 'memory', 'usage': {}}}
    with path.open('a') as file:
        file.write(json.dumps(record) + '\n')
    assert scan_sessions(tmp_path)[0].last_activity == activity
    cleanup_expired(tmp_path, activity + timedelta(days=30))
    assert path.exists()
    cleanup_expired(tmp_path, activity + timedelta(days=30, microseconds=1))
    assert not path.exists()


def test_scan_missing_storage_is_read_only_and_invalid_identity_is_visible(tmp_path):
    assert scan_sessions(tmp_path) == []
    assert not (tmp_path / '.mewcode').exists()
    journal = Journal.create(tmp_path, 'openai', 'model')
    path, identity = journal.path, journal.id
    journal.close()
    header = json.loads(path.read_text())
    header['payload']['project_root'] = str(tmp_path.parent)
    path.write_text(json.dumps(header) + '\n')
    info = scan_sessions(tmp_path)[0]
    assert not info.recoverable and info.warnings
    with pytest.raises(SessionError, match='项目根'):
        Journal.resume(tmp_path, identity, 'openai', 'model')
    cleanup_expired(tmp_path, datetime.now(timezone.utc) + timedelta(days=40))
    assert path.exists()


def test_fsync_failure_blocks_followup_and_critical_records_always_sync(tmp_path, monkeypatch):
    from mewcode.sessions import store
    journal = Journal.create(tmp_path, 'openai', 'model')
    calls = []
    actual = store.os.fsync
    monkeypatch.setattr(store.os, 'fsync', lambda fd: calls.append(fd))
    journal.append('history_commit', {'messages': [encode_message(Message('assistant', '完成'))]}, critical=False)
    assert len(calls) == 1
    def fail(fd):
        raise OSError('磁盘故障')
    monkeypatch.setattr(store.os, 'fsync', fail)
    with pytest.raises(SessionError, match='磁盘故障'):
        journal.append('task_finished', {'reason': 'model_done'})
    with pytest.raises(SessionError, match='此前写入失败'):
        journal.append('mode_changed', {'mode': 'plan'})
    monkeypatch.setattr(store.os, 'fsync', actual)
    journal.close()


def test_directory_replacement_cannot_redirect_appends(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    sessions = journal.path.parent
    moved = sessions.with_name('sessions-original')
    sessions.rename(moved)
    sessions.mkdir()
    before = (moved / journal.path.name).read_bytes()
    with pytest.raises(SessionError, match='真实路径'):
        journal.append('mode_changed', {'mode': 'plan'})
    assert (moved / journal.path.name).read_bytes() == before
    assert list(sessions.iterdir()) == []
    journal.close()


def test_cleanup_rejects_cross_session_path_and_preserves_symlink_targets(tmp_path):
    other = Journal.create(tmp_path, 'openai', 'model')
    other_relative = f'.mewcode/context/{other.id}/owned.jsonl'
    other.close()
    journal = Journal.create(tmp_path, 'openai', 'model')
    with pytest.raises(SessionError, match='归属'):
        journal.append('history_checkpoint', {'history': [], 'state': {'cache_paths': [other_relative]}})
    journal.close()
    private = Journal.create(tmp_path, 'openai', 'model')
    path, identity = private.path, private.id
    relative = f'.mewcode/context/{identity}/owned.jsonl'
    private.append('history_checkpoint', {'history': [], 'state': {'cache_paths': [relative]}})
    private.close()
    age_records(path, 31)
    target = tmp_path / 'outside.jsonl'
    target.write_text('keep')
    cache = tmp_path / relative
    cache.parent.mkdir(parents=True)
    cache.symlink_to(target)
    warnings = cleanup_expired(tmp_path)
    assert cache.is_symlink() and target.read_text() == 'keep' and path.exists()
    assert any('普通文件' in warning for warning in warnings)


def test_state_after_incomplete_interaction_is_not_restored(tmp_path):
    journal = Journal.create(tmp_path, 'anthropic', 'model')
    journal.append('task_started', {'input': '任务', 'mode': 'execute'})
    candidates, results = group()
    begin(journal, candidates)
    result(journal, results[0])
    journal.append('task_finished', {'mode': 'plan', 'state': {'failures': 3}})
    identity = journal.id
    journal.close()
    restored = Journal.resume(tmp_path, identity, 'anthropic', 'model')
    assert restored.projection.mode == 'execute'
    assert restored.projection.state.get('failures', 0) == 0
    restored.close()


def test_new_model_can_resume_its_own_provider_blocks(tmp_path):
    journal = Journal.create(tmp_path, 'anthropic', 'first-model')
    journal.append('history_commit', {'messages': [encode_message(Message('user', '无签名旧历史'))]})
    identity = journal.id
    journal.close()
    changed = Journal.resume(tmp_path, identity, 'anthropic', 'second-model')
    changed.append('history_commit', {'messages': [encode_message(Message(
        'assistant', '第二模型答复', provider_content=({'type': 'thinking', 'signature': '第二模型签名'},)))]})
    changed.close()
    restored = Journal.resume(tmp_path, identity, 'anthropic', 'second-model')
    assert restored.projection.history[-1].provider_content[0]['signature'] == '第二模型签名'
    restored.close()
    with pytest.raises(SessionError, match='续接'):
        Journal.resume(tmp_path, identity, 'anthropic', 'first-model')


def test_unknown_legacy_cache_is_preserved_with_diagnostic(tmp_path):
    cache = tmp_path / '.mewcode/context' / ('a' * 32)
    cache.mkdir(parents=True)
    file = cache / 'old.jsonl'
    file.write_text('历史缓存')
    warnings = cleanup_expired(tmp_path)
    assert file.read_text() == '历史缓存'
    assert any('无存档归属' in warning for warning in warnings)


def test_failed_unconfirmed_resume_does_not_extend_activity(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'first-model')
    path, identity = journal.path, journal.id
    journal.close()
    age_records(path, 2)
    before = path.read_bytes()
    old_activity = scan_sessions(tmp_path)[0].last_activity
    restored = Journal.resume(tmp_path, identity, 'openai', 'second-model', record_activity=False)
    assert restored.projection.last_activity == old_activity
    assert scan_sessions(tmp_path)[0].last_activity == old_activity
    restored.close()
    assert path.read_bytes() == before
    assert scan_sessions(tmp_path)[0].last_activity == old_activity


def test_confirm_resume_is_idempotent_and_records_current_model(tmp_path):
    journal = Journal.create(tmp_path, 'anthropic', 'first-model')
    path, identity = journal.path, journal.id
    journal.close()
    age_records(path, 2)
    old_activity = scan_sessions(tmp_path)[0].last_activity
    restored = Journal.resume(tmp_path, identity, 'anthropic', 'second-model', record_activity=False)
    old_seq = restored.seq
    restored.confirm_resume()
    assert restored.seq == old_seq + 1
    assert restored.projection.last_activity == old_activity
    assert scan_sessions(tmp_path)[0].last_activity > old_activity
    record = json.loads(path.read_text().splitlines()[-1])
    assert record['kind'] == 'session_resumed'
    assert record['payload']['model'] == 'second-model'
    assert record['payload']['protocol'] == 'anthropic'
    restored.confirm_resume()
    assert restored.seq == old_seq + 1
    restored.close()


def test_default_resume_is_already_confirmed(tmp_path):
    journal = Journal.create(tmp_path, 'openai', 'model')
    identity = journal.id
    journal.close()
    restored = Journal.resume(tmp_path, identity, 'openai', 'model')
    seq = restored.seq
    restored.confirm_resume()
    assert restored.seq == seq
    restored.close()
