"""纯规则判定；授权和硬限制由执行入口独立处理。"""

from collections.abc import Iterable

from .models import Evaluation, Rule, JSON_ARGUMENT_TOOLS
from ..mcp.tools import is_mcp_alias
from ..matching import match_value, path_glob as _path_glob


def _matches(rule: Rule, subject: str) -> bool:
    return match_value(subject, rule.pattern, rule.match,
                       path=rule.tool != "execute_command" and rule.tool not in JSON_ARGUMENT_TOOLS and not is_mcp_alias(rule.tool))


def merge_rules(
    rules: Iterable[Rule], tool: str, subjects: tuple[str, ...], *,
    allow_subject: str | None = None, glob_allow: bool = True,
) -> Evaluation:
    original = allow_subject if allow_subject is not None else (subjects[0] if subjects else None)
    matched = []
    for rule in rules:
        if rule.tool != tool:
            continue
        if rule.effect == "allow":
            if original is None or (rule.match == "glob" and not glob_allow):
                continue
            applicable = _matches(rule, original)
        else:
            applicable = any(_matches(rule, subject) for subject in subjects)
        if applicable:
            matched.append(rule)
    effects = {rule.effect for rule in matched}
    effect = next((value for value in ("deny", "ask", "allow") if value in effects), None)
    return Evaluation(effect, tuple(matched))


def apply_mode(effect: str | None, mode: str) -> str:
    if mode not in ("strict", "default", "bypass"):
        raise ValueError("权限模式必须为 strict、default 或 bypass")
    if effect not in (None, "deny", "ask", "allow"):
        raise ValueError("非法权限规则结果")
    if effect == "deny":
        return "deny"
    if mode == "strict":
        return "ask"
    if mode == "bypass":
        return "allow"
    return effect or "ask"
