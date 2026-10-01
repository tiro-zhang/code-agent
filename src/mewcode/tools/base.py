"""工具公共契约与可回灌的结构化结果。"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Protocol

OUTPUT_LIMIT = 64 * 1024
FILE_LIMIT = 1024 * 1024
ARGUMENT_LIMIT = 2 * 1024 * 1024


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = False


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: Any = None
    error: dict[str, Any] | None = None
    truncated: bool = False

    @classmethod
    def success(cls, data: Any, *, truncated: bool = False) -> "ToolResult":
        return cls(True, data, None, truncated)

    @classmethod
    def failure(cls, code: str, message: str, *, details: dict | None = None,
                data: Any = None, truncated: bool = False) -> "ToolResult":
        return cls(False, data, {"code": code, "message": message, "details": details or {}}, truncated)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ToolResult":
        return cls(**value)


class ToolError(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def result(self) -> ToolResult:
        return ToolResult.failure(self.code, self.message, details=self.details)


@dataclass(frozen=True)
class ToolContext:
    root: Path
    output_limit: int = OUTPUT_LIMIT
    file_limit: int = FILE_LIMIT
    emit: Callable[[dict[str, Any]], None] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.resolve())


class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult: ...


def object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def strict_json(raw: str) -> Any:
    """拒绝 Python 默认容忍的 NaN/Infinity。"""
    def reject(value: str) -> None:
        raise ValueError(value)
    return json.loads(raw, parse_constant=reject)


def utf8_prefix(value: bytes, limit: int) -> str:
    return value[:max(0, limit)].decode("utf-8", errors="ignore")
