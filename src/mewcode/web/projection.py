"""有界的界面投影；增量维护大小，绝不修改模型上下文或审批快照。"""

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import json
import math


PRIVATE_FIELDS = frozenset(('provider_content', 'api_key', 'authorization', 'headers',
                            'env', 'password', 'access_token', 'refresh_token', 'config'))
BODY_FIELDS = {'message': frozenset(('text', 'call', 'result')),
               'run': frozenset(('text', 'thinking', 'calls')),
               'call': frozenset(('arguments', 'result'))}


def unchanged(value):
    return value


def public_value(value, redact=unchanged, *, depth=0):
    """只接受普通 JSON 值；键和值均脱敏，连接与供应商内部字段直接丢弃。"""
    if depth > 24:
        return '[嵌套内容过深]'
    if isinstance(value, str):
        return redact(value)
    if value is None or type(value) in (int, bool):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {redact(key): public_value(item, redact, depth=depth + 1)
                for key, item in value.items() if isinstance(key, str) and key.lower() not in PRIVATE_FIELDS}
    if isinstance(value, (tuple, list)):
        return [public_value(item, redact, depth=depth + 1) for item in value]
    return None


def clipped(text, limit):
    raw = text.encode('utf-8', errors='replace')
    return raw[:max(0, limit)].decode('utf-8', errors='ignore'), len(raw) > limit


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _size(value):
    if value is None:
        return 0
    return len((value if isinstance(value, str) else _json(value)).encode('utf-8', errors='replace'))


def _fit(value, limit):
    """截断副本连同 JSON 包装也必须在限额内，预算不足时只留外层省略标记。"""
    if value is None or _size(value) <= limit:
        return value, False
    if isinstance(value, str):
        return clipped(value, limit)
    raw = _json(value).encode('utf-8')
    marker = {'truncated': True}
    if _size(marker) > limit:
        return None, True
    if isinstance(value, dict) and isinstance(value.get('ok'), bool):
        candidate = dict(marker, ok=value['ok'])
        if _size(candidate) <= limit:
            marker = candidate
    low, high, answer = 0, min(len(raw), limit), marker
    while low <= high:
        middle = (low + high) // 2
        candidate = dict(marker, output=raw[:middle].decode('utf-8', errors='ignore'))
        if _size(candidate) <= limit:
            answer, low = candidate, middle + 1
        else:
            high = middle - 1
    return answer, True


def usage_value(usage):
    return {key: getattr(usage, key) for key in (
        'input_tokens', 'output_tokens', 'complete', 'cache_read_tokens',
        'cache_write_tokens', 'total_input_tokens', 'cache_miss_tokens', 'cache_complete')}


@dataclass
class _Record:
    row: dict
    kind: str
    key: object
    parent: dict | None = None
    metadata: int = 0
    slots: dict = field(default_factory=dict)


@dataclass
class _Slot:
    record: _Record
    key: str
    thinking: bool = False
    size: int = 0


class Projection:
    def __init__(self, session_id=None, *, redact=unchanged, body_limit=8 * 1024 * 1024,
                 thinking_limit=256 * 1024, detail_limit=64 * 1024, call_limit=1024,
                 metadata_limit=1024 * 1024):
        if min(body_limit, thinking_limit) < 0 or min(detail_limit, call_limit, metadata_limit) < 1:
            raise ValueError('展示预算无效')
        self.session_id, self.redact = session_id, redact
        self.body_limit, self.thinking_limit = body_limit, thinking_limit
        self.detail_limit, self.call_limit, self.metadata_limit = detail_limit, call_limit, metadata_limit
        self.messages, self.runs, self.warnings = [], [], []
        self._counter = 0
        self._records = OrderedDict()
        self._body_slots, self._thinking_slots = OrderedDict(), OrderedDict()
        self._body_bytes = self._thinking_bytes = self._metadata_bytes = 0
        self._message_index, self._run_index, self._call_index = {}, {}, {}
        secrets = getattr(getattr(redact, '__self__', None), 'secrets', None)
        self._redact_tail = (max((len(secret) for secret in secrets), default=1) - 1
                             if secrets is not None else 0 if redact is unchanged else None)
        self._root_bytes = 0
        self._root_size()
        if self._root_bytes > metadata_limit:
            raise ValueError('元数据预算不足以容纳会话身份')

    def _root_size(self):
        self._root_bytes = _size({'session_id': self.session_id, 'messages': [], 'runs': [],
                                  'warnings': self.warnings})

    def warn(self, message):
        message = clipped(self.redact(message), min(4096, self.metadata_limit // 4))[0]
        if message and message not in self.warnings:
            self.warnings.append(message)
            self.warnings[:] = self.warnings[-16:]
            self._root_size()
            while self._root_bytes > self.metadata_limit and self.warnings:
                if len(self.warnings) > 1:
                    self.warnings.pop(0)
                else:
                    allowed = max(0, _size(self.warnings[0]) - (self._root_bytes - self.metadata_limit))
                    self.warnings[0] = clipped(self.warnings[0], allowed)[0]
                    if not self.warnings[0]:
                        self.warnings.clear()
                self._root_size()

    def _register(self, row, kind, key, parent=None):
        record = _Record(row, kind, key, parent)
        self._records[id(row)] = record
        self._touch(record)
        return record

    def _touch(self, record):
        # 正文绝不参与元数据序列化；每条记录另预留逗号与空格的开销。
        value = {key: value for key, value in record.row.items() if key not in BODY_FIELDS[record.kind]}
        if record.kind == 'run':
            value['calls'] = []
        size = _size(value) + 2
        self._metadata_bytes += size - record.metadata
        record.metadata = size

    def _set(self, record, key, value, *, thinking=False, size=None):
        slot = record.slots.get(key)
        if slot is None:
            slot = record.slots[key] = _Slot(record, key, thinking)
        slots = self._thinking_slots if slot.thinking else self._body_slots
        new_size = _size(value) if size is None else size
        difference = new_size - slot.size
        if slot.thinking:
            self._thinking_bytes += difference
        else:
            self._body_bytes += difference
        slot.size = new_size
        record.row[key] = value
        identity = (id(record.row), key)
        if new_size:
            slots[identity] = slot
        else:
            slots.pop(identity, None)

    def _append(self, record, key, text, *, thinking=False):
        old = record.row.get(key, '')
        slot = record.slots.get(key)
        old_size = slot.size if slot else 0
        if self._redact_tail is None:
            value = self.redact(old + text)
            size = _size(value)
        else:
            width = self._redact_tail
            tail, prefix = (old[-width:], old[:-width]) if width else ('', old)
            suffix = self.redact(tail + text)
            value = prefix + suffix
            size = old_size - _size(tail) + _size(suffix)
        self._set(record, key, value, thinking=thinking, size=size)

    def _arguments(self, arguments):
        try:
            return _json(public_value(json.loads(arguments), self.redact))
        except (ValueError, TypeError, RecursionError):
            return self.redact(arguments)

    def _history_call(self, value):
        if isinstance(value, list):
            return [self._history_call(item) for item in value]
        if isinstance(value, dict):
            value = dict(value)
            if isinstance(value.get('arguments'), str):
                value['arguments'] = self._arguments(value['arguments'])
        return value

    def _message(self, row):
        key = row['id']
        self.messages.append(row)
        self._message_index[key] = row
        record = self._register(row, 'message', key)
        for key in ('text', 'call', 'result'):
            if key not in row:
                continue
            value = row[key]
            if key != 'text':
                value, trimmed = _fit(value, self.detail_limit)
                if trimmed:
                    row['truncated'] = True
            self._set(record, key, value)
        self._touch(record)
        return record

    def seed(self, page):
        for item in tuple(self.messages):
            self._remove(self._records[id(item)])
        for source in page.get('items', []):
            row = public_value({key: source[key] for key in (
                'id', 'role', 'text', 'run_id', 'kind', 'truncated', 'call', 'result', 'result_id')
                if key in source}, self.redact)
            if 'call' in row:
                row['call'] = self._history_call(row['call'])
            if isinstance(row.get('result'), dict) and row['result'].get('result_id'):
                row['result_id'] = row['result']['result_id']
            self._message(row)
        for warning in page.get('warnings', []):
            self.warn(warning)
        if page.get('next_cursor'):
            self.warn('临时视图只加载部分历史；更早记录可在历史分页中查看。')
        self._bound()

    def input(self, text, run_id):
        self._message({'id': f'input:{run_id}', 'role': 'user', 'kind': 'message',
                       'text': self.redact(text), 'run_id': run_id, 'complete': True})
        self._bound()

    def notice(self, text, run_id=''):
        self._counter += 1
        self._message({'id': f'notice:{self._counter}', 'role': 'context', 'kind': 'notice',
                       'text': self.redact(text), 'run_id': run_id, 'complete': True})
        self._bound()

    def reconcile_message(self, item, message_id):
        """提交身份变化只更新该消息元数据，不重新计算聊天正文。"""
        record = self._records.get(id(item))
        if record is not None:
            item['id'], item['complete'] = message_id, True
            self._touch(record)
            self._bound()

    def _run(self, identity, event):
        run = self._run_index.get(identity)
        if run is None:
            # 记忆通知沿用触发它的工作任务身份，展示片段仍需独立归属。
            parent_run_id = event.parent_run_id or (event.run_id if event.purpose == 'memory' else '')
            run = {'id': identity, 'title': '摘要／维护' if event.purpose != 'work' else '任务',
                   'phase': 'running', 'reason': '', 'text': '', 'thinking': '', 'calls': [],
                   'usage': {}, 'usage_by_purpose': {}, 'parent_run_id': parent_run_id}
            self.runs.append(run)
            self._run_index[identity] = run
            self._register(run, 'run', identity)
        return run

    def event(self, event):
        if event.child_event is not None:
            self.event(event.child_event)
            return
        identity = event.run_id or f'maintenance:{event.purpose}'
        if event.purpose != 'work' and event.run_id:
            identity = f'maintenance:{event.purpose}:{event.run_id}'
        run = self._run(identity, event)
        record = self._records[id(run)]
        if event.kind == 'text_delta':
            message_id = f'answer:{identity}:{event.iteration}'
            message = self._message_index.get(message_id)
            if message is None:
                message_record = self._message({'id': message_id, 'role': 'assistant', 'kind': 'message',
                    'text': '', 'run_id': identity, 'complete': False})
                message = message_record.row
            if not message.get('truncated'):
                self._append(self._records[id(message)], 'text', event.text)
        elif event.kind == 'thinking_delta':
            if not run.get('thinking_truncated'):
                self._append(record, 'thinking', event.text, thinking=True)
        elif event.kind == 'tool_call' and event.call is not None:
            key = (identity, event.iteration, event.call.id)
            if key not in self._call_index:
                if len(run['calls']) >= self.call_limit:
                    run['calls_truncated'] = True
                    self.warn('工具索引达到上限；请查看存档，审批仍可正常处理。')
                else:
                    arguments, trimmed = _fit(self._arguments(event.call.arguments), self.detail_limit)
                    call = {'id': f'{identity}:{event.iteration}:{event.call.id}', 'source_id': event.call.id,
                            'run_id': identity, 'iteration': event.iteration, 'name': self.redact(event.call.name),
                            'arguments': arguments, 'status': 'proposed', 'result': None, 'truncated': trimmed}
                    run['calls'].append(call)
                    self._call_index[key] = call
                    call_record = self._register(call, 'call', key, run)
                    self._set(call_record, 'arguments', arguments)
        elif event.kind in {'tool_started', 'tool_result'}:
            call = self._call_index.get((identity, event.iteration, event.tool_call_id))
            if call is not None:
                call_record = self._records[id(call)]
                if event.kind == 'tool_started':
                    call['status'] = 'running'
                elif event.result is not None:
                    value, trimmed = _fit(public_value(event.result.to_dict(), self.redact), self.detail_limit)
                    self._set(call_record, 'result', value)
                    call['truncated'] |= trimmed or isinstance(value, dict) and value.get('truncated', False)
                    error = event.result.error or {}
                    details = error.get('details') or {}
                    code = error.get('code')
                    if event.result.ok:
                        call['status'] = 'completed'
                    elif details.get('side_effects_may_have_occurred') or details.get('side_effects') == 'unknown':
                        call['status'] = 'side_effect_unknown'
                    elif code in {'permission_denied', 'approval_required'}:
                        call['status'] = 'denied'
                    elif code == 'cancelled':
                        call['status'] = 'cancelled'
                    elif code in {'timeout', 'timed_out', 'tool_timeout', 'mcp_timeout'}:
                        call['status'] = 'timed_out'
                    else:
                        call['status'] = 'failed'
                self._touch(call_record)
        elif event.kind == 'usage' and event.usage is not None:
            run['usage_by_purpose'][event.purpose] = usage_value(event.usage)
            run['usage'] = usage_value(event.usage)
        elif event.kind in {'finished', 'task_finished'}:
            run['phase'], run['reason'] = 'completed', event.reason or ''
            if event.usage is not None:
                run['usage_by_purpose'][event.purpose] = usage_value(event.usage)
                run['usage'] = usage_value(event.usage)
            for item in self.messages:
                if item.get('run_id') == identity:
                    item['complete'] = True
                    self._touch(self._records[id(item)])
        elif event.kind in {'display_line', 'hook_notice', 'context_compaction', 'memory_update', 'task_status', 'team_update'}:
            if event.text:
                text, trimmed = _fit(self.redact(event.text), self.detail_limit)
                self._set(record, 'text', text)
                if trimmed:
                    run['text_truncated'] = True
                    self.warn('部分运行提示已省略。')
            if event.phase:
                run['phase'] = event.phase
        if event.warning:
            self.warn(event.warning)
        if event.kind not in {'text_delta', 'thinking_delta'}:
            self._touch(record)
        self._bound()

    def _remove(self, record):
        if id(record.row) not in self._records:
            return
        row = record.row
        if record.kind == 'run':
            for call in tuple(row['calls']):
                self._remove(self._records[id(call)])
            for message in tuple(self.messages):
                if message.get('run_id') == row['id']:
                    self._remove(self._records[id(message)])
            self.runs[:] = [item for item in self.runs if item is not row]
            self._run_index.pop(record.key, None)
        elif record.kind == 'message':
            self.messages[:] = [item for item in self.messages if item is not row]
            self._message_index.pop(record.key, None)
        else:
            record.parent['calls'][:] = [item for item in record.parent['calls'] if item is not row]
            self._call_index.pop(record.key, None)
            record.parent['calls_truncated'] = True
            self._touch(self._records[id(record.parent)])
        for key, slot in record.slots.items():
            if slot.thinking:
                self._thinking_bytes -= slot.size
                self._thinking_slots.pop((id(row), key), None)
            else:
                self._body_bytes -= slot.size
                self._body_slots.pop((id(row), key), None)
        # 外部协调器可能仍持有本轮匹配候选；移出缓存不改写它已读到的正文。
        record.slots.clear()
        self._metadata_bytes -= record.metadata
        self._records.pop(id(row), None)

    def _trim_slots(self, *, thinking=False):
        slots = self._thinking_slots if thinking else self._body_slots
        limit = self.thinking_limit if thinking else self.body_limit
        total = lambda: self._thinking_bytes if thinking else self._body_bytes
        while slots and total() > limit:
            slot = next(iter(slots.values()))
            value, _ = _fit(slot.record.row[slot.key], max(0, slot.size - (total() - limit)))
            self._set(slot.record, slot.key, value, thinking=thinking)
            marker = 'thinking_truncated' if thinking else 'text_truncated' if slot.record.kind == 'run' else 'truncated'
            slot.record.row[marker] = True
            self._touch(slot.record)
            self.warn('部分可展示思考已省略。' if thinking else '部分内容超出临时展示预算；请查看存档中的已保存记录。')

    def _bound(self):
        completed = [run for run in self.runs if run['phase'] == 'completed']
        discard = completed[:-10]
        for run in discard:
            self._remove(self._records[id(run)])
        while len(self.runs) > 11:
            self._remove(self._records[id(self.runs[0])])
            discard = True
        if discard:
            self.warn('临时视图保留当前及最近 10 个完成片段；较早片段已省略，请查看历史。')
        self._trim_slots(thinking=True)
        self._trim_slots()
        while self._metadata_bytes + self._root_bytes > self.metadata_limit and self._records:
            # 优先剪最旧消息／调用，保留仍能容纳的当前运行容器和更新条目。
            record = next((item for item in self._records.values() if item.kind != 'run'),
                          next(iter(self._records.values())))
            self._remove(record)
            self.warn('较早消息或工具元数据已省略；请查看存档。')

    def snapshot(self):
        return deepcopy({'session_id': self.session_id, 'messages': self.messages,
                         'runs': self.runs, 'warnings': self.warnings})
