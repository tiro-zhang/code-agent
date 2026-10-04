"""旧导入路径的薄适配；命令元数据统一由 commands 包提供。"""
from ..commands import parse_command
from ..commands.builtins import build_registry


def help_text(*, enhanced: bool) -> str:
    return build_registry().help_text(enhanced=enhanced)


def command_completions(text: str) -> list[str]:
    return build_registry().completions(text)
