"""启动时固定的两层 MCP 配置与连接身份；错误不携带秘密。"""

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

from mcp.client.stdio import DEFAULT_INHERITED_ENV_VARS
import yaml


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class Diagnostic:
    server: str
    stage: str
    message: str


@dataclass(frozen=True)
class ServerConfig:
    name: str
    transport: str
    fingerprint: str
    command: str = field(default="", repr=False)
    args: tuple[str, ...] = field(default=(), repr=False)
    env: dict[str, str] = field(default_factory=dict, repr=False)
    cwd: str = field(default="", repr=False)
    url: str = field(default="", repr=False)
    headers: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def safe_description(self):
        if self.transport == "stdio":
            return "stdio · " + self.command
        parsed = urlsplit(self.url)
        # URL 路径、查询和片段都可能承载凭据，仅展示已禁止 userinfo 的来源。
        return "HTTP · " + parsed.scheme + "://" + parsed.netloc


@dataclass(frozen=True)
class ConfigSnapshot:
    root: Path
    servers: tuple[ServerConfig, ...]
    diagnostics: tuple[Diagnostic, ...]


class ConfigError(ValueError):
    pass


class StrictLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in result:
                    raise ConfigError("YAML 含重复键")
                result[key] = self.construct_object(value_node, deep=deep)
            except TypeError:
                raise ConfigError("YAML 映射键无效") from None
        return result


def _document(path):
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError:
        raise ConfigError("无法读取 MCP 配置") from None
    try:
        loader = StrictLoader(raw)
        try:
            node = loader.get_single_node()
            empty = node is None or (isinstance(node, yaml.ScalarNode) and node.tag == "tag:yaml.org,2002:null" and node.value == "")
            doc = {} if empty else loader.construct_document(node)
        finally:
            loader.dispose()
    except (yaml.YAMLError, UnicodeError, RecursionError):
        raise ConfigError("YAML 格式无效或包含不安全标签") from None
    if not isinstance(doc, dict) or not set(doc).issubset({"version", "mcpServers"}):
        raise ConfigError("顶层必须是映射且不得含未知字段")
    if type(doc.get("version", 1)) is not int or doc.get("version", 1) != 1:
        raise ConfigError("仅支持 version: 1")
    entries = doc.get("mcpServers", {})
    if not isinstance(entries, dict) or any(not isinstance(key, str) or not key.strip() or "\x00" in key for key in entries):
        raise ConfigError("mcpServers 必须是以非空 Server 名为键的映射")
    return entries


def _string(value):
    return isinstance(value, str) and "\x00" not in value


def _expand(value, environment):
    def replace(match):
        name = match[1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
            raise ConfigError("变量表达式只支持 ${VAR}")
        if name not in environment:
            raise ConfigError(f"缺少环境变量 {name}")
        return environment[name]
    # 先检查原模板，再单遍替换；替换结果中的美元符号保持字面值。
    if "${" in re.sub(r"\$\{[^}]*\}", "", value):
        raise ConfigError("变量表达式不完整")
    result = re.sub(r"\$\{([^}]*)\}", replace, value)
    if "\x00" in result:
        raise ConfigError("变量值包含无效字符")
    return result


def _strings(value, environment, label):
    if not isinstance(value, dict) or any(not _string(k) or not k or not _string(v) for k, v in value.items()):
        raise ConfigError(f"{label} 必须是字符串映射")
    return {key: _expand(item, environment) for key, item in value.items()}


def _server(name, entry, root, environment):
    if not isinstance(entry, dict) or entry.get("transport") not in ("stdio", "http"):
        raise ConfigError("transport 必须为 stdio 或 http")
    transport = entry["transport"]
    identity = {"name": name, "transport": transport}
    if transport == "stdio":
        if not set(entry).issubset({"transport", "command", "args", "env"}):
            raise ConfigError("stdio 含未知或混用字段")
        command, args = entry.get("command"), entry.get("args", [])
        if not _string(command) or not command.strip() or not isinstance(args, list) or not all(_string(a) for a in args):
            raise ConfigError("command 必须非空，args 必须为字符串列表")
        env = {k: environment[k] for k in DEFAULT_INHERITED_ENV_VARS if k in environment and not environment[k].startswith("()")}
        env.update(_strings(entry.get("env", {}), environment, "env"))
        if any("=" in k for k in env):
            raise ConfigError("env 键不能包含等号")
        # 相对路径与相对 PATH 分量以固定项目根解析，禁止依赖后续 cwd 变化。
        search = os.pathsep.join(str(root / p) if not Path(p).is_absolute() else p
                                for p in env.get("PATH", os.defpath).split(os.pathsep))
        target = str(root / command) if os.path.dirname(command) and not Path(command).is_absolute() else command
        resolved = shutil.which(target, path=search)
        if resolved is None:
            raise ConfigError("无法解析可执行程序")
        # 保留符号链接入口：例如 venv 的 python 依赖入口目录定位自己的依赖。
        # 真实目标另纳入身份，既不改变启动语义，也不复用已更换程序的批准。
        identity["command_target"] = str(Path(resolved).resolve())
        fields = {"command": os.path.abspath(resolved), "args": tuple(args), "env": env, "cwd": str(root)}
    else:
        if not set(entry).issubset({"transport", "url", "headers"}):
            raise ConfigError("http 含未知或混用字段")
        url = entry.get("url")
        if not _string(url):
            raise ConfigError("url 必须是 HTTP(S) 地址")
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.port == 0:
                raise ValueError
            if any(c.isspace() for c in url):
                raise ValueError
        except ValueError:
            raise ConfigError("url 必须是无 userinfo 的 HTTP(S) 地址") from None
        headers = _strings(entry.get("headers", {}), environment, "headers")
        if any("\n" in k + v or "\r" in k + v for k, v in headers.items()):
            raise ConfigError("headers 包含无效换行")
        fields = {"url": url, "headers": headers}
    fingerprint = hashlib.sha256(canonical(identity | fields).encode()).hexdigest()
    return ServerConfig(name, transport, fingerprint, **fields)


def load_config(root: Path, *, user_path: Path | None = None, environment: dict[str, str] | None = None) -> ConfigSnapshot:
    root = Path(root).resolve()
    environment = dict(os.environ if environment is None else environment)
    user_path = user_path if user_path is not None else Path.home() / ".mewcode" / "mcp.yaml"
    try:
        merged = _document(user_path) | _document(root / ".mewcode" / "mcp.yaml")
    except (ConfigError, RecursionError) as error:
        message = str(error) if isinstance(error, ConfigError) else "配置嵌套过深"
        return ConfigSnapshot(root, (), (Diagnostic("", "配置", message),))
    servers, diagnostics = [], []
    for name, entry in sorted(merged.items()):
        try:
            servers.append(_server(name, entry, root, environment))
        except (ConfigError, ValueError, OSError, RecursionError) as error:
            message = str(error) if isinstance(error, ConfigError) else "Server 配置无效"
            diagnostics.append(Diagnostic(name, "配置", message))
    return ConfigSnapshot(root, tuple(servers), tuple(diagnostics))
