"""通过 ripgrep 查找路径和内容，不经 shell 拼接参数。"""

import asyncio
import json
from fnmatch import fnmatchcase
from functools import lru_cache
import os
from pathlib import Path
import shutil
import signal

from ..async_utils import protected

from .base import ToolContext, ToolError, ToolResult, object_schema, utf8_prefix
from .paths import checked_path
from .processes import child_process, output_chunks

MAX_RESULTS = {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}
PATTERN = {"type": "string", "minLength": 1}


def rg_command() -> list[str]:
    program = shutil.which("rg")
    if not program:
        raise ToolError("dependency_missing", "搜索需要 ripgrep，请安装 rg 后重试")
    return [program, "--no-config", "--no-follow", "--no-hidden", "--no-require-git", "--sort", "path"]


def safe_relative(value: str, context: ToolContext) -> str | None:
    try:
        path = checked_path(value, context)
        if not path.is_file() or (context.root / value).is_symlink():
            return None
        return str(path.relative_to(context.root))
    except (ToolError, OSError, ValueError):
        return None


def path_matcher(pattern: str):
    """在 rg 的默认可见路径上做 glob，避免 --glob 覆盖忽略规则。"""
    if len(pattern) > 4096 or "\0" in pattern or pattern.startswith("/") or ".." in pattern.split("/"):
        raise ToolError("invalid_pattern", "文件模式必须是工作目录内的相对路径 glob")
    parts = tuple(pattern.removeprefix("./").split("/"))
    if len(parts) > 128:
        raise ToolError("invalid_pattern", "文件模式层级过多")
    if len(parts) == 1:
        parts = ("**", *parts)

    def matches(path: str) -> bool:
        path_parts = tuple(path.split("/"))

        @lru_cache(maxsize=None)
        def visit(p: int, f: int) -> bool:
            if p == len(parts):
                return f == len(path_parts)
            if parts[p] == "**":
                return visit(p + 1, f) or (f < len(path_parts) and visit(p, f + 1))
            return f < len(path_parts) and fnmatchcase(path_parts[f], parts[p]) and visit(p + 1, f + 1)

        return visit(0, 0)

    return matches


def records(process, separator: bytes, limit: int):
    """超长记录整条丢弃并发出截断标记，缓冲不随单行大小增长。"""
    buffer = bytearray()
    dropping = False
    for name, chunk in output_chunks(process):
        if name == "stderr":
            continue
        for index, part in enumerate(chunk.split(separator)):
            if index:
                if dropping:
                    yield None
                elif buffer:
                    yield bytes(buffer)
                buffer.clear()
                dropping = False
            if not dropping:
                if len(buffer) + len(part) > limit:
                    buffer.clear()
                    dropping = True
                else:
                    buffer.extend(part)
    if dropping:
        yield None
    elif buffer:
        yield bytes(buffer)


def _candidate_matcher(tool, arguments: dict):
    name = tool if isinstance(tool, str) else tool.name
    if name == "glob_files":
        return path_matcher(arguments["pattern"])
    if name == "search_code":
        return path_matcher(arguments.get("glob", "**"))
    raise ToolError("permission_check_failed", "此工具没有搜索候选枚举能力", not_started=True)


def _candidate(record: bytes, context: ToolContext, matcher) -> str | None:
    relative = safe_relative(record.decode("utf-8", errors="surrogateescape"), context)
    if relative is None or not matcher(relative):
        return None
    try:
        relative.encode("utf-8")
    except UnicodeError:
        return None
    return str(context.root / relative)


def _enumerate_sync(tool, arguments: dict, context: ToolContext) -> tuple[str, ...]:
    matcher = _candidate_matcher(tool, arguments)
    candidates: set[str] = set()
    with child_process([*rg_command(), "--files", "-0", "--", "."], context.root) as process:
        for record in records(process, b"\0", 64 * 1024):
            if record is None:
                raise ToolError("permission_check_failed", "候选路径记录过长，无法完整检查搜索范围", not_started=True)
            path = _candidate(record, context, matcher)
            if path is not None:
                candidates.add(path)
        if process.wait() not in (0, 1):
            raise ToolError("invalid_pattern", "文件模式无效或目录不可读取，请检查模式和权限")
    return tuple(sorted(candidates))


async def _cleanup_metadata(process) -> None:
    """枚举进程有独立进程组；取消时连同可能的子进程一起回收。"""
    def stop(sig):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
    stop(signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), 0.2)
    except asyncio.TimeoutError:
        pass
    finally:
        stop(signal.SIGKILL)
        await process.wait()


async def enumerate_candidates(tool, arguments: dict, context: ToolContext,
                               cancel_event: asyncio.Event) -> tuple[str, ...]:
    """授权前仅运行 rg --files 获取元数据；绝不先读内容判断是否值得询问。"""
    matcher = _candidate_matcher(tool, arguments)
    if cancel_event.is_set():
        raise ToolError("cancelled", "任务取消，搜索候选尚未枚举", not_started=True)
    try:
        # 必须取得已创建的句柄再响应取消，否则创建窗口中的进程无人回收。
        process = await protected(asyncio.create_subprocess_exec(
            *rg_command(), "--files", "-0", "--", ".", cwd=context.root,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True,
        ), cancel_event=cancel_event)
    except OSError:
        if cancel_event.is_set():
            raise ToolError("cancelled", "任务取消，搜索候选尚未枚举", not_started=True) from None
        raise ToolError("permission_check_failed", "搜索候选枚举进程无法启动", not_started=True) from None

    async def collect():
        buffer = bytearray()
        candidates: set[str] = set()
        while chunk := await process.stdout.read(8192):
            buffer.extend(chunk)
            while (index := buffer.find(b"\0")) >= 0:
                record = bytes(buffer[:index])
                del buffer[:index + 1]
                path = _candidate(record, context, matcher)
                if path is not None:
                    candidates.add(path)
            if len(buffer) > 64 * 1024:
                raise ToolError("permission_check_failed", "候选路径记录过长，无法完整检查搜索范围", not_started=True)
        if buffer:
            path = _candidate(bytes(buffer), context, matcher)
            if path is not None:
                candidates.add(path)
        code = await process.wait()
        if code not in (0, 1):
            raise ToolError("invalid_pattern", "文件模式无效或目录不可读取，请检查模式和权限")
        return tuple(sorted(candidates))

    async def drain_errors():
        while await process.stderr.read(8192):
            pass

    collection = asyncio.create_task(collect())
    errors = asyncio.create_task(drain_errors())
    cancellation = asyncio.create_task(cancel_event.wait())
    try:
        await asyncio.wait({collection, cancellation}, return_when=asyncio.FIRST_COMPLETED)
        if cancel_event.is_set():
            raise ToolError("cancelled", "任务取消，搜索候选枚举未完成", not_started=True)
        return await collection
    except asyncio.CancelledError:
        cancel_event.set()
        raise ToolError("cancelled", "任务取消，搜索候选枚举未完成", not_started=True) from None
    finally:
        for task in (collection, errors, cancellation):
            task.cancel()
        await protected(_cleanup_metadata(process), cancel_event=cancel_event)
        await protected(asyncio.gather(collection, errors, cancellation, return_exceptions=True),
                        cancel_event=cancel_event)


def _selected_paths(tool, arguments: dict, context: ToolContext) -> tuple[str, ...]:
    matcher = _candidate_matcher(tool, arguments)
    candidates = context.authorized_paths
    if candidates is None:
        candidates = _enumerate_sync(tool, arguments, context)
    selected: set[str] = set()
    for value in candidates:
        path = checked_path(value, context)
        # 授权绑定规范真实路径；候选被换成链接后不能借原批准访问其他目标。
        if path != Path(value) or Path(value).is_symlink() or not path.is_file():
            raise ToolError("permission_check_failed", "获准搜索文件的真实目标已变化，请重新检查权限", not_started=True)
        if matcher(str(path.relative_to(context.root))):
            selected.add(str(path))
    return tuple(sorted(selected))


def _permission_info(context: ToolContext) -> dict:
    return {"permission_limited": context.permission_skipped_files > 0,
            "skipped_files": context.permission_skipped_files}


def _path_batches(paths: tuple[str, ...]):
    """限制单批参数字节与数量，避免一次把整个项目塞进 argv。"""
    batch: list[str] = []
    size = 0
    for path in paths:
        length = len(os.fsencode(path)) + 1
        if batch and (len(batch) >= 256 or size + length > 32 * 1024):
            yield batch
            batch, size = [], 0
        batch.append(path)
        size += length
    if batch:
        yield batch
    elif not paths:
        # 显式 stdin，子进程 stdin=DEVNULL：编译正则但不扫描目录或其他文件。
        yield ["-"]


class GlobFiles:
    read_only = True
    name = "glob_files"
    description = "定位文件路径优先使用本工具：按工作目录相对路径 glob 查找文件，如 **/*.py。跳过隐藏/忽略文件及链接，返回排序路径；默认最多100条，定位后用 read_file 读取内容。"
    input_schema = object_schema({"pattern": PATTERN, "max_results": MAX_RESULTS}, ["pattern"])

    def execute(self, arguments: dict, context: ToolContext) -> ToolResult:
        paths: list[str] = []
        size, truncated = 0, False
        maximum = arguments.get("max_results", 100)
        for candidate in _selected_paths(self, arguments, context):
            value = str(Path(candidate).relative_to(context.root))
            length = len(value.encode("utf-8"))
            if len(paths) >= maximum or size + length > context.output_limit:
                truncated = True
                break
            paths.append(value)
            size += length
        return ToolResult.success({"paths": paths, **_permission_info(context)}, truncated=truncated)


class SearchCode:
    read_only = True
    name = "search_code"
    description = "搜索代码内容优先使用本工具：在工作目录文本文件中按 ripgrep 正则搜索，glob 可限制文件。跳过隐藏/忽略/二进制文件与链接，返回路径、行号和匹配行；默认最多100条，编辑前再用 read_file 读取相关上下文。"
    input_schema = object_schema({"pattern": PATTERN, "glob": PATTERN, "max_results": MAX_RESULTS}, ["pattern"])

    def execute(self, arguments: dict, context: ToolContext) -> ToolResult:
        candidates = _selected_paths(self, arguments, context)
        allowed = {str(Path(path).relative_to(context.root)) for path in candidates}
        prefix = [*rg_command(), "--json", "-e", arguments["pattern"]]
        matches: list[dict] = []
        size, truncated = 0, False
        maximum = arguments.get("max_results", 100)
        pending: list[dict] = []
        pending_size, pending_truncated, selected, valid = 0, False, False, True
        for batch in _path_batches(candidates):
            with child_process([*prefix, "--", *batch], context.root) as process:
                for record in records(process, b"\n", 512 * 1024):
                    if record is None:
                        pending_truncated |= selected
                        continue
                    event = json.loads(record)
                    data = event["data"]
                    if event["type"] == "begin":
                        value = data["path"].get("text")
                        relative = safe_relative(value, context) if value is not None else None
                        selected = relative is not None and relative in allowed
                        pending, pending_size, pending_truncated, valid = [], 0, False, True
                    elif event["type"] == "end":
                        # rg 可能在命中之后才发现 NUL；只提交已确认的文本文件。
                        if selected and valid and data.get("binary_offset") is None:
                            matches.extend(pending)
                            size += pending_size
                            if pending_truncated:
                                truncated = True
                                break
                    elif event["type"] == "match" and selected:
                        text = data["lines"].get("text")
                        if text is None or "\0" in text:
                            valid = False
                            continue
                        if pending_truncated:
                            continue
                        if len(matches) + len(pending) >= maximum:
                            pending_truncated = True
                            continue
                        available = context.output_limit - size - pending_size - len(relative.encode())
                        if available <= 0:
                            pending_truncated = True
                            continue
                        encoded = text.encode()
                        displayed = utf8_prefix(encoded, available)
                        pending.append({"path": relative, "line": data["line_number"], "text": displayed})
                        pending_size += len(relative.encode()) + len(displayed.encode())
                        pending_truncated |= len(encoded) > available
                else:
                    if process.wait() not in (0, 1):
                        raise ToolError("invalid_pattern", "正则表达式无效或文件不可读取，请检查模式和权限")
            if truncated:
                break
        matches.sort(key=lambda match: (match["path"], match["line"]))
        return ToolResult.success({"matches": matches, **_permission_info(context)}, truncated=truncated)
