"""按完整交互保留近期原文，并单独固定当前任务输入。"""
from dataclasses import dataclass

from ..types import Message
from .estimate import snapshot


@dataclass
class Partition:
    older: list[Message]
    recent: list[Message]


def validate_pairs(messages) -> None:
    pending = []
    for message in messages:
        if pending:
            if message.role != 'tool' or message.tool_call_id != pending.pop(0) or message.tool_result is None:
                raise ValueError('工具调用与结果不完整配对')
        elif message.role == 'tool':
            raise ValueError('存在孤立工具结果')
        elif message.tool_calls:
            pending = [call.id for call in message.tool_calls]
            if len(set(pending)) != len(pending):
                raise ValueError('工具调用 ID 重复')
    if pending:
        raise ValueError('工具结果缺失')


def partition(history, current_user_id: str | None, protocol: str, *, tail_tokens=10000, min_messages=5) -> Partition:
    validate_pairs(history)
    old_summaries = [m for m in history if m.context_kind == 'summary']
    original = [m for m in history if m.context_kind not in {'summary', 'boundary'}]
    groups, reminders = [], []
    index = 0
    while index < len(original):
        message = original[index]
        if message.role == 'context':
            reminders.append(message)
            index += 1
            continue
        length = 1 + len(message.tool_calls)
        groups.append([*reminders, *original[index:index + length]])
        reminders = []
        index += length
    if reminders:
        if groups:
            groups[-1].extend(reminders)
        else:
            groups.append(reminders)
    tokens = count = 0
    start = len(groups)
    while start and (tokens < tail_tokens or count < min_messages):
        start -= 1
        group = groups[start]
        tokens += snapshot(group, '', (), protocol).message_tokens
        count += sum(m.role != 'context' for m in group)
    older = [m for group in groups[:start] for m in group]
    recent = [m for group in groups[start:] for m in group]
    pinned = next((m for m in original if m.id == current_user_id and m.role == 'user'), None)
    if pinned is not None and any(m.id == pinned.id for m in older):
        older = [m for m in older if m.id != pinned.id]
        recent.insert(0, pinned)
    # 不为同一份摘要重新调用模型；必须有新旧记录能被缩减。
    if not older:
        return Partition([], list(history))
    return Partition([*old_summaries, *older], recent)
