"""权限与 Hook 共用的纯匹配；不解释规则或改变授权。"""

from fnmatch import fnmatchcase
from functools import lru_cache
import re


def path_glob(pattern: str, subject: str) -> bool:
    patterns, parts = pattern.split("/"), subject.split("/")

    @lru_cache(maxsize=None)
    def visit(pi: int, si: int) -> bool:
        if pi == len(patterns):
            return si == len(parts)
        if patterns[pi] == "**":
            return visit(pi + 1, si) or (si < len(parts) and visit(pi, si + 1))
        return si < len(parts) and fnmatchcase(parts[si], patterns[pi]) and visit(pi + 1, si + 1)

    return visit(0, 0)


def exact_match(subject, value) -> bool:
    if type(subject) is not type(value):
        return False
    if isinstance(subject, dict):
        return subject.keys() == value.keys() and all(exact_match(subject[k], value[k]) for k in subject)
    if isinstance(subject, list):
        return len(subject) == len(value) and all(exact_match(a, b) for a, b in zip(subject, value))
    return subject == value


def match_value(subject, value, match: str, *, path: bool = False) -> bool:
    if match == "exact":
        return exact_match(subject, value)
    if not isinstance(subject, str) or not isinstance(value, str):
        return False
    if match == "glob":
        return path_glob(value, subject) if path else fnmatchcase(subject, value)
    if match == "regex":
        try:
            return re.fullmatch(value, subject) is not None
        except re.error:
            raise ValueError("非法正则表达式") from None
    raise ValueError("未知匹配类型")
