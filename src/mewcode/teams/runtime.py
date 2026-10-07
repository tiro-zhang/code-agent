"""稳定成员运行器；运行结束保存磁盘上下文，再发布空闲。"""
import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
import json
import hashlib
import os
from pathlib import Path
import signal
from uuid import uuid4

from ..async_utils import protected
from ..tools.base import ToolContext, ToolError, ToolResult
from ..tools.executor import ToolExecutor
from ..worktrees.paths import RepositorySnapshot, freeze_repository, git_read
from ..worktrees.manager import WorktreeManager
from ..worktrees.records import load_record
from .backends import BackendProbe, BackendLaunch, BackendHandle, TmuxBackend, WorkerObservation, select_backend
from .capabilities import TeamScope
from .control import MemberControl, PersistentBudget
from .models import Member, new_id, validate_name
from .store import TeamStore, TeamStoreError, TeamLease


class InprocessBackend:
    name = 'inprocess'

    async def probe(self):
        return BackendProbe(self.name, True, connection='独立协程、上下文、权限与工作根')


def validate_definition(value,member):
    """恢复只能采用完整冻结记录，不能扩大角色工具或修改目录身份。"""
    keys={'version','role_body','role_fingerprint','model','max_iterations','permission_mode',
          'parent_permission_mode','tools','worktree','repository'}
    if not isinstance(value,dict) or not keys<=set(value) or set(value)-keys-{'user_permission_path'} or type(value['version']) is not int or value['version']!=1:
        raise ValueError('冻结角色字段或版本无效')
    expected=getattr(member,'definition_fingerprint','')
    if expected and expected!=definition_fingerprint(value):
        raise ValueError('冻结角色能力摘要与权威花名册不符')
    if 'user_permission_path' in value and (not isinstance(value['user_permission_path'],str) or not Path(value['user_permission_path']).is_absolute()):
        raise ValueError('冻结用户权限路径无效')
    if (value['role_body'],value['role_fingerprint'])!=(member.role_body,member.role_fingerprint):
        raise ValueError('冻结角色与花名册不符')
    if value['model'] not in {'inherit','haiku','sonnet','opus'} or value['permission_mode'] not in {'inherit','bypass','default','strict'} or value['parent_permission_mode'] not in {'bypass','default','strict'}:
        raise ValueError('冻结模型或权限模式无效')
    if type(value['max_iterations']) is not int or value['max_iterations']<=0:
        raise ValueError('冻结请求预算无效')
    tools=value['tools']
    from ..tools import default_registry
    from ..mcp.tools import is_mcp_alias
    if (not isinstance(tools,list) or any(not isinstance(name,str) or not name or any(c.isspace() for c in name) for name in tools)
            or len(tools)!=len(set(tools)) or {'agent','team','team_member','team_integrate'} & set(tools)):
        raise ValueError('冻结工具范围无效')
    if any(name not in default_registry().names() and not is_mcp_alias(name) for name in tools):
        raise ValueError('冻结工具含未知执行能力')
    tree,repo=value['worktree'],value['repository']
    tree_keys={'name','task_id','origin_root','checkout_root','common_git_dir','worktree_root','workspace_root','branch','base_commit','git_dir'}
    repo_keys={'origin_root','checkout_root','common_git_dir','project_relative','base_commit'}
    if (not isinstance(tree,dict) or set(tree)!=tree_keys or not isinstance(repo,dict) or set(repo)!=repo_keys
            or any(not isinstance(v,str) or not v for v in (*tree.values(),*repo.values()))):
        raise ValueError('冻结工作树字段无效')
    if (tree['workspace_root'],tree['branch'],tree['task_id'])!=(member.workspace_root,member.branch,member.member_id):
        raise ValueError('冻结工作树身份不符')
    if any(tree[key]!=repo[key] for key in ('origin_root','checkout_root','common_git_dir','base_commit')):
        raise ValueError('冻结仓库关联不符')
    if not Path(repo['project_relative']).is_absolute() and '..' not in Path(repo['project_relative']).parts:
        return value
    raise ValueError('冻结项目相对位置无效')


def definition_fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,
                                     separators=(',',':')).encode()).hexdigest()


def tmux_handle(metadata):
    raw = metadata['launch']
    launch = BackendLaunch(raw['team_id'],raw['member_id'],raw['generation'],raw['nonce'],
        Path(raw['workspace_root']),Path(raw['config_path']),Path(raw['startup_path']))
    return BackendHandle(launch,**metadata['handle'])


async def observe_worker(service, launch):
    try:
        value = service.store.read_json(service.scope.team_name, f'members/{launch.member_id}/runtime.json')
        return WorkerObservation(**value)
    except (OSError, ValueError, TypeError):
        return None


def ensure_tmux(service):
    if not hasattr(service, 'tmux'):
        service.tmux = TmuxBackend(observe=lambda launch:observe_worker(service, launch),startup_timeout=30)
    return service.tmux


async def spawn_member(service, args):
    parent, team = service.session, service.require_participant(lead=True)
    if team.status != 'active' or team.active_goal_id is None:
        raise ValueError('派生成员前需要一个活动目标')
    name, role_name = args['name'], args['role']
    validate_name(name)
    if type(args.get('require_plan_approval',False)) is not bool:
        raise ValueError('计划审批开关必须是布尔值')
    if any(m.name == name for m in team.members.values()):
        raise ValueError('成员花名已登记，请直接发消息接续')
    role = parent.roles.get(role_name)
    selected = await select_backend(args.get('backend',getattr(parent.config,'team_backend','auto')),
        ensure_tmux(service),InprocessBackend(),service._notify)
    identity = new_id('member')
    goal = team.goals[team.active_goal_id]
    snapshot = replace(freeze_repository(parent.executor.context.root),base_commit=goal.baseline_commit)
    manager = WorktreeManager(snapshot,current_mode=lambda:parent.mode)
    tree_name=f'team/{team.team_id}/{identity}'
    from ..worktrees.records import key
    from ..worktrees.paths import managed_target
    await service.authorize_git(snapshot.checkout_root,'worktree','add','-b','mewcode/worktree/'+key(tree_name),
                                '--',str(managed_target(manager.container,tree_name)),snapshot.base_commit)
    tree = await manager.create(tree_name,task_id=identity)
    record = load_record(manager.record_path(tree.name))
    record['team_pin'] = {'team_id':team.team_id,'member_id':identity,'storage':str(service.store.team_path(team.name))}
    manager.save(tree.name,record)
    member = Member(identity,name,role_name,str(tree.workspace_root),role_body=role.body,
        role_fingerprint=role.fingerprint,branch=tree.branch,backend=selected.backend.name,
        require_plan_approval=args.get('require_plan_approval',False),state='starting',generation=uuid4().hex)
    await service.store.register_member(team.name,member)
    ceiling = role.tools(parent.delegation_tools() | {'team_task','team_message'}) - {'agent','team','team_member','team_integrate'}
    definition = {'version':1,'role_body':role.body,'role_fingerprint':role.fingerprint,'model':role.model,
        'max_iterations':role.max_iterations,'permission_mode':role.permission_mode,
        'parent_permission_mode':parent.permissions.mode,'tools':sorted(ceiling),
        'user_permission_path':str(parent.permissions.config.paths[0]),
        'worktree':tree.metadata(),'repository':{key:str(value) for key,value in asdict(snapshot).items()}}
    service.store.write_json(team.name,f'members/{identity}/definition.json',definition)
    member.definition_fingerprint=definition_fingerprint(definition)
    await service.store.update_team(team.name,lambda t:setattr(t.members[identity],'definition_fingerprint',member.definition_fingerprint))
    return await launch_member(service,member,definition,selected)


async def launch_member(service,member,definition,selected=None):
    """首次与恢复共用后端；不会注册新身份或创建新工作目录。"""
    parent,team=service.session,service.require_participant(lead=True)
    identity=member.member_id
    validate_definition(definition,member)
    role_config=parent.config if definition['model']=='inherit' else parent.config.for_agent(definition['model'])
    if selected is None:
        selected=await select_backend(member.backend,ensure_tmux(service),InprocessBackend(),service._notify)
        member=replace(member,generation=uuid4().hex,state='starting')
        await service.store.update_team(team.name,lambda t:t.members.__setitem__(identity,member))
    if selected.backend.name == 'inprocess':
        provider = parent.provider.fork(role_config) if hasattr(parent.provider,'fork') else parent.provider_factory(role_config)
        permissions = parent.permissions.fork(definition['permission_mode'],root=Path(member.workspace_root))
        runtime = MemberRuntime(service,member,definition,role_config,provider,permissions,
                                registry=parent.executor.registry,mcp=parent.executor.mcp)
        current=parent.delegation_tools() | ({'team_task','team_message'} if team.mode=='plan' else set())
        runtime.ceiling=frozenset(definition['tools']) & current
        try:
            await runtime.setup()
            service.runners[identity] = runtime
            runtime.task = asyncio.create_task(runtime.serve())
        except BaseException:
            await runtime.close()
            raise
    else:
        config_path = getattr(parent,'config_path',None)
        if config_path is None:
            await service.store.update_team(team.name,lambda t:setattr(t.members[identity],'state','failed'))
            raise ValueError('tmux 完整实例需要真实配置文件路径；选定后不会切换后端')
        startup = service.store.member_path(team.name,identity)/'startup.json'
        launch = BackendLaunch(team.team_id,identity,member.generation,uuid4().hex,
                               Path(member.workspace_root),Path(config_path).absolute(),startup)
        metadata = {'version':1,'team_name':team.name,'store_root':str(service.store.root),
                    'launch':{key:str(value) for key,value in asdict(launch).items()},
                    'handle':None,'activated':False}
        service.store.write_json(team.name,f'members/{identity}/startup.json',metadata)
        service.store.write_json(team.name,f'members/{identity}/registration.json',{'version':1,**metadata['launch'],
            'policy':{'permission_mode':parent.permissions.mode,'user_permission_path':str(parent.permissions.config.paths[0]),
                      'tools':sorted((parent.delegation_tools() | ({'team_task','team_message'} if team.mode=='plan' else set())) & set(definition['tools']))}})
        async def on_started(handle):
            metadata['handle'] = handle.metadata()
            metadata['handle'].pop('generation',None)
            service.store.write_json(team.name,f'members/{identity}/startup.json',metadata)
        try:
            handle = await selected.backend.start(launch,on_started=on_started)
            service.handles[identity] = handle
            metadata['activated'] = True
            service.store.write_json(team.name,f'members/{identity}/startup.json',metadata)
        except BaseException:
            await service.store.update_team(team.name,lambda t:setattr(t.members[identity],'state','needs_review'))
            raise
    return service.store.load(team.name).members[identity]


class MemberRuntime:
    def __init__(self, service, member, definition, config, provider, permissions, *, registry=None, mcp=None, launch=None):
        self.owner = service
        self.store = service.store
        self.team_name = service.scope.team_name
        self.member = member
        self.definition = definition
        self.config,self.provider,self.permissions = config,provider,permissions
        self.registry,self.mcp,self.launch = registry,mcp,launch
        self.wake,self.cancel = asyncio.Event(),asyncio.Event()
        self.stop_requested = False
        self.busy = False
        self.task = None
        self.session = None
        self.lease = self.tree_lease = None
        self.control = None
        self.run_id = new_id('run')
        self.ceiling = frozenset(definition['tools'])
        self.budget = None
        self.before_shutdown = None
        self.before_ready = None
        self.shutdown_monitor = None
        self._close_failed = False
        self._closed = False
        self.revising = False
        self.conflict_lease = None
        self.conflict_root = None
        self._conflict_original = None

    async def state(self,state):
        def update(team):
            member = team.members[self.member.member_id]
            if member.generation != self.member.generation:
                raise ValueError('成员运行代次已更换')
            member.state = state
            if self.control:
                member.task_id,member.claim_id = self.control.task_id,self.control.claim_id
                member.plan_id,member.plan_version = self.control.plan_id,self.control.plan_version
                member.plan_approved = self.control.approved
        await self.store.update_team(self.team_name,update)
        if state in {'idle','awaiting_approval','blocked','needs_review'} and hasattr(self.owner,'_notify'):
            self.owner._notify(f'成员 {self.member.name} · {state}；空闲和成果待验收分别记录')
        if self.launch:
            self.store.write_json(self.team_name,f'members/{self.member.member_id}/runtime.json',{
                'team_id':self.launch.team_id,'member_id':self.launch.member_id,'generation':self.launch.generation,
                'nonce':self.launch.nonce,'pid':os.getpid(),'lease_owned':self.lease is not None,
                'state':state,'shutdown_confirmed':state=='stopped' and self.lease is None})

    async def setup(self):
        from ..session import ChatSession
        from ..prompts import build_system_prompt
        if not getattr(self.member,'definition_fingerprint',''):
            raise ValueError('成员冻结能力缺少权威指纹，不能恢复')
        validate_definition(self.definition,self.member)
        self.lease = self.store.member_lease(self.team_name,self.member.member_id)
        raw = self.definition['repository']
        repo = RepositorySnapshot(**{key:Path(value) if key!='base_commit' else value for key,value in raw.items()})
        self.manager = WorktreeManager(repo)
        tree = self.manager.recover(self.definition['worktree']['name'],task_id=self.member.member_id)
        self.tree_lease = self.manager.enter(tree)
        storage = self.store.member_path(self.team_name,self.member.member_id)
        executor = ToolExecutor(self.registry or __import__('mewcode.tools',fromlist=['default_registry']).default_registry(),
            ToolContext(Path(self.member.workspace_root)),permissions=self.permissions,mcp=self.mcp)
        self.session = ChatSession(self.provider,executor=executor,config=self.config,
            max_iterations=self.definition['max_iterations'],user_root=self.owner.session.user_root,memory_enabled=False,
            persistent=True,resume=self.member.session_ref or None,session_storage=storage,
            team_scope=TeamScope(self.team_name,self.member.member_id,False))
        self.session.teams.runtime = self
        self.session.mode = self.store.load(self.team_name).mode
        # 协程之间共享可信邮箱路由，而非通过 Lead 转发消息内容。
        self.session.teams.store = self.store
        self.session.teams.notify_member = self.owner.notify_member
        self.session.agent.system_prompt = build_system_prompt() + '\n\n固定团队角色：\n' + self.definition['role_body'] + (
            '\n团队成员只能修改自己的工作目录。领取任务后使用 team_task start 确认依赖同步；'
            '结束前提交成果和验证证据，由 Lead 验收。需要计划审批时先使用 team_message '
            'type=plan_request 发给 lead；批准计划不授予工具权限。禁止普通 Agent 或独立 Skill 派生。')
        original = self.session.effective_tools
        self.session.effective_tools = lambda: frozenset(original() & self.ceiling & (
            {'read_file','glob_files','search_code','load_skill','team_task','team_message'}
            if self.control and self.control.awaiting_plan else self.ceiling))
        # 构造期间保存过绑定方法，必须同时更新模型与 Skill 的动态能力回调。
        self.session.agent.allowed_tools = lambda:self.session.effective_tools()
        self.session.skill_service.allowed_tools = lambda:self.session.effective_tools()
        self.session.hooks.current_mode = self.hook_mode
        self.session.hooks.runner.current_mode = self.hook_mode
        self.session.agent.before_request = self.before_request
        self.session.agent.system_passthrough = False
        self.session.executor.cache_reader = self.read_cache
        team = self.store.load(self.team_name)
        saved = self.session.journal.projection.team_control
        self.control = (MemberControl.from_dict(saved) if saved else MemberControl(team.team_id,self.member.member_id,
            team.lead_id,require_plan_approval=self.member.require_plan_approval))
        if (self.control.team_id,self.control.member_id,self.control.lead_id) != (team.team_id,self.member.member_id,team.lead_id):
            raise ValueError('成员存档身份不匹配')
        self.reconcile_control()
        recovery_blocked = await self.owner.isolate_member_projection(self.member,
            self.session.journal.projection, receiver=True)
        self.save_control()
        self.restore_budget()
        await self.store.update_team(self.team_name,lambda t:setattr(t.members[self.member.member_id],'session_ref',self.session.session_id))
        await self.session.teams.inbox().reconcile(self.member.member_id,self.control.consumed_ids)
        if self.before_ready:
            await self.before_ready()
            self.before_ready=None
        await self.state('needs_review' if recovery_blocked else 'idle')

    def reconcile_control(self):
        team,tasks=self.session.teams.board()._load()
        member=team.members[self.member.member_id]
        self.control.require_plan_approval = member.require_plan_approval
        task=tasks.get(member.task_id)
        if (not task or task.goal_id!=team.active_goal_id or team.goals[task.goal_id].state!='active'
                or task.owner_id!=member.member_id or task.claim_id!=member.claim_id):
            self.control.bind_task(None,None)
            self.control.plan_id,self.control.plan_version,self.control.approved=None,0,False
            return
        if (self.control.task_id,self.control.claim_id) != (member.task_id,member.claim_id):
            self.control.bind_task(member.task_id,member.claim_id)
        if (self.control.plan_id,self.control.plan_version,self.control.approved) != (
                member.plan_id,member.plan_version,member.plan_approved):
            self.control.plan_id,self.control.plan_version = member.plan_id,member.plan_version
            self.control.approved = False

    def restore_budget(self):
        def commit(value):
            candidate = MemberControl.from_dict(self.control.to_dict())
            candidate.budget = value
            self.session.journal.append('team_control',candidate.to_dict())
            self.control.budget = value
        limit=self.definition['max_iterations']
        if self.control.task_id:
            _,tasks=self.session.teams.board()._load()
            task=tasks.get(self.control.task_id)
            if task and task.claim_id==self.control.claim_id:
                limit=min(limit,task.budget_limit)
        candidate = (PersistentBudget.from_dict(self.control.budget,commit=commit) if self.control.budget else
            PersistentBudget(limit,self.control.claim_id or self.run_id,commit=commit))
        if self.busy and self.budget:
            # Agent.run 正在引用此对象。切换领取只能更新对象，不能留下旧闭包。
            if self.budget.root_run_id!=candidate.root_run_id:
                self.budget.usage=[]
            self.budget.limit,self.budget.root_run_id,self.budget.used,self.budget.commit=(
                candidate.limit,candidate.root_run_id,candidate.used,candidate.commit)
        else:
            self.budget=candidate
        if self.control.task_id and task and task.claim_id==self.control.claim_id:
            if task.budget_used > self.budget.limit:
                raise ValueError('共享任务预算超出冻结上限')
            if task.budget_used > self.budget.used:
                self.budget.used=task.budget_used
                commit(self.budget.to_dict())
        if self.control.budget is None:
            commit(self.budget.to_dict())

    def save_control(self):
        self.session.journal.append('team_control',self.control.to_dict())

    def refresh_context(self):
        """每次空闲接续从持有租约的磁盘存档读取，不依赖旧内存。"""
        from ..sessions.store import Journal
        from ..context.spill import ResultCache
        old=self.session.journal
        identity,root,storage=old.id,old.root,old.storage_root
        old.close()
        journal=Journal.resume(root,identity,self.config.protocol,self.config.model,storage_root=storage)
        self.session.journal=self.session.agent.journal=journal
        self.session.history[:]=journal.projection.history
        self.session.context.cache.close()
        cache=ResultCache(root,session_id=identity,persistent=True,storage_root=storage)
        cache.reopen(journal.projection.cache_paths)
        for path in journal.projection.cache_paths:
            cache.restore(path)
        self.session.context.cache=cache
        self.session.context.restore_state(journal.projection.state)
        from ..skills.catalog import discover_skills
        self.session.skills.catalog=discover_skills(root,user_root=self.session.user_root)
        self.session.skills.restore(journal.projection.active_skills)
        if journal.projection.team_control:
            self.control=MemberControl.from_dict(journal.projection.team_control)
            self.reconcile_control()
            self.save_control()
            self.restore_budget()

    def hook_mode(self):
        """生命周期动作与模型工具共用当前领取、审批和开工上限。"""
        if (not self.control or self.control.awaiting_plan or self.session.mode == 'plan'
                or 'execute_command' not in self.session.effective_tools()):
            return 'plan'
        try:
            team,tasks = self.session.teams.board()._load()
            task = tasks.get(self.control.task_id)
            if (not task or task.owner_id != self.member.member_id or task.claim_id != self.control.claim_id
                    or task.goal_id != team.active_goal_id or team.goals[task.goal_id].state != 'active'
                    or task.state != 'running' or (task.code and not task.synced_commit)):
                return 'plan'
        except (OSError,ValueError):
            return 'plan'
        return 'execute'

    def guard(self,name,arguments):
        if name not in self.session.effective_tools():
            raise ToolError('tool_not_allowed','成员当前运行范围禁止此工具',not_started=True)
        if name == 'load_skill' and arguments and 'resource' not in arguments:
            if self.session.skills.catalog.get(arguments['name']).mode=='isolated':
                raise ToolError('tool_not_allowed','团队成员不能派生独立 Skill',not_started=True)
        if not arguments:
            return
        mutating = name not in {'read_file','glob_files','search_code','load_skill','team_task','team_message'}
        if name == 'team_task' and arguments.get('action') == 'submit':
            mutating = True
        if mutating:
            team,tasks = self.session.teams.board()._load()
            task = tasks.get(self.control.task_id)
            if (not task or task.owner_id != self.member.member_id or task.claim_id != self.control.claim_id
                    or task.goal_id != team.active_goal_id or team.goals[task.goal_id].state != 'active'
                    or task.state != 'running' or (task.code and not task.synced_commit)):
                raise ToolError('tool_not_allowed','成员执行前必须领取当前任务并确认依赖同步',not_started=True)

    async def read_cache(self,args):
        path = args['path']
        cache = self.session.context.cache
        if path not in cache.paths:
            return None
        with os.fdopen(cache._read_fd(path),'r',encoding='utf-8') as stream:
            start,count = args.get('start_line',1),args.get('max_lines',200)
            selected=[]
            size=0
            truncated=False
            for index,line in enumerate(stream,1):
                if index < start:
                    continue
                if index >= start+count or size+len(line.encode())>self.session.executor.context.output_limit:
                    truncated=True
                    break
                selected.append(line)
                size+=len(line.encode())
        return ToolResult.success({'path':path,'content':''.join(selected),'start_line':start,
                                   'end_line':start+len(selected)-1},truncated=truncated)

    async def receive(self):
        inbox = self.session.teams.inbox()
        await inbox.reconcile(self.member.member_id,self.control.consumed_ids)
        messages = [m for m in await inbox.read(self.member.member_id) if m.message_id not in self.control.consumed_ids]
        if not messages:
            return []
        from ..types import Message
        candidate = list(self.session.history)
        for m in messages:
            if not any(old.id=='mail-'+m.message_id for old in candidate):
                candidate.append(Message('context',json.dumps(m.to_dict(),ensure_ascii=False),
                                         id='mail-'+m.message_id,context_kind='task_resume'))
        # 锁读取可能经历等待；提交前取消或停止不消费新信件。
        # 已提交的旧 consumed_ids 已在开头对账，不因取消撤销事实。
        if self.cancel.is_set() or self.stop_requested:
            return []
        # 历史先落盘。若控制记录前崩溃，稳定消息 ID 可重建并避免重复注入。
        self.session._checkpoint(candidate,self.session.context.state())
        self.session.history[:]=candidate
        for m in messages:
            if m.type=='shutdown_request' and m.sender_id==self.control.lead_id and m.fields['generation']==self.member.generation:
                self.stop_requested=True
                self.cancel.set()
            elif m.type=='task_assignment' and m.sender_id==self.control.lead_id and self.protocol_current(m):
                self.control.bind_task(m.fields['task_id'],m.fields['claim_id'])
                self.restore_budget()
            elif m.type=='plan_decision' and self.protocol_current(m):
                try:
                    self.control.decide(m.sender_id,m.fields['plan_id'],m.fields['plan_version'],m.fields['approved'])
                except ToolError:
                    # 旧决定作为历史保留，不释放当前版本的门禁。
                    pass
            self.control.consumed_ids.append(m.message_id)
        self.save_control()
        await inbox.ack(self.member.member_id,self.control.consumed_ids)
        return messages

    def protocol_current(self,message):
        team,tasks = self.session.teams.board()._load()
        fields = message.fields
        member = team.members[self.member.member_id]
        task = tasks.get(fields.get('task_id'))
        if (not task or task.goal_id != fields.get('goal_id') or task.goal_id != team.active_goal_id
                or team.goals[task.goal_id].state != 'active' or task.owner_id != self.member.member_id
                or task.claim_id != fields.get('claim_id') or member.task_id != task.task_id
                or member.claim_id != task.claim_id):
            return False
        if message.type == 'plan_decision':
            return (message.sender_id == team.lead_id and member.state == 'awaiting_approval'
                and member.plan_id == fields.get('plan_id') and member.plan_version == fields.get('plan_version'))
        return True

    async def before_request(self):
        self.session.mode=self.store.load(self.team_name).mode
        await self.receive()
        self.session._before_request()
        waiting_notice='计划尚未批准。先只读规划，再向 lead 发送 plan_request，随后等待匹配版本决定。'
        if self.control.awaiting_plan:
            self.session.prompt_state.workspace_notice = waiting_notice
            if self.control.plan_id and not self.revising:
                return False
        elif self.session.prompt_state.workspace_notice==waiting_notice:
            self.session.prompt_state.workspace_notice=f'实际工具工作目录：{self.session.executor.context.root}。计划门禁已解除，工具仍按实际权限和任务开工状态检查。'
        if self.control.task_id and self.budget.remaining:
            board=self.session.teams.board()
            task=await board.get(self.member.member_id,self.control.task_id)
            amount=self.budget.used+1-task.budget_used
            if amount>0:
                # 先占用共享预算；存档或实际请求失败也不能恢复这一份额度。
                await board.use_budget(self.member.member_id,task.task_id,self.control.claim_id,amount=amount)

    async def propose(self,body):
        if not self.control.task_id or not self.control.claim_id:
            raise ValueError('请求计划批准前需要有效任务领取身份')
        values=self.control.propose(self.control.task_id,self.control.claim_id,body)
        self.revising=False
        self.save_control()
        await self.state('awaiting_approval')
        team=self.store.load(self.team_name)
        return {'goal_id':team.active_goal_id,**values}

    async def start_task(self,identity,claim):
        if self.control.awaiting_plan:
            raise ToolError('tool_not_allowed','计划尚未批准',not_started=True)
        if (identity,claim)!=(self.control.task_id,self.control.claim_id):
            raise ToolError('tool_not_allowed','开工必须匹配当前已恢复的领取身份',not_started=True)
        task=await self.session.teams.board().get(self.member.member_id,identity)
        if task.owner_id!=self.member.member_id or task.claim_id!=claim:
            raise ValueError('领取身份不匹配')
        root=self.session.executor.context.root
        if task.code:
            from .integration import IntegrationService
            team=self.store.load(self.team_name)
            goal=team.goals[task.goal_id]
            service=IntegrationService(Path(team.workspace_root),self.store.team_path(team.name)/'integration',
                goal.goal_id,goal.baseline_commit,goal.target_branch,
                authorize=lambda cwd,args:self.session.teams.authorize_git(cwd,*args))
            required=goal.baseline_commit
            if any(t.code for t in [await self.session.teams.board().get(self.member.member_id,i) for i in task.depends_on]):
                status=await service.status()
                required=status['head']
            try:
                git_read(root,'merge-base','--is-ancestor',required,'HEAD')
            except ToolError:
                await service.initialize()
                await service.sync_member(root,self.member.branch,required)
        head=git_read(root,'rev-parse','HEAD')
        return await self.session.teams.board().start(self.member.member_id,identity,claim,synced_commit=head)

    async def enter_conflict_workspace(self):
        """只有可信交接记录和真实排他租约可改变本次运行工作根。"""
        if not self.control.task_id:
            return
        try:
            grant=self.store.read_json(self.team_name,f'members/{self.member.member_id}/conflict.json')
        except TeamStoreError as error:
            if isinstance(error.__cause__,FileNotFoundError):
                return
            raise
        keys={'version','goal_id','task_id','claim_id','operation_id','member_id','token','workspace_root','conflict_files'}
        if set(grant)!=keys or type(grant['version']) is not int or grant['version']!=1:
            raise ValueError('冲突交接记录无效')
        if (grant['task_id'],grant['claim_id'])!=(self.control.task_id,self.control.claim_id):
            return
        team=self.store.load(self.team_name)
        task=await self.session.teams.board().get(self.member.member_id,grant['task_id'])
        if (grant['member_id']!=self.member.member_id or grant['goal_id']!=team.active_goal_id
                or task.owner_id!=self.member.member_id or task.claim_id!=grant['claim_id'] or task.code
                or task.state not in {'claimed','running'}):
            raise ValueError('冲突交接与当前领取不匹配')
        from .integration import IntegrationService
        goal=team.goals[grant['goal_id']]
        service=IntegrationService(Path(team.workspace_root),self.store.team_path(team.name)/'integration',
            goal.goal_id,goal.baseline_commit,goal.target_branch)
        root=service.worktree_root.resolve()
        if Path(grant['workspace_root']).resolve()!=root:
            raise ValueError('冲突目录与受管整合目录不符')
        lease=service.acquire_conflict_lease(grant['operation_id'],self.member.member_id,grant['token'])
        self.conflict_lease,self.conflict_root=lease,root
        self._conflict_original=(self.session.executor.context,self.session.permissions,self.session.hooks,
                                 self.session.prompt_state.workspace_notice)
        permissions=self.permissions.fork(root=root)
        permissions.writable_protected_roots.add(root)
        self.session.permissions=self.session.executor.permissions=permissions
        self.session.executor.context=ToolContext(root)
        from ..hooks.runtime import create_runtime
        hooks=create_runtime(root,permissions,self.hook_mode)
        self.session.hooks=self.session.agent.hooks=hooks
        self.session.prompt_state.workspace_notice=f'本次任务持有冲突处理租约，实际工具和 shell 工作根：{root}。解决冲突并提交合并；不修改成员原目录。'
        self.session.prompt_state.full_pending=True

    async def leave_conflict_workspace(self):
        if not self.conflict_lease:
            return
        # Hook 的后台命令也必须结束后才释放目录租约。
        await self.session.hooks.close()
        context,permissions,hooks,notice=self._conflict_original
        self.session.executor.context=context
        self.session.permissions=self.session.executor.permissions=permissions
        self.session.hooks=self.session.agent.hooks=hooks
        self.session.prompt_state.workspace_notice=notice
        self.session.prompt_state.full_pending=True
        self.conflict_lease.close()
        self.conflict_lease=self.conflict_root=self._conflict_original=None

    async def acquire_slot(self):
        maximum=getattr(self.config,'team_max_running',4)
        queued=getattr(self.config,'team_max_queued',32)
        if self.cancel.is_set():
            raise asyncio.CancelledError
        # 活动名额已独立计数；仅未拿到名额的成员占用排队容量。
        for index in range(maximum):
            try:
                return TeamLease(self.store.team_path(self.team_name)/'locks'/f'slot-{index}.lock')
            except TeamStoreError:
                pass
        queue_lease=None
        for index in range(queued):
            try:
                queue_lease=TeamLease(self.store.team_path(self.team_name)/'locks'/f'queue-{index}.lock')
                break
            except TeamStoreError:
                pass
        if queue_lease is None:
            raise ValueError('团队活动与排队容量已耗尽')
        try:
            while not self.cancel.is_set():
                for index in range(maximum):
                    try:
                        lease=TeamLease(self.store.team_path(self.team_name)/'locks'/f'slot-{index}.lock')
                        queue_lease.close()
                        return lease
                    except TeamStoreError:
                        pass
                await asyncio.sleep(.05)
            raise asyncio.CancelledError
        finally:
            queue_lease.close()

    async def serve(self):
        self.shutdown_monitor = asyncio.create_task(self.watch_shutdown())
        try:
            while not self.stop_requested:
                self.wake.clear()
                if await self.session.teams.inbox().read(self.member.member_id):
                    self.refresh_context()
                messages=await self.receive()
                actionable=[m for m in messages if m.type=='text' or
                            (m.type in {'task_assignment','plan_decision'} and self.protocol_current(m))]
                if actionable and not self.stop_requested:
                    recovery_blocked = await self.owner.isolate_member_projection(self.member,
                        self.session.journal.projection, receiver=True)
                    valid_assignment=any(m.type=='task_assignment' and self.protocol_current(m) for m in actionable)
                    blocked=self.store.load(self.team_name).members[self.member.member_id].state in {'blocked','failed','needs_review'}
                    self.revising=any(m.sender_id==self.control.lead_id and (m.type=='text' or
                        (m.type=='plan_decision' and m.fields.get('approved') is False and self.protocol_current(m))) for m in actionable)
                    if recovery_blocked:
                        await self.state('needs_review')
                    elif blocked and not valid_assignment:
                        await self.state('blocked')
                    elif self.control.awaiting_plan and self.control.plan_id and not self.revising:
                        await self.state('awaiting_approval')
                    elif self.budget.remaining:
                        await self.run('\n'.join(m.body or m.type for m in actionable))
                    else:
                        await self.state('blocked')
                if not self.stop_requested:
                    try:
                        await asyncio.wait_for(self.wake.wait(),.25)
                    except TimeoutError:
                        pass
        except asyncio.CancelledError:
            self.cancel.set()
        except (OSError,ValueError,ToolError) as error:
            if self.session:
                self.session.warnings.append(f'成员运行停止，待核查：{error}')
            await self.state('needs_review')
        finally:
            await protected(self.close())

    async def watch_shutdown(self):
        """流式回复和工具执行期间只处理停止，不注入普通邮箱正文。"""
        while not self.stop_requested:
            team=self.store.load(self.team_name)
            messages=await self.session.teams.inbox().read(self.member.member_id)
            if team.status!='active' or any(m.type=='shutdown_request' and m.sender_id==team.lead_id
                    and m.fields.get('generation')==self.member.generation for m in messages):
                self.stop_requested=True
                self.cancel.set()
                self.wake.set()
                return
            if self.launch:
                try:
                    lease=TeamLease(self.store.team_path(self.team_name)/'lead.lock')
                except TeamStoreError:
                    pass
                else:
                    lease.close()
                    self.stop_requested=True
                    self.cancel.set()
                    self.wake.set()
                    return
            await asyncio.sleep(.1)

    async def run(self,question):
        self.session.mode=self.store.load(self.team_name).mode
        self.busy=True
        self.run_id=new_id('run')
        slot=None
        source=None
        finished=None
        try:
            slot=await self.acquire_slot()
            await self.enter_conflict_workspace()
            self.session.hooks.resume_mutations()
            await self.state('planning' if self.session.mode=='plan' or self.control.awaiting_plan else 'running')
            source=self.session.agent.run(question,history=self.session.history,mode=self.session.mode,
                cancel_event=self.cancel,budget=self.budget,run_id=self.run_id)
            async for event in source:
                if event.kind=='finished':
                    finished=event
            if self.session.agent.storage_blocked:
                raise OSError('成员上下文存储失败')
            self.session._checkpoint(self.session.history,self.session.context.state())
            self.save_control()
            if self.control.task_id and finished and finished.reason!='model_done' and not self.control.awaiting_plan:
                await self.session.teams.board().report(self.member.member_id,self.control.task_id,self.control.claim_id,
                    state='blocked',reason=finished.reason,budget_used=self.budget.used)
            state='awaiting_approval' if self.control.awaiting_plan and self.control.plan_id else (
                'idle' if finished and finished.reason=='model_done' else 'blocked')
            if source:
                await protected(source.aclose(),cancel_event=self.cancel)
                source=None
            await protected(self.session.hooks.quiesce(cancel_event=self.cancel),cancel_event=self.cancel)
            await protected(self.leave_conflict_workspace(),cancel_event=self.cancel)
            await self.state(state)
            if state=='idle':
                team=self.store.load(self.team_name)
                await self.session.teams.inbox().send(self.member.member_id,team.lead_id,'成员已保存上下文并空闲；任务仍需明确验收',
                    type='idle',fields={'goal_id':team.active_goal_id,'run_id':self.run_id})
        finally:
            if source:
                await protected(source.aclose(),cancel_event=self.cancel)
            await protected(self.leave_conflict_workspace(),cancel_event=self.cancel)
            if slot:
                slot.close()
            self.busy=False

    async def stop(self):
        self.stop_requested=True
        self.cancel.set()
        self.wake.set()
        if self.task and self.task is not asyncio.current_task():
            await protected(self.task,cancel_event=self.cancel)
        else:
            await self.close()

    async def close(self):
        if self._closed:
            return
        if self._close_failed:
            raise OSError('成员服务收尾此前失败；不可冒充停止')
        if self.shutdown_monitor and self.shutdown_monitor is not asyncio.current_task():
            self.shutdown_monitor.cancel()
            await asyncio.gather(self.shutdown_monitor,return_exceptions=True)
            self.shutdown_monitor=None
        if self.session:
            await self.leave_conflict_workspace()
            await self.session.aclose()
        if self.provider is not self.owner.session.provider:
            await self.provider.aclose()
        if self.before_shutdown:
            try:
                await self.before_shutdown()
            except BaseException:
                self._close_failed = True
                await self.state('needs_review')
                raise
            self.before_shutdown=None
        if self.tree_lease:
            # 不调用一次性子任务 exit/delete。团队 pin 随暂停持久保留。
            record=load_record(self.manager.record_path(self.tree_lease.tree.name))
            record.update(stage='stopped',evidence_protected=True)
            self.manager.save(self.tree_lease.tree.name,record)
            self.tree_lease.close()
            self.tree_lease=None
        if self.lease:
            self.lease.close()
            self.lease=None
        await self.state('stopped')
        if self.launch and self.session:
            team=self.store.load(self.team_name)
            await self.session.teams.inbox().send(self.member.member_id,team.lead_id,'成员已收尾全部操作',
                type='shutdown_ack',fields={'generation':self.member.generation,'stopped':True})
        self._closed = True
