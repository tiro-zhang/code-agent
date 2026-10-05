"""会话激活映射；持久提交成功后才发布候选。"""

from dataclasses import dataclass

from ..tools.base import ToolError
from .catalog import SkillCatalog
from .models import Skill


@dataclass(frozen=True)
class Activation:
    skill: Skill
    args: str
    body: str


class SkillRuntime:
    def __init__(self, catalog: SkillCatalog, *, commit=None, parent_tools=None):
        self.catalog = catalog
        self.active: tuple[Activation, ...] = ()
        self.commit = commit
        self.parent_tools = frozenset(parent_tools) if parent_tools is not None else None

    def descriptors(self, active=None):
        values = self.active if active is None else active
        return [{"name": a.skill.name, "args": a.args, "order": order,
                 "fingerprint": a.skill.fingerprint} for order, a in enumerate(values)]

    def _publish(self, active, catalog=None):
        if self.commit:
            self.commit(self.descriptors(active))
        self.active = tuple(active)
        if catalog is not None:
            self.catalog = catalog

    def activate(self, name: str, args: str = "", *, allow_isolated=False) -> Activation:
        skill = self.catalog.get(name)
        if skill.mode != "shared" and not allow_isolated:
            raise ToolError("invalid_arguments", "独立 Skill 必须通过独立运行入口加载")
        activation = Activation(skill, args, skill.render(args))
        candidate = {a.skill.name: a for a in self.active}
        candidate[name] = activation
        self._publish(tuple(candidate.values()))
        return activation

    def deactivate(self, name: str):
        if name == "--all":
            candidate = ()
        else:
            self.catalog.get(name)
            candidate = tuple(a for a in self.active if a.skill.name != name)
        self._publish(candidate)

    def allowed_tools(self, registered, *, mode="execute") -> frozenset[str]:
        names = set(registered)
        systems = names & {"load_skill", "agent"}
        ordinary = names - systems
        if mode == "plan":
            ordinary &= {"read_file", "glob_files", "search_code"}
        if self.parent_tools is not None:
            ordinary &= self.parent_tools
        for activation in self.active:
            if activation.skill.allowed_tools is not None:
                ordinary &= activation.skill.allowed_tools
        return frozenset(ordinary | systems)

    def render_active(self) -> str:
        if not self.active:
            return "当前无激活 Skill。"
        return "\n\n".join(f"### Skill: {a.skill.name}\n{a.body}" for a in self.active)

    def _restore_candidate(self, catalog, descriptors):
        active, warnings, seen = [], [], set()
        for record in descriptors:
            name, args = record["name"], record["args"]
            if name in seen:
                raise ValueError("激活描述名称重复")
            seen.add(name)
            try:
                skill = catalog.get(name)
            except ToolError:
                warnings.append(f"Skill {name} 已失效，已停用")
                continue
            if skill.mode != "shared":
                warnings.append(f"Skill {name} 已改为独立模式，已停用共享激活")
                continue
            active.append(Activation(skill, args, skill.render(args)))
            if record["fingerprint"] != skill.fingerprint:
                warnings.append(f"Skill {name} 已采用当前来源：{skill.path}")
        return tuple(active), warnings

    def replace_catalog(self, catalog):
        active, warnings = self._restore_candidate(catalog, self.descriptors())
        self._publish(active, catalog)
        return warnings

    def restore(self, descriptors):
        active, warnings = self._restore_candidate(self.catalog, descriptors)
        self.active = active
        return warnings
