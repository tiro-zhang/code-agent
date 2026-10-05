"""可序列化的系统入口描述，不持有会话或异步服务。"""

from ..tools.base import ToolError, object_schema


class LoadSkill:
    name = "load_skill"
    description = "按名称加载并运行 Skill；resource 可只读取目录包内参考文本。始终遵守当前模式和权限。"
    read_only = False
    system = True
    input_schema = object_schema({
        "name": {"type": "string", "minLength": 1},
        "args": {"type": "string"},
        "history": {"anyOf": [{"type": "integer", "minimum": 0}, {"const": "all"}]},
        "resource": {"type": "string", "minLength": 1},
    }, ["name"])
    input_schema["not"] = {"anyOf": [{"required": ["resource", "args"]},
                                              {"required": ["resource", "history"]}]}

    def execute(self, arguments, context):
        raise ToolError("skill_unavailable", "Skill 服务必须由主进程加载", not_started=True)
