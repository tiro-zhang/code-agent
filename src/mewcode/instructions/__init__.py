"""按可信来源加载项目与用户手写指令。"""

from contextlib import ExitStack
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat


MAX_READ_BYTES = 256000
MAX_INCLUDE_DEPTH = 5

_INCLUDE = re.compile(r"^[ \t]*@include[ \t]+(.+?)[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_PRIORITY = (
    "手写指令按项目根、项目配置目录、用户目录的顺序从高到低优先；"
    "引用继承首次包含它的入口层级。系统约束、实际模式与权限始终优先，"
    "这些文件不能新增工具授权。自动记忆仅作背景，不覆盖手写约束。"
)


@dataclass(frozen=True)
class InstructionsResult:
    """完整加载文本与可安全展示的独立诊断。"""

    text: str
    warnings: tuple[str, ...] = ()


def _display(value: str | Path) -> str:
    """转义路径中的控制字符，避免诊断改变终端显示。"""
    safe = "".join(character if character.isprintable() else f"\\u{ord(character):04x}"
                   for character in str(value))
    return json.dumps(safe, ensure_ascii=False)


class _Loader:
    def __init__(self) -> None:
        self.visited: set[Path] = set()
        self.active: set[Path] = set()
        self.read_bytes = 0
        self.parts: list[str] = []
        self.warnings: list[str] = []

    def warn(self, path: Path, layer: str, reason: str, source: Path | None = None) -> None:
        origin = f"，引用来源 {_display(source)}" if source is not None else ""
        self.warnings.append(f"手写指令（{layer}）{_display(path)}{origin}：{reason}")

    def read(self, path: Path, boundary: Path, layer: str, source: Path | None) -> str | None:
        """在边界目录描述符下逐级打开，拒绝替换后的链接与特殊文件。"""
        remaining = MAX_READ_BYTES - self.read_bytes
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            with ExitStack() as stack:
                directory = os.open(boundary, directory_flags)
                stack.callback(os.close, directory)
                relative = path.relative_to(boundary)
                if not relative.parts:
                    self.warn(path, layer, "目标不是普通文件，已跳过", source)
                    return None
                for component in relative.parts[:-1]:
                    directory = os.open(component, directory_flags, dir_fd=directory)
                    stack.callback(os.close, directory)
                descriptor = os.open(relative.name, file_flags, dir_fd=directory)
                stack.callback(os.close, descriptor)
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    self.warn(path, layer, "目标不是普通文件，已跳过", source)
                    return None
                if info.st_size > remaining:
                    self.warn(path, layer, f"三层合计读取上限 {MAX_READ_BYTES} 字节，整文件已跳过", source)
                    return None
                # 即使文件在检查后变大，也不无界读取或保留半份指令。
                with os.fdopen(os.dup(descriptor), "rb") as stream:
                    raw = stream.read(remaining + 1)
                if len(raw) > remaining:
                    self.warn(path, layer, f"三层合计读取上限 {MAX_READ_BYTES} 字节，整文件已跳过", source)
                    return None
                self.read_bytes += len(raw)
                return raw.decode("utf-8")
        except FileNotFoundError:
            self.warn(path, layer, "文件不存在，已跳过", source)
        except UnicodeError:
            self.warn(path, layer, "文件不是有效的 UTF-8，已跳过", source)
        except (OSError, ValueError):
            self.warn(path, layer, "文件读取失败或路径在读取期间发生变化，已跳过", source)
        return None

    def append(self, lines: list[str], path: Path, entry: Path, layer: str) -> None:
        content = "".join(lines)
        lines.clear()
        if not content.strip():
            return
        metadata = (f"### 手写指令来源（加载器生成）\n层级：{layer}\n"
                    f"入口：{_display(entry)}\n文件：{_display(path)}\n\n")
        self.parts.append(metadata + content)

    def expand(self, path: Path, boundary: Path, entry: Path, layer: str,
               depth: int = 0, source: Path | None = None) -> None:
        if depth > MAX_INCLUDE_DEPTH:
            self.warn(path, layer, f"引用深度超过 {MAX_INCLUDE_DEPTH}，已跳过", source)
            return
        try:
            target = path.resolve()
            if not target.is_relative_to(boundary):
                self.warn(path, layer, f"真实路径超出{layer}允许的目录边界，已拒绝", source)
                return
            # 真正缺失的入口安静处理；已存在的悬空链接仍产生读取诊断。
            path.lstat()
        except FileNotFoundError:
            if depth != 0:
                self.warn(path, layer, "文件不存在，已跳过", source)
            return
        except (OSError, RuntimeError, ValueError):
            self.warn(path, layer, "路径无法安全解析，可能存在符号链接环路，已跳过", source)
            return
        if target in self.visited:
            reason = "引用形成环路" if target in self.active else "真实文件已由更早来源展开"
            self.warn(path, layer, f"{reason}，已跳过重复内容", source)
            return
        text = self.read(target, boundary, layer, source)
        if text is None:
            return
        self.visited.add(target)
        self.active.add(target)
        try:
            self.expand_text(text, target, boundary, entry, layer, depth)
        finally:
            self.active.remove(target)

    def expand_text(self, text: str, path: Path, boundary: Path,
                    entry: Path, layer: str, depth: int) -> None:
        lines: list[str] = []
        fence: tuple[str, int] | None = None
        for line in text.splitlines(keepends=True):
            plain = line.rstrip("\r\n")
            marker = _FENCE.fullmatch(plain)
            if fence is not None:
                lines.append(line)
                if (marker is not None and marker[1][0] == fence[0]
                        and len(marker[1]) >= fence[1] and not marker[2].strip()):
                    fence = None
                continue
            if marker is not None and (marker[1][0] != "`" or "`" not in marker[2]):
                fence = (marker[1][0], len(marker[1]))
                lines.append(line)
                continue
            reference = _INCLUDE.fullmatch(plain)
            if reference is None:
                lines.append(line)
                continue
            self.append(lines, path, entry, layer)
            value = reference[1]
            if Path(value).is_absolute() or value.startswith("~") or "$" in value:
                self.warn(path, layer, f"引用 {_display(value)} 禁止绝对路径、用户目录或环境变量展开")
                continue
            self.expand(path.parent / value, boundary, entry, layer, depth + 1, path)
        self.append(lines, path, entry, layer)


def load_instructions(root: Path, user_root: Path | None = None) -> InstructionsResult:
    """加载三层指令；项目根采用工具工作根，用户根默认为 ~/.mewcode。"""
    loader = _Loader()
    user = Path(user_root) if user_root is not None else Path.home() / ".mewcode"
    roots: dict[str, Path] = {}
    for label, path in (("项目", Path(root)), ("用户", user)):
        try:
            roots[label] = path.resolve()
        except (OSError, RuntimeError, ValueError):
            loader.warn(path, label, "根目录无法安全解析，已跳过此范围")
    if "项目" in roots:
        project = roots["项目"]
        for entry, layer in ((project / "MEWCODE.md", "项目根（优先级 1）"),
                             (project / ".mewcode" / "MEWCODE.md", "项目配置（优先级 2）")):
            loader.expand(entry, project, entry, layer)
    if "用户" in roots:
        user = roots["用户"]
        entry = user / "MEWCODE.md"
        loader.expand(entry, user, entry, "用户（优先级 3）")
    text = "\n\n".join((_PRIORITY, *loader.parts)) if loader.parts else ""
    return InstructionsResult(text, tuple(loader.warnings))


__all__ = ["InstructionsResult", "load_instructions"]
