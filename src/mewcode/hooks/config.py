"""启动时加载项目 Hook；任一错误停用整份声明。"""

from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .conditions import parse_condition
from .events import EVENT_FIELDS
from .models import HookAction, HookRule, HookConfigSnapshot, HookConfigError, HookDiagnostic


class StrictLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in result:
                    raise HookConfigError("YAML 含重复键")
                result[key] = self.construct_object(value_node, deep=deep)
            except TypeError:
                raise HookConfigError("YAML 映射键无效") from None
        return result


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\0" not in value


def parse_action(raw) -> HookAction:
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
        raise HookConfigError("action 必须声明 type")
    kind = raw["type"]
    fields = {"command": {"command", "timeout_seconds"}, "prompt": {"text"},
              "http": {"url", "method", "headers"}, "subagent": {"agent", "prompt"}}
    if kind not in fields or not raw.keys() <= fields[kind] | {"type"}:
        raise HookConfigError("未知动作或混用动作字段")
    if kind in ("command", "prompt", "subagent"):
        required = {"command": ("command",), "prompt": ("text",), "subagent": ("agent", "prompt")}[kind]
        if not all(_text(raw.get(name)) for name in required):
            raise HookConfigError("动作必填文本必须非空")
    if kind == "command":
        timeout = raw.get("timeout_seconds", 30)
        if type(timeout) is not int or not 1 <= timeout <= 120:
            raise HookConfigError("timeout_seconds 必须为 1 至 120 的整数")
    if kind == "http":
        try:
            url = raw.get("url")
            if not _text(url) or any(c.isspace() for c in url):
                raise ValueError
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment or "#" in url or parsed.port == 0:
                raise ValueError
        except ValueError:
            raise HookConfigError("url 必须为无凭据或片段的绝对 HTTP(S) 地址") from None
        method = raw.get("method", "POST")
        if method not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            raise HookConfigError("HTTP method 无效")
        headers = raw.get("headers", {})
        if not isinstance(headers, dict) or not all(_text(k) and isinstance(v, str) and not any(c in k + v for c in "\r\n\0") for k, v in headers.items()):
            raise HookConfigError("headers 必须为无换行的字符串映射")
        return HookAction(kind, url=url, method=method, headers=tuple(headers.items()))
    return HookAction(**raw)


def load_config(root: Path) -> HookConfigSnapshot:
    root = Path(root).resolve()
    path = root / ".mewcode/hooks.yaml"
    source = str(path)
    diagnostics, rules = [], []

    def error(index, event, message):
        diagnostics.append(HookDiagnostic(source, index, event if event in EVENT_FIELDS else "", message))

    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return HookConfigSnapshot(root)
    except OSError:
        error(None, "", "无法读取 Hook 配置")
        return HookConfigSnapshot(root, diagnostics=tuple(diagnostics))
    try:
        doc = yaml.load(raw, Loader=StrictLoader)
        if not isinstance(doc, dict) or doc.keys() != {"version", "hooks"}:
            raise HookConfigError("顶层必须声明 version 和 hooks，不接受未知字段")
        if type(doc["version"]) is not int or doc["version"] != 1 or not isinstance(doc["hooks"], list):
            raise HookConfigError("仅支持 version: 1 和 hooks 列表")
        for index, entry in enumerate(doc["hooks"]):
            before = len(diagnostics)
            if not isinstance(entry, dict) or not {"event", "action"} <= entry.keys() or not entry.keys() <= {"event", "if", "action", "once", "async"}:
                error(index, "", "规则必须声明 event/action，不接受未知字段")
                continue
            event = entry["event"]
            if not isinstance(event, str) or event not in EVENT_FIELDS:
                error(index, "", "未知生命周期事件")
            condition = None
            if "if" in entry and isinstance(event, str) and event in EVENT_FIELDS:
                try:
                    condition = parse_condition(entry["if"], event)
                except HookConfigError as exc:
                    error(index, event, str(exc))
            for control in ("once", "async"):
                if type(entry.get(control, False)) is not bool:
                    error(index, event if isinstance(event, str) else "", "执行控制必须为布尔值")
            try:
                action = parse_action(entry["action"])
                if entry.get("async") and (event == "tool.before" or action.type == "prompt"):
                    raise HookConfigError("tool.before 和 prompt 不允许 async")
            except HookConfigError as exc:
                error(index, event if isinstance(event, str) else "", str(exc))
            if len(diagnostics) == before:
                rules.append(HookRule(event, action, condition, entry.get("once", False),
                                      entry.get("async", False), source, index))
    except (HookConfigError, yaml.YAMLError, UnicodeError, TypeError, ValueError, RecursionError):
        error(None, "", "Hook YAML 无效、重复键或包含不安全对象")
    return HookConfigSnapshot(root, () if diagnostics else tuple(rules), tuple(diagnostics))
