"""工具快照格式化不读取外部资源。"""

import json
from pathlib import Path

from mewcode.terminal.result_view import format_result


def snapshot(data, **fields):
    return json.dumps(dict(ok=True, data=data, error=None, truncated=False, **fields), ensure_ascii=False)


def test_file_and_command_keep_copyable_body():
    file = format_result('read_file', {'path': '中文/文件.txt'}, snapshot(
        {'path': '中文/文件.txt', 'content': '第一行\n  第二行\n', 'start_line': 3, 'end_line': 4}))
    assert '中文/文件.txt' in file and '3–4' in file
    assert '第一行\n  第二行\n' in file
    command = format_result('execute_command', {'command': 'printf hello'}, snapshot(
        {'stdout': 'one\ntwo\n', 'stderr': 'warn\n', 'exit_code': 0}))
    assert 'stdout\none\ntwo\n' in command
    assert 'stderr\nwarn\n' in command and '退出码：0' in command


def test_structured_mcp_and_empty_result_do_not_read_cache(monkeypatch):
    monkeypatch.setattr(Path, 'read_text', lambda *a, **k: (_ for _ in ()).throw(AssertionError('不应读取')))
    text = format_result('mcp_demo', {}, snapshot({'content': [{'type': 'text', 'text': '你好\n世界'}],
                                                  'structuredContent': {'count': 2}, 'cache': '/tmp/private'}))
    assert '你好\n世界' in text and 'count' in text and '/tmp/private' in text
    assert '空结果' in format_result('other', {}, snapshot(None))


def test_local_edit_diff_marks_scope_and_missing_final_newline():
    text = format_result('edit_file', {'path': 'a.txt', 'old_text': 'old', 'new_text': 'new\n'}, snapshot({'edited': True}))
    assert '局部 diff' in text and '仅依据调用参数' in text
    assert '-old' in text and '+new' in text and '原文末尾无换行' in text
    assert '整个文件' not in text


def test_missing_or_truncated_arguments_do_not_claim_complete_diff():
    for arguments, truncated in [({'old_text': 'old'}, False), ('{"old_text":"old', True),
                                 ({'old_text': 'old', 'new_text': 'new'}, True)]:
        text = format_result('edit_file', arguments, snapshot({}), arguments_truncated=truncated)
        assert '不完整' in text and '局部 diff\n' not in text
    text = format_result('write_file', {'path': 'a', 'content': 'draft\n'}, snapshot({'written': 6}))
    assert '写入内容快照' in text and 'draft\n' in text and 'written' in text


def test_raw_and_formatted_views_redact_decoded_json_and_controls():
    secret = 'secret-key'
    args = '{"path":"sec\\u0072et-key", "command":"\\u001b]0;evil\\u0007"}'
    for raw in [False, True]:
        text = format_result('execute_command', args, snapshot({'stdout': secret + '\n\x1b[31m',
            'side_effects_may_have_occurred': True}), secret=secret, raw=raw)
        assert secret not in text and '[已隐藏]' in text
        assert '\x1b' not in text and '\x07' not in text
        assert 'side_effects_may_have_occurred' in text


def test_invalid_result_and_eviction_retain_reason_and_raw_fragment():
    text = format_result('read_file', '{}', '{"data": "part', result_truncated=True)
    assert '截断' in text and '无法解析' in text and 'part' in text
    text = format_result('other', '{}', None, result_evicted=True)
    assert '正文已移出' in text and '容量' in text


def test_raw_json_retains_duplicate_fields_and_deep_snapshot_falls_back():
    original = '{"path":"first", "path":"secret-key", "control":"\\u001b"}'
    text = format_result('other', original, '{}', raw=True, secret='secret-key')
    assert 'first' in text and text.count('"path"') == 2 and 'secret-key' not in text
    nested = '[' * 1100 + '0' + ']' * 1100
    text = format_result('other', '{}', nested)
    assert '无法解析' in text and nested in text
