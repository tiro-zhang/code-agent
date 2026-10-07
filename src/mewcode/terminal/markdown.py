"""仅重排尚未提交的正文；表头最多前瞻一行，代码保持原字符。"""

import re
from bisect import bisect_left, bisect_right

from prompt_toolkit.utils import get_cwidth


CANDIDATE_LIMIT = 64 * 1024
MIN_COLUMN_WIDTH = 6
_CODE_STYLE = 'italic ansicyan'
_ESCAPABLE = frozenset('\\*_`|')


def _append(fragments, style, text):
    """合并相邻同样式片段，避免逐字符对象积累。"""
    if not text:
        return
    if fragments and fragments[-1][0] == style:
        fragments[-1] = (style, fragments[-1][1] + text)
    else:
        fragments.append((style, text))


def _inline(text, style=''):
    """只解释闭合粗体与代码；转义、标识符及未闭合内容受控保留。"""
    fragments = []
    pending, pending_style = [], style

    def append(token, value):
        nonlocal pending_style
        if token != pending_style:
            if pending:
                fragments.append((pending_style, ''.join(pending)))
                pending.clear()
            pending_style = token
        pending.append(value)

    # 先单遍建立闭合位置，避免每个未闭合定界符重复扫描整行。
    code_positions, bold_positions = {}, {'**': [], '__': []}
    matches = list(re.finditer(r'`+|\*\*|__', text))
    code_spans, opened = [], None
    for match in matches:
        if not match[0].startswith('`'):
            continue
        position, length = match.start(), len(match[0])
        code_positions.setdefault(length, []).append(position)
        slash = position - 1
        while slash >= 0 and text[slash] == '\\':
            slash -= 1
        if opened is not None:
            if length == opened[1]:
                code_spans.append((opened[0], match.end()))
                opened = None
        elif (position - slash - 1) % 2 == 0:
            opened = (position, length)
    span_index = 0
    for match in matches:
        marker, position = match[0], match.start()
        if marker.startswith('`'):
            continue
        while span_index < len(code_spans) and position >= code_spans[span_index][1]:
            span_index += 1
        if span_index < len(code_spans) and code_spans[span_index][0] < position:
            continue
        before = text[position - 1:position]
        after = text[match.end():match.end() + 1]
        slash = position - 1
        while slash >= 0 and text[slash] == '\\':
            slash -= 1
        if (before and not before.isspace() and (position - slash - 1) % 2 == 0
                and (marker != '__' or not (after.isalnum() or after == '_'))):
            bold_positions[marker].append(position)
    cursor = 0
    while cursor < len(text):
        char = text[cursor]
        if char == '\\' and cursor + 1 < len(text) and text[cursor + 1] in _ESCAPABLE:
            append(style, text[cursor + 1])
            cursor += 2
            continue
        if char == '`':
            stop = cursor + 1
            while stop < len(text) and text[stop] == '`':
                stop += 1
            marker = text[cursor:stop]
            positions = code_positions.get(len(marker), [])
            index = bisect_left(positions, stop)
            if index < len(positions):
                closing = positions[index]
                body = text[stop:closing]
                if body and '\n' not in body:
                    append((style + ' ' + _CODE_STYLE).strip(), body)
                    cursor = closing + len(marker)
                    continue
            append(style, marker)
            cursor = stop
            continue
        marker = text[cursor:cursor + 2]
        if marker in {'**', '__'}:
            before = text[cursor - 1] if cursor else ''
            start = cursor + 2
            opening_ok = start < len(text) and not text[start].isspace() and text[start] != marker[0]
            if marker == '__' and (before.isalnum() or before == '_'):
                opening_ok = False
            positions = bold_positions[marker]
            index = bisect_right(positions, start)
            if opening_ok and index < len(positions):
                closing = positions[index]
                body = text[start:closing]
                if '\n' not in body:
                    for token, value in _inline(body, (style + ' bold').strip()):
                        append(token, value)
                    cursor = closing + 2
                    continue
            # 未匹配定界符整体保留，避免从第二个字符再次开启格式。
            append(style, marker)
            cursor += 2
            continue
        append(style, char)
        cursor += 1
    if pending:
        fragments.append((pending_style, ''.join(pending)))
    return fragments


def _cells(line):
    """按未转义且不在反引号内的竖线分列，保留单元格原文。"""
    text = line.rstrip('\r\n').strip()
    cells, current = [], []
    cursor, code, separators = 0, '', 0
    while cursor < len(text):
        char = text[cursor]
        if char == '\\' and cursor + 1 < len(text) and not code:
            current.extend(text[cursor:cursor + 2])
            cursor += 2
            continue
        if char == '`':
            stop = cursor + 1
            while stop < len(text) and text[stop] == '`':
                stop += 1
            marker = text[cursor:stop]
            if not code:
                code = marker
            elif code == marker:
                code = ''
            current.append(marker)
            cursor = stop
            continue
        if char == '|' and not code:
            cells.append(''.join(current).strip())
            current = []
            separators += 1
        else:
            current.append(char)
        cursor += 1
    if not separators or code:
        return None
    cells.append(''.join(current).strip())
    if text.startswith('|'):
        cells.pop(0)
    if text.endswith('|') and cells and not cells[-1]:
        cells.pop()
    return cells or None


def _separator(cells):
    """有效分隔行每列至少三个横线，可带对齐冒号。"""
    return cells is not None and all(re.fullmatch(r':?-{3,}:?', cell) for cell in cells)


def _wrap(fragments, width):
    """按终端显示宽度折行，保留样式、空格和所有字符。"""
    lines, current, used = [], [], 0
    for style, text in fragments:
        for char in text:
            size = max(0, get_cwidth(char))
            if used and used + size > width:
                lines.append((current, used))
                current, used = [], 0
            _append(current, style, char)
            used += size
    lines.append((current, used))
    return lines


class MarkdownStyle:
    def __init__(self):
        self.fence = ''
        self._fence_length = 0
        self.language = ''
        self._candidate = ''
        self._headers = None

    def render(self, text, *, commit=True, width=80):
        """提交完整行；预览不改状态且包含仍在前瞻的表头原文。"""
        if not commit:
            fragments = []
            _append(fragments, '', self._candidate)
            _append(fragments, _CODE_STYLE if self.fence else '', text)
            return fragments
        fragments = []
        for line in text.splitlines(keepends=True):
            fragments.extend(self._line(line, max(1, width)))
        return fragments

    def flush(self, *, width=80, reset=True):
        """提交待定原文；行长度退化保留围栏，角色结束才重置。"""
        fragments = [('', self._candidate)] if self._candidate else []
        self._candidate = ''
        self._headers = None
        if reset:
            self.fence = self.language = ''
        self._fence_length = 0
        return fragments

    def _line(self, line, width):
        fragments = []
        content = line.removeprefix('MewCode> ').lstrip()
        oversized = len(line.encode('utf-8')) >= CANDIDATE_LIMIT
        cells = _cells(content) if not self.fence and not oversized else None
        if self._candidate:
            header = self._candidate
            self._candidate = ''
            headers = _cells(header.removeprefix('MewCode> ').lstrip())
            if _separator(cells) and len(cells) == len(headers):
                self._headers = headers
                prefix = 'MewCode> ' if header.startswith('MewCode> ') else ''
                if prefix:
                    fragments.append(('', prefix + '\n'))
                fragments.extend(self._table_row(headers, width, header=True))
                return fragments
            # 缺损候选保留原文，不对其行内字符猜测转换。
            fragments.append(('', header))
        if self._headers is not None:
            if cells is not None and len(cells) == len(self._headers) and not _separator(cells):
                fragments.extend(self._table_row(cells, width))
                return fragments
            self._headers = None
            if '|' in content:
                fragments.append(('', line))
                return fragments
        marker = re.match(r'(`{3,}|~{3,})([^\n]*)', content)
        if marker:
            fragments.append(('ansibrightblack', line))
            if not self.fence:
                # 保存开闭类型／长度而不保留无界围栏或语言字符串。
                self.fence = marker[1][0]
                self._fence_length = len(marker[1])
                self.language = marker[2].strip().lower()[:32]
            elif (marker[1][0] == self.fence[0] and len(marker[1]) >= self._fence_length
                    and not marker[2].strip()):
                self.fence = self.language = ''
                self._fence_length = 0
            return fragments
        if self.fence:
            style = _CODE_STYLE
            if self.language in {'diff', 'patch'}:
                if line.startswith('+'):
                    style = 'italic ansigreen'
                elif line.startswith('-'):
                    style = 'italic ansired'
                elif line.startswith('@@'):
                    style = 'bold italic ansicyan'
            fragments.append((style, line))
            return fragments
        if oversized and '|' in content:
            fragments.append(('', line))
            return fragments
        if (cells is not None and not _separator(cells)
                and line.endswith('\n') and any(cells)):
            self._candidate = line
            return fragments
        style = ''
        if re.match(r'#{1,6}\s', content):
            style = 'bold ansicyan'
        elif re.match(r'(?:[-*+] |\d+[.)] )', content):
            style = 'ansiblue'
        fragments.extend(_inline(line, style))
        return fragments

    def _table_row(self, cells, width, *, header=False):
        """按当前宽度提交一行；窄屏每个字段携带表头，后续可切换布局。"""
        count = len(cells)
        available = width - 3 * (count - 1)
        fragments = []
        if available < MIN_COLUMN_WIDTH * count:
            if header:
                for index, cell in enumerate(cells):
                    if index:
                        fragments.append(('', ' | '))
                    fragments.extend(_inline(cell, 'bold'))
                fragments.append(('', '\n'))
                return fragments
            for name, cell in zip(self._headers, cells):
                fragments.extend(_inline(name, 'bold'))
                fragments.append(('', '：'))
                fragments.extend(_inline(cell))
                fragments.append(('', '\n'))
            return fragments
        column_widths = [available // count + (index < available % count) for index in range(count)]
        wrapped = [_wrap(_inline(cell, 'bold' if header else ''), size)
                   for cell, size in zip(cells, column_widths)]
        height = max(len(column) for column in wrapped)
        for row in range(height):
            for index, (column, size) in enumerate(zip(wrapped, column_widths)):
                if index:
                    fragments.append(('ansibrightblack', ' │ '))
                parts, used = column[row] if row < len(column) else ([], 0)
                fragments.extend(parts)
                fragments.append(('', ' ' * (size - used)))
            fragments.append(('', '\n'))
        return fragments
