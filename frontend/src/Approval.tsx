import {useEffect,useRef,useState} from 'react';
import {ShieldCheck,LoaderCircle} from 'lucide-react';
import type {Approval,ApprovalPage} from './types';
import {expiry_ms} from './state';
import {Api,ApiError} from './api';
const labels:Record<string,string>={targets:'目标',arguments:'参数',content:'内容',scope:'范围'};
export function ApprovalCard({approval,api,disabled,on_decide}:{approval:Approval;api:Api;disabled:boolean;on_decide:(d:string)=>Promise<void>}){
    const [page,set_page]=useState<ApprovalPage|null>(null);
    const [reviewed,set_reviewed]=useState<Set<string>>(new Set());
    const [offset,set_offset]=useState(0);const [loading,set_loading]=useState(false);
    const [error,set_error]=useState('');const [closed,set_closed]=useState(false);
    const [now,set_now]=useState(Date.now());const request=useRef(0);
    const expired=expiry_ms(approval.expires_at)<=now;
    async function load(section='targets',start=0){
        const serial=++request.current;set_loading(true);set_error('');
        try{
            const next=await api.get<ApprovalPage>(`/approvals/${encodeURIComponent(approval.id)}?section=${encodeURIComponent(section)}&offset=${start}&limit=32768`);
            if(serial!==request.current)return;
            if(next.id!==approval.id||next.session_id!==approval.session_id||next.generation!==approval.generation||next.run_id!==approval.run_id)throw new Error('审批身份已变化，请重新同步。');
            set_page(next);set_offset(start);
            if(next.next_offset===null)set_reviewed(previous=>new Set([...previous,next.section]));
        }catch(error){if(serial===request.current){set_error(error instanceof Error?error.message:'无法读取审批');if(error instanceof ApiError&&[404,409,410].includes(error.status))set_closed(true);}}
        finally{if(serial===request.current)set_loading(false);}
    }
    useEffect(()=>{void load();const timer=setInterval(()=>set_now(Date.now()),1000);return()=>{clearInterval(timer);request.current++;};},[approval.id]);
    const full=Boolean(page?.sections.length&&page.sections.every(section=>reviewed.has(section)));
    const inactive=disabled||expired||closed||loading;
    async function decide(decision:string){
        if(inactive||(decision!=='deny'&&!full))return;
        set_loading(true);set_error('');
        try{await on_decide(decision);set_closed(true);}catch(error){set_error(error instanceof Error?error.message:'审批失败');if(error instanceof ApiError&&[404,409,410].includes(error.status))set_closed(true);}finally{set_loading(false);}
    }
    return <section className="approval-card" aria-label={`审批 ${approval.tool}`}>
        <div className="approval-title"><ShieldCheck/>{approval.tool} · 拟执行操作</div>
        <p>{approval.reason}</p>
        <div className="run-meta">请求 {approval.id} · 任务 {approval.run_id}<br/>会话 {approval.session_id} · 代次 {approval.generation}</div>
        <div className="expiry">{expired?'审批已过期，不能再回答':closed?'审批已处理或失效':`剩余 ${Math.max(0,Math.ceil((expiry_ms(approval.expires_at)-now)/1000))} 秒`}</div>
        {page?.root&&<p>项目：{page.root}</p>}
        <div className="approval-sections">{(page?.sections||[]).map(section=><button className={page?.section===section?'selected':''} key={section} disabled={loading} aria-label={`${labels[section]||section}${reviewed.has(section)?' · 已读':''}`} onClick={()=>void load(section)}>{labels[section]||section}{reviewed.has(section)?' ✓':''}</button>)}</div>
        {loading&&<span className="muted"><LoaderCircle className="spinner" size={12}/> 读取中</span>}
        {page&&<><pre className="raw">{page.content||'（此分区无内容）'}</pre><div className="expiry">已显示分块起点 {offset} · 分区共 {page.total_bytes} 字节</div>{page.next_offset!==null&&<button disabled={loading} onClick={()=>void load(page.section,page.next_offset!)}>下一块</button>}</>}
        {!full&&!expired&&!closed&&<p>请审阅全部分区及后续分块，再选择授权范围。</p>}
        {error&&<div className="notice-inline" role="alert">{error}</div>}
        <div className="approval-actions"><button className="danger" disabled={inactive} onClick={()=>void decide('deny')}>拒绝</button><button className="primary" disabled={inactive||!full} onClick={()=>void decide('once')}>仅本次允许</button><button disabled={inactive||!full} onClick={()=>void decide('session')}>本会话允许</button><button disabled={inactive||!full} onClick={()=>void decide('permanent')}>永久允许</button></div>
        <p>批准仍需服务端规则复核；取消不会回滚已发生的修改。</p>
    </section>;
}
