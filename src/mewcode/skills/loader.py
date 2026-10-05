"""严格元信息解析和无链接的有界包读取。"""

from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import re
import stat

import yaml

from ..tools.base import OUTPUT_LIMIT, ToolError
from .models import Skill


class _StrictLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in result:
                    raise ValueError("重复 YAML 字段")
                result[key] = self.construct_object(value_node, deep=deep)
            except TypeError:
                raise ValueError("YAML 键必须是标量") from None
        return result


def _read(root: Path, relative: Path) -> bytes:
    """逐级打开目录描述符，拒绝路径替换后的链接和特殊文件。"""
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ToolError("skill_resource_denied", "资源必须是包内相对路径")
    try:
        with ExitStack() as stack:
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, descriptor)
            for part in relative.parts[:-1]:
                descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                stack.callback(os.close, descriptor)
            descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            stack.callback(os.close, descriptor)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ToolError("skill_resource_denied", "Skill 只允许普通文件")
            if info.st_size > OUTPUT_LIMIT:
                raise ToolError("skill_content_too_large", "Skill 文件超过 64 KiB，请拆分参考内容")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                raw = stream.read(OUTPUT_LIMIT + 1)
            if len(raw) > OUTPUT_LIMIT:
                raise ToolError("skill_content_too_large", "Skill 文件超过 64 KiB")
            return raw
    except (OSError, ValueError):
        raise ToolError("skill_resource_denied", "Skill 文件无法安全读取，禁止链接和特殊文件") from None


def _text(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
        if "\x00" in text:
            raise UnicodeError
        return text
    except UnicodeError:
        raise ToolError("invalid_skill", "Skill 只支持不含 NUL 的 UTF-8 文本") from None


def parse_skill(path: Path, *, layer: str, package: bool = False) -> Skill:
    path = Path(os.path.abspath(path))
    # 从文件系统根逐级检查，祖先链接同样不能开放包边界。
    raw = _read(Path(path.anchor), path.relative_to(path.anchor))
    text = _text(raw)
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)(.*)\Z", text, re.DOTALL)
    if not match or not match[2].strip():
        raise ToolError("invalid_skill", "缺少 YAML frontmatter 或非空 SOP 正文")
    try:
        metadata = yaml.load(match[1], Loader=_StrictLoader)
        fields = {"name", "description", "allowed-tools", "mode", "history", "model"}
        if not isinstance(metadata, dict) or not set(metadata) <= fields:
            raise ValueError("元信息必须是映射且不得含未知字段")
        name, description, mode = (metadata.get(key) for key in ("name", "description", "mode"))
        if not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) is None:
            raise ValueError("name 必须是小写规范标识")
        if (not isinstance(description, str) or not description.strip()
                or len(description.splitlines()) != 1):
            raise ValueError("description 必须是非空单行说明")
        if mode not in ("shared", "isolated"):
            raise ValueError("mode 必须为 shared 或 isolated")
        if mode == "shared" and ("history" in metadata or "model" in metadata):
            raise ValueError("共享模式不得指定 history 或 model")
        history = metadata.get("history", 0)
        if not (type(history) is int and history >= 0) and history != "all":
            raise ValueError("history 必须为非负整数或 all")
        model = metadata.get("model")
        if "model" in metadata and (not isinstance(model, str) or not model.strip()):
            raise ValueError("model 必须是非空模型 ID")
        allowed = metadata.get("allowed-tools")
        if "allowed-tools" in metadata:
            if (not isinstance(allowed, list) or any(not isinstance(name, str) or not name.strip() for name in allowed)):
                raise ValueError("allowed-tools 必须是精确工具名字符串列表")
            allowed = frozenset(allowed)
        return Skill(name, description, mode, allowed, history, model, path, layer, package,
                     raw, match[2], hashlib.sha256(str(path).encode() + b"\x00" + raw).hexdigest())
    except (yaml.YAMLError, ValueError, TypeError, RecursionError) as error:
        raise ToolError("invalid_skill", f"Skill 元信息无效：{type(error).__name__}") from None


def read_resource(skill: Skill, resource: str) -> str:
    if not skill.package:
        raise ToolError("skill_resource_denied", "单文件 Skill 不开放邻近资源")
    path = Path(resource)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ToolError("skill_resource_denied", "资源必须是包内相对路径")
    # 包根及其祖先重新逐级检查，避免任务快照后替换父目录链接。
    root = skill.path.parent
    return _text(_read(Path(root.anchor), root.relative_to(root.anchor) / path))
