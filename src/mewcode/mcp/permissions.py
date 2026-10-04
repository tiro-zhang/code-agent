"""无需活跃连接即可验证的外部工具批准范围。"""

import re

from ..tools.base import strict_json
from .config import canonical
from .tools import is_mcp_alias, tool_alias


def grant_value(tool, arguments):
    return canonical({"server": tool.server_name, "fingerprint": tool.fingerprint,
                      "tool": tool.original_name, "arguments": arguments})


def validate_grant(alias, value):
    if not is_mcp_alias(alias):
        raise ValueError("外部工具别名无效")
    scope = strict_json(value)
    if not isinstance(scope, dict) or set(scope) != {"server", "fingerprint", "tool", "arguments"}:
        raise ValueError("外部批准范围字段无效")
    if not isinstance(scope["server"], str) or not isinstance(scope["tool"], str):
        raise ValueError("外部批准身份无效")
    if tool_alias(scope["server"], scope["tool"]) != alias or not re.fullmatch(r"[0-9a-f]{64}", scope["fingerprint"]):
        raise ValueError("外部批准身份不一致")
    if not isinstance(scope["arguments"], dict):
        raise ValueError("外部批准参数必须为对象")
    return canonical(scope)
