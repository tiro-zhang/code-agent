"""从尾部保留原文且不会切开工具批次。"""
from mewcode.context.partition import partition, validate_pairs
from mewcode.tools.base import ToolResult
from mewcode.types import Message, ToolCall


def history_for_task():
    user = Message('user', '当前任务 原文！')
    history = [Message('user', '很早的问题'), Message('assistant', '文' * 12000), user]
    for i in range(8):
        history.extend([Message('context', '应用提醒'), Message('assistant', '文' * 2500,
                        (ToolCall(str(i), 'read_file', '{}'),)),
                        Message('tool', tool_call_id=str(i), tool_result=ToolResult.success({'text': '文' * 1000}))])
    return user, history


def test_partition_preserves_current_user_and_whole_recent_batches():
    user, history = history_for_task()
    result = partition(history, user.id, 'openai')
    assert result.older and result.recent[0] is user
    assert result.recent.count(user) == 1
    assert all(any(m is old for old in history) for m in result.recent)
    assert len([m for m in result.recent if m.role != 'context']) >= 5
    validate_pairs(result.recent)
    assert history[0] in result.older


def test_insufficient_history_keeps_all_and_pending_user_never_inserted():
    user, history = history_for_task()
    result = partition(history[-3:], user.id, 'openai')
    assert not result.older and result.recent == history[-3:]
    assert user not in result.recent


def test_application_reminders_do_not_count_as_five_messages():
    history = [Message('user', '文' * 11000), Message('assistant', '答'),
               *(Message('context', '提醒') for _ in range(20))]
    assert not partition(history, history[0].id, 'openai').older
