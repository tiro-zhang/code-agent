"""隔离角色的实际工具执行、提示基线与退出结果。"""

from pathlib import Path
import asyncio

from conftest import ScriptedProvider,async_test
from mewcode.agents.definitions import parse_role
from mewcode.types import ToolCall,Message
from test_agent_definitions import entry
from test_skills_session import session,response
from test_subagent_runtime import submit
from test_worktree_paths import repository,git


def role(root):
    return parse_role(entry(root/'.mewcode/agents/worker.md',name='worker',extra='isolation: worktree\n'),layer='project')


@async_test
async def test_isolated_child_reads_frozen_and_writes_own_directory(tmp_path):
    root=repository(tmp_path/'repo')
    (root/'MEWCODE.md').write_text('冻结项目指令')
    git(root,'add','.');git(root,'commit','-m','指令')
    (root/'MEWCODE.md').write_text('父后来项目指令')
    (root/'project/app.py').write_text('parent dirty\n')
    provider=ScriptedProvider([
        response(calls=(ToolCall('read','read_file','{"path":"project/app.py"}'),)),
        response(calls=(ToolCall('write','edit_file','{"path":"project/app.py","old_text":"frozen\\n","new_text":"child output\\n"}'),)),
        response('子完成')])
    chat=session(root,provider)
    tasks,parent,record=submit(chat,{'type':'defined','prompt':'修改','role':'worker'},role(root))
    try:
        await tasks.wait_terminal(record.task_id)
        assert (root/'project/app.py').read_text()=='parent dirty\n'
        info=record.outcome.worktree
        child=Path(info['workspace_root'])
        assert child != root and (child/'project/app.py').read_text()=='child output\n'
        assert info['state']=='retained' and info['base_commit']==git(root,'rev-parse','HEAD')
        payload='\n'.join(message.content for request,_ in provider.requests for message in request)
        assert '冻结项目指令' in payload and '父后来项目指令' not in payload
        assert str(child) in payload and '不包含父目录未提交' in payload
        assert record.outcome.reason=='model_done'
    finally:
        await tasks.aclose();await chat.aclose()


@async_test
async def test_clean_child_removed_and_parent_keeps_its_workroot(tmp_path):
    root=repository(tmp_path/'repo')
    provider=ScriptedProvider([response('已读取目标，无修改')])
    chat=session(root,provider)
    tasks,parent,record=submit(chat,{'type':'defined','prompt':'说明','role':'worker'},role(root))
    try:
        await tasks.wait_terminal(record.task_id)
        info=record.outcome.worktree
        assert info['state']=='removed'
        assert not Path(info['worktree_root']).exists()
        assert chat.executor.context.root==root and not provider.closed
        assert record.report()['worktree']==info
    finally:
        await tasks.aclose();await chat.aclose()


@async_test
async def test_queued_snapshot_keeps_head_before_parent_new_commit(tmp_path):
    root=repository(tmp_path/'repo')
    chat=session(root,ScriptedProvider([response('子完成')]))
    from mewcode.agents.runtime import freeze_child,ChildRuntime
    from mewcode.tasks.manager import TaskManager,TaskOutcome
    tasks=chat.tasks=TaskManager(max_running=1)
    parent=tasks.new_parent('父目标')
    release=asyncio.Event()
    async def block(record):
        await release.wait()
        return TaskOutcome('model_done','占用已结束')
    tasks.submit(parent.task_id,block,type='defined')
    snapshot=freeze_child(chat,{'type':'defined','prompt':'说明'},role=role(root))
    record=tasks.submit(parent.task_id,lambda r:ChildRuntime(chat,r).run(),type='defined',snapshot=snapshot)
    baseline=git(root,'rev-parse','HEAD')
    (root/'project/app.py').write_text('B\n')
    git(root,'add','.');git(root,'commit','-m','B')
    try:
        assert not (root/'.mewcode/worktrees').exists()
        release.set()
        await tasks.wait_terminal(record.task_id)
        assert record.outcome.worktree['base_commit']==baseline
        assert baseline != git(root,'rev-parse','HEAD')
    finally:
        release.set();await tasks.aclose();await chat.aclose()


@async_test
async def test_archive_allows_cleanup_and_parent_resume_reads_full_evidence(tmp_path):
    from mewcode.session import ChatSession
    from mewcode.sessions import Journal
    from mewcode.context.spill import ResultCache
    from test_skills_persistence import CONFIG
    root=repository(tmp_path/'repo')
    (root/'large').write_text('文'*14000)
    git(root,'add','.');git(root,'commit','-m','大文件')
    provider=ScriptedProvider([response(calls=(ToolCall('read','read_file','{"path":"large"}'),)),response('已归档')])
    chat=session(root,provider,config=CONFIG,persistent=True)
    identity=chat.journal.id
    tasks,parent,record=submit(chat,{'type':'defined','prompt':'读取','role':'worker'},role(root))
    await tasks.wait_terminal(record.task_id)
    try:
        info=record.outcome.worktree
        assert info['state']=='removed' and info['archive']=='complete'
        evidence=record.outcome.evidence[0]
        path=evidence['cache_path']
        assert path and str(root/path)!=str(Path(evidence['source_root'])/evidence['source_cache_path'])
        assert chat.context.cache.restore(path).data['content']=='文'*14000
        assert not Path(info['worktree_root']).exists()
        assert not provider.closed
    finally:
        await chat.aclose()
    journal=Journal.resume(root,identity,CONFIG.protocol,CONFIG.model)
    cache=ResultCache(root,session_id=identity,persistent=True)
    try:
        cache.reopen(journal.projection.cache_paths)
        assert cache.restore(path).data['content']=='文'*14000
        assert journal.projection.worktrees[record.task_id]['worktree']['state']=='removed'
        assert journal.projection.history==[]
    finally:
        journal.close();cache.close()


@async_test
async def test_archive_failure_releases_lease_but_preserves_evidence(tmp_path,monkeypatch):
    from mewcode.worktrees.records import load_record,Lease
    from mewcode.worktrees.manager import WorktreeManager
    from mewcode.worktrees.paths import freeze_repository
    root=repository(tmp_path/'repo')
    (root/'large').write_text('文'*14000);git(root,'add','.');git(root,'commit','-m','大文件')
    provider=ScriptedProvider([response(calls=(ToolCall('read','read_file','{"path":"large"}'),)),response('子完成')])
    chat=session(root,provider)
    def fail(*args,**kwargs): raise OSError('模拟磁盘故障')
    monkeypatch.setattr(chat.context.cache,'save',fail)
    tasks,parent,record=submit(chat,{'type':'defined','prompt':'读取','role':'worker'},role(root))
    try:
        await tasks.wait_terminal(record.task_id)
        info=record.outcome.worktree
        assert info['state']=='retained' and info['archive']=='failed'
        assert record.outcome.reason=='model_done'
        manager=WorktreeManager(freeze_repository(root))
        assert load_record(manager.record_path(record.task_id))['evidence_protected'] is True
        lease=Lease(manager.lock_path(record.task_id));lease.close()
        evidence=record.outcome.evidence[0]
        source=Path(info['workspace_root'])/evidence['cache_path']
        assert source.exists()
        await tasks.aclose()
        assert source.exists()
    finally:
        await chat.aclose()


@async_test
async def test_initialization_failure_never_requests_model(tmp_path):
    root=repository(tmp_path/'repo')
    (root/'.mewcode').mkdir()
    (root/'.mewcode/worktrees.yaml').write_text('version: 1\ncopy-files:\n  - {path: .env.required, required: true}\n')
    provider=ScriptedProvider([]);chat=session(root,provider)
    tasks,parent,record=submit(chat,{'type':'defined','prompt':'读取','role':'worker'},role(root))
    try:
        await tasks.wait_terminal(record.task_id)
        assert provider.requests==[] and parent.budget.used==0
        assert record.outcome.reason=='worktree_startup_failed'
        assert record.outcome.worktree['initialization']=='incomplete'
        assert Path(record.outcome.worktree['worktree_root']).exists()
    finally:
        await chat.aclose()


@async_test
async def test_child_project_subdirectory_mapping_and_shell_cwd(tmp_path):
    import json
    root=repository(tmp_path/'repo')
    provider=ScriptedProvider([response(calls=(ToolCall('cwd','execute_command',json.dumps({'command':'pwd'})),)),response('已验证')])
    chat=session(root/'project',provider)
    tasks,parent,record=submit(chat,{'type':'defined','prompt':'验证 cwd','role':'worker'},role(root/'project'))
    try:
        await tasks.wait_terminal(record.task_id)
        info=record.outcome.worktree
        assert Path(info['workspace_root'])==Path(info['worktree_root'])/'project'
        data=record.outcome.evidence[0]['result']['data']
        assert data['stdout'].strip()==info['workspace_root']
        assert chat.executor.context.root==root/'project'
    finally:
        await chat.aclose()


@async_test
async def test_two_children_edit_same_name_with_independent_read_state(tmp_path):
    import json
    root=repository(tmp_path/'repo')
    (root/'project/app.py').write_text('parent dirty\n')
    class IndependentProvider(ScriptedProvider):
        def __init__(self):
            super().__init__([])
            self.children = []
        def fork(self, config):
            label = ('one', 'two')[len(self.children)]
            child = ScriptedProvider([
                response(calls=(ToolCall('read', 'read_file', '{"path":"project/app.py"}'),)),
                response(calls=(ToolCall('edit', 'edit_file', json.dumps({'path':'project/app.py', 'old_text':'frozen\n', 'new_text':label+'\n'})),)),
                response('完成')])
            self.children.append(child)
            return child
    provider = IndependentProvider()
    chat=session(root,provider)
    from mewcode.agents.runtime import freeze_child,ChildRuntime
    from mewcode.tasks.manager import TaskManager
    tasks=chat.tasks=TaskManager(max_running=2)
    parent=tasks.new_parent('并行')
    snapshot=freeze_child(chat,{'type':'defined','prompt':'读后编辑'},role=role(root))
    children=[tasks.submit(parent.task_id,lambda record:ChildRuntime(chat,record).run(),type='defined',snapshot=snapshot) for _ in range(2)]
    try:
        await asyncio.gather(*(tasks.wait_terminal(record.task_id) for record in children))
        directories={record.outcome.worktree['workspace_root'] for record in children}
        assert len(directories)==2
        assert (root/'project/app.py').read_text()=='parent dirty\n'
        assert {(Path(path)/'project/app.py').read_text() for path in directories} == {'one\n','two\n'}
        assert all(record.outcome.worktree['state']=='retained' for record in children)
    finally:
        await chat.aclose()


@async_test
async def test_queued_cancel_creates_no_worktree(tmp_path):
    from mewcode.agents.runtime import freeze_child,ChildRuntime
    from mewcode.tasks.manager import TaskManager,TaskOutcome
    root=repository(tmp_path/'repo');chat=session(root,ScriptedProvider([]))
    tasks=chat.tasks=TaskManager(max_running=1)
    parent=tasks.new_parent('父');release=asyncio.Event()
    async def block(record):
        await release.wait();return TaskOutcome('model_done','完成')
    tasks.submit(parent.task_id,block,type='defined')
    snapshot=freeze_child(chat,{'type':'defined','prompt':'排队'},role=role(root))
    record=tasks.submit(parent.task_id,lambda record:ChildRuntime(chat,record).run(),type='defined',snapshot=snapshot)
    try:
        await tasks.cancel(record.task_id)
        assert not (root/'.mewcode/worktrees').exists()
        assert record.outcome.reason=='cancelled'
    finally:
        release.set();await chat.aclose()


@async_test
async def test_background_hook_holds_lease_until_real_command_end(tmp_path):
    from test_hook_runtime import make_runtime,rule as hook_rule
    from mewcode.worktrees.records import Lease
    from mewcode.worktrees.manager import WorktreeManager
    from mewcode.worktrees.paths import freeze_repository
    from mewcode.tools.base import ToolError
    import pytest
    root=repository(tmp_path/'repo');chat=session(root,ScriptedProvider([response('模型已完成')]))
    await chat.hooks.close()
    chat.hooks=make_runtime(root,[hook_rule(0,'command',command='touch started; sleep .5; touch finished',background=True)])
    tasks,parent,record=submit(chat,{'type':'defined','prompt':'说明','role':'worker'},role(root))
    manager=WorktreeManager(freeze_repository(root))
    path=manager.container/record.task_id
    try:
        for _ in range(200):
            if (path/'started').exists(): break
            await asyncio.sleep(.01)
        assert (path/'started').exists()
        assert not record.done.is_set()
        with pytest.raises(ToolError): Lease(manager.lock_path(record.task_id))
        await tasks.wait_terminal(record.task_id)
        assert (path/'finished').exists() and record.outcome.reason=='model_done'
        lease=Lease(manager.lock_path(record.task_id));lease.close()
        assert not chat.hooks.closed
    finally:
        await chat.aclose()


@async_test
async def test_exit_record_failure_preserves_model_outcome_and_actual_metadata(tmp_path,monkeypatch):
    from mewcode.worktrees.manager import WorktreeManager
    root=repository(tmp_path/'repo');chat=session(root,ScriptedProvider([response('真实成功结果')]))
    original=WorktreeManager.save
    def fail_exit(self,name,data):
        if data['stage']=='stopped': raise OSError('磁盘失败')
        return original(self,name,data)
    monkeypatch.setattr(WorktreeManager,'save',fail_exit)
    tasks,parent,record=submit(chat,{'type':'defined','prompt':'说明','role':'worker'},role(root))
    try:
        await tasks.wait_terminal(record.task_id)
        assert record.outcome.reason=='model_done' and record.outcome.text=='真实成功结果'
        assert record.outcome.worktree['state']=='retained'
        assert Path(record.outcome.worktree['worktree_root']).exists()
        assert '退出' in record.outcome.worktree['reason']
    finally:
        await chat.aclose()


@async_test
async def test_plan_can_run_in_complete_preexisting_worktree(tmp_path):
    from mewcode.agents.runtime import freeze_child,ChildRuntime
    from mewcode.tasks.manager import TaskRecord
    from mewcode.worktrees.manager import WorktreeManager
    root=repository(tmp_path/'repo');chat=session(root,ScriptedProvider([response('只读结果')]))
    chat.enter_plan()
    snapshot=freeze_child(chat,{'type':'defined','prompt':'只读'},role=role(root))
    manager=WorktreeManager(*snapshot.worktree)
    tree=await manager.create('existing',task_id='existing')
    record=TaskRecord('existing','parent','defined','plan',0,snapshot=snapshot)
    try:
        outcome=await ChildRuntime(chat,record).run()
        assert outcome.reason=='model_done' and outcome.worktree['state']=='retained'
        assert tree.worktree_root.exists()
        assert len(chat.provider.requests)==1
    finally:
        await chat.aclose()


@async_test
async def test_child_skill_resources_and_short_reminder_use_child_identity(tmp_path):
    from test_skills_catalog import entry as skill_entry
    root=repository(tmp_path/'repo')
    path=skill_entry(root/'.mewcode/skills/scoped.md','scoped',body='冻结 Skill 正文')
    git(root,'add','.');git(root,'commit','-m','冻结资源')
    skill_entry(path,'scoped',body='父未提交 Skill 正文')
    provider=ScriptedProvider([response(calls=(ToolCall('load','load_skill','{"name":"scoped"}'),)),response('已加载')])
    chat=session(root,provider)
    tasks,parent,record=submit(chat,{'type':'defined','prompt':'加载 scoped','role':'worker'},role(root))
    try:
        await tasks.wait_terminal(record.task_id)
        second=provider.requests[1][0][-1].content
        assert '冻结 Skill 正文' in second and '父未提交 Skill 正文' not in second
        assert record.outcome.worktree['workspace_root'] in second
        assert '不包含父目录未提交' in second
        assert '环境信息' not in second
        assert chat.skills.active==()
    finally:
        await chat.aclose()


@async_test
async def test_cancel_real_child_command_drains_then_next_task_runs(tmp_path):
    import json
    root=repository(tmp_path/'repo')
    command='touch started; sleep 30; touch too-late'
    provider=ScriptedProvider([response(calls=(ToolCall('run','execute_command',json.dumps({'command':command})),)),response('下一任务完成')])
    chat=session(root,provider)
    tasks,parent,record=submit(chat,{'type':'defined','prompt':'执行命令','role':'worker'},role(root))
    path=root/'.mewcode/worktrees'/record.task_id
    try:
        for _ in range(300):
            if (path/'started').exists(): break
            await asyncio.sleep(.01)
        assert (path/'started').exists()
        await tasks.cancel(record.task_id)
        if not record.handle.done(): await record.handle
        assert record.outcome.reason=='cancelled'
        assert record.outcome.worktree['state']=='retained'
        assert not (path/'too-late').exists()
        from mewcode.worktrees.records import Lease
        from mewcode.worktrees.manager import WorktreeManager
        from mewcode.worktrees.paths import freeze_repository
        m=WorktreeManager(freeze_repository(root));lease=Lease(m.lock_path(record.task_id));lease.close()
        next_parent=tasks.new_parent('下一任务')
        from mewcode.agents.runtime import freeze_child,ChildRuntime
        snapshot=freeze_child(chat,{'type':'defined','prompt':'说明'},role=role(root))
        next_record=tasks.submit(next_parent.task_id,lambda record:ChildRuntime(chat,record).run(),type='defined',snapshot=snapshot)
        await tasks.wait_terminal(next_record.task_id)
        assert next_record.outcome.reason=='model_done' and next_record.outcome.worktree['state']=='removed'
    finally:
        await chat.aclose()


@async_test
async def test_child_reads_own_project_memory_and_shared_user_without_extraction(tmp_path):
    from test_memory_store import write_note,note_value
    from test_skills_persistence import CONFIG
    root=repository(tmp_path/'repo')
    write_note(root/'.mewcode/memory',note_value(summary='冻结项目记忆'))
    git(root,'add','.');git(root,'commit','-m','冻结项目笔记')
    write_note(root/'.mewcode/memory',note_value(summary='父未提交项目记忆'))
    write_note(root/'user/memory',note_value('b'*32,scope='user',category='user_preference',summary='共享用户偏好',
        sources=[{'session_id':'session-one','task_id':'task-one','message_id':'message-one','quote':'以后所有项目都使用中文注释'}]))
    provider=ScriptedProvider([response('只读完成')])
    from mewcode.session import ChatSession
    from mewcode.tools import default_registry
    from mewcode.tools.base import ToolContext
    from mewcode.tools.executor import ToolExecutor
    from conftest import permission_bypass
    executor=ToolExecutor(default_registry(),ToolContext(root),permissions=permission_bypass(root))
    chat=ChatSession(provider,executor=executor,user_root=root/'user',config=CONFIG,persistent=True,memory_enabled=True)
    tasks,parent,record=submit(chat,{'type':'defined','prompt':'说明','role':'worker'},role(root))
    try:
        await tasks.wait_terminal(record.task_id)
        text=provider.requests[0][0][-1].content
        assert '冻结项目记忆' in text and '父未提交项目记忆' not in text
        assert '共享用户偏好' in text
        assert chat.memory._queue.empty() and len(provider.requests)==1
    finally:
        await chat.aclose()


@async_test
async def test_session_close_waits_actual_handle_after_display_timeout(tmp_path):
    from mewcode.async_utils import protected
    from mewcode.tasks.manager import TaskManager,TaskOutcome
    manager=TaskManager(cleanup_timeout=.01)
    parent=manager.new_parent('父')
    entered,release,drained=asyncio.Event(),asyncio.Event(),asyncio.Event()
    async def runner(record):
        entered.set()
        try: await record.cancel.wait()
        finally:
            await protected(release.wait())
            drained.set()
        return TaskOutcome('cancelled','真实收尾结束')
    record=manager.submit(parent.task_id,runner,type='defined')
    await entered.wait()
    close=asyncio.create_task(manager.aclose())
    try:
        await asyncio.sleep(.08)
        assert record.done.is_set() and not record.handle.done()
        assert not close.done()
        release.set();await close
        assert drained.is_set() and record.handle.done()
    finally:
        release.set();await close


@async_test
async def test_real_cleanup_metadata_survives_display_cancel_timeout():
    from mewcode.async_utils import protected
    from mewcode.tasks.manager import TaskManager,TaskOutcome
    tasks=TaskManager(cleanup_timeout=.01);parent=tasks.new_parent('父')
    entered,release=asyncio.Event(),asyncio.Event()
    async def run(record):
        entered.set()
        try: await record.cancel.wait()
        finally: await protected(release.wait())
        from mewcode.types import TokenUsage
        return TaskOutcome('cancelled','实际结束',evidence=({'source':'actual-result'},),usage=TokenUsage(input_tokens=10),
                           request_usage=(TokenUsage(input_tokens=10),),side_effects='possible',
                           worktree={'state':'retained','archive':'failed','reason':'证据保护'})
    record=tasks.submit(parent.task_id,run,type='defined');await entered.wait()
    try:
        await tasks.cancel(record.task_id)
        assert record.done.is_set() and not record.handle.done()
        release.set();await record.handle
        assert record.outcome.worktree['state']=='retained'
        assert record.report()['worktree']['archive']=='failed'
        assert record.outcome.evidence==({'source':'actual-result'},)
        assert record.outcome.usage.input_tokens==10 and record.outcome.request_usage[0].input_tokens==10
        assert record.outcome.side_effects=='possible'
    finally:
        release.set();await tasks.aclose()


@async_test
async def test_background_return_confirmed_only_after_durable_main_history(tmp_path):
    import json
    from conftest import collect
    from test_skills_persistence import CONFIG
    root=repository(tmp_path/'repo')
    chat=session(root,ScriptedProvider([response('父已收到')]),config=CONFIG,persistent=True)
    identity=chat.journal.id
    info={'workspace_root':str(root/'child'),'archive':'complete','state':'removed'}
    report={'parent_task_id':'parent_task','task_id':'agent_child','worktree':info,'content':'真实结果'}
    chat.agent.before_request=None
    chat._prepared_results=(report,)
    chat.prompt_state.task_results=json.dumps([report],ensure_ascii=False)
    try:
        await collect(chat.agent.run('接收',history=chat.history,mode='execute'))
        path=root/'.mewcode/sessions'/f'{identity}.jsonl'
        records=[json.loads(line) for line in path.read_text().splitlines()]
        returned=next(r for r in records if r['kind']=='worktree_event' and r['payload']['stage']=='returned')
        commit=next(r for r in records if r['seq']==returned['payload']['history_commit_seq'])
        assert commit['kind']=='history_commit' and commit['seq']<returned['seq']
        assert any('真实结果' in m['content'] for m in commit['payload']['messages'])
    finally:
        await chat.aclose()
