"""从有效事件重建最后完整工作历史，不重放任何工具。"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
import re

from ..context.partition import validate_pairs
from ..types import Message
from .codec import decode_message, decode_messages, encode_message, json_value


KINDS = {'session_created', 'session_resumed', 'task_started', 'task_finished', 'mode_changed',
         'maintenance_finished', 'interaction_started', 'tool_result', 'history_commit',
         'history_checkpoint', 'checkpoint'}
STATE_KEYS = {'version', 'failures', 'quotes', 'summary_files', 'cache_paths', 'circuit_open',
              'history_version', 'summary_version'}


@dataclass
class Projection:
    history: list[Message] = field(default_factory=list)
    mode: str = 'execute'
    state: dict = field(default_factory=dict)
    cache_paths: set[str] = field(default_factory=set)
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def timestamp(value) -> datetime:
    if not isinstance(value, str):
        raise ValueError('记录时间无效')
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError('记录时间必须为 UTC')
    return result.astimezone(timezone.utc)


def cache_path(value: str, identity: str) -> bool:
    parts = PurePosixPath(value).parts
    return (len(parts) == 4 and parts[:3] == ('.mewcode', 'context', identity)
            and bool(re.fullmatch(r'[a-zA-Z0-9_-]+\.jsonl', parts[-1])))


def state_value(value, identity: str) -> dict:
    if not isinstance(value, dict) or not set(value) <= STATE_KEYS:
        raise ValueError('检查点控制状态无效')
    for key in ('version', 'failures', 'history_version', 'summary_version'):
        if key in value and (type(value[key]) is not int or value[key] < 0):
            raise ValueError('摘要版本或失败次数无效')
    if 'circuit_open' in value and type(value['circuit_open']) is not bool:
        raise ValueError('熔断状态无效')
    quotes = value.get('quotes', {})
    if (not isinstance(quotes, dict) or any(not isinstance(key, str) or not key
            or not isinstance(items, list) or any(not isinstance(item, str) for item in items)
            for key, items in quotes.items())):
        raise ValueError('原话来源结构无效')
    for key in ('summary_files', 'cache_paths'):
        if key in value and (not isinstance(value[key], list)
                or any(not isinstance(path, str) or not cache_path(path, identity) for path in value[key])):
            raise ValueError('缓存归属无效')
    return json_value(value)


def validate_payload(kind: str, payload, identity: str, seq: int) -> dict:
    if kind not in KINDS or not isinstance(payload, dict):
        raise ValueError('记录类型或数据无效')
    payload = json_value(payload)
    if 'mode' in payload and payload['mode'] not in {'plan', 'execute'}:
        raise ValueError('结构化模式无效')
    if 'state' in payload:
        state_value(payload['state'], identity)
    if kind == 'task_started':
        if not isinstance(payload.get('input'), str):
            raise ValueError('真实任务输入无效')
    if kind == 'session_resumed':
        if (payload.get('session_id') != identity
                or any(not isinstance(payload.get(key), str) or not payload[key]
                       for key in ('protocol', 'model'))):
            raise ValueError('恢复活动身份无效')
    if kind in {'interaction_started', 'history_commit'}:
        messages = decode_messages(payload.get('messages'))
        if kind == 'interaction_started':
            if (not isinstance(payload.get('interaction_id'), str) or not payload['interaction_id']
                    or not messages or messages[-1].role != 'assistant' or not messages[-1].tool_calls
                    or any(message.role not in {'user', 'context'} for message in messages[:-1])):
                raise ValueError('工具交互候选无效')
        else:
            validate_pairs(messages)
            if payload.get('interaction_id') is not None and not isinstance(payload['interaction_id'], str):
                raise ValueError('批次身份无效')
        for message in messages:
            if message.cache_path and not cache_path(message.cache_path, identity):
                raise ValueError('消息缓存不属于本会话')
    if kind == 'tool_result':
        message = decode_message(payload.get('message'))
        if (message.role != 'tool' or not isinstance(payload.get('interaction_id'), str)
                or not payload['interaction_id']
                or (message.cache_path and not cache_path(message.cache_path, identity))):
            raise ValueError('结果消息或批次身份无效')
    if kind in {'checkpoint', 'history_checkpoint'}:
        messages = decode_messages(payload.get('history'))
        validate_pairs(messages)
        if len({message.id for message in messages}) != len(messages):
            raise ValueError('检查点消息身份重复')
        for message in messages:
            if message.cache_path and not cache_path(message.cache_path, identity):
                raise ValueError('检查点缓存不属于本会话')
        state_value(payload.get('state', {}), identity)
        covers = payload.get('covers_seq')
        if type(covers) is not int or not 0 <= covers < seq:
            raise ValueError('检查点覆盖边界无效')
    return payload


def activity(record) -> bool:
    if record['kind'] in {'session_created', 'session_resumed', 'task_started', 'task_finished', 'mode_changed'}:
        return True
    payload = record['payload']
    return (record['kind'] == 'maintenance_finished'
            and payload.get('purpose') in {'summary', 'compact'}
            and payload.get('manual', payload.get('user_initiated', True)))


def build_projection(records: list[dict], warnings: list[str]) -> Projection:
    projection = Projection(last_activity=timestamp(records[0]['timestamp']))
    checkpoint = None
    for record in records:
        if activity(record):
            projection.last_activity = max(projection.last_activity, timestamp(record['timestamp']))
        payload = record['payload']
        messages = payload.get('messages', payload.get('history', []))
        if record['kind'] == 'tool_result':
            messages = [payload['message']]
        projection.cache_paths.update(message['cache_path'] for message in messages if message.get('cache_path'))
        state = payload.get('state', {})
        projection.cache_paths.update(state.get('cache_paths', []))
        projection.cache_paths.update(state.get('summary_files', []))
        if record['kind'] in {'checkpoint', 'history_checkpoint'}:
            checkpoint = record
    start = 0
    if checkpoint:
        projection.history = decode_messages(checkpoint['payload']['history'])
        projection.state = dict(checkpoint['payload'].get('state', {}))
        start = checkpoint['seq']
        # 模式不藏在摘要正文中；检查点之前最近的结构化模式仍有效。
        for record in records:
            if record['seq'] > start:
                break
            if 'mode' in record['payload']:
                projection.mode = record['payload']['mode']
    known = {message.id: encode_message(message) for message in projection.history}
    pending = None
    committed_batches = {}

    def add(messages):
        values = [encode_message(message) for message in messages]
        local = dict(known)
        additions = []
        for message, value in zip(messages, values):
            if message.id in local:
                if local[message.id] != value:
                    raise ValueError('重复消息身份冲突')
            else:
                local[message.id] = value
                additions.append(message)
        projection.history.extend(additions)
        known.update(local)

    def finish():
        nonlocal pending
        if pending is None:
            return
        candidates = pending['messages']
        calls = candidates[-1].tool_calls
        if set(pending['results']) != {call.id for call in calls}:
            raise ValueError('工具结果缺失')
        full = [*candidates, *(pending['results'][call.id] for call in calls)]
        validate_pairs(full)
        add(full)
        committed_batches[pending['id']] = [encode_message(message) for message in full]
        pending = None

    for record in records:
        if record['seq'] <= start:
            continue
        payload, kind = record['payload'], record['kind']
        try:
            if kind == 'interaction_started':
                if pending and payload['interaction_id'] == pending['id']:
                    if payload['messages'] != [encode_message(m) for m in pending['messages']]:
                        raise ValueError('批次身份冲突')
                    continue
                finish()
                if payload['interaction_id'] in committed_batches:
                    raise ValueError('已完成批次身份被重用')
                pending = {'id': payload['interaction_id'], 'messages': decode_messages(payload['messages']),
                           'results': {}, 'mode': projection.mode, 'state': dict(projection.state)}
            elif kind == 'tool_result':
                message = decode_message(payload['message'])
                if pending is None or payload['interaction_id'] != pending['id']:
                    old = committed_batches.get(payload['interaction_id'], [])
                    if payload['message'] in old:
                        continue
                    raise ValueError('孤立工具结果')
                if message.tool_call_id not in {call.id for call in pending['messages'][-1].tool_calls}:
                    raise ValueError('工具结果调用身份冲突')
                previous = pending['results'].get(message.tool_call_id)
                if previous and encode_message(previous) != payload['message']:
                    raise ValueError('工具结果重复冲突')
                pending['results'][message.tool_call_id] = message
            elif kind == 'history_commit':
                identity = payload.get('interaction_id')
                if pending and identity == pending['id']:
                    expected = [*pending['messages'], *(pending['results'].get(call.id)
                                 for call in pending['messages'][-1].tool_calls)]
                    if any(message is None for message in expected) or payload['messages'] != [encode_message(m) for m in expected]:
                        raise ValueError('提交组与真实工具结果不一致')
                    finish()
                else:
                    finish()
                    if identity and identity in committed_batches:
                        if payload['messages'] != committed_batches[identity]:
                            raise ValueError('已提交批次重复冲突')
                    else:
                        add(decode_messages(payload['messages']))
                        if identity:
                            committed_batches[identity] = payload['messages']
            elif kind in {'checkpoint', 'history_checkpoint'}:
                continue
            if 'mode' in payload:
                projection.mode = payload['mode']
            if 'state' in payload:
                projection.state.update(payload['state'])
        except ValueError as error:
            if pending:
                projection.mode, projection.state = pending['mode'], pending['state']
            warnings.append(f'会话在记录 {record["seq"]} 前截断：{error}；未确认操作可能已有副作用。')
            pending = None
            break
    else:
        try:
            finish()
        except ValueError as error:
            if pending:
                projection.mode, projection.state = pending['mode'], pending['state']
            warnings.append(f'会话在不完整交互之前截断：{error}；未确认操作可能已有副作用。')
    return projection
