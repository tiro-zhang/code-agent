"""运行绑定的身份能力；元数据和模型参数不能替代可信身份。"""
from dataclasses import dataclass
import os

from ..tools.base import ToolError


COLLABORATION = frozenset({'team_task', 'team_message', 'team_member', 'team_integrate'})
READ_ACTIONS = {'team': {'list', 'status'}, 'team_member': {'status'},
                'team_task': {'list', 'get'}, 'team_integrate': {'status'}}
LEAD_TASK_ACTIONS = {'accept', 'reject', 'reassign', 'integrate', 'complete'}


@dataclass(frozen=True)
class TeamScope:
    team_name: str
    member_id: str
    lead: bool

    def coordinator(self, config):
        return (self.lead and getattr(config, 'team_coordinator_enabled', False)
                and os.environ.get('MEWCODE_COORDINATOR') == '1')


def allowed_tools(names, scope, config, *, delegation=False):
    """委派范围保留真实策略上限，不继承 coordinator 本人分工过滤。"""
    allowed = set(names)
    if scope is None:
        allowed -= COLLABORATION
    elif not scope.lead:
        allowed -= {'team', 'team_member', 'team_integrate', 'agent'}
    if scope is not None and scope.coordinator(config) and not delegation:
        allowed &= {'read_file', 'glob_files', 'search_code', 'load_skill', 'execute_command',
                    'team', *COLLABORATION}
    return frozenset(allowed)


def guard_action(scope, name, arguments, *, mode='execute', awaiting_plan=False):
    """具体动作层重复检查；计划门禁和工具授权是两个独立条件。"""
    def deny():
        raise ToolError('tool_not_allowed', '当前团队身份或模式禁止此动作', not_started=True)
    if name in COLLABORATION and scope is None:
        deny()
    if scope is not None and not scope.lead:
        if name in {'team', 'team_member', 'team_integrate', 'agent'}:
            deny()
        if name == 'team_task' and arguments and arguments.get('action') in LEAD_TASK_ACTIONS:
            deny()
    if not arguments:
        return
    if mode == 'plan' and name == 'team_message' and arguments.get('type') == 'plan_decision' and arguments.get('fields', {}).get('approved') is True:
        deny()
    if mode == 'plan' or awaiting_plan:
        if name in {'team', *COLLABORATION}:
            action = arguments.get('action')
            if name == 'team_message':
                if action not in {'send', 'read'} or arguments.get('type', 'text') not in {
                        'text', 'plan_request', 'plan_decision', 'idle', 'shutdown_ack'}:
                    deny()
            elif action not in READ_ACTIONS.get(name, set()):
                deny()
        elif name not in {'read_file', 'glob_files', 'search_code', 'load_skill'}:
            deny()
