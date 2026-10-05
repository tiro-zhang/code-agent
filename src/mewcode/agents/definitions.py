"""有界角色入口、四层完整覆盖及原子目录发布。"""

from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat

import yaml

from ..tools.base import OUTPUT_LIMIT, ToolError


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


@dataclass(frozen=True)
class AgentRole:
    name: str
    description: str
    body: str
    path: Path
    layer: str
    fingerprint: str
    allowed_tools: frozenset[str] | None = None
    disallowed_tools: frozenset[str] = frozenset()
    model: str = "inherit"
    max_iterations: int = 20
    permission_mode: str = "inherit"
    isolation: str | None = None

    def tools(self, parent_tools) -> frozenset[str]:
        """只收紧父范围；系统入口同样遵守白黑名单。"""
        allowed = frozenset(parent_tools)
        if self.allowed_tools is not None:
            allowed &= self.allowed_tools
        return allowed - self.disallowed_tools


def _read_entry(path: Path) -> bytes:
    """逐级拒绝链接，避免检查与读取之间的路径替换。"""
    try:
        with ExitStack() as stack:
            descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, descriptor)
            for part in path.parts[1:-1]:
                descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                stack.callback(os.close, descriptor)
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            stack.callback(os.close, descriptor)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("只允许普通文件")
            if info.st_size > OUTPUT_LIMIT:
                raise ValueError("角色文件超过 64 KiB")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                raw = stream.read(OUTPUT_LIMIT + 1)
            if len(raw) > OUTPUT_LIMIT:
                raise ValueError("角色文件超过 64 KiB")
            return raw
    except (OSError, ValueError) as error:
        message = str(error) if isinstance(error, ValueError) else "无法安全读取，禁止链接及特殊文件"
        raise ToolError("invalid_agent_definition", message, not_started=True) from None


def _tools(metadata, field, default):
    if field not in metadata:
        return default
    value = metadata[field]
    if (not isinstance(value, list) or any(not isinstance(name, str) or not name
            or any(c.isspace() or not c.isprintable() for c in name) for name in value)):
        raise ValueError(f"{field} 必须是精确工具名列表")
    return frozenset(value)


def parse_role(path: Path, *, layer: str) -> AgentRole:
    path = Path(os.path.abspath(path))
    raw = _read_entry(path)
    try:
        text = raw.decode("utf-8")
        if "\x00" in text:
            raise ValueError("角色文本不得含 NUL")
        match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)(.*)\Z", text, re.DOTALL)
        if not match or not match[2].strip():
            raise ValueError("缺少 YAML frontmatter 或非空角色正文")
        metadata = yaml.load(match[1], Loader=_StrictLoader)
        fields = {"name", "description", "allowed-tools", "disallowed-tools", "model", "max-iterations", "permission-mode", "isolation"}
        if not isinstance(metadata, dict) or not metadata.keys() <= fields:
            raise ValueError("元信息必须是映射且不得含未知字段")
        name, description = metadata.get("name"), metadata.get("description")
        if not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) is None:
            raise ValueError("name 必须是小写规范标识")
        if not isinstance(description, str) or not description.strip() or len(description.splitlines()) != 1:
            raise ValueError("description 必须是非空单行说明")
        model, permission = metadata.get("model", "inherit"), metadata.get("permission-mode", "inherit")
        if model not in {"inherit", "haiku", "sonnet", "opus"}:
            raise ValueError("model 必须为 inherit、haiku、sonnet 或 opus")
        if permission not in {"inherit", "strict", "default", "bypass"}:
            raise ValueError("permission-mode 无效")
        isolation = metadata.get("isolation")
        if "isolation" in metadata and isolation != "worktree":
            raise ValueError("isolation 仅接受 worktree；共享模式请省略该字段")
        iterations = metadata.get("max-iterations", 20)
        if type(iterations) is not int or iterations <= 0:
            raise ValueError("max-iterations 必须为正整数")
        return AgentRole(name, description, match[2], path, layer,
                         hashlib.sha256(str(path).encode() + b"\x00" + raw).hexdigest(),
                         _tools(metadata, "allowed-tools", None),
                         _tools(metadata, "disallowed-tools", frozenset()), model, iterations, permission, isolation)
    except (yaml.YAMLError, ValueError, TypeError, RecursionError) as error:
        message = str(error) if type(error) is ValueError else "角色元信息或 UTF-8 无效"
        raise ToolError("invalid_agent_definition", message, not_started=True) from None


@dataclass(frozen=True)
class RoleCatalog:
    roles: tuple[AgentRole, ...]
    warnings: tuple[str, ...] = ()

    def get(self, name: str) -> AgentRole:
        for role in self.roles:
            if role.name == name:
                return role
        raise ToolError("agent_role_not_found", f"未发现 Agent 角色：{name}", not_started=True)

    def index_text(self) -> str:
        return "\n".join(f"- {role.name}: {role.description}" for role in self.roles)

    def validate_tools(self, registered) -> None:
        for role in self.roles:
            unknown = ((role.allowed_tools or frozenset()) | role.disallowed_tools) - set(registered)
            if unknown:
                raise ValueError(f"Agent {role.name}（{role.path}）包含未知工具：{', '.join(sorted(unknown))}")


def discover_roles(root: Path, *, user_root: Path | None = None,
                   builtin_root: Path | None = None, plugin_dirs=()) -> RoleCatalog:
    user = Path(user_root) if user_root is not None else Path.home() / ".mewcode"
    builtin = Path(builtin_root) if builtin_root is not None else Path(__file__).parent / "builtin"
    layers = (("plugin", tuple(Path(path) for path in plugin_dirs)), ("builtin", (builtin,)),
              ("user", (user / "agents",)), ("project", (Path(root) / ".mewcode" / "agents",)))
    result, warnings = {}, []
    for layer, directories in layers:
        current = {}
        for directory in directories:
            try:
                if directory.is_symlink():
                    raise OSError("目录是链接")
                paths = sorted(directory.iterdir())
            except FileNotFoundError:
                continue
            except OSError:
                warnings.append(f"Agent 目录 {directory} 无法安全读取，已跳过")
                continue
            for path in paths:
                if path.suffix != ".md":
                    continue
                try:
                    role = parse_role(path, layer=layer)
                except ToolError as error:
                    warnings.append(f"Agent {path}：{error.message}，已跳过")
                    continue
                if role.name in current:
                    raise ValueError(f"同层 Agent 重名 {role.name}：{current[role.name].path} 与 {path}")
                current[role.name] = role
        result.update(current)
    return RoleCatalog(tuple(result[name] for name in sorted(result)), tuple(warnings))


class RoleStore:
    """校验成功后才发布整代目录，任务持有不可变定义。"""

    def __init__(self, root: Path, registered, **options):
        self.root, self.registered, self.options = root, frozenset(registered), options
        self.refresh()

    def refresh(self) -> RoleCatalog:
        candidate = discover_roles(self.root, **self.options)
        candidate.validate_tools(self.registered)
        self.catalog = candidate
        return candidate
