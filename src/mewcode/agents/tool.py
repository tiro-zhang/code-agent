"""稳定的主进程委派入口，不把角色目录写入 Schema。"""

from ..tools.base import ToolError, object_schema


class DelegateAgent:
    name = "agent"
    description = "将明确子任务委派给非交互 Agent。defined 选择角色，fork 继承本请求前缀并强制后台；结果或后台 task_id 凭据不代表业务验证通过。"
    read_only = False
    system = True
    input_schema = object_schema({
        "type": {"type":"string", "enum":["defined", "fork"]},
        "role": {"type":"string", "pattern":"^[a-z][a-z0-9_-]{0,63}$"},
        "prompt": {"type":"string", "minLength":1, "pattern":r"\S"},
        "background": {"type":"boolean"},
    }, ["type", "prompt"])
    input_schema["allOf"] = [
        {"if":{"properties":{"type":{"const":"defined"}}}, "then":{"required":["role"]}},
        {"if":{"properties":{"type":{"const":"fork"}}},
         "then":{"not":{"required":["role"]}, "properties":{"background":{"const":True}}}},
    ]

    def execute(self, arguments, context):
        raise ToolError("agent_unavailable", "Agent 入口必须由会话服务执行", not_started=True)
