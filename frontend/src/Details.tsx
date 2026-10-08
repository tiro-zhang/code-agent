import {useEffect,useRef,useState} from 'react';
import {Terminal,ChevronRight} from 'lucide-react';
import type {ToolCall,ResultPage,Run} from './types';
import type {Api} from './api';
import {status_label} from './state';
import {LazyDetails} from './LazyDetails';
export function show_value(value:unknown):string{return typeof value==='string'?value:JSON.stringify(value,null,2)??'未知';}
export function ResultViewer({id,session_id,api}:{id:string;session_id:string;api:Api}){
    const [page,set_page]=useState<ResultPage|null>(null),[error,set_error]=useState(''),[loading,set_loading]=useState(false);
    const requests=useRef(0),controller=useRef<AbortController|null>(null);
    useEffect(()=>{requests.current++;controller.current?.abort();set_page(null);set_error('');set_loading(false);return()=>{requests.current++;controller.current?.abort();};},[id,session_id,api]);
    async function load(cursor?:string){
        const request=++requests.current;controller.current?.abort();const abort=new AbortController();controller.current=abort;set_loading(true);
        try{const next=await api.get<ResultPage>(`/results/${encodeURIComponent(id)}${cursor?`?cursor=${encodeURIComponent(cursor)}`:''}`,abort.signal);if(request!==requests.current||abort.signal.aborted)return;if(next.session_id!==session_id||next.id!==id)throw new Error('结果不属于此会话或登记身份');set_page(next);set_error('');}
        catch(error){if(request===requests.current&&!abort.signal.aborted)set_error(error instanceof Error?error.message:'无法读取保留结果');}finally{if(request===requests.current)set_loading(false);}
    }
    return <div><button disabled={loading} onClick={()=>void load()}>读取已保留详情</button>{page&&<><pre className="raw">{page.content}</pre>{page.truncated&&<p className="notice-inline">来源已截断，缺失内容无法补回。</p>}{page.warnings?.map((warning,i)=><p className="notice-inline" key={i}>{warning}</p>)}{page.next_cursor&&<button disabled={loading} onClick={()=>void load(page.next_cursor!)}>下一块结果</button>}</>}{error&&<p className="notice-inline" role="alert">{error}</p>}</div>;
}
export function ToolCard({call,session_id,api}:{call:ToolCall;session_id:string;api:Api}){
    return <LazyDetails className="tool-card" summary={<><Terminal/><strong>{call.name}</strong><span className="tool-status">{status_label[call.status]||call.status}</span><ChevronRight size={12}/></>}>{()=> <ToolBody call={call} session_id={session_id} api={api}/>}</LazyDetails>;
}
function ToolBody({call,session_id,api}:{call:ToolCall;session_id:string;api:Api}){
    let parsed=call.arguments;
    if(typeof parsed==='string'){try{parsed=JSON.parse(parsed);}catch{/* 仅使用可验证的 JSON 参数显示差异。 */}}
    const args=parsed&&typeof parsed==='object'?parsed as Record<string,unknown>:{};
    const result=call.result||{};
    const data=result.data&&typeof result.data==='object'?result.data as Record<string,unknown>:{};
    const preview=['proposed','pending','requested','awaiting_approval'].includes(call.status);
    return <div className="tool-body">
        <div className="run-meta">调用 {call.id}<br/>任务 {call.run_id||'未提供'} · 轮次 {call.iteration??'未知'}{call.parent_run_id&&<><br/>父任务 {call.parent_run_id}</>}</div>
        {preview&&<p className="notice-inline">拟执行预览 · 尚未确认执行</p>}
        {['unknown','side_effect_unknown'].includes(call.status)&&<p className="notice-inline">副作用未知，请核查已有产物。停止不等于回滚。</p>}
        <h4>已捕获参数</h4><pre className="raw">{show_value(call.arguments)}</pre>
        {typeof args.old_text==='string'&&typeof args.new_text==='string'&&<><h4>{preview?'拟执行':'该调用的'}局部差异 · 非整个项目差异</h4><div className="diff">{args.old_text.split('\n').map((line,i)=><span className="removed" key={`o${i}`}>− {line}</span>)}{args.new_text.split('\n').map((line,i)=><span className="added" key={`n${i}`}>+ {line}</span>)}</div></>}
        {typeof data.diff==='string'&&<><h4>该次操作的局部差异</h4><pre className="raw">{data.diff}</pre></>}
        {Object.keys(result).length>0&&<><h4>实际已知结果</h4>{['stdout','stderr'].map(key=>result[key]!==undefined||data[key]!==undefined?<div key={key}><h4>{key}</h4><pre className="raw">{show_value(result[key]??data[key])}</pre></div>:null)}<pre className="raw">{show_value(result)}</pre></>}
        {Boolean(call.truncated||result.truncated)&&<p className="notice-inline">展示副本已截断；这里只展示实际保留范围。</p>}
        {call.result_id&&<ResultViewer key={`${session_id}:${call.result_id}`} id={call.result_id} session_id={session_id} api={api}/>}
    </div>;
}
export function RunDetails({run,session_id,api}:{run:Run;session_id:string;api:Api}){
    return <section className="detail-section"><div className="run-title">{run.title||'任务执行'}</div><div className="run-meta">{run.id}{run.parent_run_id&&<> · 父任务 {run.parent_run_id}</>}</div>
        <div className="detail-row"><span>状态</span><span>{status_label[run.phase]||run.phase}</span></div>
        {run.reason&&<div className="notice-inline">停止原因：{run.reason}</div>}
        {run.text&&<pre className="raw">{run.text}</pre>}
        {run.thinking&&<LazyDetails className="thinking" summary="查看供应商思考片段">{()=> <><pre>{run.thinking}</pre><small>仅本次服务保留的可展示片段</small></>}</LazyDetails>}
        {(run.calls||[]).map(call=><ToolCard key={call.id} call={call} session_id={session_id} api={api}/>)}
        <div className="detail-row"><span>已知用量</span><span>{run.usage&&Object.keys(run.usage).length?`${show_value(run.usage)}${run.usage.complete===false?' · 不完整':''}`:'未知 / 不完整'}</span></div>
        {run.usage_by_purpose&&<><h3>按用途区分的用量</h3><table className="usage-table"><tbody>{Object.entries(run.usage_by_purpose).map(([key,value])=><tr key={key}><td>{key}</td><td>{show_value(value)}</td></tr>)}</tbody></table></>}
    </section>;
}
