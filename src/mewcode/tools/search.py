"""通过 ripgrep 查找路径和内容，不经 shell 拼接参数。"""

import json
from fnmatch import fnmatchcase
from functools import lru_cache
import shutil

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


class GlobFiles:
    read_only = True
    name = "glob_files"
    description = "按工作目录相对路径 glob 查找文件，如 **/*.py。跳过隐藏/忽略文件及链接，返回排序路径；默认最多100条。"
    input_schema = object_schema({"pattern": PATTERN, "max_results": MAX_RESULTS}, ["pattern"])

    def execute(self, arguments: dict, context: ToolContext) -> ToolResult:
        matches = path_matcher(arguments["pattern"])
        argv = [*rg_command(), "--files", "-0", "--", "."]
        paths: list[str] = []
        size, truncated = 0, False
        maximum = arguments.get("max_results", 100)
        with child_process(argv, context.root) as process:
            for record in records(process, b"\0", context.output_limit):
                if record is None:
                    truncated = True
                    continue
                value = safe_relative(record.decode("utf-8", errors="surrogateescape"), context)
                if value is None or not matches(value):
                    continue
                try:
                    length = len(value.encode("utf-8"))
                except UnicodeError:
                    continue
                if len(paths) >= maximum or size + length > context.output_limit:
                    truncated = True
                    break
                paths.append(value)
                size += length
            else:
                if process.wait() not in (0, 1):
                    raise ToolError("invalid_pattern", "文件模式无效或目录不可读取，请检查模式和权限")
        return ToolResult.success({"paths": sorted(paths)}, truncated=truncated)


class SearchCode:
    read_only = True
    name = "search_code"
    description = "在工作目录文本文件中按 ripgrep 正则搜索，glob 可限制文件。跳过隐藏/忽略/二进制文件与链接，返回路径、行号和匹配行；默认最多100条。"
    input_schema = object_schema({"pattern": PATTERN, "glob": PATTERN, "max_results": MAX_RESULTS}, ["pattern"])

    def execute(self, arguments: dict, context: ToolContext) -> ToolResult:
        matches_path = path_matcher(arguments.get("glob", "**"))
        argv = [*rg_command(), "--json", "-e", arguments["pattern"]]
        argv += ["--", "."]
        matches: list[dict] = []
        size, truncated = 0, False
        maximum = arguments.get("max_results", 100)
        pending: list[dict] = []
        pending_size, pending_truncated, selected, valid = 0, False, False, True
        with child_process(argv, context.root) as process:
            for record in records(process, b"\n", 512 * 1024):
                if record is None:
                    pending_truncated |= selected
                    continue
                event = json.loads(record)
                data = event["data"]
                if event["type"] == "begin":
                    value = data["path"].get("text")
                    relative = safe_relative(value, context) if value is not None else None
                    selected = relative is not None and matches_path(relative)
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
        matches.sort(key=lambda match: (match["path"], match["line"]))
        return ToolResult.success({"matches": matches}, truncated=truncated)
