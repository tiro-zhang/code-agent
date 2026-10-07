"""跨轮共享的有界内存正文；只使用事件快照，不读写文件。"""

from collections import OrderedDict
from dataclasses import dataclass
import sys

from ..tools.base import utf8_prefix
from .text import StreamText, terminal_text, redact_json


@dataclass
class DetailEntry:
    """正文仅存一份，移出仍保留可辨状态。"""

    title: str
    body: str = ''
    thinking: bool = False
    truncated: bool = False
    evicted: bool = False
    version: int = 0
    owner: str = ''


class DetailStore:
    """8 MiB 共享正文、256 KiB 思考、64 KiB 单条参数和结果。"""

    def __init__(self, secret, *, body_limit=8 * 1024 * 1024,
                 thinking_limit=256 * 1024, tool_limit=64 * 1024,
                 view_cache_limit=256 * 1024):
        self.secret = secret
        self.body_limit, self.thinking_limit, self.tool_limit = body_limit, thinking_limit, tool_limit
        self.view_cache_limit = view_cache_limit
        self.entries = OrderedDict()
        self._streams = {}
        self.metadata_enforcer = None
        self._body_bytes = self._thinking_bytes = 0
        self._finished_owners = set()
        self._view_key, self._view = None, None
        self._pages_key, self._pages = None, None
        self.view_bytes = 0

    @property
    def body_bytes(self):
        return self._body_bytes

    @property
    def thinking_bytes(self):
        return self._thinking_bytes

    def _replace(self, entry, body):
        difference = len(body.encode('utf-8')) - len(entry.body.encode('utf-8'))
        self._body_bytes += difference
        if entry.thinking:
            self._thinking_bytes += difference
        if body != entry.body:
            entry.body = body
            entry.version += 1
            self.clear_pages()

    def _evict(self):
        # 按已结束轮次优先，轮次内沿正文首次到达顺序移出。
        candidates = sorted(self.entries.values(), key=lambda entry: entry.owner not in self._finished_owners)
        for entry in candidates:
            if self.body_bytes <= self.body_limit:
                break
            if entry.body:
                self._replace(entry, '')
                entry.evicted = True
        if self.body_bytes > self.body_limit and self._view:
            self._body_bytes -= len(self._view.encode('utf-8'))
            self._view = ''
            self.clear_pages()

    def scope(self, owner):
        """提供单轮兼容接口，正文仍由本共享存储持有。"""
        return DetailScope(self, owner)

    def put(self, key, title, body, *, owner=''):
        """更新快照，已移出正文不重新载入。"""
        entry = self.entries.setdefault(('tool', key), DetailEntry('', owner=owner))
        entry.title = terminal_text(title, self.secret, multiline=True, limit=2048)
        if not entry.evicted:
            safe = terminal_text(redact_json(body, self.secret), self.secret, multiline=True).encode('utf-8')
            self._replace(entry, utf8_prefix(safe, self.tool_limit))
            entry.truncated = len(safe) > self.tool_limit
            self._evict()
        return entry

    def think(self, key, title, text, *, owner=''):
        entry = self.entries.setdefault(('thought', key), DetailEntry(
            terminal_text(title, self.secret, limit=2048), thinking=True, owner=owner))
        # 思考达到上限后不再保存脱敏流尾缀或创建无限量空条目。
        stream = self._streams.setdefault(key, StreamText(self.secret))
        safe = stream.feed(text).encode('utf-8')
        remaining = max(0, self.thinking_limit - self.thinking_bytes)
        if not entry.evicted:
            self._replace(entry, entry.body + utf8_prefix(safe, remaining))
        entry.truncated |= len(safe) > remaining
        self._evict()
        return entry

    def finish_thinking(self, *, owner=None):
        for key, stream in list(self._streams.items()):
            entry = self.entries.get(('thought', key))
            if entry is None or owner is not None and entry.owner != owner:
                continue
            safe = stream.feed('', final=True).encode('utf-8')
            remaining = max(0, self.thinking_limit - self.thinking_bytes)
            if not entry.evicted:
                self._replace(entry, entry.body + utf8_prefix(safe, remaining))
            entry.truncated |= len(safe) > remaining
            del self._streams[key]
        self._evict()

    def finish_owner(self, owner):
        self.finish_thinking(owner=owner)
        self._finished_owners.add(owner)

    def remove(self, key):
        entry = self.entries.pop(key, None)
        if entry:
            self._replace(entry, '')
        if key[0] == 'thought':
            self._streams.pop(key[1], None)
        self.clear_view()

    def remove_owner(self, owner):
        for key, entry in list(self.entries.items()):
            if entry.owner == owner:
                self.remove(key)
        self._finished_owners.discard(owner)

    def text(self, section, *, owner=None):
        thinking = section == 'thinking'
        lines = []
        for entry in self.entries.values():
            if entry.thinking != thinking or owner is not None and entry.owner != owner:
                continue
            lines.append(entry.title)
            lines.append('正文已不可用（容量限制）；保留状态和既有引用' if entry.evicted else entry.body)
            if entry.truncated:
                lines.append('详情已截断（容量限制）')
        return '\n'.join(lines) or ('暂无 API 思考记录' if thinking else '暂无调用详情')

    def cache_view(self, key, body):
        """只缓存当前格式化正文，并计入共享预算。"""
        self.clear_view()
        self._view_key = key
        self._view = utf8_prefix(body.encode('utf-8'), self.body_limit)
        self._body_bytes += len(self._view.encode('utf-8'))
        self._evict()
        return self._view

    def get_view(self, key):
        return self._view if self._view_key == key else None

    def clear_view(self):
        if self._view is not None:
            self._body_bytes -= len(self._view.encode('utf-8'))
        self._view_key, self._view = None, None
        self.clear_pages()

    def cache_pages(self, key, value):
        """单个搜索／分页缓存，连同容器开销计入独立小额上限。"""
        self.clear_pages()
        seen = set()
        def size(item):
            identity = id(item)
            if identity in seen:
                return 0
            seen.add(identity)
            amount = sys.getsizeof(item)
            if isinstance(item, str):
                amount = max(amount, len(item.encode('utf-8')))
            elif isinstance(item, dict):
                amount += sum(size(k) + size(v) for k, v in item.items())
            elif isinstance(item, (list, tuple, set, frozenset)):
                amount += sum(size(child) for child in item)
            return amount
        amount = size(value) + size(key)
        if amount > self.view_cache_limit:
            return False
        self._pages_key, self._pages, self.view_bytes = key, value, amount
        return True

    def get_pages(self, key):
        return self._pages if self._pages_key == key else None

    def clear_pages(self):
        self._pages_key, self._pages, self.view_bytes = None, None, 0

    def clear(self):
        self.clear_view()
        self.entries.clear()
        self._streams.clear()
        self._finished_owners.clear()
        self._body_bytes = self._thinking_bytes = 0


class DetailScope:
    """当前轮次的兼容文字入口，不复制正文。"""

    def __init__(self, store, owner):
        self.store, self.owner = store, owner

    def put(self, key, title, body):
        return self.store.put((self.owner, key), title, body, owner=self.owner)

    def think(self, key, title, text):
        # 单轮思考索引也受限；超限继续追加到最后一个有界条目。
        keys = [entry_key for entry_key, entry in self.store.entries.items()
                if entry.thinking and entry.owner == self.owner]
        if len(keys) >= 1024 and ('thought', (self.owner, key)) not in self.store.entries:
            key = keys[-1][1][1]
        entry = self.store.think((self.owner, key), title, text, owner=self.owner)
        if self.store.metadata_enforcer is not None:
            self.store.metadata_enforcer()
        return entry

    def finish_thinking(self):
        self.store.finish_thinking(owner=self.owner)

    def text(self, section):
        return self.store.text(section, owner=self.owner)
