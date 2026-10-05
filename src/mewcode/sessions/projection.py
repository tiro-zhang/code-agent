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
         'history_checkpoint', 'checkpoint', 'skills_changed', 'child_event'}
STATE_KEYS = {'version', 'failures', 'quotes', 'summary_files', 'cache_paths', 'circuit_open',
              'history_version', 'summary_version'}


@dataclass
class Projection:
    history: list[Message] = field(default_factory=list)
    mode: str = 'execute'
    state: dict = field(default_factory=dict)
    cache_paths: set[str] = field(default_factory=set)
    active_skills: list[dict] = field(default_factory=list)
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
    if kind == 'child_event':
        if (set(payload) != {'parent_task_id', 'run_id', 'skill', 'kind', 'payload'}
                or any(not isinstance(payload[key], str) or not payload[key] for key in ('parent_task_id', 'run_id', 'skill'))
                or payload['kind'] not in {'task_started', 'task_finished', 'interaction_started', 'tool_result',
                                           'history_commit', 'history_checkpoint', 'skills_changed'}):
            raise ValueError('子运行关联记录无效')
        validate_payload(payload['kind'], payload['payload'], identity, seq)
        return payload
    if 'mode' in payload and payload['mode'] not in {'plan', 'execute'}:
        raise ValueError('结构化模式无效')
    if 'state' in payload:
        state_value(payload['state'], identity)
    if 'active_skills' in payload or kind == 'skills_changed':
        activations_value(payload.get('active_skills'))
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


def activations_value(values):
    """只接受显式、有序描述；正文、旧工具结果不参与恢复。"""
    if not isinstance(values, list):
        raise ValueError('激活描述必须是列表')
    seen = set()
    for order, item in enumerate(values):
        if (not isinstance(item, dict) or set(item) != {'name', 'args', 'order', 'fingerprint'}
                or not isinstance(item['name'], str)
                or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', item['name'])
                or not isinstance(item['args'], str) or type(item['order']) is not int
                or item['order'] != order or not isinstance(item['fingerprint'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', item['fingerprint']) or item['name'] in seen):
            raise ValueError('激活描述字段或顺序无效')
        seen.add(item['name'])
    return values


def activity(record) -> bool:
    if record['kind'] in {'session_created', 'session_resumed', 'task_started', 'task_finished', 'mode_changed', 'skills_changed'}:
        return True
    if record['kind'] == 'history_checkpoint' and record['payload'].get('reset'):
        return True
    payload = record['payload']
    return (record['kind'] == 'maintenance_finished'
            and payload.get('purpose') in {'summary', 'compact'}
            and payload.get('manual', payload.get('user_initiated', True)))


def build_projection(records: list[dict], warnings: list[str]) -> Projection:
    projection = Projection(last_activity=timestamp(records[0]['timestamp']))
    checkpoint = None
    children, returned = {}, set()
    for record in records:
        if activity(record):
            projection.last_activity = max(projection.last_activity, timestamp(record['timestamp']))
        payload = record['payload']
        if record['kind'] == 'child_event':
            children[payload['run_id']] = payload
            payload = payload['payload']
        elif payload.get('child_run_id'):
            returned.add(payload['child_run_id'])
        messages = payload.get('messages', payload.get('history', []))
        inner_kind = record['payload']['kind'] if record['kind'] == 'child_event' else record['kind']
        if inner_kind == 'tool_result':
            messages = [payload['message']]
        if record['kind'] != 'child_event':
            for message in messages:
                data = (message.get('tool_result') or {}).get('data')
                if isinstance(data, dict) and isinstance(data.get('child_run_id'), str):
                    returned.add(data['child_run_id'])
        projection.cache_paths.update(message['cache_path'] for message in messages if message.get('cache_path'))
        state = payload.get('state', {})
        projection.cache_paths.update(state.get('cache_paths', []))
        projection.cache_paths.update(state.get('summary_files', []))
        if record['kind'] in {'checkpoint', 'history_checkpoint'}:
            checkpoint = record
    for run_id, child in children.items():
        if run_id not in returned:
            warnings.append(f'独立 Skill {child["skill"]}（{run_id}）主回流未完成；'
                            '请核查存档 child_event 的实际证据及可能副作用，不自动重跑。')
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
            if 'active_skills' in record['payload']:
                projection.active_skills = record['payload']['active_skills']
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
        if kind == 'child_event':
            continue
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
            if 'active_skills' in payload:
                projection.active_skills = payload['active_skills']
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
