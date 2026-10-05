"""不可变 Skill 入口快照。"""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal

from ..tools.base import OUTPUT_LIMIT, ToolError

HistoryScope = int | Literal["all"]
_PLACEHOLDER = re.compile(r"\{\{(args|skill_dir)\}\}")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    mode: Literal["shared", "isolated"]
    allowed_tools: frozenset[str] | None
    history: HistoryScope
    model: str | None
    path: Path
    layer: str
    package: bool
    raw: bytes
    body: str
    fingerprint: str

    def render(self, args: str = "") -> str:
        """仅遍历原模板，替换结果不再解释。"""
        values = {"args": args, "skill_dir": str(self.path.parent)}
        text = _PLACEHOLDER.sub(lambda match: values[match[1]], self.body)
        if len(text.encode("utf-8")) > OUTPUT_LIMIT:
            raise ToolError("skill_content_too_large", "展开后的 Skill 超过 64 KiB，不予激活")
        return text
