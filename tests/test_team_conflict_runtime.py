"""真实 Git 冲突交接同时验证工具根、能力和排他租约。"""
import json
from pathlib import Path

import pytest

from conftest import ScriptedProvider, async_test
from test_agent_loop import answer
from test_team_integration import git
from test_team_runtime import wait_idle
from test_team_service import make_session
from mewcode.teams.integration import IntegrationService
from mewcode.tools.base import ToolError


@async_test
async def test_conflict_member_uses_leased_directory_then_lead_publishes(tmp_path):
    session=make_session(tmp_path)
    root=session.executor.context.root
    (root/'shared.txt').write_text('base\n')
    git(root,'add','shared.txt')
    git(root,'-c','user.name=测试','-c','user.email=test@example.invalid','commit','-m','基线内容')
    session.provider_factory=lambda config:ScriptedProvider([answer('等待开工')])
    await session.teams.control({'action':'create','name':'alpha'})
    goal=await session.teams.control({'action':'goal','description':'并行修改'})
    team=session.teams.store.load('alpha')
    tasks=[]
    members=[]
    for name in ('alice','bob'):
        member=await session.teams.member({'action':'spawn','name':name,'role':'general','backend':'inprocess'})
        members.append(member)
        task=await session.teams.task({'action':'create','title':name,'code':True})
        task=await session.teams.task({'action':'reassign','task_id':task['task_id'],'member_id':member['member_id']})
        directory=Path(member['workspace_root'])
        (directory/'shared.txt').write_text(name+'\n')
        git(directory,'add','shared.txt')
        git(directory,'-c','user.name=测试','-c','user.email=test@example.invalid','commit','-m',name)
        board=session.teams.board()
        task=await board.submit(member['member_id'],task['task_id'],task['claim_id'],{
            'summary':name,'verified':True,'evidence':['本次检查'],'branch':member['branch'],
            'commit':git(directory,'rev-parse','HEAD')})
        accepted=await session.teams.task({'action':'accept','task_id':task.task_id,'expected_revision':task.revision,
                                          'reason':'检查通过','validation_command':'test -f shared.txt'})
        tasks.append(accepted)
    first=await session.teams.integrate({'action':'integrate','task_id':tasks[0]['task_id'],
                                         'validation_command':'test -f shared.txt'})
    second=await session.teams.integrate({'action':'integrate','task_id':tasks[1]['task_id'],
                                          'validation_command':'test -f shared.txt'})
    assert first['state']=='published' and second['state']=='conflict'
    grant=await session.teams.integrate({'action':'handoff','operation_id':second['operation_id'],
                                         'member_id':members[1]['member_id']})
    runtime=await wait_idle(session,members[1]['member_id'])
    original=runtime.session.executor.context.root
    await runtime.enter_conflict_workspace()
    integration=IntegrationService(root,session.teams.store.team_path('alpha')/'integration',
        goal['goal_id'],goal['baseline_commit'],goal['target_branch'])
    assert runtime.session.executor.context.root==integration.worktree_root
    with pytest.raises(ToolError):
        integration.acquire_conflict_lease(grant['operation_id'],grant['member_id'],grant['token'])
    await runtime.start_task(grant['task_id'],grant['claim_id'])
    result=await runtime.session.executor.execute('read_file',json.dumps({'path':'shared.txt'}),
                                                  allowed_tools=runtime.session.effective_tools())
    assert result.ok,result.to_dict()
    result=await runtime.session.executor.execute('edit_file',json.dumps({'path':'shared.txt',
        'old_text':result.data['content'],'new_text':'alice and bob\n'}),
                                                  allowed_tools=runtime.session.effective_tools())
    assert result.ok,result.to_dict()
    assert (original/'shared.txt').read_text()=='bob\n'
    result=await runtime.session.executor.execute('execute_command',json.dumps({'command':
        'git add shared.txt && git -c user.name=测试 -c user.email=test@example.invalid commit -m 合并冲突'}),
        allowed_tools=runtime.session.effective_tools())
    assert result.ok and result.data['exit_code']==0,result.to_dict()
    resolution=await runtime.session.teams.task({'action':'submit','task_id':grant['task_id'],'claim_id':grant['claim_id'],
        'result':{'summary':'已合并','verified':True,'evidence':['实际合并提交']}})
    await session.teams.task({'action':'accept','task_id':grant['task_id'],'expected_revision':resolution['revision'],
                             'reason':'证据已核对'})
    with pytest.raises(ToolError):
        await session.teams.integrate({'action':'finish_conflict','operation_id':grant['operation_id'],
            'member_id':grant['member_id'],'token':grant['token'],'validation_command':'test -f shared.txt'})
    await runtime.leave_conflict_workspace()
    assert runtime.session.executor.context.root==original
    published=await session.teams.integrate({'action':'finish_conflict','operation_id':grant['operation_id'],
        'member_id':grant['member_id'],'token':grant['token'],'validation_command':'test -f shared.txt'})
    assert published['state']=='published'
    assert (await session.teams.board().get(team.lead_id,tasks[1]['task_id'])).state=='integrated'
    assert git(root,'rev-parse','HEAD')==goal['baseline_commit']
    await session.aclose()
