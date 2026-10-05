"""仅在原子存储成功后替换工具结果；文件由本会话独占管理。"""
from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import re
from uuid import uuid4

from ..tools.base import ToolResult, strict_json
from .estimate import dump, estimate_text

SINGLE_LIMIT = 8000
BATCH_LIMIT = 20000
PREVIEW_LIMIT = 1000


def preview(text: str, limit: int) -> str:
    units, end = 0, 0
    for char in text:
        units += 1 if ord(char) < 128 else 4
        if units > limit * 4:
            break
        end += 1
    return text[:end]


class ResultCache:
    def __init__(self, root: Path, *, session_id: str | None = None, persistent=False):
        self.root = root.resolve()
        if session_id is not None and not re.fullmatch(r'[a-zA-Z0-9_-]+', session_id):
            raise ValueError('会话缓存身份无效')
        self.session_id = session_id or uuid4().hex
        self.persistent = persistent
        self._reopening = False
        self.relative = Path('.mewcode/context') / self.session_id
        self.directory = self.root / self.relative
        self._fds: list[int] = []
        self._files: set[str] = set()
        self.failed = False
        self.attempted: set[str] = set()

    @property
    def count(self):
        return len(self._files - {"results-index.jsonl"})

    def _open(self):
        if self._fds:
            self._check()
            return
        descriptors = []
        try:
            descriptors.append(os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            for name in ('.mewcode', 'context', self.session_id):
                try:
                    os.mkdir(name, mode=0o700, dir_fd=descriptors[-1])
                except FileExistsError:
                    if name == self.session_id and not self._reopening:
                        raise OSError('会话目录已存在')
                descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     dir_fd=descriptors[-1])
                descriptors.append(descriptor)
                if os.fstat(descriptor).st_uid != os.getuid():
                    raise OSError('缓存目录所有者不一致')
                if name != '.mewcode':
                    os.fchmod(descriptor, 0o700)
                if name == 'context':
                    # 缓存在任意用户项目中都必须排除，不能依赖 MewCode 仓库的规则。
                    try:
                        ignore_fd = os.open('.gitignore', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                            0o600, dir_fd=descriptor)
                    except FileExistsError:
                        ignore_fd = os.open('.gitignore', os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                                            dir_fd=descriptor)
                        with os.fdopen(ignore_fd, 'r') as ignore:
                            if not stat.S_ISREG(os.fstat(ignore.fileno()).st_mode) or ignore.read(16) != '*\n':
                                raise OSError('缓存忽略规则不安全')
                    else:
                        with os.fdopen(ignore_fd, 'w') as ignore:
                            ignore.write('*\n')
            self._fds = descriptors
            self._check()
        except OSError:
            for fd in descriptors:
                os.close(fd)
            self._fds = []
            raise

    def _check(self):
        current = self.root
        for name, fd in zip(('.mewcode', 'context', self.session_id), self._fds[1:]):
            current /= name
            actual, owned = current.lstat(), os.fstat(fd)
            if not stat.S_ISDIR(actual.st_mode) or (actual.st_dev, actual.st_ino) != (owned.st_dev, owned.st_ino):
                raise OSError('缓存目录真实路径发生变化')

    @property
    def index_path(self) -> str:
        return str(self.relative / "results-index.jsonl")

    @property
    def paths(self) -> set[str]:
        return {str(self.relative / name) for name in self._files}

    def reopen(self, paths) -> None:
        """仅接管存档明确登记的文件，不能扫描目录猜测所有权。"""
        paths = set(paths)
        for path in paths:
            relative = Path(path)
            if relative.parent != self.relative or not re.fullmatch(r'[a-zA-Z0-9_.-]+\.jsonl', relative.name):
                raise OSError('缓存登记路径不属于当前会话')
        if not paths:
            # 空存档可能尚未创建缓存目录；首次写入仍安全创建。
            self._reopening = self.directory.exists()
            return
        if not self.directory.is_dir():
            raise OSError('存档缓存目录缺失')
        self._reopening = True
        self._open()
        self._files = {Path(path).name for path in paths}

    def write_index(self, paths) -> str:
        from ..types import Message
        message = Message("tool", tool_result=ToolResult.success({"files": sorted(paths)}))
        return self.save(message, "context_result_index", _index=True)

    def save(self, message, tool_name: str, *, _index=False, namespace='', provenance=None) -> str:
        if namespace and not re.fullmatch(r'[a-zA-Z0-9_-]+', namespace):
            raise ValueError('缓存命名空间无效')
        self._open()
        name = (namespace + '_' if namespace else '') + ('results-index.jsonl' if _index else uuid4().hex + '.jsonl')
        temporary = uuid4().hex + '.tmp'
        raw = message.tool_result.to_json()
        header = {'format': 'mewcode-result-v1', 'source': message.id,
                  'tool_call_id': message.tool_call_id, 'tool_name': tool_name,
                  'characters': len(raw), 'read': '用 read_file 的 start_line/max_lines 分页；依次拼接每行 chunk 后解析 JSON 可恢复完整结果。仅保存采集层实际返回内容。'}
        if provenance is not None:
            header['provenance'] = provenance
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=self._fds[-1])
            with os.fdopen(fd, 'w', encoding='utf-8') as file:
                file.write(dump(header) + '\n')
                for offset in range(0, len(raw), 512):
                    file.write(dump({'chunk': raw[offset:offset + 512]}) + '\n')
                file.flush()
                os.fsync(file.fileno())
            self._check()
            os.rename(temporary, name, src_dir_fd=self._fds[-1], dst_dir_fd=self._fds[-1])
            self._files.add(name)
            return str(self.relative / name)
        finally:
            try:
                os.unlink(temporary, dir_fd=self._fds[-1])
            except FileNotFoundError:
                pass

    def _read_fd(self, path):
        if not self._fds:
            raise OSError("会话缓存不可用")
        self._check()
        relative = Path(path)
        if relative.parent != self.relative or relative.name not in self._files:
            raise OSError('非本会话缓存引用')
        return os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._fds[-1])

    def describe(self, path: str) -> dict:
        """来源只从已登记的普通缓存文件读取，不接受绝对引用。"""
        with os.fdopen(self._read_fd(path), encoding='utf-8') as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise OSError('缓存不是普通文件')
            header = strict_json(file.readline(8193))
            if (not isinstance(header, dict) or header.get('format') != 'mewcode-result-v1'
                    or type(header.get('characters')) is not int or header['characters'] < 0
                    or not isinstance(header.get('source'), str) or not header['source']):
                raise ValueError('缓存来源无效')
            return header

    def restore(self, path: str, *, max_characters: int | None = None) -> ToolResult:
        fd = self._read_fd(path)
        with os.fdopen(fd, encoding='utf-8') as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise OSError('缓存不是普通文件')
            try:
                header = strict_json(next(file))
                if (not isinstance(header, dict) or header.get('format') != 'mewcode-result-v1'
                        or type(header.get('characters')) is not int or header['characters'] < 0
                        or not isinstance(header.get('source'), str) or not header['source']):
                    raise ValueError('缓存格式无效')
                chunks, size = [], 0
                lines = iter(lambda: file.readline(max_characters + 1), '') if max_characters is not None else file
                for line in lines:
                    if max_characters is not None and len(line) > max_characters:
                        raise ValueError('缓存正文超限')
                    chunk = strict_json(line)['chunk']
                    size += len(chunk)
                    if max_characters is not None and size > max_characters:
                        raise ValueError('缓存正文超限')
                    chunks.append(chunk)
                raw = ''.join(chunks)
                if len(raw) != header['characters']:
                    raise ValueError('缓存正文不完整')
                from ..sessions.codec import decode_result
                return decode_result(strict_json(raw))
            except (ValueError, TypeError, KeyError, StopIteration):
                raise ValueError('缓存格式或正文损坏') from None

    def close(self) -> None:
        if not self._fds:
            return
        try:
            if self.persistent:
                return
            # unlink 不跟随链接；仅删除应用成功创建并登记的文件。
            for name in tuple(self._files):
                try:
                    os.unlink(name, dir_fd=self._fds[-1])
                except FileNotFoundError:
                    pass
                self._files.remove(name)
            self._check()
            os.rmdir(self.session_id, dir_fd=self._fds[-2])
        finally:
            for fd in self._fds:
                os.close(fd)
            self._fds = []


def _states(value):
    """保留权限、启动及副作用等结构化状态，正文从引用文件读取。"""
    if not isinstance(value, dict):
        return {}
    result = {key: item for key, item in value.items()
            if isinstance(item, (bool, int, float)) or item is None
            or key in {'status', 'code', 'side_effects', 'side_effects_uncertain'}}
    paths = value.get('cache_paths')
    if isinstance(paths, list) and len(paths) <= 512 and all(
            isinstance(path, str) and re.fullmatch(r'\.mewcode/context/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+\.jsonl', path)
            for path in paths):
        result['cache_paths'] = paths
    return result


def _reference(message, path, tool_name):
    original = message.tool_result
    raw = original.to_json()
    return replace(message, cache_path=path, tool_result=ToolResult(
        original.ok, {'stored_result': path, 'source': message.id, 'tool_name': tool_name,
                      'characters': len(raw), 'state': _states(original.data),
                      'read_hint': 'read_file 分页读取 JSONL；缓存是历史快照，编辑前重新读取当前文件。',
                      'preview': preview(raw, PREVIEW_LIMIT)}, original.error, original.truncated))


def spill_history(history, cache: ResultCache) -> tuple[int, list[str]]:
    changed, warnings = 0, []
    if cache.failed:
        return changed, warnings
    for index, assistant in enumerate(history):
        if not assistant.tool_calls:
            continue
        positions = list(range(index + 1, min(len(history), index + 1 + len(assistant.tool_calls))))
        if any(history[i].role != 'tool' for i in positions):
            continue
        names = {call.id: call.name for call in assistant.tool_calls}
        def size(i):
            return estimate_text(history[i].tool_result.to_json())
        def spill(i):
            nonlocal changed
            message = history[i]
            if message.id in cache.attempted:
                return
            cache.attempted.add(message.id)
            name = names.get(message.tool_call_id, '')
            path = cache.save(message, name)
            replacement = _reference(message, path, name)
            # 超长不可缩减状态保留原文，不能为落盘反而扩大上下文。
            if estimate_text(replacement.tool_result.to_json()) < size(i):
                history[i] = replacement
                changed += 1
        try:
            for i in positions:
                if not history[i].cache_path and size(i) > SINGLE_LIMIT:
                    spill(i)
            for i in sorted(positions, key=lambda i: -size(i)):
                if sum(size(p) for p in positions) <= BATCH_LIMIT:
                    break
                if not history[i].cache_path:
                    spill(i)
            if sum(size(p) for p in positions) > BATCH_LIMIT:
                for i in positions:
                    message = history[i]
                    if message.cache_path:
                        data = dict(message.tool_result.data)
                        data['preview'] = ''
                        history[i] = replace(message, tool_result=replace(message.tool_result, data=data))
        except (OSError, ValueError):
            cache.failed = True
            warnings.append('工具结果缓存写入失败，保留未落盘原文；请检查 .mewcode/context 路径和磁盘权限。')
            break
    return changed, warnings
