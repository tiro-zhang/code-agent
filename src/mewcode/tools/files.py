"""读取、新建和唯一原文匹配编辑。"""

import codecs
import os
from pathlib import Path
import stat
import tempfile

from .base import ToolContext, ToolError, ToolResult, object_schema
from .paths import checked_path, open_regular

PATH = {"type": "string", "minLength": 1, "description": "工作目录内的文件路径"}
TEXT = {"type": "string"}


def decode_text(data: bytes) -> str:
    if b"\0" in data:
        raise ToolError("unsupported_encoding", "仅支持不含 NUL 的 UTF-8 文本")
    return data.decode("utf-8")


def stage_file(target: Path, data: bytes, mode: int = 0o600) -> Path:
    fd, name = tempfile.mkstemp(prefix=".mewcode-", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


class ReadFile:
    read_only = True
    name = "read_file"
    description = "读取工作目录内的 UTF-8 文本，支持行范围。只读工具，可用于规划或核对修改后的实际内容。"
    input_schema = object_schema({"path": PATH,
        "start_line": {"type": "integer", "minimum": 1, "default": 1},
        "max_lines": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200}}, ["path"])

    def execute(self, arguments: dict, context: ToolContext) -> ToolResult:
        path = checked_path(arguments["path"], context)
        relative = str(path.relative_to(context.root))
        start, lines = arguments.get("start_line", 1), arguments.get("max_lines", 200)
        budget = max(0, context.output_limit - len(relative.encode()))
        collected = bytearray()
        line = 1
        truncated = False
        decoder = codecs.getincrementaldecoder("utf-8")()
        with open_regular(path) as stream:
            while chunk := stream.readline(8192):
                if line >= start + lines or (line >= start and len(collected) >= budget):
                    truncated = True
                    break
                if b"\0" in chunk:
                    raise ToolError("unsupported_encoding", "文件包含 NUL，不能作为文本读取")
                decoder.decode(chunk)
                if line >= start:
                    available = budget - len(collected)
                    collected.extend(chunk[:available])
                    if len(chunk) > available:
                        truncated = True
                        break
                if chunk.endswith(b"\n"):
                    line += 1
            if not truncated:
                decoder.decode(b"", final=True)
        # 原始数据已经校验过 UTF-8；这里只去掉被字节上限切断的末尾字符。
        text = bytes(collected).decode("utf-8", errors="ignore")
        count = text.count("\n") + int(bool(text) and not text.endswith("\n"))
        return ToolResult.success({"path": relative, "content": text,
            "start_line": start, "end_line": start + count - 1}, truncated=truncated)


class WriteFile:
    read_only = False
    name = "write_file"
    description = "在工作目录内新建 UTF-8 文件，自动创建父目录；目标已存在则拒绝覆盖，请用 edit_file 修改。内容上限 1 MiB。"
    input_schema = object_schema({"path": PATH, "content": TEXT}, ["path", "content"])

    def execute(self, arguments: dict, context: ToolContext) -> ToolResult:
        original = checked_path(arguments["path"], context, resolve=False)
        if os.path.lexists(original):
            raise FileExistsError
        path = checked_path(arguments["path"], context)
        data = arguments["content"].encode("utf-8")
        if len(data) > context.file_limit:
            raise ToolError("input_too_large", "新建文件内容超过 1 MiB 上限")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = stage_file(path, data)
        try:
            checked_path(str(path), context)
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return ToolResult.success({"path": str(path.relative_to(context.root)), "bytes_written": len(data)})


class EditFile:
    read_only = False
    name = "edit_file"
    description = "修改现存 UTF-8 文件：old_text 必须非空且精确匹配一次；零次或多次会报错。请提供足够上下文，文件和新内容上限 1 MiB。"
    input_schema = object_schema({"path": PATH, "old_text": {"type": "string", "minLength": 1},
                                  "new_text": TEXT}, ["path", "old_text", "new_text"])

    def execute(self, arguments: dict, context: ToolContext) -> ToolResult:
        path = checked_path(arguments["path"], context)
        with open_regular(path) as stream:
            before_stat = os.fstat(stream.fileno())
            before = stream.read(context.file_limit + 1)
        if len(before) > context.file_limit:
            raise ToolError("input_too_large", "编辑目标超过 1 MiB 上限")
        text = decode_text(before)
        old, new = arguments["old_text"], arguments["new_text"]
        if not old:
            raise ToolError("invalid_arguments", "old_text 不能为空")
        matches, first, position = 0, -1, 0
        while (position := text.find(old, position)) >= 0:
            if first < 0:
                first = position
            matches += 1
            position += 1
        if matches == 0:
            raise ToolError("text_not_found", "原文未匹配，请重新读取文件确认内容", matches=0)
        if matches != 1:
            raise ToolError("multiple_matches", "原文匹配多次，请扩大原文上下文", matches=matches)
        after = (text[:first] + new + text[first + len(old):]).encode("utf-8")
        if len(after) > context.file_limit:
            raise ToolError("input_too_large", "编辑后的内容超过 1 MiB 上限")
        temporary = stage_file(path, after, stat.S_IMODE(before_stat.st_mode))
        try:
            checked_path(str(path), context)
            with open_regular(path) as stream:
                now_stat = os.fstat(stream.fileno())
                current = stream.read(context.file_limit + 1)
            if (now_stat.st_ino, now_stat.st_mtime_ns, current) != (before_stat.st_ino, before_stat.st_mtime_ns, before):
                raise ToolError("file_changed", "文件已被其他操作修改，请重新读取后再编辑")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return ToolResult.success({"path": str(path.relative_to(context.root)), "replacements": 1})
