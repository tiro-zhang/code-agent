"""私有 Markdown 笔记是事实源，索引是可重建的有界视图。"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

import yaml

NOTE_BYTES = 16000
INDEX_BYTES = 25000
INDEX_LINES = 200
CATEGORIES = {"user_preference", "correction", "project_knowledge", "reference"}
PRIORITY = {"user_preference": 0, "correction": 0, "project_knowledge": 1, "reference": 2}
NOTE_ID = re.compile(r"[0-9a-f]{32}")


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("笔记时间必须是字符串")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("笔记时间必须包含时区")
    return result.timestamp()


def valid_source(value):
    if not isinstance(value, dict) or set(value) - {"session_id", "task_id", "message_id", "quote"}:
        raise ValueError("笔记来源字段非法")
    for key in ("session_id", "task_id", "message_id"):
        text = value.get(key)
        if not isinstance(text, str) or not text or len(text) > 128 or any(ord(char) < 32 for char in text):
            raise ValueError("笔记来源身份非法")
    quote = value.get("quote")
    if quote is not None and (not isinstance(quote, str) or not quote.strip() or len(quote) > 2000):
        raise ValueError("笔记原话非法")
    return dict(value)


def general_preference(quote):
    """通用范围必须同时表达偏好或持续请求，频次／故障状态不算意图。"""
    if not isinstance(quote, str):
        return False
    if re.search(r"这个项目|本项目|当前项目|this project", quote, re.IGNORECASE):
        return False
    # 纯范围引导语可用逗号连接自我偏好；故障陈述后的“请排查”不继承范围。
    if re.search(r"(?:^|[,，;；。.!?\n])\s*(?:在|对于)?(?:所有|任何|每个)项目(?:里|中|内)?\s*[,，]"
                 r"\s*我(?:都)?(?:希望|喜欢|偏好|要求)", quote):
        return True
    for clause in re.split(r"[,，;；。.!?\n]", quote):
        scope = re.search(r"所有项目|任何项目|每个项目|跨项目|以后.*?(?:都|一律)|通用偏好|"
                          r"all projects|any project|every project|from now on|in general", clause, re.IGNORECASE)
        intent = re.search(r"我(?:都)?(?:希望|喜欢|偏好|要求)|请|通用偏好|"
                           r"\b(?:please|I\s+(?:prefer|want|would like)|my\s+preference)\b",
                           clause, re.IGNORECASE)
        future_request = re.search(r"^\s*(?:以后|今后|从现在起).*?(?:都|一律)"
                                   r"(?:尽量|不要|别|用|使用|采用|按|遵循|优先|保持|统一|回答|回复)|"
                                   r"^\s*from now on.*?\b(?:use|answer|reply|respond|keep|avoid)\b",
                                   clause, re.IGNORECASE)
        # “请始终用中文”是持续请求；“测试始终失败，请排查”是两个不同陈述。
        continuing_request = re.search(r"(?:请|务必|必须|我希望|我要求|希望你)(?:以后|今后)?(?:总是|始终|一直)\S|"
                                       r"^\s*(?:please\s+)?always\s+(?:use|answer|reply|respond|write|keep|avoid)\b",
                                       clause, re.IGNORECASE)
        if (scope and (intent or future_request)) or continuing_request:
            return True
    return False


@dataclass(frozen=True)
class Note:
    id: str
    category: str
    scope: str
    summary: str
    created_at: str
    updated_at: str
    sources: tuple[dict, ...]
    content: str

    @classmethod
    def from_dict(cls, value, *, scope):
        if not isinstance(value, dict):
            raise ValueError("笔记必须是对象")
        identity = value.get("id")
        if not isinstance(identity, str) or not NOTE_ID.fullmatch(identity):
            raise ValueError("笔记身份非法")
        category = value.get("category")
        if category not in CATEGORIES or value.get("scope") != scope:
            raise ValueError("笔记类别或范围非法")
        summary = value.get("summary")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 200 or any(ord(char) < 32 for char in summary):
            raise ValueError("笔记摘要必须是不超过 200 字符的一行事实")
        content = value.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("笔记正文不能为空")
        created, updated = value.get("created_at"), value.get("updated_at")
        if timestamp(updated) < timestamp(created):
            raise ValueError("笔记更新时间早于创建时间")
        raw_sources = value.get("sources")
        if not isinstance(raw_sources, (list, tuple)) or not raw_sources:
            raise ValueError("笔记必须有来源")
        sources = tuple(valid_source(source) for source in raw_sources)
        if scope == "user" and (category != "user_preference" or not any(general_preference(source.get("quote")) for source in sources)):
            raise ValueError("用户域只保存有明确通用原话的偏好")
        result = cls(identity, category, scope, summary, created, updated, sources, content)
        if len(result.encode()) > NOTE_BYTES:
            raise ValueError("笔记超过 16000 字节")
        return result

    def as_dict(self):
        return {"id": self.id, "category": self.category, "scope": self.scope,
                "summary": self.summary, "created_at": self.created_at, "updated_at": self.updated_at,
                "sources": [dict(source) for source in self.sources], "content": self.content}

    def encode(self):
        value = self.as_dict()
        content = value.pop("content")
        return ("---\n" + yaml.safe_dump(value, allow_unicode=True, sort_keys=False) + "---\n" + content.rstrip("\n") + "\n").encode("utf-8")


@dataclass(frozen=True)
class MemorySnapshot:
    notes: tuple[Note, ...]
    version: str


def sorted_notes(notes):
    return sorted(notes, key=lambda note: (PRIORITY[note.category], -timestamp(note.updated_at), note.id, note.scope))


def render_index(notes, *, combined=False):
    """先按价值整项选择，再按项目／用户段呈现；所有标题也占额度。"""
    selected = []
    # PromptState 为合并内容加外层标题，提醒段之间还会加换行。
    outer_header = "## 长期记忆\n" if combined else ""
    line_limit = INDEX_LINES - (1 if combined else 0)
    byte_limit = INDEX_BYTES - len(outer_header.encode("utf-8")) - (1 if combined else 0)

    def render(items):
        parts = []
        for scope, title in (("project", "项目自动记忆"), ("user", "用户通用偏好")):
            group = [note for note in items if note.scope == scope]
            if not group:
                continue
            parts.append(f"## {title}\n")
            for note in group:
                prefix = (".mewcode/memory/" if scope == "project" else "~/.mewcode/memory/") if combined else ""
                parts.append(f"- {note.summary} （笔记：{prefix}{note.id}.md）\n")
        return "".join(parts)

    for note in sorted_notes(notes):
        candidate = render([*selected, note])
        if len(candidate.splitlines()) <= line_limit and len(candidate.encode("utf-8")) <= byte_limit:
            selected.append(note)
    return render(selected)


class MemoryStore:
    def __init__(self, root, *, scope="project"):
        if scope not in {"project", "user"}:
            raise ValueError("未知笔记范围")
        self.base = Path(root).absolute()
        self.scope = scope
        self.root = self.base / ".mewcode" / "memory" if scope == "project" else self.base / "memory"
        self.diagnostics = []

    def _ensure(self):
        # 拒绝专用目录自身的链接；项目工作根允许调用方先解析真实路径。
        paths = [self.base / ".mewcode", self.root] if self.scope == "project" else [self.base, self.root]
        for path in paths:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
                raise ValueError("笔记目录不是安全的普通目录")
            os.chmod(path, 0o700)
        if self.root.resolve() != self.root:
            raise ValueError("笔记目录存在符号链接逃逸")
        ignore = self.root / ".gitignore"
        if not ignore.exists():
            self._atomic(ignore, b"*\n!.gitignore\n")
        else:
            if self._read(ignore, 4096) != b"*\n!.gitignore\n":
                self._atomic(ignore, b"*\n!.gitignore\n")

    def _check(self, path, *, missing=False):
        if path.parent != self.root:
            raise ValueError("笔记路径越界")
        try:
            info = path.lstat()
        except FileNotFoundError:
            if missing:
                return
            raise
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_nlink != 1:
            raise ValueError("笔记路径不是普通文件")

    def _read(self, path, limit):
        self._check(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("笔记文件类型非法")
            if os.fstat(descriptor).st_mode & 0o777 != 0o600:
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                return handle.read(limit + 1)
        finally:
            os.close(descriptor)

    def _atomic(self, path, data):
        self._check(path, missing=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".memory-", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self._check(path, missing=True)
            if self.root.resolve() != self.root:
                raise ValueError("笔记目录在写入时已变化")
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    @contextmanager
    def locked(self):
        self._ensure()
        path = self.root / ".lock"
        self._check(path, missing=True)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("笔记锁不是普通文件")
            os.fchmod(descriptor, 0o600)
            # 不阻塞当前工作输入；另一进程正提交时本轮维护保守跳过。
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(descriptor)

    def _snapshot(self):
        notes, digest = [], hashlib.sha256()
        for path in sorted(self.root.glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            digest.update(path.name.encode())
            try:
                raw = self._read(path, NOTE_BYTES)
                digest.update(raw)
                info = path.stat()
                digest.update(f"{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}".encode())
                if len(raw) > NOTE_BYTES:
                    raise ValueError("笔记超过大小限制")
                text = raw.decode("utf-8")
                if not text.startswith("---\n") or "\n---\n" not in text[4:]:
                    raise ValueError("笔记缺少 frontmatter")
                frontmatter, content = text[4:].split("\n---\n", 1)
                value = yaml.safe_load(frontmatter)
                if not isinstance(value, dict):
                    raise ValueError("frontmatter 不是对象")
                value["content"] = content
                note = Note.from_dict(value, scope=self.scope)
                if path.name != note.id + ".md":
                    raise ValueError("笔记身份与文件名不匹配")
                notes.append(note)
            except (OSError, ValueError, UnicodeError, yaml.YAMLError, TypeError):
                digest.update(b"invalid")
                self.diagnostics.append(f"跳过损坏或不安全笔记：{path.name}")
        return MemorySnapshot(tuple(sorted_notes(notes)), digest.hexdigest())

    def snapshot(self):
        with self.locked():
            return self._snapshot()

    def _rebuild(self, snapshot):
        text = render_index(snapshot.notes)
        path = self.root / "MEMORY.md"
        try:
            old = self._read(path, INDEX_BYTES).decode("utf-8")
        except (OSError, ValueError, UnicodeError):
            old = None
        if old != text:
            self._atomic(path, text.encode("utf-8"))
            self.diagnostics.append("自动记忆索引已从合法笔记重建")
        return text

    def refresh(self):
        with self.locked():
            return self._rebuild(self._snapshot())

    def commit(self, operations, *, version):
        """单域版本检查后提交；目标先写，合并删除随后，索引最后。"""
        messages = []
        with self.locked():
            snapshot = self._snapshot()
            if snapshot.version != version:
                return 0, ["自动记忆版本冲突，保留当前笔记并跳过旧候选"]
            changed = 0
            for note, remove in operations:
                try:
                    note = Note.from_dict(note.as_dict(), scope=self.scope)
                    for identity in remove:
                        if not isinstance(identity, str) or not NOTE_ID.fullmatch(identity) or identity == note.id:
                            raise ValueError("合并身份非法")
                        self._check(self.root / (identity + ".md"))
                    self._atomic(self.root / (note.id + ".md"), note.encode())
                    changed += 1
                    for identity in remove:
                        path = self.root / (identity + ".md")
                        self._check(path)
                        path.unlink()
                except (OSError, ValueError):
                    messages.append("笔记提交部分失败；已写入目标保留，未成功文件未覆盖")
            if changed:
                try:
                    self._rebuild(self._snapshot())
                except (OSError, ValueError):
                    messages.append("笔记已提交，但索引写入失败；下次读取将重建")
            return changed, messages
