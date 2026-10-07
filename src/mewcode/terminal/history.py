"""有界的展示轮次与调用身份，不拥有当前执行控制或会话消息。"""

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json

from .details import DetailEntry, DetailStore
from .text import terminal_text


def bounded_id(value):
    """超长标识保留前缀与完整值指纹，防止身份索引随正文增长。"""
    value = str(value or '')
    if len(value) <= 512:
        return value
    return value[:447] + '…' + hashlib.sha256(value.encode('utf-8')).hexdigest()


@dataclass
class CallRecord:
    """只保存有界元信息，参数和结果引用同一份正文条目。"""

    id: str
    number: int
    name: str
    original_id: str
    source: str = ''
    operation: str = ''
    status: str = '已接收'
    arguments: DetailEntry | None = None
    result: DetailEntry | None = None
    attention: bool = False
    original_result_truncated: bool = False
    references: dict = field(default_factory=dict)
    _identifier: str = ''
    _metadata_bytes: int = 0
    _active_metadata: int = 0


@dataclass
class ThoughtRecord:
    """按请求关联 API 思考，无正文副本。"""

    id: str
    name: str
    source: str
    entry: DetailEntry


@dataclass
class TurnRecord:
    """一个主执行段；父任务最终状态独立显示。"""

    id: str
    title: str
    started_at: object
    source: str = ''
    parent_id: str = ''
    run_id: str = ''
    status: str = '运行中'
    finished: bool = False
    reason: str = ''
    total_calls: int = 0
    index_evicted: bool = False
    _calls: OrderedDict = field(default_factory=OrderedDict, repr=False)
    _details: DetailStore | None = field(default=None, repr=False)
    unindexed_events: int = 0
    total_calls_complete: bool = True
    _seen: set = field(default_factory=set, repr=False)

    @property
    def calls(self):
        return tuple(self._calls.values())

    @property
    def thoughts(self):
        if self._details is None:
            return ()
        return tuple(ThoughtRecord(hashlib.sha256(repr(key).encode('utf-8')).hexdigest(),
                                   entry.title, entry.title.split(' · ')[0], entry)
                     for key, entry in self._details.entries.items()
                     if entry.owner == self.id and entry.thinking)


class TerminalHistory:
    """当前主段加最近十个已结束段，共享正文和有界索引预算。"""

    def __init__(self, secret='', *, finished_limit=10, call_limit=1024,
                 metadata_limit=1024 * 1024, tombstone_limit=4096):
        self.secret = secret
        self.finished_limit, self.call_limit, self.metadata_limit = finished_limit, call_limit, metadata_limit
        self.tombstone_limit = tombstone_limit
        self.details = DetailStore(secret)
        self.details.metadata_enforcer = self._enforce_metadata
        self._turns = OrderedDict()
        self._runs = {}
        self._current_id = None
        self._serial = 0
        self._metadata_bytes = 0

    @property
    def turns(self):
        return tuple(self._turns.values())

    @property
    def current(self):
        return self.get(self._current_id)

    @property
    def metadata_bytes(self):
        # 思考标题与脱敏流尾缀也计入索引预算，正文不在此重复计数。
        thought_bytes = sum(2048 + 2 * len(entry.title.encode('utf-8')) +
                            2 * len(str(key).encode('utf-8'))
                            for key, entry in self.details.entries.items() if entry.thinking)
        return self._metadata_bytes + thought_bytes

    def get(self, identity):
        return self._turns.get(identity)

    def for_run(self, run_id):
        return self.get(self._runs.get(bounded_id(run_id)))

    def begin(self, *, title='', started_at=None, source='', parent_id=''):
        if self.current is not None and not self.current.finished:
            self.close(reason='interrupted', status='执行段结束（终态未知）')
        self._serial += 1
        identity = f'turn-{self._serial}'
        safe = lambda text: terminal_text(str(text), self.secret, limit=512)
        turn = TurnRecord(identity, '自动接续' if source == '自动接续' else safe(title or '工作任务'),
                          started_at if started_at is not None else datetime.now(timezone.utc).isoformat(timespec='seconds'),
                          safe(source), safe(bounded_id(parent_id)), _details=self.details)
        self._turns[identity] = turn
        self._current_id = identity
        self._metadata_bytes += self._turn_size(turn)
        self._enforce_metadata()
        return turn

    @staticmethod
    def _turn_size(turn):
        return 2048 + 128 * len(turn._seen) + sum(len(str(value).encode('utf-8')) for value in
                          (turn.id, turn.title, turn.started_at, turn.source, turn.parent_id, turn.run_id, turn.reason))

    def bind(self, run_id):
        turn = self.current
        if turn is None:
            return
        before = self._turn_size(turn)
        turn.run_id = terminal_text(bounded_id(run_id), self.secret, limit=600)
        self._runs[bounded_id(run_id)] = turn.id
        self._metadata_bytes += self._turn_size(turn) - before
        self._enforce_metadata()

    def close(self, *, status='执行段结束', reason='', turn_id=None):
        turn = self.get(turn_id) if turn_id is not None else self.current
        if turn is None or turn.finished:
            return
        before = self._turn_size(turn)
        turn.status, turn.reason = status, terminal_text(reason, self.secret, limit=128)
        turn.finished = True
        for call in turn.calls:
            if call.result is None:
                call.status = f'{status}，终态未知（{call.status}）'
                call.attention = True
                self._refresh_call_size(call)
        turn._seen.clear()
        self._metadata_bytes += self._turn_size(turn) - before
        self.details.finish_owner(turn.id)
        finished = [item for item in self.turns if item.finished]
        for item in finished[:-self.finished_limit] if self.finished_limit else finished:
            self._remove_turn(item)
        self._enforce_metadata()

    def _remove_turn(self, turn):
        self._turns.pop(turn.id, None)
        for run, owner in list(self._runs.items()):
            if owner == turn.id:
                del self._runs[run]
        self._metadata_bytes -= self._turn_size(turn) + sum(call._metadata_bytes for call in turn.calls)
        self.details.remove_owner(turn.id)
        if self._current_id == turn.id:
            self._current_id = next(reversed(self._turns), None)

    def call_for(self, turn, identifier):
        return turn._calls.get(bounded_id(identifier)) if turn else None

    def record(self, event):
        """只更新可确认归属；已结束段不接收新调用身份。"""
        turn = self.for_run(event.run_id)
        if turn is None:
            return None
        identifier = bounded_id(event.tool_call_id or (event.call.id if event.call else ''))
        if not identifier:
            return None
        call = turn._calls.get(identifier)
        if call is None:
            fingerprint = hashlib.sha256(identifier.encode('utf-8')).digest()
            if turn.finished or event.kind not in {'tool_call', 'tool_started', 'permission_requested', 'permission_resolved', 'tool_result'} or fingerprint in turn._seen:
                return None
            # 未知迟到结果在结束段已被挡住；当前段的无起始结果仍是合法快照。
            if len(turn._seen) >= self.tombstone_limit or self._turn_size(turn) + 128 > self.metadata_limit:
                turn.index_evicted = True
                turn.total_calls_complete = False
                turn.unindexed_events += 1
                return None
            turn._seen.add(fingerprint)
            self._metadata_bytes += 128
            turn.total_calls += 1
            source = terminal_text(event.skill_name, self.secret, limit=128)
            original = identifier.rsplit(':', 1)[-1] if source else identifier
            safe_id = terminal_text(identifier, self.secret, limit=600)
            selection_id = hashlib.sha256(identifier.encode('utf-8')).hexdigest()
            call = CallRecord(f'{turn.id}:{selection_id}:{safe_id}', turn.total_calls,
                              terminal_text(event.tool_name or (event.call.name if event.call else ''), self.secret, limit=128),
                              terminal_text(original, self.secret, limit=600), source, _identifier=identifier)
            turn._calls[identifier] = call
        if event.call is not None and call.arguments is None:
            call.arguments = self.details.put((turn.id, identifier + ':arguments'),
                f'调用 #{call.number} · {call.id} · 参数', event.call.arguments, owner=turn.id)
            try:
                arguments = json.loads(call.arguments.body) if not call.arguments.truncated else {}
                call.operation = next((terminal_text(arguments[key], self.secret, limit=180)
                    for key in ('path', 'command', 'pattern') if isinstance(arguments, dict) and isinstance(arguments.get(key), str)), '')
            except (ValueError, RecursionError):
                call.operation = '参数快照不完整'
        if event.kind == 'tool_result' and event.result is not None and call.result is None:
            result = event.result
            data = result.data if isinstance(result.data, dict) else {}
            call.references = {key: value if isinstance(value, (int, bool, type(None))) else terminal_text(str(value), self.secret, limit=512)
                               for key, value in data.items() if key in {'path', 'cache_path', 'cache_paths', 'output_file',
                                'permission_limited', 'skipped_files', 'side_effects_may_have_occurred', 'exit_code'}}
            error = result.error or {}
            error_details = error.get('details') or {}
            for flag in ('not_started', 'side_effects_may_have_occurred'):
                if flag in error_details:
                    call.references[flag] = bool(error_details[flag])
            code = str(error.get('code', 'unknown'))
            call.status = '成功' if result.ok else {'cancelled': '取消', 'timeout': '超时', 'permission_denied': '权限拒绝',
                'permission_blacklisted': '权限拒绝', 'permission_protected': '权限拒绝'}.get(code, '失败')
            call.original_result_truncated = result.truncated
            call.attention |= bool(not result.ok or result.truncated or data.get('permission_limited') or
                data.get('side_effects_may_have_occurred') or (error.get('details') or {}).get('side_effects_may_have_occurred'))
            call.result = self.details.put((turn.id, identifier + ':result'),
                f'调用 #{call.number} · {call.id} · {call.status}' +
                ('\n引用> ' + json.dumps(call.references, ensure_ascii=False) if call.references else ''),
                result.to_json(), owner=turn.id)
            call.attention |= call.result.truncated
        elif call.result is None and not turn.finished:
            if event.kind == 'tool_started':
                call.status = '执行中'
            elif event.kind == 'permission_requested' and call.status != '执行中':
                call.status = '等待授权'
            elif event.kind == 'permission_resolved' and call.status != '执行中':
                call.status = '已拒绝，未启动' if event.permission_decision == 'deny' else '已批准，未启动'
                call.attention |= event.permission_decision == 'deny'
        call.attention |= bool(event.warning)
        self._refresh_call_size(call)
        while len(turn._calls) > self.call_limit:
            self._remove_call(turn, next(iter(turn._calls.values())))
        self._enforce_metadata()
        return call if identifier in turn._calls else None

    def _refresh_call_size(self, call):
        # 预留当前状态简短参数、结果、授权集合与容器成本，避免另一份状态逃逸预算。
        amount = 1024 + call._active_metadata + sum(2 * len(str(value).encode('utf-8')) for value in
            (call.id, call.original_id, call.name, call.source, call.operation, call.status, call.references,
             call.arguments.title if call.arguments else '', call.result.title if call.result else ''))
        self._metadata_bytes += amount - call._metadata_bytes
        call._metadata_bytes = amount

    def _remove_call(self, turn, call):
        turn._calls.pop(call._identifier, None)
        self._metadata_bytes -= call._metadata_bytes
        turn.index_evicted = True
        for suffix in (':arguments', ':result'):
            self.details.remove(('tool', (turn.id, call._identifier + suffix)))

    def _enforce_metadata(self):
        # 历史先移出，当前再移出最早调用；身份数量范围内保留轮次摘要。
        for turn in sorted(self.turns, key=lambda item: not item.finished):
            while turn._calls and self.metadata_bytes > self.metadata_limit:
                self._remove_call(turn, next(iter(turn._calls.values())))
            if self.metadata_bytes <= self.metadata_limit:
                break
        if self.metadata_bytes > self.metadata_limit:
            for key, entry in list(self.details.entries.items()):
                if entry.thinking and self.metadata_bytes > self.metadata_limit:
                    self.details.remove(key)
                    turn = self.get(entry.owner)
                    if turn:
                        turn.index_evicted = True

    def text(self, turn_id=None, section='tools'):
        turn = self.get(turn_id) if turn_id else self.current
        if turn is None:
            return '暂无调用详情' if section == 'tools' else '暂无 API 思考记录'
        notice = f'部分索引已移出 · 累计{"至少" if not turn.total_calls_complete else ""} {turn.total_calls} 次调用' + (f' · {turn.unindexed_events} 条事件无法确认新身份' if turn.unindexed_events else '') + '\n' if turn.index_evicted else ''
        return notice + self.details.text(section, owner=turn.id)

    def clear(self):
        self.details.clear()
        self._turns.clear()
        self._runs.clear()
        self._current_id = None
        self._metadata_bytes = 0
