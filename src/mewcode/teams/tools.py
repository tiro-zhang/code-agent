"""固定团队工具声明；花名册不改变供应商 Schema。"""
from ..tools.base import ToolError, object_schema


def schema(actions, properties, required=()):
    return object_schema({'action': {'type': 'string', 'enum': actions}, **properties},
                         ['action', *required])


TEXT = {'type': 'string', 'minLength': 1}
ID = {'type': 'string', 'minLength': 1, 'maxLength': 128}
REVISION = {'type': 'integer', 'minimum': 0}


class TeamTool:
    read_only = False
    system = True
    requires_scope = True
    default_visible = False

    def execute(self, arguments, context):
        raise ToolError('team_unavailable', '团队工具必须经可信运行服务执行', not_started=True)


class TeamControl(TeamTool):
    default_visible = True
    requires_scope = False
    name = 'team'
    description = '创建、显式恢复、查看或暂停本地持久团队。创建后以 goal(description) 建立目标，再拆共享任务与派生成员；团队固定绑定当前 Git 仓库，恢复不自动执行旧目标。'
    input_schema = schema(['create', 'resume', 'list', 'status', 'pause', 'goal'], {
        'name': {'type':'string', 'pattern':'^[a-z][a-z0-9_-]{0,63}$'}, 'description': TEXT})


class TeamMember(TeamTool):
    name = 'team_member'
    description = 'Lead 派生成员、停止运行或查看成员状态；角色模型和工具只能取已有配置。'
    input_schema = schema(['spawn', 'stop', 'status'], {
        'name': TEXT, 'role': TEXT, 'member': ID,
        'backend': {'type':'string', 'enum':['auto','tmux','inprocess']},
        'require_plan_approval': {'type':'boolean'}})


class TeamTask(TeamTool):
    name = 'team_task'
    description = '共享任务 CRUD、依赖、原子领取、成果提交；只有 Lead 能验收与改派。自然空闲不等于任务完成。'
    input_schema = schema(['create','list','get','update','delete','claim','start','submit','accept','reject','reassign'], {
        'task_id': ID, 'goal_id': ID, 'title': TEXT, 'description': {'type':'string'},
        'dependencies': {'type':'array', 'items': ID, 'uniqueItems':True},
        'expected_revision': REVISION, 'claim_id': ID, 'member_id': ID,
        'result': object_schema({'summary':TEXT,'verified':{'type':'boolean'},
            'evidence':{'type':'array','minItems':1,'items':{}},'branch':TEXT,'commit':TEXT,'run_id':ID},
            ['summary','verified','evidence']), 'code': {'type':'boolean'}, 'reason': TEXT,
        'validation_command':{**TEXT,'description':'接纳代码时必填：Lead 在成员目录独立运行的验证命令；例如 python3 -m unittest discover -s tests'}})


class TeamMessage(TeamTool):
    name = 'team_message'
    description = '直接邮箱通信、广播和严格 v1 协议；保存成功与通知成功分别报告。发件身份由运行绑定。'
    input_schema = schema(['send','broadcast','read'], {
        'recipient': TEXT, 'body': {'type':'string', 'maxLength':65536},
        'type': {'type':'string','enum':['text','task_assignment','plan_request','plan_decision','idle','result_submitted','shutdown_request','shutdown_ack']},
        'fields': {'type':'object','description':
            'text 为 {}；task_assignment={goal_id,task_id,claim_id}；plan_request 由成员运行器自动生成；'
            'plan_decision 必须原样回复请求的 goal_id/task_id/claim_id/plan_id/plan_version，加 approved 布尔值。'
            'idle={goal_id,run_id}；result_submitted={goal_id,task_id,claim_id,run_id}；'
            'shutdown_request={generation}；shutdown_ack={generation,stopped:true}。不要添加其他字段。'},
        'call_id': ID, 'broadcast_id': ID})


class TeamIntegrate(TeamTool):
    name = 'team_integrate'
    description = 'Lead 验证并串行整合已接纳提交、安全同步依赖或最终发布；失败只回滚本次整合。'
    input_schema = schema(['status','integrate','sync','finalize','rollback','handoff','finish_conflict'], {
        'task_id': ID, 'member_id': ID, 'operation_id': ID, 'token': ID,
        'validation_command': TEXT})


def team_tools():
    return [TeamControl(), TeamMember(), TeamTask(), TeamMessage(), TeamIntegrate()]


def required_actions(tool, contracts):
    """按动作校验所需字段，避免缺字段进入执行后才抛异常。"""
    tool.input_schema['allOf'] = [{'if':{'properties':{'action':{'const':action}}},
        'then':{'required':required}} for action,required in contracts.items()]


required_actions(TeamControl, {'create':['name'],'resume':['name'],'goal':['description']})
required_actions(TeamMember, {'spawn':['name','role'],'stop':['member']})
required_actions(TeamTask, {'create':['title'],'get':['task_id'],'update':['task_id','expected_revision'],
    'delete':['task_id','expected_revision'],'claim':['task_id'],'start':['task_id','claim_id'],
    'submit':['task_id','claim_id','result'],'accept':['task_id','expected_revision','reason'],
    'reject':['task_id','expected_revision','reason'],'reassign':['task_id','member_id']})
required_actions(TeamMessage, {'send':['recipient','body'],'broadcast':['body','broadcast_id']})
required_actions(TeamIntegrate, {'integrate':['task_id','validation_command'],'sync':['member_id'],
    'finalize':['validation_command'],'rollback':['operation_id'],'handoff':['operation_id','member_id'],
    'finish_conflict':['operation_id','member_id','token','validation_command']})
