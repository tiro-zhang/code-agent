import type { Draft, Message, ServerState } from './types';

export const empty_state: ServerState = {
    server_instance_id: '', seq: -1, project: {root:'',key:'',model:''},
    runtime:{session_id:null,generation:0,state_version:0,phase:'unloaded',busy:false,run_id:null,mode:'execute',permission_mode:'default',activities:[]},
    projection:{session_id:null,messages:[],runs:[],warnings:[]}, approvals:[], commands:[],
};
export function accept_snapshot(state:ServerState,next:ServerState):ServerState {
    if (state.server_instance_id===next.server_instance_id && state.seq>=next.seq) return state;
    return next;
}
export function should_send(event:{key:string;ctrlKey:boolean;metaKey:boolean;isComposing:boolean;keyCode?:number}) {
    return event.key==='Enter' && (event.ctrlKey||event.metaKey) && !event.isComposing && event.keyCode!==229;
}
export function input_error(text:string) {
    if (!text.trim()) return '请输入任务内容';
    if (new TextEncoder().encode(text).byteLength>256*1024) return '内容超过 256 KiB，请缩短后发送；草稿仍然保留。';
    return '';
}
export function clear_accepted_draft(draft:Draft,sent:Draft):Draft {
    return draft.version===sent.version && draft.text===sent.text ? {text:'',version:draft.version+1} : draft;
}
export function merge_messages(a:Message[],b:Message[]):Message[] {
    const messages=new Map(a.map(message=>[message.id,message]));
    for (const message of b) messages.set(message.id,message);
    return [...messages.values()];
}
export const phase_label:Record<string,string>={unloaded:'只读浏览',preparing:'准备会话',idle:'可以开始',running:'正在执行',awaiting_approval:'等待审批',background:'后台任务',maintenance:'维护中',cancelling:'正在停止',blocked:'阻塞 · 待核查',closing:'服务收尾'};
export const status_label:Record<string,string>={proposed:'拟执行',pending:'等待执行',requested:'已请求',awaiting_approval:'待审批',running:'执行中',started:'已启动',success:'成功',succeeded:'成功',completed:'已完成',failed:'失败',error:'失败',denied:'已拒绝',timeout:'超时',timed_out:'超时',cancelled:'已取消',canceled:'已取消',unknown:'副作用未知',side_effect_unknown:'副作用未知'};
export function expiry_ms(value:string|number):number {return typeof value==='number' ? (value<1e12?value*1000:value) : Date.parse(value);}
export function bound_history(page:import('./types').HistoryPage,previous?:import('./types').HistoryPage,limit=4*1024*1024):import('./types').HistoryPage {
    const combined=merge_messages(previous?.items||[],page.items),kept:Message[]=[];
    let used=0,trimmed=false;
    for(let i=combined.length-1;i>=0;i--){
        const item=combined[i],size=new TextEncoder().encode(JSON.stringify(item)).byteLength;
        if(kept.length>=200||used+size>limit){trimmed=true;break;}
        kept.unshift(item);used+=size;
    }
    if(!kept.length&&combined.length){const item=combined[combined.length-1];kept.push({id:item.id,role:item.role,kind:item.kind,text:'此条正文超过页面缓存上限，请查看已登记详情。',truncated:true,result_id:item.result_id});}
    const warnings=[...new Set([...(previous?.warnings||[]),...page.warnings,...(trimmed?['较早浏览内容已从页面缓存释放；重新打开会话可从头读取。']:[])])].slice(-32);
    return {...page,items:kept,warnings};
}
export function refresh_history_page(previous:import('./types').HistoryPage|undefined,page:import('./types').HistoryPage,preserve_cursor=false):import('./types').HistoryPage {
    const cache_warnings=(previous?.warnings||[]).filter(warning=>warning.startsWith('较早浏览内容已从页面缓存释放'));
    return bound_history({items:merge_messages(previous?.items||[],page.items),next_cursor:preserve_cursor&&previous?previous.next_cursor:page.next_cursor,warnings:[...page.warnings,...cache_warnings]});
}
