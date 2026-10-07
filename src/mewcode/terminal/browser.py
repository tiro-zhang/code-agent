"""F2 的只读层级浏览器，选择与当前任务、聊天输入独立。"""

from datetime import datetime
import json

from prompt_toolkit.utils import get_cwidth

from .result_view import format_result
from .text import terminal_text


class DetailsBrowser:
    """只浏览已有内存快照，使用稳定身份和逻辑正文锚点。"""

    def __init__(self, history):
        self.history = history
        self.is_open = False
        self.view = 'call_list'
        self.section = 'tools'
        self.turn_id = self.call_id = None
        self._turn_cursor = None
        self.anchor = (0, 0)
        self.raw = False
        self.searching = False
        self.search_text = ''
        self._query = ''
        self.exception_only = False
        self.notice = ''
        self._size = (80, 10)
        self._selections = {}
        self._anchors = {}

    def _safe(self, value):
        return terminal_text(str(value), self.history.secret)

    def _turn(self):
        return self.history.get(self.turn_id)

    def _remember(self):
        if self.turn_id:
            self._selections[(self.turn_id, self.section)] = (self.call_id, self.anchor, self.raw, dict(self._anchors))
        self._anchors[self.raw] = self.anchor

    def _restore(self):
        saved = self._selections.get((self.turn_id, self.section))
        self.call_id, self.anchor, self.raw, self._anchors = saved if saved else (None, (0, 0), False, {})

    def _sync(self):
        turns = self.history.turns
        valid = {turn.id for turn in turns}
        self._selections = {key: value for key, value in self._selections.items() if key[0] in valid}
        if self.turn_id not in valid:
            if self.turn_id is not None:
                self.notice = '所选轮次已移出（保留数量／容量限制），选择最近可用轮次'
            turn = self.history.current or (turns[-1] if turns else None)
            self.turn_id = turn.id if turn else None
            self.call_id = None
            self.view = 'call_list'
            self.anchor = (0, 0)
            self._anchors.clear()
            self._query = ''
        if self._turn_cursor not in valid:
            self._turn_cursor = self.turn_id
        records = self._records(filtered=False)
        if self.call_id is not None and not any(record.id == self.call_id for record in records):
            self.notice = '所选调用索引已移出（容量限制），保留内容不可恢复'
            self.call_id = None
            self.view = 'call_list'
            self.anchor = (0, 0)
        if self.call_id is None and records:
            self.call_id = records[0].id

    def open(self):
        """首次定位当前轮，再次打开恢复仍有效的浏览位置。"""
        self._sync()
        self.is_open = True

    def close(self):
        """关闭面板但保留选择，丢弃未提交搜索和分页副本。"""
        self.is_open = False
        self.searching = False
        self.search_text = ''
        self.history.details.clear_pages()

    def _records(self, *, filtered=True):
        turn = self._turn()
        if turn is None:
            return []
        records = list(turn.calls if self.section == 'tools' else getattr(turn, 'thoughts', ()))
        if filtered and self.section == 'tools':
            if self.exception_only:
                records = [record for record in records if record.attention or
                           any(entry and entry.truncated for entry in (record.arguments, record.result))]
            if self._query:
                records = [record for record in records if self._query in self._call_search_text(record)]
        return records

    def _call_search_text(self, record):
        """搜索完整安全元信息，与可见行的列宽取舍无关。"""
        if self.section == 'thinking':
            return self._safe(f'{getattr(record, "name", "API 思考")} · {record.source} · {record.id}')
        return self._safe(f'#{record.number} · {record.name} · {record.operation} · '
                          f'{record.source or "主任务"} · {record.status} · {record.original_id}')

    def _shorten(self, value, width):
        text = self._safe(value)
        return text if get_cwidth(text) <= width else self._fit(text, max(0, width - 1)) + '…'

    def _call_label(self, record, width):
        """先保留真实状态和工具名，再给来源及操作摘要分配列宽。"""
        if self.section == 'thinking':
            return self._call_search_text(record)
        status = self._shorten(record.status, 14)
        name = self._shorten(record.name, max(8, min(24, width // 2)))
        source = self._shorten(record.source or '主任务', 8)
        prefix = f'#{record.number} {status} {name} {source}'
        remaining = width - get_cwidth(prefix) - 1
        return prefix + (' ' + self._shorten(record.operation, remaining) if remaining > 1 else '')

    def _turn_label(self, turn, width):
        """时间、阶段、来源与接续父标识优先于可能很长的问题摘要。"""
        stamp = turn.started_at
        try:
            started = datetime.fromtimestamp(stamp) if isinstance(stamp, (int, float)) else datetime.fromisoformat(str(stamp))
            stamp = started.strftime('%H:%M:%S')
        except (ValueError, TypeError, OverflowError, OSError):
            stamp = self._shorten(stamp, 8)
        status = {'执行段结束': '段结束', '执行段结束（终态未知）': '段结束(未知)'}.get(turn.status, turn.status)
        prefix = f'{stamp} {self._shorten(status, 14)} {self._shorten(turn.source or "用户提交", 8)}'
        if turn.parent_id:
            parent = self._safe(turn.parent_id)
            prefix += ' @' + (parent if get_cwidth(parent) <= 6 else '…' + self._fit(parent[-6:], 6))
        remaining = width - get_cwidth(prefix) - 1
        return prefix + (' ' + self._shorten(turn.title, remaining) if remaining > 1 else '')

    def _call(self):
        return next((record for record in self._records(filtered=False) if record.id == self.call_id), None)

    def _change_call(self, identifier):
        if identifier != self.call_id:
            self.call_id = identifier
            self.anchor = (0, 0)
            self._anchors.clear()
            self.raw = False
            self.history.details.clear_view()
            self.history.details.clear_pages()

    def insert_search(self, text):
        """粘贴仅编辑独立搜索缓冲，换行不会提交或批准。"""
        if self.searching:
            safe = terminal_text(text, self.history.secret, multiline=True)
            self.search_text = (self.search_text + ' '.join(safe.splitlines()))[:512]

    def handle(self, key):
        """处理浏览按键，返回是否消费；未知文字仍不交给聊天。"""
        if not self.is_open:
            return False
        self._sync()
        if key == 'f2':
            self.close()
            return True
        if self.searching:
            if key == 'escape':
                self.searching = False
            elif key == 'enter':
                self._query = terminal_text(self.search_text, self.history.secret)
                self.searching = False
                if self.view == 'detail':
                    self._jump_match(1, first=True)
                else:
                    records = self._records()
                    if records:
                        self._change_call(records[0].id)
                    self.notice = '' if records else '无匹配；仅搜索当前轮次保留索引'
            elif key == 'backspace':
                self.search_text = self.search_text[:-1]
            elif len(key) == 1:
                self.insert_search(key)
            return True
        if key == 'escape':
            if self.view == 'detail':
                self.view = 'call_list'
                self._query = ''
            elif self.view == 'turn_list':
                self.view = 'call_list'
            else:
                self.close()
            return True
        if key == 't':
            self._remember()
            self.view = 'turn_list'
            self._turn_cursor = self.turn_id
            return True
        if key == 'tab':
            self._remember()
            self.section = 'thinking' if self.section == 'tools' else 'tools'
            self._restore()
            self.view = 'call_list'
            self._query = ''
            self._sync()
            return True
        if key == '/' and self.view != 'turn_list':
            self.searching = True
            self.search_text = self._query
            return True
        if self.view == 'detail':
            if key in {'pageup', 'pagedown'}:
                rows = self._rows(self._size[0])
                start = self._anchor_index(rows)
                index = min(max(0, start + (-1 if key == 'pageup' else 1) * self._size[1]), len(rows) - 1)
                self.anchor = rows[index][1:]
            elif key == 'r' and self.section == 'tools':
                self._anchors[self.raw] = self.anchor
                self.raw = not self.raw
                self.anchor = self._anchors.get(self.raw, (0, 0))
            elif key in {'n', 'N'}:
                self._jump_match(1 if key == 'n' else -1)
            return True
        if key == 'e' and self.view == 'call_list' and self.section == 'tools':
            self.exception_only = not self.exception_only
            records = self._records()
            if records and not any(record.id == self.call_id for record in records):
                self._change_call(records[0].id)
            self.notice = '' if records else '无匹配异常调用；未保留正文不能推断成功'
            return True
        records = list(self.history.turns) if self.view == 'turn_list' else self._records()
        selected = self._turn_cursor if self.view == 'turn_list' else self.call_id
        index = next((i for i, record in enumerate(records) if record.id == selected), 0)
        if key in {'up', 'down'} and records:
            index = min(max(0, index + (-1 if key == 'up' else 1)), len(records) - 1)
            if self.view == 'turn_list':
                self._turn_cursor = records[index].id
            else:
                self._change_call(records[index].id)
        elif key == 'enter' and records:
            if self.view == 'turn_list':
                self._remember()
                self.turn_id = records[index].id
                self._restore()
                self._query = ''
                self.view = 'call_list'
                self._sync()
            else:
                self._change_call(records[index].id)
                self._query = ''
                self.view = 'detail'
        return True

    def _body_key(self):
        record = self._call()
        if record is None:
            return None
        if self.section == 'thinking':
            entry = getattr(record, 'entry', record)
            return (self.turn_id, self.call_id, self.section, entry.version)
        return (self.turn_id, self.call_id, self.section, self.raw,
                getattr(record.arguments, 'version', 0), getattr(record.result, 'version', 0), record.status)

    def _body(self):
        record = self._call()
        if record is None:
            return '暂无 API 思考记录' if self.section == 'thinking' else '暂无调用详情'
        key = self._body_key()
        cached = self.history.details.get_view(key)
        if cached is not None:
            return cached or '格式化正文已移出（容量限制），原始快照仍以实际保留状态为准'
        if self.section == 'thinking':
            entry = getattr(record, 'entry', record)
            body = entry.body
            if entry.evicted:
                body += '\n思考正文已移出（容量限制），不可恢复'
            if entry.truncated:
                body += '\n思考已截断；仅展示保留片段'
        else:
            args, result = record.arguments, record.result
            body = format_result(record.name, args.body if args and not args.evicted else None, result.body if result and not result.evicted else None,
                raw=self.raw, secret=self.history.secret,
                arguments_truncated=bool(args and args.truncated), result_truncated=bool(result and result.truncated) or getattr(record, 'original_result_truncated', False),
                arguments_evicted=bool(args and args.evicted), result_evicted=bool(result and result.evicted))
        identity = f'完整调用标识：{self._safe(record.id)}\n来源：{self._safe(record.source)}'
        if self.section == 'tools':
            identity = f'完整调用标识：{self._safe(record.id)}\n原始调用标识：{self._safe(record.original_id)}\n工具：{self._safe(record.name)} · 来源：{self._safe(record.source or "主任务")} · 实际状态：{self._safe(record.status)}'
        references = getattr(record, 'references', {})
        if references:
            identity += '\n已有引用／状态：' + self._safe(json.dumps(references, ensure_ascii=False))
            if references.get('side_effects_may_have_occurred'):
                identity += '\n操作状态未知，可能已有副作用，请检查实际状态'
            if references.get('permission_limited'):
                identity += '\n受限结果：部分内容因权限未展示'
        body = identity + '\n' + body
        stored = self.history.details.cache_view(key, body)
        return stored or '格式化正文已移出（容量限制），原始快照仍以实际保留状态为准'

    def _rows(self, width):
        """当前视图按显示宽度折行，记录每行原逻辑行与字符偏移。"""
        width = max(2, width)
        key = (self._body_key(), width)
        cached = self.history.details.get_pages(key)
        if cached is not None:
            return cached
        rows = []
        for line_number, line in enumerate(self._body().split('\n')):
            row, used, offset = '', 0, 0
            for char_index, char in enumerate(line):
                value = ' ' * (4 - used % 4) if char == '\t' else char
                size = max(0, get_cwidth(value))
                if row and used + size > width:
                    rows.append((row, line_number, offset))
                    row, used, offset = '', 0, char_index
                row += value
                used += size
            rows.append((row, line_number, offset))
        self.history.details.cache_pages(key, rows)
        return rows

    def _anchor_index(self, rows):
        index = 0
        for i, (_, line, char) in enumerate(rows):
            if (line, char) > self.anchor:
                break
            index = i
        return index

    def _jump_match(self, direction, *, first=False):
        if not self._query:
            self.notice = '请输入搜索文本；仅搜索当前调用保留正文'
            return
        candidate = fallback = None
        for line_number, line in enumerate(self._body().split('\n')):
            start = 0
            while True:
                char = line.find(self._query, start)
                if char < 0:
                    break
                position = (line_number, char)
                if direction > 0:
                    if fallback is None:
                        fallback = position
                    if candidate is None and (first or position > self.anchor):
                        candidate = position
                else:
                    fallback = position
                    if position < self.anchor:
                        candidate = position
                start = char + max(1, len(self._query))
        found = candidate if candidate is not None else fallback
        if found is None:
            self.notice = '无匹配；仅搜索当前调用保留正文，截断／移出内容未搜索'
        else:
            self.anchor = found
            self.notice = '匹配保留正文；截断／移出内容未搜索'

    def hint(self):
        if self.searching:
            return '搜索：Enter确认 Esc返回 F2关闭'
        if self.view == 'turn_list':
            return '↑↓选择 Enter确认 Esc返回 F2关闭'
        if self.view == 'detail':
            return 'PgUp/PgDn翻页 /搜索 n/N匹配 r原始 Esc返回 F2关闭 Tab思考'
        return '↑↓选择 Enter详情 t轮次 /搜索 e异常 Tab思考 Esc/F2关闭'

    def _fit(self, text, width):
        value, used = '', 0
        for char in text:
            size = max(0, get_cwidth(char))
            if used + size > width:
                break
            value += char
            used += size
        return value

    def render(self, width, height):
        """只生成当前面板，导航始终占最后一行；更新不抢浏览身份。"""
        self._sync()
        width, height = max(2, width), max(1, height)
        if height == 1:
            return self._fit('F2关闭 Esc返回', width)
        hint = self.hint()
        turn = self._turn()
        label = '思考' if self.section == 'thinking' else '调用'
        title = self._safe(f'{turn.title if turn else "暂无轮次"} · {label}' + (' · 原始 JSON' if self.raw and self.view == 'detail' else ''))
        lines = [title]
        if self.notice and height >= 4:
            lines.append(self._fit(self.notice, width))
        if self.searching and height >= 3:
            lines.append(self._fit('搜索> ' + self._safe(self.search_text), width))
        available = max(0, height - len(lines) - 1)
        self._size = width, max(1, available)
        if self.view == 'detail':
            rows = self._rows(width)
            start = self._anchor_index(rows)
            lines.extend(row[0] for row in rows[start:start + available])
        elif self.view == 'turn_list':
            records = list(self.history.turns)
            selected = self._turn_cursor
            labels = [self._turn_label(item, width - 2) for item in records]
            lines.extend(self._list_window(records, labels, selected, available, width))
        else:
            if turn and turn.index_evicted and available:
                count = (f'累计 {turn.total_calls} 次' if getattr(turn, 'total_calls_complete', True) else
                         f'至少 {turn.total_calls} 次 · 未索引事件 {turn.unindexed_events}')
                lines.append(self._fit(f'部分调用索引已移出；{count}，不能恢复', width))
                available -= 1
            records = self._records()
            labels = [self._call_label(record, width - 2) for record in records]
            if records:
                lines.extend(self._list_window(records, labels, self.call_id, available, width))
            elif available:
                lines.append(self._fit('无匹配' if self._query or self.exception_only else
                    ('暂无 API 思考记录' if self.section == 'thinking' else '暂无调用详情'), width))
        # 窄屏也完整保留关闭键，其余提示按可用列数截短。
        lines.append(self._fit('F2关闭 ' + hint.replace('F2关闭', ''), width))
        return '\n'.join(self._fit(line, width) for line in lines[:height])

    def _list_window(self, records, labels, selected, height, width):
        index = next((i for i, record in enumerate(records) if record.id == selected), 0)
        start = max(0, index - max(0, height - 1))
        return [self._fit(('> ' if record.id == selected else '  ') + label, width)
                for record, label in zip(records[start:start + height], labels[start:start + height])]
