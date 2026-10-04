"""工具登记和执行前校验。"""

from collections.abc import Iterable
from typing import Any

from jsonschema import Draft202012Validator

from .base import ARGUMENT_LIMIT, Tool, ToolContext, ToolDefinition, ToolError, ToolResult, strict_json


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具名称重复：{tool.name}")
        Draft202012Validator.check_schema(tool.input_schema)
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolError("unknown_tool", "工具未注册，请使用提供的工具列表", name=name)
        return self._tools[name]

    def names(self, *, read_only: bool | None = None) -> frozenset[str]:
        return frozenset(t.name for t in self._tools.values()
                         if read_only is None or bool(getattr(t, "read_only", False)) == read_only)

    def definitions(self, *, allowed_tools: frozenset[str] | None = None) -> tuple[ToolDefinition, ...]:
        return tuple(ToolDefinition(t.name, t.description, t.input_schema, bool(getattr(t, "read_only", False)))
                     for t in self._tools.values() if allowed_tools is None or t.name in allowed_tools)

    def prepare(self, name: str, raw: str, *, allowed_tools: frozenset[str] | None = None) -> tuple[Tool, dict[str, Any]]:
        tool = self.get(name)
        if allowed_tools is not None and name not in allowed_tools:
            raise ToolError("tool_not_allowed", "当前模式禁止使用此工具", name=name)
        try:
            if len(raw.encode("utf-8")) > ARGUMENT_LIMIT:
                raise ToolError("input_too_large", "工具参数超过 2 MiB 上限")
            arguments = strict_json(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise ToolError("invalid_arguments", "参数必须是完整合法的 JSON 对象") from None
        if not isinstance(arguments, dict):
            raise ToolError("invalid_arguments", "工具参数必须是 JSON 对象")
        try:
            error = next(Draft202012Validator(tool.input_schema).iter_errors(arguments), None)
        except Exception:
            # 外部 Schema 的解析／递归错误也必须产生结果，不能悬空调度等待者。
            raise ToolError("invalid_schema", "工具 Schema 无法安全校验参数") from None
        if error:
            # 校验器原文可能包含整个写入内容，只返回字段及约束名称。
            location = ".".join(str(x) for x in error.path) or "参数对象"
            raise ToolError("invalid_arguments", f"{location} 不满足 {error.validator} 约束，请核对参数 Schema")
        return tool, arguments

    def invoke(self, name: str, raw: str, context: ToolContext, *,
               allowed_tools: frozenset[str] | None = None) -> ToolResult:
        try:
            tool, arguments = self.prepare(name, raw, allowed_tools=allowed_tools)
            return tool.execute(arguments, context)
        except ToolError as error:
            return error.result()
        except FileNotFoundError:
            return ToolResult.failure("file_not_found", "文件或父目录不存在，请检查路径")
        except FileExistsError:
            return ToolResult.failure("file_exists", "目标已存在，拒绝覆盖；修改普通文件请使用 edit_file")
        except PermissionError:
            return ToolResult.failure("permission_denied", "没有访问目标的权限")
        except UnicodeError:
            return ToolResult.failure("unsupported_encoding", "仅支持不含 NUL 的 UTF-8 文本")
        except Exception:
            return ToolResult.failure("execution_error", "工具执行失败，请检查输入、文件状态和运行环境")
