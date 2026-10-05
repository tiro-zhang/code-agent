"""内置委派入口的完整规范参数目标。"""

from ..mcp.config import canonical
from ..tools.base import strict_json


def canonical_object(raw: str) -> str:
    value = strict_json(raw)
    if not isinstance(value, dict):
        raise ValueError("批准目标必须是完整 JSON 对象")
    return canonical(value)
