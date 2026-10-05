"""最近任务的内存详情；只使用事件快照，不读取或写入文件。"""

from collections import OrderedDict
from dataclasses import dataclass

from ..tools.base import OUTPUT_LIMIT, utf8_prefix
from .text import StreamText, terminal_text, redact_json


@dataclass
class _Entry:
    title: str
    body: str = ''
    thinking: bool = False
    truncated: bool = False
    evicted: bool = False


class DetailStore:
    """正文总计 2 MiB，实际 API 思考最多 256 KiB，超限后保留关联标识。"""

    def __init__(self, secret, *, body_limit=2 * 1024 * 1024,
                 thinking_limit=256 * 1024, tool_limit=OUTPUT_LIMIT):
        self.secret = secret
        self.body_limit, self.thinking_limit, self.tool_limit = body_limit, thinking_limit, tool_limit
        self.entries = OrderedDict()
        self._streams = {}

    @property
    def body_bytes(self):
        return sum(len(entry.body.encode('utf-8')) for entry in self.entries.values())

    @property
    def thinking_bytes(self):
        return sum(len(entry.body.encode('utf-8')) for entry in self.entries.values() if entry.thinking)

    def _evict(self):
        for entry in self.entries.values():
            if self.body_bytes <= self.body_limit:
                break
            if entry.body:
                entry.body, entry.evicted = '', True

    def put(self, key, title, body):
        """更新同一调用的快照，已淘汰正文不重新载入。"""
        key = ('tool', key)
        entry = self.entries.setdefault(key, _Entry(''))
        entry.title = terminal_text(title, self.secret, multiline=True, limit=2048)
        if not entry.evicted:
            safe = terminal_text(redact_json(body, self.secret), self.secret, multiline=True).encode('utf-8')
            entry.body = utf8_prefix(safe, self.tool_limit)
            entry.truncated = len(safe) > self.tool_limit
            self._evict()

    def think(self, key, title, text):
        entry = self.entries.setdefault(('thought', key), _Entry(
            terminal_text(title, self.secret), thinking=True))
        stream = self._streams.setdefault(key, StreamText(self.secret))
        safe = stream.feed(text).encode('utf-8')
        remaining = max(0, self.thinking_limit - self.thinking_bytes)
        if not entry.evicted:
            entry.body += utf8_prefix(safe, remaining)
        entry.truncated |= len(safe) > remaining
        self._evict()

    def finish_thinking(self):
        for key, stream in self._streams.items():
            entry = self.entries[('thought', key)]
            safe = stream.feed('', final=True).encode('utf-8')
            remaining = max(0, self.thinking_limit - self.thinking_bytes)
            if not entry.evicted:
                entry.body += utf8_prefix(safe, remaining)
            entry.truncated |= len(safe) > remaining
        self._evict()

    def text(self, section):
        thinking = section == 'thinking'
        lines = []
        for entry in self.entries.values():
            if entry.thinking != thinking:
                continue
            lines.append(entry.title)
            lines.append('正文已不可用（容量限制）；保留状态和既有引用' if entry.evicted else entry.body)
            if entry.truncated:
                lines.append('详情已截断（容量限制）')
        return '\n'.join(lines) or ('暂无 API 思考记录' if thinking else '暂无调用详情')
