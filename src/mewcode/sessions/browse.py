"""有界、只读的展示投影；不参与模型恢复或运行资源初始化。"""
import asyncio
import base64
import codecs
from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import threading

from .projection import activity, cache_path, timestamp, validate_payload
from .store import (ID_PATTERN, Journal, SessionError, _check_directories,
                    _close_directories, _directories, _locked, _regular, _root)


PAGE_BYTES = 512 * 1024
CHUNK_BYTES = 128 * 1024
PREVIEW_BYTES = 4096
MAX_IDENTITIES = 65536
MAX_NODES = 65536
MAX_RECORD_MEMORY = 2 * 1024 * 1024


class _Warnings(list):
    def append(self, warning):
        if len(self) < 31:
            super().append(warning)
        elif len(self) == 31:
            super().append('更多存档异常未逐项展示，请检查原始存档。')


class _LongText(str):
    """只保留短预览和 JSON 字符串的文件范围。"""

    def __new__(cls, value, start, end, digest):
        result = super().__new__(cls, value)
        result.start, result.end, result.digest = start, end, digest
        return result


class _Reader:
    def __init__(self, fd, end, offset=0):
        self.fd, self.end, self.pos = fd, end, offset
        self.buffer, self.base = b'', offset

    def peek(self):
        if self.pos >= self.end:
            return b''
        if not self.base <= self.pos < self.base + len(self.buffer):
            self.base = self.pos
            self.buffer = os.pread(self.fd, min(65536, self.end - self.pos), self.pos)
        return self.buffer[self.pos - self.base:self.pos - self.base + 1]

    def take(self, size=1):
        chunks = []
        while size and self.peek():
            count = min(size, len(self.buffer) - (self.pos - self.base))
            chunks.append(self.buffer[self.pos - self.base:self.pos - self.base + count])
            self.pos += count
            size -= count
        if size:
            raise ValueError('记录正文不完整')
        return b''.join(chunks)

    def whitespace(self):
        while self.peek() in (b' ', b'\t', b'\r'):
            self.pos += 1

    def skip_line(self):
        while self.peek():
            index = self.buffer.find(b'\n', self.pos - self.base)
            if index >= 0:
                self.pos = self.base + index + 1
                return
            self.pos = self.base + len(self.buffer)


_SPECIAL = re.compile(br'["\\\x00-\x1f]')


def _string_parts(reader):
    if reader.take() != b'"':
        raise ValueError('JSON 字符串无效')
    decoder = codecs.getincrementaldecoder('utf-8')()
    while reader.peek():
        offset = reader.pos - reader.base
        match = _SPECIAL.search(reader.buffer, offset)
        end = match.start() if match else len(reader.buffer)
        if end > offset:
            data = reader.buffer[offset:end]
            reader.pos += len(data)
            yield decoder.decode(data)
        if match is None:
            continue
        decoder.decode(b'', final=True)
        marker = reader.take()
        if marker == b'"':
            return
        if marker != b'\\':
            raise ValueError('JSON 字符串含控制字符')
        escape = reader.take()
        if escape == b'u':
            raw = b'\\u' + reader.take(4)
            value = json.loads((b'"' + raw + b'"').decode('ascii'))
            if 0xD800 <= ord(value) <= 0xDBFF and reader.peek() == b'\\':
                position = reader.pos
                candidate = reader.take(6)
                try:
                    second = json.loads((b'"' + candidate + b'"').decode('ascii'))
                    if len(second) == 1 and 0xDC00 <= ord(second) <= 0xDFFF:
                        value = chr(0x10000 + ((ord(value) - 0xD800) << 10) + ord(second) - 0xDC00)
                    else:
                        reader.pos = position
                except (ValueError, UnicodeError):
                    reader.pos = position
            yield value
        else:
            yield json.loads((b'"\\' + escape + b'"').decode('ascii'))
    raise ValueError('JSON 字符串不完整')


class _Parser:
    """逐块验证 JSON，字符串正文不会随着记录大小无限驻留。"""

    def __init__(self, reader):
        self.reader, self.nodes, self.memory = reader, 0, 0

    def value(self, depth=0):
        self.nodes += 1
        if self.nodes > MAX_NODES or depth > 48:
            raise ValueError('记录结构超出有界浏览范围')
        reader = self.reader
        reader.whitespace()
        marker = reader.peek()
        if marker == b'"':
            start, parts, size = reader.pos, [], 0
            digest = hashlib.sha256()
            for part in _string_parts(reader):
                data = part.encode('utf-8', errors='replace')
                digest.update(data)
                if size < PREVIEW_BYTES:
                    parts.append(data[:PREVIEW_BYTES - size].decode('utf-8', errors='ignore'))
                size += len(data)
            text = ''.join(parts)
            self.memory += len(text.encode('utf-8'))
            if self.memory > MAX_RECORD_MEMORY:
                raise ValueError('记录结构超出有界浏览范围')
            return _LongText(text, start, reader.pos, digest.hexdigest()) if size > PREVIEW_BYTES else text
        if marker in (b'[', b'{'):
            reader.pos += 1
            is_object = marker == b'{'
            closing = b'}' if is_object else b']'
            result = {} if is_object else []
            reader.whitespace()
            if reader.peek() == closing:
                reader.pos += 1
                return result
            while True:
                if is_object:
                    if reader.peek() != b'"':
                        raise ValueError('JSON 对象键无效')
                    key = self.value(depth + 1)
                    if isinstance(key, _LongText) or key in result:
                        raise ValueError('JSON 对象键重复或超长')
                    reader.whitespace()
                    if reader.take() != b':':
                        raise ValueError('JSON 对象无效')
                    result[key] = self.value(depth + 1)
                else:
                    result.append(self.value(depth + 1))
                reader.whitespace()
                separator = reader.take()
                if separator == closing:
                    return result
                if separator != b',':
                    raise ValueError('JSON 集合无效')
                reader.whitespace()
        token = bytearray()
        while reader.peek() and reader.peek() not in (b',', b']', b'}', b' ', b'\r', b'\t', b'\n'):
            token.extend(reader.take())
            if len(token) > 128:
                raise ValueError('JSON 标量超长')
        value = json.loads(token)
        if isinstance(value, float) and (value != value or abs(value) == float('inf')):
            raise ValueError('JSON 数值无效')
        return value


def _plain(value):
    if isinstance(value, str):
        return str(value)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _fingerprint(value):
    def compact(item):
        if isinstance(item, _LongText):
            return {'long_text_sha256': item.digest}
        if isinstance(item, list):
            return [compact(child) for child in item]
        if isinstance(item, dict):
            return {key: compact(child) for key, child in item.items()}
        return item
    return hashlib.sha256(json.dumps(compact(value), sort_keys=True).encode()).hexdigest()


def _limit(value):
    if type(value) is not int or value <= 0:
        raise SessionError('分页大小必须为正整数')
    return min(value, 50)


class ArchiveBrowser:
    """服务实例拥有签名密钥；所有文件读取在线程中执行。"""

    def __init__(self, root, secret=''):
        self.root = _root(root)
        self.secret = secret
        self._key = secrets.token_bytes(32)
        info = self.root.stat()
        self._root_identity = (info.st_dev, info.st_ino)
        self._lists = OrderedDict()
        self._lists_lock = threading.Lock()

    async def list_sessions(self, cursor=None, limit=50):
        return await asyncio.to_thread(self._list_sessions, cursor, limit)

    async def create(self, protocol, model):
        return await asyncio.to_thread(self._create, protocol, model)

    async def history(self, identity, cursor=None, limit=50):
        return await asyncio.to_thread(self._history, identity, cursor, limit)

    async def result(self, result_id, cursor=None):
        return await asyncio.to_thread(self._result, result_id, cursor)

    def _check_root(self):
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != self._root_identity:
            raise SessionError('项目真实根目录发生变化')

    def _sign(self, purpose, value):
        raw = json.dumps({'purpose': purpose, **value}, separators=(',', ':')).encode()
        signature = hmac.new(self._key, raw, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(signature + raw).decode().rstrip('=')

    def _verify(self, token, purpose):
        try:
            if not isinstance(token, str) or not token or len(token) > 16384:
                raise ValueError
            raw = base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True)
            if len(raw) < 33 or not hmac.compare_digest(raw[:32], hmac.new(self._key, raw[32:], hashlib.sha256).digest()):
                raise ValueError
            value = json.loads(raw[32:])
            if value.pop('purpose') != purpose:
                raise ValueError
            return value
        except (ValueError, TypeError, KeyError):
            raise SessionError('浏览身份或游标无效、已过期或不属于本次服务') from None

    @contextmanager
    def _archive(self, identity):
        descriptors, fd = [], None
        try:
            if not isinstance(identity, str) or not ID_PATTERN.fullmatch(identity):
                raise SessionError('会话 ID 格式无效')
            self._check_root()
            descriptors = _directories(self.root)
            fd = os.open(identity + '.jsonl', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=descriptors[-1])
            _regular(fd)
            _check_directories(self.root, descriptors)
            yield fd
            _check_directories(self.root, descriptors)
            actual = os.stat(identity + '.jsonl', dir_fd=descriptors[-1], follow_symlinks=False)
            owned = os.fstat(fd)
            if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (owned.st_dev, owned.st_ino):
                raise SessionError('会话存档真实路径发生变化')
        except SessionError:
            raise
        except (OSError, ValueError):
            raise SessionError('会话存档不可用或路径边界不安全') from None
        finally:
            if fd is not None:
                os.close(fd)
            _close_directories(descriptors)

    def _snapshot(self, fd, previous=None):
        info = os.fstat(fd)
        end = previous['end'] if previous else info.st_size
        if previous and (previous['device'], previous['inode']) != (info.st_dev, info.st_ino):
            raise SessionError('存档已替换，原浏览游标失效')
        if end > info.st_size:
            raise SessionError('存档已缩短，原浏览游标失效')
        partial = False
        if not previous:
            while end:
                start = max(0, end - 65536)
                chunk = os.pread(fd, end - start, start)
                index = chunk.rfind(b'\n')
                if index >= 0:
                    end = start + index + 1
                    break
                end = start
            partial = end != info.st_size
        digest = hashlib.sha256()
        for offset in range(0, end, 65536):
            data = os.pread(fd, min(65536, end - offset), offset)
            if not data:
                raise SessionError('存档读取期间发生变化')
            digest.update(data)
        value = {'device': info.st_dev, 'inode': info.st_ino, 'end': end,
                 'digest': digest.hexdigest(), 'partial': previous['partial'] if previous else partial}
        if previous and value != previous:
            raise SessionError('存档内容已变化，原浏览游标失效')
        return value

    def _create(self, protocol, model):
        self._check_root()
        journal = Journal.create(self.root, protocol, model)
        try:
            return {'session_id': journal.id}
        finally:
            journal.close()

    def _records(self, fd, identity, snapshot, warnings):
        reader = _Reader(fd, snapshot['end'])
        known, last_seq, header = {}, 0, None
        if snapshot['partial']:
            warnings.append('存档尾部存在不完整行，本次仅展示此前完整记录。')
        while reader.pos < reader.end:
            start = reader.pos
            try:
                reader.whitespace()
                if reader.peek() == b'\n':
                    reader.pos += 1
                    continue
                record = _Parser(reader).value()
                reader.whitespace()
                if reader.take() != b'\n':
                    raise ValueError('记录边界无效')
                if (not isinstance(record, dict) or type(record.get('schema_version')) is not int
                        or record['schema_version'] != 1 or type(record.get('seq')) is not int
                        or record['seq'] <= 0 or not isinstance(record.get('record_id'), str)
                        or not 0 < len(record['record_id']) <= 128 or isinstance(record['record_id'], _LongText)
                        or not isinstance(record.get('kind'), str)):
                    raise ValueError('记录版本、序号或身份无效')
                timestamp(record.get('timestamp'))
                validate_payload(record['kind'], _plain(record.get('payload')), identity, record['seq'])
                fingerprint = _fingerprint(record)
                if record['record_id'] in known:
                    if known[record['record_id']] != fingerprint:
                        raise ValueError('记录身份重复冲突')
                    continue
                if record['seq'] <= last_seq:
                    raise ValueError('记录顺序号重复或倒退')
                if len(known) >= MAX_IDENTITIES:
                    warnings.append('记录身份索引达到上限，后续内容未展示。')
                    break
                known[record['record_id']], last_seq = fingerprint, record['seq']
                if header is None:
                    candidate = record['payload']
                    if (record['kind'] != 'session_created' or candidate.get('session_id') != identity
                            or candidate.get('project_root') != str(self.root)
                            or any(not isinstance(candidate.get(key), str) or not candidate[key] for key in ('protocol', 'model'))):
                        raise SessionError('会话身份或真实项目根不匹配')
                    try:
                        timestamp(candidate.get('created_at'))
                    except ValueError:
                        raise SessionError('会话创建时间无效') from None
                    header = candidate
                elif record['kind'] == 'session_created' and record['payload'] != header:
                    raise SessionError('会话身份记录冲突')
                record['_start'] = start
                yield record
            except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
                if len(warnings) < 32:
                    warnings.append(f'存档字节 {start} 处记录无效，已跳过。')
                if reader.pos > start and os.pread(fd, 1, reader.pos - 1) != b'\n':
                    reader.skip_line()
        if header is None:
            raise SessionError('会话缺少有效身份记录')

    def _safe(self, value):
        if isinstance(value, str):
            return str(value).replace(self.secret, '[已隐藏]') if self.secret else str(value)
        if isinstance(value, list):
            return [self._safe(item) for item in value]
        if isinstance(value, dict):
            return {key: self._safe(item) for key, item in value.items()
                    if key.lower() not in {'provider_content', 'signature', 'encrypted_content',
                                          'api_key', 'authorization', 'password', 'access_token',
                                          'refresh_token', 'client_secret'}}
        return value

    def _message(self, message, record, identity, snapshot, selector, run_id, kind=None):
        role = message['role']
        item = {'id': message['id'] if not run_id else run_id + ':' + message['id'],
                'kind': kind or ('context' if role == 'context' else 'message'),
                'role': role, 'text': self._safe(message['content']), 'seq': record['seq'], 'run_id': run_id}
        # 主运行的消息身份保持存档身份，子运行额外使用所属运行命名空间。
        if not selector or selector[0] != 'child':
            item['id'] = message['id']
        if role == 'context':
            item['kind'] = message['context_kind'] or 'context'
        if message['tool_calls']:
            item['call'] = self._safe(message['tool_calls'])
        if role == 'tool':
            item['kind'] = 'tool_result'
            item['result'] = self._safe(message['tool_result'])
            item['call'] = {'id': message['tool_call_id']}
            if not item['text']:
                item['text'] = json.dumps(item['result'], ensure_ascii=False)[:PREVIEW_BYTES]
        def large(value):
            if isinstance(value, _LongText):
                return True
            if isinstance(value, (list, dict)):
                return any(large(child) for child in (value.values() if isinstance(value, dict) else value))
            return False
        public = {key: value for key, value in message.items() if key != 'provider_content'}
        oversized = large(public) or len(json.dumps(item, ensure_ascii=False).encode()) > 32 * 1024
        if oversized or message['cache_path']:
            token = self._sign('result', {'session_id': identity, 'snapshot': snapshot,
                'record': record['_start'], 'selector': selector, 'message_id': message['id']})
            if role != 'tool':
                item['result'] = {}
            item['result']['result_id'] = token
            item['truncated'] = True
            if oversized and len(json.dumps(item, ensure_ascii=False).encode()) > 32 * 1024:
                item['call'] = {'truncated': True}
                item['result'] = {'result_id': token, 'truncated': True}
        return item

    def _timeline(self, fd, identity, snapshot, warnings, metadata):
        seen, pending, committed = {}, {}, {}
        run_id = ''

        def messages(record, values, selector, scope, kind=None):
            for index, message in enumerate(values):
                key = (scope, message['id'])
                fingerprint = _fingerprint(message)
                if key in seen:
                    if seen[key] != fingerprint and len(warnings) < 32:
                        warnings.append('重复消息身份冲突，冲突正文未展示。')
                    continue
                if len(seen) >= MAX_IDENTITIES:
                    raise SessionError('消息身份索引达到浏览上限')
                seen[key] = fingerprint
                if message['role'] != 'context':
                    metadata['message_count'] += 1
                yield self._message(message, record, identity, snapshot, [*selector, index], scope or run_id, kind)

        def finish(scope):
            group = pending.pop(scope, None)
            if group is None:
                return
            calls = group['messages'][-1]['tool_calls']
            missing = any(call['id'] not in group['results'] for call in calls)
            if missing and len(warnings) < 32:
                warnings.append('工具交互未完整提交；仅展示已保存证据，未确认操作可能已有副作用。')
            yield from messages(group['record'], group['messages'], group['selector'], scope,
                                'uncommitted' if missing else None)
            for call in calls:
                result = group['results'].get(call['id'])
                if result:
                    record, message, selector = result
                    yield from messages(record, [message], selector, scope)
            if not missing:
                committed[(scope, group['id'])] = _fingerprint([*group['messages'],
                    *(group['results'][call['id']][1] for call in calls)])

        for record in self._records(fd, identity, snapshot, warnings):
            kind, payload, scope = record['kind'], record['payload'], ''
            if activity(record):
                metadata['last_activity'] = max(metadata['last_activity'], record['timestamp'])
            if kind == 'task_started':
                run_id = payload.get('run_id', payload.get('task_id', ''))
                if metadata['title'] == '未命名会话':
                    metadata['title'] = next((line.strip()[:60] for line in payload['input'].splitlines() if line.strip()), '未命名会话')
            if kind == 'run_started':
                run_id = payload.get('run_id', run_id)
            prefix = []
            if kind == 'child_event':
                scope, kind, payload = payload['run_id'], payload['kind'], payload['payload']
                prefix = ['child']
            if kind in {'task_started', 'run_started'}:
                message = self._task_message(record, payload, kind)
                yield self._message(message, record, identity, snapshot, [*prefix, 'task_input'], scope or run_id)
            if kind == 'interaction_started':
                yield from finish(scope)
                if len(pending) >= 32:
                    warnings.append('并行交互证据达到保留上限，后续交互未展示。')
                    break
                pending[scope] = {'id': payload['interaction_id'], 'messages': payload['messages'],
                    'results': {}, 'record': record, 'selector': [*prefix, 'messages']}
            elif kind == 'tool_result':
                group = pending.get(scope)
                message = payload['message']
                if (group is None or group['id'] != payload['interaction_id']
                        or message['tool_call_id'] not in {call['id'] for call in group['messages'][-1]['tool_calls']}):
                    if len(warnings) < 32:
                        warnings.append('孤立或归属不匹配的工具结果未展示。')
                    continue
                previous = group['results'].get(message['tool_call_id'])
                if previous and _fingerprint(previous[1]) != _fingerprint(message):
                    warnings.append('工具结果重复冲突，冲突正文未展示。')
                    continue
                group['results'][message['tool_call_id']] = record, message, [*prefix, 'message']
            elif kind == 'history_commit':
                group = pending.get(scope)
                batch = payload.get('interaction_id')
                valid = True
                if group and batch == group['id']:
                    calls = group['messages'][-1]['tool_calls']
                    expected = [*group['messages'], *(group['results'].get(call['id'], (None, None))[1] for call in calls)]
                    valid = None not in expected and _fingerprint(expected) == _fingerprint(payload['messages'])
                yield from finish(scope)
                previous = committed.get((scope, batch)) if batch else None
                if not valid or (previous and previous != _fingerprint(payload['messages'])):
                    warnings.append('历史提交与已保存工具证据冲突，该提交未展示。')
                    continue
                yield from messages(record, payload['messages'], [*prefix, 'messages'], scope)
                if batch:
                    committed[(scope, batch)] = _fingerprint(payload['messages'])
            elif kind in {'checkpoint', 'history_checkpoint'}:
                yield from finish(scope)
                if payload.get('reset'):
                    yield {'id': record['record_id'], 'kind': 'reset', 'role': 'context',
                           'text': '会话上下文已重置；此前记录仅用于历史展示。', 'seq': record['seq'], 'run_id': scope or run_id}
                summary = [message for message in payload['history'] if message['context_kind'] in {'summary', 'boundary'}]
                if summary:
                    for index, message in enumerate(payload['history']):
                        if message in summary:
                            yield from messages(record, [message], [*prefix, 'history_one', index], scope)
            elif kind in {'task_finished', 'run_finished', 'maintenance_finished'}:
                yield from finish(scope)
                labels = {'task_finished': '任务已结束', 'run_finished': '运行已结束',
                          'maintenance_finished': '会话维护已结束'}
                yield {'id': record['record_id'], 'kind': kind, 'role': 'context',
                       'text': labels[kind] + ('：' + self._safe(payload['status']) if isinstance(payload.get('status'), str) else ''),
                       'seq': record['seq'], 'run_id': scope or run_id}
        for scope in list(pending):
            yield from finish(scope)

    def _task_message(self, record, payload, kind):
        return {'id': record['record_id'], 'role': 'context', 'context_kind': kind,
                'content': payload.get('input', '运行已开始'), 'tool_calls': [],
                'tool_result': None, 'cache_path': ''}

    def _metadata(self, identity):
        return {'id': identity, 'title': '未命名会话', 'last_activity': '', 'message_count': 0,
                'active': False, 'recoverable': False, 'warnings': _Warnings()}

    def _history(self, identity, cursor, limit):
        limit = _limit(limit)
        value = self._verify(cursor, 'history') if cursor else None
        if value and value['session_id'] != identity:
            raise SessionError('历史游标不属于该会话')
        warnings, items, used, next_cursor = _Warnings(), [], 0, None
        with self._archive(identity) as fd:
            snapshot = self._snapshot(fd, value['snapshot'] if value else None)
            after = value['after'] if value else 0
            metadata = self._metadata(identity)
            timeline = self._timeline(fd, identity, snapshot, warnings, metadata)
            try:
                for index, item in enumerate(timeline):
                    if index < after:
                        continue
                    size = len(json.dumps(item, ensure_ascii=False).encode())
                    if len(items) >= limit or used + size > PAGE_BYTES - 8192:
                        next_cursor = self._sign('history', {'session_id': identity, 'snapshot': snapshot, 'after': index})
                        break
                    items.append(item)
                    used += size
            finally:
                timeline.close()
            self._snapshot(fd, snapshot)
        return {'items': items, 'next_cursor': next_cursor, 'warnings': self._safe(warnings[:32])}

    def _list_sessions(self, cursor, limit):
        limit = _limit(limit)
        if cursor:
            value = self._verify(cursor, 'list')
            with self._lists_lock:
                snapshot = self._lists.get(value['snapshot'])
            if snapshot is None:
                raise SessionError('会话列表快照已过期，请重新加载列表')
            offset = value['offset']
        else:
            self._check_root()
            descriptors = []
            try:
                try:
                    descriptors = _directories(self.root)
                except FileNotFoundError:
                    return {'items': [], 'next_cursor': None}
                with os.scandir(descriptors[-1]) as entries:
                    identities = []
                    for entry in entries:
                        if entry.name.endswith('.jsonl') and ID_PATTERN.fullmatch(entry.name[:-6]):
                            identities.append(entry.name[:-6])
                            if len(identities) > 4096:
                                raise SessionError('会话数量超过有界列表上限，请先整理存档')
            except SessionError:
                raise
            except OSError:
                raise SessionError('会话目录不可用或路径边界不安全') from None
            finally:
                _close_directories(descriptors)
            snapshot = []
            for identity in identities:
                item = self._metadata(identity)
                try:
                    with self._archive(identity) as fd:
                        item['active'] = _locked(fd)
                        boundary = self._snapshot(fd)
                        for _ in self._timeline(fd, identity, boundary, item['warnings'], item):
                            pass
                        self._snapshot(fd, boundary)
                        item['recoverable'] = not item['active']
                except SessionError as error:
                    item['warnings'].append(str(error))
                item['title'] = self._safe(item['title'])
                item['warnings'] = self._safe(item['warnings'][:32])
                snapshot.append(item)
            snapshot.sort(key=lambda item: item['id'])
            snapshot.sort(key=lambda item: item['last_activity'], reverse=True)
            key = secrets.token_hex(16)
            with self._lists_lock:
                self._lists[key] = snapshot
                while len(self._lists) > 4:
                    self._lists.popitem(last=False)
            value, offset = {'snapshot': key}, 0
        items = snapshot[offset:offset + limit]
        next_cursor = self._sign('list', {'snapshot': value['snapshot'], 'offset': offset + len(items)}) if offset + len(items) < len(snapshot) else None
        return {'items': items, 'next_cursor': next_cursor}

    @contextmanager
    def _cache(self, identity, message):
        descriptors, fd = [], None
        path = message['cache_path']
        if not cache_path(path, identity):
            raise SessionError('结果缓存不属于该会话')
        try:
            self._check_root()
            descriptors = _directories(self.root, leaf='context')
            descriptors.append(os.open(identity, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=descriptors[-1]))
            if os.fstat(descriptors[-1]).st_uid != os.getuid():
                raise SessionError('结果缓存目录归属无效')
            name = path.rsplit('/', 1)[1]
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptors[-1])
            _regular(fd)
            yield fd
            _check_directories(self.root, descriptors[:3], 'context')
            actual = (self.root / '.mewcode/context' / identity).lstat()
            owned = os.fstat(descriptors[-1])
            if not stat.S_ISDIR(actual.st_mode) or (actual.st_dev, actual.st_ino) != (owned.st_dev, owned.st_ino):
                raise SessionError('结果缓存目录已变化')
            actual, owned = os.stat(name, dir_fd=descriptors[-1], follow_symlinks=False), os.fstat(fd)
            if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (owned.st_dev, owned.st_ino):
                raise SessionError('结果缓存已变化')
        except SessionError:
            raise
        except (OSError, ValueError):
            raise SessionError('结果缓存缺失、已回收或路径边界不安全') from None
        finally:
            if fd is not None:
                os.close(fd)
            _close_directories(descriptors)

    def _cache_parts(self, fd, message, boundary):
        reader = _Reader(fd, boundary['end'])
        header = _Parser(reader).value()
        reader.whitespace()
        if (reader.take() != b'\n' or not isinstance(header, dict)
                or header.get('format') != 'mewcode-result-v1'
                or header.get('source') != message['id']
                or header.get('tool_call_id') != message['tool_call_id']
                or type(header.get('characters')) is not int or header['characters'] < 0):
            raise SessionError('结果缓存来源与消息归属不一致')
        size = 0
        while reader.pos < reader.end:
            value = _Parser(reader).value()
            reader.whitespace()
            if (reader.take() != b'\n' or not isinstance(value, dict) or set(value) != {'chunk'}
                    or not isinstance(value['chunk'], str) or isinstance(value['chunk'], _LongText)):
                raise SessionError('结果缓存正文格式损坏')
            size += len(value['chunk'])
            yield value['chunk']
        if boundary['partial'] or size != header['characters']:
            raise SessionError('结果缓存正文不完整')

    def _public_parts(self, value, fd, end):
        if isinstance(value, str):
            yield '"'
            parts = _string_parts(_Reader(fd, end, value.start)) if isinstance(value, _LongText) else [value]
            for part in self._redacted(parts):
                yield json.dumps(part, ensure_ascii=False)[1:-1]
            yield '"'
        elif isinstance(value, (dict, list)):
            is_object = isinstance(value, dict)
            yield '{' if is_object else '['
            first = True
            for key, child in (value.items() if is_object else enumerate(value)):
                if is_object and key not in self._safe({key: None}):
                    continue
                if not first:
                    yield ','
                first = False
                if is_object:
                    yield json.dumps(key, ensure_ascii=False) + ':'
                yield from self._public_parts(child, fd, end)
            yield '}' if is_object else ']'
        else:
            yield json.dumps(value, ensure_ascii=False)

    def _redacted(self, parts):
        pending = ''
        for part in parts:
            text = pending + part
            pending = ''
            if self.secret:
                text = text.replace(self.secret, '[已隐藏]')
                for count in range(min(len(text), len(self.secret) - 1), 0, -1):
                    if text.endswith(self.secret[:count]):
                        pending, text = text[-count:], text[:-count]
                        break
            yield text.encode('utf-8', errors='replace').decode('utf-8')
        if pending:
            yield pending

    def _body_page(self, parts, offset):
        position, collected, size, more, next_offset = 0, [], 0, False, offset
        for part in self._redacted(parts):
            start, position = position, position + len(part)
            if position <= offset:
                continue
            if more:
                continue
            part = part[max(0, offset - start):]
            raw = part.encode('utf-8')
            if size + len(raw) > CHUNK_BYTES:
                part = raw[:CHUNK_BYTES - size].decode('utf-8', errors='ignore')
                more = True
            collected.append(part)
            size += len(part.encode('utf-8'))
            next_offset += len(part)
        if offset > position:
            raise SessionError('结果分页位置已失效')
        return ''.join(collected), next_offset if more else None

    def _result(self, result_id, cursor):
        value = self._verify(result_id, 'result')
        page = self._verify(cursor, 'result-page') if cursor else None
        token_hash = hashlib.sha256(result_id.encode()).hexdigest()
        if page and page['result'] != token_hash:
            raise SessionError('结果游标不属于该结果')
        identity, offset = value['session_id'], page['offset'] if page else 0
        with self._archive(identity) as fd:
            snapshot = self._snapshot(fd, value['snapshot'])
            try:
                reader = _Reader(fd, snapshot['end'], value['record'])
                record = _Parser(reader).value()
                validate_payload(record['kind'], _plain(record['payload']), identity, record['seq'])
                payload = record['payload']
                selector = list(value['selector'])
                if selector[0] == 'child':
                    payload = payload['payload']
                    selector.pop(0)
                field = selector.pop(0)
                if field == 'task_input':
                    message = self._task_message(record, payload, record['kind'])
                elif field == 'message':
                    message = payload['message']
                elif field == 'history_one':
                    message = payload['history'][selector[0]]
                else:
                    message = payload[field][selector[0]]
                if message['id'] != value['message_id']:
                    raise SessionError('结果消息身份已变化')
                boundary = None
                if message['cache_path']:
                    with self._cache(identity, message) as cache_fd:
                        boundary = self._snapshot(cache_fd, page['cache'] if page else None)
                        content, following = self._body_page(self._cache_parts(cache_fd, message, boundary), offset)
                        self._snapshot(cache_fd, boundary)
                else:
                    body = message['tool_result'] if message['role'] == 'tool' else message['content']
                    if message['tool_calls']:
                        body = {'text': message['content'], 'calls': message['tool_calls']}
                    if isinstance(body, _LongText):
                        parts = _string_parts(_Reader(fd, snapshot['end'], body.start))
                    elif isinstance(body, str):
                        parts = [body]
                    else:
                        parts = self._public_parts(body, fd, snapshot['end'])
                    content, following = self._body_page(parts, offset)
                self._snapshot(fd, snapshot)
            except (ValueError, TypeError, KeyError, IndexError, UnicodeError):
                raise SessionError('登记结果结构损坏或归属无效') from None
        next_cursor = self._sign('result-page', {'result': token_hash, 'offset': following, 'cache': boundary}) if following is not None else None
        return {'id': result_id, 'session_id': identity, 'content': content, 'next_cursor': next_cursor,
                'truncated': bool((message['tool_result'] or {}).get('truncated')), 'warnings': []}
