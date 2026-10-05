"""严格 YAML 配置和保留最新规则的原子批准存储。"""

from collections.abc import Sequence
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import tempfile

import yaml

from .models import Approval, PolicySnapshot, Rule
from ..mcp.config import canonical
from ..mcp.tools import is_mcp_alias
from ..mcp.permissions import validate_grant
from ..tools.base import strict_json


TOOLS = frozenset(("read_file", "write_file", "edit_file", "execute_command", "glob_files", "search_code", "load_skill"))


class PermissionConfigError(ValueError):
    """权限配置不能安全读取或提交。"""


class _StrictLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in result:
                    raise yaml.constructor.ConstructorError(None, None, "重复映射键", key_node.start_mark)
                result[key] = self.construct_object(value_node, deep=deep)
            except TypeError as error:
                raise yaml.constructor.ConstructorError(None, None, "映射键必须可比较", key_node.start_mark) from error
        return result


def _fail(path: Path, reason: str):
    raise PermissionConfigError(f"权限配置 {path}：{reason}")


def _mapping(value, expected: set[str], path: Path, label: str):
    if not isinstance(value, dict) or set(value) != expected:
        _fail(path, f"{label}字段缺失或含未知字段")


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\x00" not in value


def _absolute_normal(value) -> bool:
    return _text(value) and Path(value).is_absolute() and os.path.normpath(value) == value


def _approval(value, path: Path) -> Approval:
    _mapping(value, {"id", "root", "tool", "scope"}, path, "批准记录")
    _mapping(value["scope"], {"kind", "value"}, path, "批准范围")
    if not _text(value["id"]) or not _absolute_normal(value["root"]):
        _fail(path, "批准标识或真实项目根无效")
    tool, kind, target = value["tool"], value["scope"]["kind"], value["scope"]["value"]
    if not isinstance(tool, str) or (tool not in TOOLS and not is_mcp_alias(tool)) or not _text(target):
        _fail(path, "批准工具或范围无效")
    if is_mcp_alias(tool):
        if kind != "mcp":
            _fail(path, "外部工具批准必须使用 mcp 范围")
        try:
            target = validate_grant(tool, target)
        except (ValueError, TypeError, RecursionError):
            _fail(path, "外部工具批准身份或参数无效")
    elif tool in {"execute_command", "load_skill"}:
        if kind != "command":
            _fail(path, "命令工具批准必须使用精确 command 范围")
    elif kind != "path" or not _absolute_normal(target) or not Path(target).is_relative_to(Path(value["root"])):
        _fail(path, "文件批准必须使用项目内的真实绝对 path")
    return Approval(value["id"], value["root"], tool, kind, target)


def _document(raw: bytes | None, path: Path, local: bool):
    try:
        if raw is None:
            doc = {}
        else:
            loader = _StrictLoader(raw)
            try:
                node = loader.get_single_node()
                empty = node is None or (isinstance(node, yaml.ScalarNode) and node.tag == "tag:yaml.org,2002:null" and node.value == "")
                doc = {} if empty else loader.construct_document(node)
            finally:
                loader.dispose()
    except (yaml.YAMLError, UnicodeError, RecursionError) as error:
        _fail(path, "YAML 格式无效、含重复键或不安全对象")
    allowed = {"version", "rules", "approvals"} if local else {"version", "rules"}
    if not isinstance(doc, dict) or not set(doc).issubset(allowed):
        _fail(path, "顶层必须是映射且不得含未知字段")
    version = doc.get("version", 1)
    if type(version) is not int or version != 1:
        _fail(path, "仅支持 version: 1")
    entries = doc.get("rules", [])
    if not isinstance(entries, list):
        _fail(path, "rules 必须是列表")
    rules = []
    for entry in entries:
        _mapping(entry, {"effect", "rule", "match"}, path, "规则")
        if entry["effect"] not in ("allow", "ask", "deny") or entry["match"] not in ("exact", "glob"):
            _fail(path, "规则 effect 或 match 无效")
        expression = entry["rule"]
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\((.*)\)", expression, re.DOTALL) if isinstance(expression, str) else None
        if match is None or not _text(match[2]):
            _fail(path, "rule 必须使用非空的工具名(模式)表达式")
        tool = "execute_command" if match[1] == "Bash" else match[1]
        if tool not in TOOLS and not is_mcp_alias(tool):
            _fail(path, "规则引用未知工具")
        pattern = match[2]
        if is_mcp_alias(tool) and entry["match"] == "exact":
            try:
                arguments = strict_json(pattern)
                if not isinstance(arguments, dict):
                    raise ValueError
                pattern = canonical(arguments)
            except (ValueError, TypeError, RecursionError):
                _fail(path, "外部 exact 规则必须包含完整 JSON 对象参数")
        rules.append(Rule(entry["effect"], tool, pattern, entry["match"], str(path)))
    entries = doc.get("approvals", [])
    if not isinstance(entries, list):
        _fail(path, "approvals 必须是列表")
    approvals = tuple(_approval(entry, path) for entry in entries)
    if len({a.id for a in approvals}) != len(approvals):
        _fail(path, "批准标识不得重复")
    return doc, tuple(rules), approvals


class PermissionConfig:
    def __init__(self, root: Path, *, user_path: Path | None = None):
        self.root = Path(root).resolve()
        self.paths = (
            Path(user_path) if user_path is not None else Path.home() / ".mewcode" / "permissions.yaml",
            self.root / ".mewcode" / "permissions.yaml",
            self.root / ".mewcode" / "permissions.local.yaml",
        )

    def protected_paths(self) -> tuple[Path, ...]:
        return tuple(path.resolve() for path in self.paths)

    def _read_sources(self):
        sources = []
        for path in self.paths:
            try:
                target = path.resolve()
                raw = path.read_bytes()
            except FileNotFoundError:
                raw = None
            except (OSError, RuntimeError) as error:
                _fail(path, "无法读取配置文件")
            sources.append((str(target), raw))
        return tuple(sources)

    def _snapshot(self, sources):
        rules, approvals, documents = [], [], []
        digest = hashlib.sha256()
        for index, (target, raw) in enumerate(sources):
            digest.update(target.encode("utf-8"))
            digest.update(b"\x00" + (b"missing" if raw is None else b"present" + raw) + b"\x00")
            doc, file_rules, file_approvals = _document(raw, self.paths[index], index == 2)
            documents.append(doc)
            rules.extend(file_rules)
            approvals.extend(file_approvals)
        return PolicySnapshot(tuple(rules), tuple(approvals), digest.hexdigest()), documents

    def load(self) -> PolicySnapshot:
        return self._snapshot(self._read_sources())[0]

    @contextmanager
    def _write_lock(self):
        path = self.paths[2]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with (path.parent / ".permissions.lock").open("a+b") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except OSError as error:
            _fail(path, "无法锁定或提交批准存储")

    def _update(self, additions: Sequence[Approval] = (), *, revoke: bool = False):
        path = self.paths[2]
        with self._write_lock():
            sources = self._read_sources()
            snapshot, documents = self._snapshot(sources)
            approvals, scopes = [], set()
            for approval in snapshot.approvals:
                scope = (approval.root, approval.tool, approval.kind, approval.value)
                if scope in scopes or (revoke and approval.root == str(self.root)):
                    continue
                approvals.append(approval)
                scopes.add(scope)
            ids = {a.id for a in approvals}
            for item in additions:
                candidate = _approval(item.to_dict(), path)
                scope = (candidate.root, candidate.tool, candidate.kind, candidate.value)
                if scope in scopes:
                    continue
                if candidate.id in ids:
                    _fail(path, "新增批准标识与已有记录冲突")
                approvals.append(candidate)
                scopes.add(scope)
                ids.add(candidate.id)
            doc = dict(documents[2])
            doc["approvals"] = [a.to_dict() for a in approvals]
            destination = Path(sources[2][0])
            temporary = None
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, prefix=".permissions-", delete=False) as output:
                    temporary = Path(output.name)
                    yaml.safe_dump(doc, output, allow_unicode=True, sort_keys=False)
                    output.flush()
                    os.fsync(output.fileno())
                if self._read_sources() != sources:
                    _fail(path, "配置在保存期间发生并发变化，请重新决定")
                os.replace(temporary, destination)
            except (OSError, yaml.YAMLError) as error:
                _fail(path, "批准存储写入失败")
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def save_approvals(self, approvals: Sequence[Approval]) -> None:
        self._update(approvals)

    def revoke_permanent(self) -> None:
        self._update(revoke=True)
