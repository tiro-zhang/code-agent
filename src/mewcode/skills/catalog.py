"""三层入口发现、覆盖和整代校验。"""

from dataclasses import dataclass
from pathlib import Path

from ..tools.base import ToolError
from .loader import parse_skill
from .models import Skill


@dataclass(frozen=True)
class SkillCatalog:
    skills: tuple[Skill, ...]
    warnings: tuple[str, ...] = ()

    def get(self, name: str) -> Skill:
        for skill in self.skills:
            if skill.name == name:
                return skill
        raise ToolError("skill_not_found", f"未发现 Skill：{name}", not_started=True)

    def index_text(self) -> str:
        return "\n".join(f"- {skill.name}: {skill.description}" for skill in self.skills)

    def validate_names(self, reserved) -> None:
        for skill in self.skills:
            if skill.name in reserved:
                raise ValueError(f"Skill {skill.name}（{skill.path}）与控制命令冲突")

    def validate_tools(self, registered) -> None:
        for skill in self.skills:
            unknown = (skill.allowed_tools or frozenset()) - set(registered)
            if unknown:
                raise ValueError(f"Skill {skill.name}（{skill.path}）包含未知工具：{', '.join(sorted(unknown))}")


def discover_skills(root: Path, *, user_root: Path | None = None,
                    builtin_root: Path | None = None) -> SkillCatalog:
    """高层坏项不遮蔽低层合法项，同层重名拒绝整体候选。"""
    user = Path(user_root) if user_root is not None else Path.home() / ".mewcode"
    builtin = Path(builtin_root) if builtin_root is not None else Path(__file__).parent / "builtin"
    result, warnings = {}, []
    for layer, directory in (("builtin", builtin), ("user", user / "skills"),
                             ("project", Path(root) / ".mewcode" / "skills")):
        current = {}
        try:
            if directory.is_symlink():
                raise OSError("目录是链接")
            paths = sorted(directory.iterdir())
        except FileNotFoundError:
            continue
        except OSError:
            warnings.append(f"Skill 目录 {directory} 无法安全读取，已跳过")
            continue
        for path in paths:
            package = path.is_dir()
            if not package and path.suffix != ".md":
                continue
            entry = path / "SKILL.md" if package else path
            if package and not entry.exists():
                continue
            try:
                skill = parse_skill(entry, layer=layer, package=package)
            except ToolError as error:
                warnings.append(f"Skill {entry}：{error.message}，已跳过")
                continue
            if skill.name in current:
                raise ValueError(f"同层 Skill 重名 {skill.name}：{current[skill.name].path} 与 {entry}")
            current[skill.name] = skill
        result.update(current)
    return SkillCatalog(tuple(result[name] for name in sorted(result)), tuple(warnings))
