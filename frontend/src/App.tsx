import {useCallback,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {Cat,Plus,Folder,PanelRight,PanelLeft,X,Sparkles,ArrowDown,Square,Shield,Code2,Search,ArrowUpRight,Activity,RefreshCw,LoaderCircle} from 'lucide-react';
import {Api,ApiError,type IdentityIssue} from './api';
import {parse_sse} from './stream';
import {empty_state,accept_snapshot,clear_accepted_draft,merge_messages,phase_label,input_error,bound_history,refresh_history_page} from './state';
import type {ServerState,SessionItem,HistoryPage,Draft,SubmittedDraft,Message,Approval} from './types';
import {Composer} from './Composer';
import {SafeMarkdown} from './Markdown';
import {ApprovalCard} from './Approval';
import {RunDetails,ResultViewer,show_value} from './Details';

const error_text=(error:unknown)=>error instanceof Error?error.message:'操作未完成，请稍后重试。';
const blank_draft:Draft={text:'',version:0};
const delay=(ms:number,signal:AbortSignal)=>new Promise<void>(resolve=>{const timer=setTimeout(done,ms);function done(){clearTimeout(timer);signal.removeEventListener('abort',done);resolve();}signal.addEventListener('abort',done,{once:true});});
function read_local(key:string){try{return sessionStorage.getItem(key);}catch{return null;}}
function date_text(date:string){const parsed=new Date(date);return Number.isNaN(+parsed)?'时间未知':parsed.toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit'});}

export function App(){
    const [api]=useState(()=>new Api());
    const [state,set_state]=useState<ServerState>(empty_state),state_ref=useRef(empty_state);
    const [sessions,set_sessions]=useState<SessionItem[]>([]),[sessions_cursor,set_sessions_cursor]=useState<string|null>(null);
    const [selected,set_selected]=useState<string|null>(null),selected_ref=useRef<string|null>(null);
    const [history_pages,set_history_pages]=useState<Record<string,HistoryPage>>({});
    const history_pages_ref=useRef(history_pages);history_pages_ref.current=history_pages;
    const history_epochs=useRef(new Map<string,number>()),history_paginated=useRef(new Set<string>()),history_old_cursor=useRef(new Set<string>());
    const previous_runtime=useRef<{key:string;busy:boolean}|null>(null);
    const history_anchor=useRef<{session_id:string;message_id:string|null;offset:number;scroll_top:number}|null>(null);
    const [drafts,set_drafts]=useState<Record<string,Draft>>({}),drafts_ref=useRef<Record<string,Draft>>({});
    const [connection,set_connection]=useState('连接中'),[authenticated,set_authenticated]=useState(false),[auth_error,set_auth_error]=useState('');
    const [notice,set_notice]=useState(''),[error,set_error]=useState(''),[storage_warning,set_storage_warning]=useState('');
    const [working,set_working]=useState(false),[pending,set_pending]=useState(false),[unknown_operation,set_unknown_operation]=useState(false);
    const [identity_issue,set_identity_issue]=useState<IdentityIssue|null>(null);
    const [details_open,set_details_open]=useState(true),[drawer,set_drawer]=useState<'sessions'|'details'|null>(null);
    const [confirm_control,set_confirm_control]=useState<string|null>(null);
    const timeline=useRef<HTMLDivElement>(null),positions=useRef(new Map<string,number>()),near_bottom=useRef(true);
    const [has_new,set_has_new]=useState(false);
    const syncing=useRef<Promise<ServerState>|null>(null);
    const old_focus=useRef<HTMLElement|null>(null),panel_ref=useRef<HTMLElement|null>(null);

    const save_draft=useCallback((session_id:string,draft:Draft)=>{
        drafts_ref.current={...drafts_ref.current,[session_id]:draft};set_drafts(drafts_ref.current);
        try{sessionStorage.setItem(`mewcode:draft:${state_ref.current.project.key}:${session_id}`,JSON.stringify(draft));}
        catch{set_storage_warning('标签页存储不可用或已满；草稿仍在当前页面，请在刷新前复制。');}
    },[]);
    const get_draft=useCallback((session_id:string):Draft=>{
        if(drafts_ref.current[session_id])return drafts_ref.current[session_id];
        try{const saved=JSON.parse(read_local(`mewcode:draft:${state_ref.current.project.key}:${session_id}`)||'null');if(saved&&typeof saved.text==='string'&&typeof saved.version==='number')return saved;}catch{/* 损坏的本地值不进入服务端。 */}
        return blank_draft;
    },[]);
    const consume_receipt=useCallback((receipt:{draft?:SubmittedDraft;accepted:boolean;body:any}|null)=>{
        if(!receipt)return;
        if(receipt.accepted&&receipt.draft){const current=get_draft(receipt.draft.session_id);save_draft(receipt.draft.session_id,clear_accepted_draft(current,receipt.draft));}
        if(!receipt.accepted)set_error(receipt.body?.error?.message||'原提交未被接受，草稿已保留。');
        set_pending(Boolean(api.pending));set_unknown_operation(false);
    },[api,get_draft,save_draft]);
    const load_sessions=useCallback(async(cursor?:string)=>{
        const page=await api.get<{items:SessionItem[];next_cursor:string|null}>(`/sessions${cursor?`?cursor=${encodeURIComponent(cursor)}`:''}`);
        set_sessions(old=>cursor?[...new Map([...old,...page.items].map(item=>[item.id,item])).values()]:page.items);set_sessions_cursor(page.next_cursor);
    },[api]);
    const synchronize=useCallback(async()=>{
        if(syncing.current)return syncing.current;
        const promise=(async()=>{
            const next=await api.get<ServerState>('/state');
            if(state_ref.current.server_instance_id&&state_ref.current.server_instance_id!==next.server_instance_id){
                await api.connect(next);set_identity_issue(api.identity_issue);set_pending(false);set_unknown_operation(false);set_history_pages({});history_epochs.current.clear();history_paginated.current.clear();history_old_cursor.current.clear();
                set_notice('本机服务已重新启动。旧操作和审批已失效；请检查已保存历史，再明确继续会话。未确认草稿没有自动发送。');
            }
            const accepted=accept_snapshot(state_ref.current,next);state_ref.current=accepted;set_state(accepted);return accepted;
        })();
        syncing.current=promise;try{return await promise;}finally{syncing.current=null;}
    },[api]);

    useEffect(()=>{
        const abort=new AbortController();let alive=true;
        async function start(){
            try{
                // 启动凭据只用于首次交换，读取后立刻清除地址栏片段。
                const fragment=location.hash.slice(1);if(fragment){history.replaceState(null,'',location.pathname+location.search);const token=new URLSearchParams(fragment).get('token')||fragment;await api.authenticate(token);}
                const initial=await api.get<ServerState>('/state');if(!alive)return;
                const changed=await api.connect(initial);state_ref.current=initial;set_state(initial);set_authenticated(true);set_storage_warning(api.storage_error);
                if(changed)set_notice('本机服务已重新启动，旧操作不会重发。请先检查历史，再明确继续。');
                set_pending(Boolean(api.pending));set_identity_issue(api.identity_issue);
                if(api.pending&&!api.identity_issue){try{consume_receipt(await api.recover());}catch(error){if(error instanceof ApiError&&error.status===404)set_unknown_operation(true);set_error(error_text(error));set_identity_issue(api.identity_issue);set_pending(Boolean(api.pending));}}
                const saved=read_local(`mewcode:selected:${initial.project.key}`);if(saved){selected_ref.current=saved;set_selected(saved);const draft=get_draft(saved);drafts_ref.current={...drafts_ref.current,[saved]:draft};set_drafts(drafts_ref.current);}
                await load_sessions();
                let cursor=initial.seq,instance=initial.server_instance_id;
                while(alive&&!abort.signal.aborted){
                    try{
                        const response=await fetch(`/api/v1/events?after=${cursor}`,{credentials:'same-origin',cache:'no-store',signal:abort.signal});
                        if(response.status===401){set_auth_error('访问凭据已过期，请重新打开终端中的本次启动链接。');set_authenticated(false);break;}
                        if(response.status===429){
                            set_connection('轮询同步');
                            for(let i=0;i<10&&alive;i++){const next=await synchronize();cursor=next.seq;instance=next.server_instance_id;await delay(1500,abort.signal);}
                            continue;
                        }
                        if(!response.ok||!response.body)throw new Error('事件连接暂不可用');
                        set_connection('已连接');
                        for await(const event of parse_sse(response.body)){
                            if(!alive)break;
                            if(event.server_instance_id===instance&&event.seq<=cursor)continue;
                            if(event.server_instance_id!==instance){const next=await synchronize();cursor=next.seq;instance=next.server_instance_id;continue;}
                            cursor=event.seq;
                            if(event.kind==='snapshot_replace'||event.kind==='state'){
                                const payload=event.payload as ServerState;
                                if(payload?.server_instance_id&&payload.runtime&&payload.projection){const accepted=accept_snapshot(state_ref.current,payload);state_ref.current=accepted;set_state(accepted);}
                                else await synchronize();
                            }else await synchronize();
                            if(event.kind==='sessions_changed')await load_sessions();
                        }
                        if(alive)set_connection('重新连接');
                    }catch(error){
                        if(abort.signal.aborted)break;
                        set_connection('连接中断');
                        try{const next=await synchronize();cursor=next.seq;instance=next.server_instance_id;set_connection('重新连接');}catch{ /* 断连不修改运行终态。 */ }
                    }
                    await delay(1200,abort.signal);
                }
            }catch(error){if(alive){set_auth_error(error instanceof ApiError&&error.status===401?'请使用终端输出的本次启动链接访问工作台。':error_text(error));set_connection('连接中断');}}
        }
        void start();return()=>{alive=false;abort.abort();};
    },[api,consume_receipt,get_draft,load_sessions,synchronize]);

    useEffect(()=>{
        if(!selected||!authenticated)return;
        const controller=new AbortController(),epoch=(history_epochs.current.get(selected)||0)+1;
        history_epochs.current.set(selected,epoch);history_paginated.current.delete(selected);history_old_cursor.current.delete(selected);
        void api.get<HistoryPage>(`/sessions/${encodeURIComponent(selected)}/history`,controller.signal).then(page=>{
            if(controller.signal.aborted||history_epochs.current.get(selected)!==epoch)return;
            set_history_pages(previous=>({...Object.fromEntries(Object.entries(previous).filter(([key])=>key!==selected).slice(-9)),[selected]:bound_history(page)}));
        }).catch(error=>{if(!controller.signal.aborted)set_error(error_text(error));});
        return()=>controller.abort();
    },[selected,authenticated,state.server_instance_id,api]);
    useEffect(()=>{
        const runtime=state.runtime,key=`${state.server_instance_id}:${runtime.session_id}:${runtime.generation}`;
        const previous=previous_runtime.current;previous_runtime.current={key,busy:runtime.busy};
        const session_id=runtime.session_id;
        if(!authenticated||!session_id||previous?.key!==key||!previous.busy||runtime.busy||runtime.phase!=='idle')return;
        if(!history_pages_ref.current[session_id]&&selected_ref.current!==session_id)return;
        const controller=new AbortController(),epoch=(history_epochs.current.get(session_id)||0)+1;
        history_epochs.current.set(session_id,epoch);
        void api.get<HistoryPage>(`/sessions/${encodeURIComponent(session_id)}/history`,controller.signal).then(page=>{
            if(controller.signal.aborted||history_epochs.current.get(session_id)!==epoch)return;
            const element=timeline.current;
            if(selected_ref.current===session_id&&element&&!near_bottom.current){
                const top=element.getBoundingClientRect().top;
                const anchor=[...element.querySelectorAll<HTMLElement>('[data-message-id]')].find(item=>item.getBoundingClientRect().bottom>top);
                history_anchor.current={session_id,message_id:anchor?.dataset.messageId||null,offset:anchor?anchor.getBoundingClientRect().top-top:0,scroll_top:element.scrollTop};
            }
            const preserve_cursor=history_paginated.current.has(session_id);
            if(preserve_cursor)history_old_cursor.current.add(session_id);
            set_history_pages(old=>({...old,[session_id]:refresh_history_page(old[session_id],page,preserve_cursor)}));
        }).catch(error=>{if(!controller.signal.aborted)set_error(`任务已结束，历史快照刷新失败：${error_text(error)}。重新打开会话可重试。`);});
        return()=>controller.abort();
    },[authenticated,state.server_instance_id,state.runtime.session_id,state.runtime.generation,state.runtime.busy,state.runtime.phase,api]);
    useLayoutEffect(()=>{
        const saved=history_anchor.current,element=timeline.current;if(!saved||!element)return;
        history_anchor.current=null;if(selected!==saved.session_id)return;
        const anchor=[...element.querySelectorAll<HTMLElement>('[data-message-id]')].find(item=>item.dataset.messageId===saved.message_id);
        if(anchor)element.scrollTop+=anchor.getBoundingClientRect().top-element.getBoundingClientRect().top-saved.offset;
        else element.scrollTop=saved.scroll_top;
    },[history_pages,selected]);
    function choose_session(session_id:string){
        if(selected_ref.current&&timeline.current)positions.current.set(selected_ref.current,timeline.current.scrollTop);
        selected_ref.current=session_id;set_selected(session_id);set_drawer(null);set_has_new(false);
        const draft=get_draft(session_id);drafts_ref.current={...drafts_ref.current,[session_id]:draft};set_drafts(drafts_ref.current);
        try{sessionStorage.setItem(`mewcode:selected:${state.project.key}`,session_id);}catch{set_storage_warning('标签页存储不可用；会话选择仅保留在当前页面。');}
    }
    const viewing_runtime=selected===state.projection.session_id;
    const history_page=selected?history_pages[selected]:null;
    const messages:Message[]=viewing_runtime?merge_messages(history_page?.items||[],state.projection.messages):history_page?.items||[];
    const message_stamp=messages.map(message=>`${message.id}:${message.text.length}`).join('|');
    useEffect(()=>{
        const element=timeline.current;if(!element)return;
        const top=selected?positions.current.get(selected):undefined;
        element.scrollTop=top??element.scrollHeight;near_bottom.current=top===undefined||element.scrollHeight-element.scrollTop-element.clientHeight<90;set_has_new(false);
    },[selected]);
    useEffect(()=>{
        const element=timeline.current;if(!element)return;
        if(near_bottom.current)element.scrollTop=element.scrollHeight;else set_has_new(true);
    },[message_stamp]);
    useEffect(()=>{
        if(!drawer)return;old_focus.current=document.activeElement as HTMLElement;
        const target=drawer==='sessions'?document.querySelector<HTMLElement>('.sidebar'):panel_ref.current;
        target?.querySelector<HTMLElement>('button')?.focus();
        function keys(event:KeyboardEvent){
            if(event.key==='Escape'){set_drawer(null);return;}
            if(event.key!=='Tab'||!target)return;
            const focusable=[...target.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],select:not(:disabled),summary,textarea')].filter(el=>el.getClientRects().length);
            const first=focusable[0],last=focusable[focusable.length-1];
            if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
        }
        document.addEventListener('keydown',keys);return()=>{document.removeEventListener('keydown',keys);old_focus.current?.focus();};
    },[drawer]);

    async function mutate(path:string,data:Record<string,unknown>,draft?:SubmittedDraft){
        set_working(true);set_error('');
        try{const body=await api.mutate(path,data,state_ref.current,draft);if(draft)consume_receipt({body,draft,accepted:true});await synchronize();await load_sessions();return body;}
        catch(error){set_error(error_text(error));throw error;}finally{set_working(false);set_pending(Boolean(api.pending));set_identity_issue(api.identity_issue);}
    }
    async function load_more_history(){
        const session_id=selected,cursor=history_page?.next_cursor;if(!session_id||!cursor)return;
        const instance=state.server_instance_id;history_paginated.current.add(session_id);
        try{
            const next=await api.get<HistoryPage>(`/sessions/${encodeURIComponent(session_id)}/history?cursor=${encodeURIComponent(cursor)}`);
            if(state_ref.current.server_instance_id!==instance)return;
            // 游标仍绑定先前稳定快照，相关提示明确归属于读取时的历史。
            const page=history_old_cursor.current.has(session_id)?{...next,warnings:next.warnings.map(warning=>`旧分页快照：${warning}`)}:next;
            set_history_pages(old=>({...old,[session_id]:bound_history(page,old[session_id])}));
        }catch(error){set_error(error_text(error));}
    }
    async function create_session(){try{const body=await mutate('/sessions',{});choose_session(body.session_id);}catch{}}
    async function activate(){if(!selected)return;try{await mutate(`/sessions/${encodeURIComponent(selected)}/activate`,{});}catch{}}
    async function send(){
        if(!selected)return;const draft=get_draft(selected);if(input_error(draft.text))return;
        try{await mutate(`/sessions/${encodeURIComponent(selected)}/inputs`,{text:draft.text},{...draft,session_id:selected});}catch{}
    }
    async function control(action:string,value?:string,target=selected){if(!target)return;try{await mutate(`/sessions/${encodeURIComponent(target)}/controls`,{action,...(value!==undefined?{value}:{})});set_confirm_control(null);}catch{}}
    async function recover(retry=false){set_working(true);set_error('');try{consume_receipt(retry?await api.retry_pending():await api.recover());await synchronize();await load_sessions();}catch(error){if(error instanceof ApiError&&error.status===404)set_unknown_operation(true);set_error(error_text(error));}finally{set_working(false);set_pending(Boolean(api.pending));set_identity_issue(api.identity_issue);}}
    async function replace_identity(){
        set_working(true);set_error('');
        try{await api.replace_identity(true);set_unknown_operation(false);set_notice('已登记新的标签页身份。草稿保持，旧操作没有重发；需要执行时请再次明确发送。');await synchronize();}
        catch(error){set_error(error_text(error));}
        finally{set_working(false);set_pending(Boolean(api.pending));set_identity_issue(api.identity_issue);}
    }
    async function decide(approval:Approval,decision:string){
        if(approval.generation!==state_ref.current.runtime.generation||!state_ref.current.approvals.some(item=>item.id===approval.id))throw new Error('审批已失效，请同步当前状态。');
        await mutate(`/approvals/${encodeURIComponent(approval.id)}/decision`,{decision});
    }
    const selected_item=sessions.find(item=>item.id===selected);
    const runtime=state.runtime,own_runtime=selected===runtime.session_id;
    const unavailable=working||pending||Boolean(identity_issue)||!authenticated||['连接中断','连接中'].includes(connection);
    const editable=Boolean(selected&&own_runtime&&runtime.phase==='idle'&&!runtime.busy&&!unavailable);
    const can_activate=Boolean(selected&&!runtime.busy&&!unavailable&&selected_item?.recoverable!==false&&!selected_item?.active);
    const approvals=state.approvals.filter(item=>item.session_id===selected);
    const warnings=[...(history_page?.warnings||[]),...(viewing_runtime?state.projection.warnings:[])];
    const draft=selected?(drafts[selected]||get_draft(selected)):blank_draft;
    const connection_short=connection==='已连接'?'在线':connection==='轮询同步'?'轮询':'重连';
    const show_details=()=>{if(matchMedia('(max-width: 950px)').matches)set_drawer('details');else set_details_open(value=>!value);};

    if(!authenticated)return <main className="auth-screen"><div className="auth-card"><div className="logo"><Cat size={27}/></div><h1>MewCode 本机工作台</h1><p>{auth_error||'正在连接你的项目…'}</p><pre>uv run mewcode --config .env --web</pre><p>打开终端中的启动链接。项目和模型配置始终留在本机服务。</p>{auth_error&&<button onClick={()=>location.reload()}><RefreshCw/>重新连接</button>}</div></main>;
    return <div className={`workbench ${details_open?'':'details-closed'}`}>
        <button className={`scrim ${drawer?'active':''}`} aria-label="关闭抽屉" tabIndex={drawer?0:-1} onClick={()=>set_drawer(null)}/>
        <aside className={`sidebar ${drawer==='sessions'?'drawer-open':''}`} aria-label="会话导航">
            <div className="brand"><span className="logo"><Cat size={22}/></span>MewCode <small>LOCAL</small></div>
            <button className="new-session" onClick={()=>void create_session()} disabled={unavailable}><Plus/>新建会话</button>
            <div className="sidebar-label"><span>工作会话</span><span>{sessions.length}</span></div>
            <nav className="session-list" aria-label="项目会话">{sessions.map(session=>{
                const count=state.approvals.filter(item=>item.session_id===session.id).length;
                return <button className={`session-button ${selected===session.id?'current':''}`} key={session.id} onClick={()=>choose_session(session.id)} aria-current={selected===session.id?'page':undefined} title={session.warnings?.join('\n')}><span className="session-title">{session.title||'新会话'}</span><span className="session-meta"><span>{date_text(session.last_activity)} · {session.message_count} 条</span><span className={count?'pending-label':''}>{count?`${count} 待审批`:session.active&&session.id!==runtime.session_id?'外部占用':session.id===runtime.session_id?phase_label[runtime.phase]:!session.recoverable?'无法继续':session.warnings?.length?'有提示':'历史'}</span></span></button>;
            })}{!sessions.length&&<p className="empty-detail">从新会话开始第一项任务。</p>}{sessions_cursor&&<button className="load-more" onClick={()=>void load_sessions(sessions_cursor).catch(error=>set_error(error_text(error)))}>更多会话</button>}</nav>
            <div className="sidebar-footer"><div className="local-label"><span className="dot"/>本机 · 单项目工作区</div>执行占用仅协调本次 Web 服务</div>
        </aside>
        <header className="topbar"><div className="project-identity"><button className="icon-button mobile-only" aria-label="打开会话导航" onClick={()=>set_drawer('sessions')}><PanelLeft/></button><Folder/><div><div className="project-name">{state.project.root.split('/').filter(Boolean).at(-1)||'本机项目'}</div><div className="project-root" title={state.project.root}>{state.project.root}</div></div></div><div className="topbar-right"><span className="model-label">{state.project.model||'模型未提供'}</span><span className={`connection ${connection==='已连接'?'':'offline'}`} data-short={connection_short}><span className="dot"/>{connection}</span><button className="icon-button" aria-label={details_open?'收起或打开任务详情':'打开任务详情'} onClick={show_details}><PanelRight/></button></div></header>
        <main className="chat-column">
            <div className="chat-heading"><div><div className="eyebrow">YOUR WORKSPACE</div><h1>{selected_item?.title||'一起把想法变成代码'}</h1><div className="session-statusline">{state.project.model||'模型未知'} · {runtime.mode==='plan'?'规划只读':'执行模式'} · {{strict:'严格审批',default:'默认权限',bypass:'跳过常规审批'}[runtime.permission_mode]||runtime.permission_mode}</div></div><div className="heading-actions"><span className={`state-pill ${runtime.busy?'busy':''}`}>{own_runtime?phase_label[runtime.phase]:'只读浏览'}</span></div></div>
            {runtime.busy&&<div className="occupancy"><Activity size={16}/><div><strong>{phase_label[runtime.phase]}</strong> · {sessions.find(item=>item.id===runtime.session_id)?.title||runtime.session_id}<small>{runtime.reason||'项目执行槽已占用；其他会话仍可浏览和起草。'}{state.approvals.length?` · ${state.approvals.length} 项待审批`:''}</small></div>{!own_runtime&&runtime.session_id&&<button onClick={()=>{choose_session(runtime.session_id!);set_details_open(true);if(matchMedia('(max-width:950px)').matches)set_drawer('details');}}>查看任务</button>}<button className="danger" disabled={unavailable||runtime.phase==='closing'} onClick={()=>void control('stop',undefined,runtime.session_id)}><Square size={11}/>{runtime.phase==='cancelling'?'正在停止':'停止'}</button></div>}
            {notice&&<div className="notice" role="status">{notice}<button className="quiet" onClick={()=>set_notice('')} aria-label="关闭提示"><X size={12}/></button></div>}
            {error&&<div className="notice error" role="alert">{error}<button className="quiet" aria-label="关闭错误提示" onClick={()=>set_error('')}><X size={12}/></button></div>}
            {storage_warning&&<div className="notice" role="alert">{storage_warning}</div>}
            {identity_issue&&<div className="notice" role="status">{identity_issue.message}<button disabled={working} onClick={()=>void replace_identity()}>{identity_issue.uncertain?'已核查历史，登记新身份':'登记新的标签页身份'}</button></div>}
            {pending&&!identity_issue&&<div className="notice">原操作尚未确认。不会自动重发或自动发送草稿。<button disabled={working} onClick={()=>void recover()}>查询原回执</button>{unknown_operation&&<button disabled={working} onClick={()=>void recover(true)}>按原身份重试</button>}</div>}
            {runtime.error&&<div className="notice error">{runtime.error}</div>}
            <div className="timeline" ref={timeline} aria-label="聊天记录" onScroll={()=>{const element=timeline.current;if(element){near_bottom.current=element.scrollHeight-element.scrollTop-element.clientHeight<90;if(near_bottom.current)set_has_new(false);}}}>
                {warnings.map((warning,index)=><div className="notice-inline" key={index}>{warning}</div>)}
                {history_page?.next_cursor&&<button className="load-more" onClick={()=>void load_more_history()}>继续读取历史</button>}
                {!messages.length&&<div className="welcome"><div className="welcome-mark"><Sparkles size={25}/></div><h2>专注想法，<br/>让代码自然发生。</h2><p>理解项目、实现功能，或一起解决一个棘手的问题。每一步操作都可以在这里查看与掌控。</p><div className="suggestions"><button className="suggestion" onClick={()=>{if(selected)save_draft(selected,{text:'读取 README.md，概括项目结构和启动步骤。',version:draft.version+1});else void create_session();}}><Search/><strong>从理解项目开始</strong><small>梳理结构，找到入口</small></button><button className="suggestion" onClick={()=>{if(selected)save_draft(selected,{text:'先阅读项目，为下一项功能制定实施计划，不修改文件。',version:draft.version+1});else void create_session();}}><Code2/><strong>构思下一项功能</strong><small>先有计划，再动手实现</small></button></div></div>}
                {messages.map(message=><MessageView key={message.id} message={message} api={api} session_id={selected!}/>)}
                {viewing_runtime&&runtime.busy&&!messages.some(message=>message.complete===false)&&<div className="boundary"><LoaderCircle size={13} className="spinner"/>{phase_label[runtime.phase]} · 任务由本机服务持续管理</div>}
            </div>
            {has_new&&<button className="new-content" onClick={()=>{if(timeline.current)timeline.current.scrollTop=timeline.current.scrollHeight;near_bottom.current=true;set_has_new(false);}}>有新内容 <ArrowDown size={12}/></button>}
            {selected&&!own_runtime&&<div className="continue-note"><div><strong>{selected_item?.active?'会话被外部进程占用':selected_item?.recoverable===false?'此会话暂时无法恢复':'正在只读浏览历史'}</strong>继续会话会准备运行上下文；新的运行代次不保留旧的会话批准。</div><button disabled={!can_activate} onClick={()=>void activate()}>继续会话 <ArrowUpRight size={13}/></button></div>}
            <Composer text={draft.text} on_change={text=>{if(selected)save_draft(selected,{text,version:get_draft(selected).version+1});}} on_send={()=>void send()} disabled={!editable} commands={state.commands}/>
        </main>
        {(details_open||drawer==='details')&&<aside className={`details-panel ${drawer==='details'?'drawer-open':''}`} ref={panel_ref} aria-label="任务详情"><div className="panel-heading"><div><Activity/>任务详情</div><button className="icon-button" aria-label="关闭任务详情" onClick={()=>{set_details_open(false);set_drawer(null);}}><X/></button></div><div className="details-scroll">
            {approvals.map(approval=><ApprovalCard key={`${state.server_instance_id}:${approval.id}`} approval={approval} api={api} disabled={unavailable} on_decide={decision=>decide(approval,decision)}/>)}
            {!approvals.length&&state.approvals.length>0&&<div className="approval-card"><div className="approval-title"><Shield/>其他会话有待审批操作</div><button onClick={()=>choose_session(state.approvals[0].session_id)}>前往审批会话</button></div>}
            <section className="detail-section"><h3>会话环境</h3><div className="detail-row"><span>模型</span><span>{state.project.model||'未知'}</span></div><div className="detail-row"><span>模式</span><select aria-label="运行模式" disabled={!editable} value={runtime.mode} onChange={event=>void control(event.target.value)}><option value="execute">执行模式</option><option value="plan">规划模式 · 只读</option></select></div><div className="detail-row"><span>权限</span><select aria-label="权限模式" disabled={!editable} value={runtime.permission_mode} onChange={event=>void control('permission_mode',event.target.value)}><option value="strict">严格审批</option><option value="default">默认权限</option><option value="bypass">跳过常规审批</option></select></div><div className="detail-row"><span>运行代次</span><span>{own_runtime?runtime.generation:'未加载'}</span></div><div className="detail-buttons"><button disabled={!editable} onClick={()=>void control('compact')}>摘要上下文</button><button disabled={!editable} onClick={()=>set_confirm_control('reset')}>重置上下文</button><button disabled={!editable} onClick={()=>set_confirm_control('revoke')}>撤销会话批准</button></div>{confirm_control&&<div className="notice-inline">{confirm_control==='reset'?'重置后保留可读历史，模型从新上下文开始。':'撤销当前运行代次中的会话批准。'}<div className="detail-buttons"><button className="danger" disabled={!editable} onClick={()=>void control(confirm_control,confirm_control==='revoke'?'session':undefined)}>确认{confirm_control==='reset'?'重置':'撤销'}</button><button onClick={()=>set_confirm_control(null)}>取消</button></div></div>}</section>
            {viewing_runtime&&state.projection.runs.map(run=><RunDetails key={run.id} run={run} session_id={selected!} api={api}/>)}
            {(!viewing_runtime||!state.projection.runs.length)&&<section className="detail-section"><h3>执行轨迹</h3><p className="empty-detail">任务开始后，工具调用、修改详情和实际结果会显示在这里。</p></section>}
            {runtime.activities?.length>0&&<section className="detail-section"><h3>占用原因</h3><pre className="raw">{show_value(runtime.activities)}</pre></section>}
            <section className="detail-section"><h3>可用命令</h3><div className="help-list">{state.commands.map(command=><details key={command.name}><summary><code>/{command.name}</code> · {command.description}</summary><p>{command.usage}{command.aliases?.length?` · 别名 ${command.aliases.join('、')}`:''}</p>{command.kind==='terminal'&&<p>终端专属，在 Web 不适用。</p>}</details>)}</div></section>
        </div></aside>}
    </div>;
}
export function MessageView({message,api,session_id}:{message:Message;api:Api;session_id:string}){
    if(['boundary','reset','compact','summary_boundary','interrupted'].includes(message.kind||''))return <div className="boundary">{message.text||message.kind}</div>;
    const user=message.role==='user'&&(!message.kind||['user','user_input','message'].includes(message.kind));
    const context=message.role==='context'||message.role==='system';
    const tool=message.role==='tool';
    const result_id=message.result_id||(message.result&&typeof message.result==='object'&&'result_id' in message.result&&typeof message.result.result_id==='string'?message.result.result_id:null);
    const label=user?'你':message.role==='assistant'?'MewCode':tool?'工具记录':message.kind?.includes('skill')?'技能展开':'应用上下文';
    const sources:Record<string,string>={runtime:'运行上下文',task_started:'任务启动记录',run_started:'运行启动记录',skill:'技能展开',instructions:'项目指令',notice:'应用提示'};
    const content=<>
        {user?<div className="user-text">{message.text}</div>:tool?<pre className="raw">{message.text}</pre>:<SafeMarkdown text={message.text}/>}
        {message.truncated&&<div className="notice-inline">该条内容超出保留范围，显示不完整。</div>}
        {result_id&&<ResultViewer id={result_id} session_id={session_id} api={api}/>}
        {message.call!==undefined&&<details><summary>已保存的调用</summary><pre className="raw">{show_value(message.call)}</pre></details>}
        {message.result!==undefined&&<details><summary>已保存的结果</summary><pre className="raw">{show_value(message.result)}</pre></details>}
    </>;
    return <article className="message" data-message-id={message.id}><div className={`avatar ${user?'user':''}`}>{user?'你':<Cat/>}</div><div className="message-body">
        <div className="message-byline">{label}<small>{message.complete===false?'生成中':message.role==='assistant'?'ASSISTANT':user?'YOU':message.kind}</small></div>
        {context||tool?<details className="history-record"><summary>{tool?'查看工具记录':`查看${sources[message.kind||'']||message.kind||'应用上下文'}`}</summary><div className="history-record-content">{content}</div></details>:content}
    </div></article>;
}
