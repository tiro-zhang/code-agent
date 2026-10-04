"""MCP 工具的纯数据描述与有界结果；不持有运行中的 SDK 连接。"""

from collections import Counter
from dataclasses import dataclass
import hashlib
import re

from jsonschema import Draft202012Validator

from ..tools.base import OUTPUT_LIMIT, ToolResult
from .config import Diagnostic, canonical


def tool_alias(server: str, tool: str) -> str:
    def slug(value, limit, fallback):
        return re.sub(r"[^A-Za-z0-9_]", "_", value)[:limit] or fallback
    digest = hashlib.sha256(canonical([server, tool]).encode()).hexdigest()[:16]
    return f"mcp__{slug(server, 12, 'server')}__{slug(tool, 20, 'tool')}__{digest}"


def is_mcp_alias(name) -> bool:
    return isinstance(name, str) and re.fullmatch(r"mcp__[A-Za-z0-9_]{1,12}__[A-Za-z0-9_]{1,20}__[0-9a-f]{16}", name) is not None


@dataclass(frozen=True)
class MCPTool:
    name: str
    description: str
    input_schema: dict
    server_name: str
    original_name: str
    fingerprint: str
    read_only: bool = False
    connection_description: str = ""

    def execute(self, arguments, context):
        return ToolResult.failure("mcp_unavailable", "外部工具需要父进程的异步连接", details={"not_started": True})


def _schema(schema):
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("inputSchema 必须是对象 Schema")
    Draft202012Validator.check_schema(schema)

    edges = {}

    def visit(value):
        key = id(value)
        if key in edges or isinstance(value, bool):
            return
        edges[key] = []
        if isinstance(value, dict):
            if "$schema" in value and value["$schema"].rstrip("#") != "https://json-schema.org/draft/2020-12/schema":
                raise ValueError("仅支持 JSON Schema 2020-12")
            if "$id" in value or "$dynamicRef" in value:
                raise ValueError("不支持重设引用基址或动态引用")
            if "$ref" in value:
                ref = value["$ref"]
                if not isinstance(ref, str) or not ref.startswith("#/"):
                    raise ValueError("仅支持文档内 JSON Pointer 引用")
                target = schema
                try:
                    for segment in ref[2:].split("/"):
                        segment = segment.replace("~1", "/").replace("~0", "~")
                        target = target[int(segment)] if isinstance(target, list) else target[segment]
                except (KeyError, TypeError, ValueError, IndexError):
                    raise ValueError("Schema 引用不存在") from None
                Draft202012Validator.check_schema(target)
                edges[key].append(id(target))
                visit(target)
            # 只把作用于同一实例的 Schema 连接起来。properties/items 等向子实例
            # 递进，合法的树形递归不会构成此图中的无限校验循环。
            for keyword in ("not", "if", "then", "else", "additionalProperties", "unevaluatedProperties",
                            "propertyNames", "items", "contains", "unevaluatedItems", "contentSchema"):
                if keyword in value:
                    item = value[keyword]
                    if keyword in {"not", "if", "then", "else"}:
                        edges[key].append(id(item))
                    visit(item)
            for keyword in ("$defs", "definitions", "properties", "patternProperties", "dependentSchemas"):
                for item in value.get(keyword, {}).values():
                    if keyword == "dependentSchemas":
                        edges[key].append(id(item))
                    visit(item)
            for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
                for item in value.get(keyword, []):
                    if keyword != "prefixItems":
                        edges[key].append(id(item))
                    visit(item)
    visit(schema)
    visiting, visited = set(), set()
    def acyclic(key):
        if key in visiting:
            raise ValueError("Schema 引用在同一实例上无限递归")
        if key in visited:
            return
        visiting.add(key)
        for target in edges.get(key, ()):
            acyclic(target)
        visiting.remove(key)
        visited.add(key)
    for key in edges:
        acyclic(key)
    canonical(schema)


def adapt_tools(server: str, fingerprint: str, definitions, reserved=()):
    definitions = [d.model_dump(by_alias=True, exclude_none=True) if hasattr(d, "model_dump") else d for d in definitions]
    names = Counter(d.get("name") for d in definitions if isinstance(d, dict) and isinstance(d.get("name"), str))
    aliases = Counter(tool_alias(server, n) for n in names)
    tools, diagnostics = [], []
    for name in sorted(names):
        alias = tool_alias(server, name)
        if names[name] != 1 or aliases[alias] != 1 or alias in reserved:
            diagnostics.append(Diagnostic(server, "工具适配", "工具原名重复或别名冲突，已排除相关项"))
            continue
        definition = next(d for d in definitions if d.get("name") == name)
        try:
            if (definition.get("execution") or {}).get("taskSupport") == "required":
                raise ValueError("不支持 task-only 工具")
            schema = definition.get("inputSchema")
            _schema(schema)
            description = definition.get("description") or f"外部 Server {server} 的工具 {name}"
            tools.append(MCPTool(alias, description, schema, server, name, fingerprint))
        except Exception:
            # JSON Schema 的异常正文可能包含远端敏感内容，不原样输出。
            diagnostics.append(Diagnostic(server, "工具适配", "工具 Schema、执行方式或描述不受支持，已排除该项"))
    return tuple(tools), tuple(diagnostics)


def adapt_result(result, *, limit: int = OUTPUT_LIMIT) -> ToolResult:
    if hasattr(result, "model_dump"):
        result = result.model_dump(by_alias=True, exclude_unset=True)
    if not isinstance(result, dict):
        return ToolResult.failure("mcp_protocol_error", "外部响应结构无效")
    if result.get("resultType") == "input_required":
        return ToolResult.failure("mcp_unsupported_capability", "外部工具要求未实现的输入续调能力；未自动再次调用")
    if not isinstance(result.get("content"), list):
        return ToolResult.failure("mcp_protocol_error", "外部响应缺少有效 content")
    remaining = limit
    omissions = []
    data = {}

    def accept(value):
        nonlocal remaining
        size = len(canonical(value).encode())
        if size > remaining:
            return False
        remaining -= size
        return True

    try:
        if "structuredContent" in result:
            if accept(result["structuredContent"]):
                data["structuredContent"] = result["structuredContent"]
            else:
                omissions.append("结构化数据超过预算，已整体省略")
        content = []
        for item in result["content"]:
            kind = item.get("type")
            if kind == "text":
                block = {"type": "text", "text": item["text"]}
            elif kind == "resource_link":
                block = {key: item[key] for key in ("type", "uri", "name", "title", "description", "mimeType", "size") if key in item}
            elif kind == "resource":
                resource = item["resource"]
                block = {"type": kind, "resource": {key: resource[key] for key in ("uri", "mimeType", "text") if key in resource}}
                if "blob" in resource:
                    block["unsupported"] = True
            else:
                block = {"type": kind, "mimeType": item.get("mimeType"), "unsupported": True}
            if accept(block):
                content.append(block)
            elif kind == "text":
                # 按 JSON 编码后的字节数选择前缀，保留 UTF-8 与转义的完整边界。
                text = block["text"]
                low, high = 0, min(len(text), remaining)
                while low < high:
                    middle = (low + high + 1) // 2
                    if len(canonical({"type": "text", "text": text[:middle]}).encode()) <= remaining:
                        low = middle
                    else:
                        high = middle - 1
                block["text"] = text[:low]
                if low and accept(block):
                    content.append(block)
                omissions.append("文本超过剩余预算，已截短或省略")
            else:
                omissions.append("内容或资源元信息超过剩余预算，已省略")
        data["content"] = content
        if omissions:
            data["omissions"] = list(dict.fromkeys(omissions))
        if result.get("isError", False):
            return ToolResult.failure("mcp_tool_error", "外部工具报告失败", data=data, truncated=bool(omissions))
        return ToolResult.success(data, truncated=bool(omissions))
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return ToolResult.failure("mcp_protocol_error", "外部响应不能转换为有效工具结果")


def adapt_request_error(error, *, secrets=(), limit=OUTPUT_LIMIT, code="mcp_request_error", started=True):
    secrets = tuple(sorted({s for s in secrets if isinstance(s, str) and s}, key=len, reverse=True))
    def scrub(value):
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "[已隐藏]")
            return value
        if isinstance(value, dict):
            return {scrub(key): scrub(item) for key, item in value.items()}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return "[已隐藏]" if str(value) in secrets else value
    try:
        safe = scrub(error)
        converted = adapt_result({"content": [{"type": "text", "text": str(safe.get("message", "外部请求失败"))}],
                                  "structuredContent": safe}, limit=limit)
    except (RecursionError, TypeError, AttributeError):
        return ToolResult.failure(code, "外部请求失败；错误详情无法转换", truncated=True)
    return ToolResult.failure(code, "外部请求失败", data=converted.data, truncated=converted.truncated,
                              details={"side_effects_may_have_occurred": started})
