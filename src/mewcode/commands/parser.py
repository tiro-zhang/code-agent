"""只区分输入类别，不维护命令名称或解释参数。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedInput:
    kind: str
    text: str = ''
    name: str = ''


def parse_command(draft: str) -> ParsedInput:
    stripped = draft.strip()
    if not stripped:
        return ParsedInput('empty')
    if '\n' in draft or '\r' in draft:
        return ParsedInput('message', draft)
    if stripped.startswith('//'):
        start = len(draft) - len(draft.lstrip())
        return ParsedInput('message', draft[:start] + draft[start + 1:])
    if not stripped.startswith('/'):
        return ParsedInput('message', draft)
    parts = stripped.split(maxsplit=1)
    return ParsedInput('command', parts[1].strip() if len(parts) == 2 else '', parts[0][1:].lower())
