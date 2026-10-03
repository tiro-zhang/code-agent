"""文件工具的工作目录与普通文件边界。"""

import os
from pathlib import Path
import stat
from typing import BinaryIO

from .base import ToolContext, ToolError


def checked_path(value: str, context: ToolContext, *, resolve: bool = True) -> Path:
    if not value or "\0" in value:
        raise ToolError("invalid_arguments", "路径不能为空或含 NUL")
    original = Path(value) if Path(value).is_absolute() else context.root / value
    try:
        target = original.resolve()
    except (ValueError, RuntimeError):
        raise ToolError("invalid_arguments", "路径无法解析或包含循环链接") from None
    if not target.is_relative_to(context.root):
        raise ToolError("path_outside_workspace", "文件路径超出启动工作目录")
    if context.authorized_paths is not None and str(target) not in context.authorized_paths:
        raise ToolError("permission_denied", "实际文件目标不在本次批准范围内", not_started=True)
    return target if resolve else original


def open_regular(path: Path) -> BinaryIO:
    """非阻塞打开后再检查，避免检查与打开之间换成 FIFO 导致挂起。"""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ToolError("not_regular_file", "目标不是普通文件")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
