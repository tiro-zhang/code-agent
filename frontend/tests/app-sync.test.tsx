import {beforeEach,afterEach,it,expect,vi} from 'vitest';
import {render,screen,fireEvent,waitFor,act} from '@testing-library/react';
import {App} from '../src/App';
import {fixture_state,fixture_sessions} from './fixtures';
import type {ServerState,Envelope} from '../src/types';
beforeEach(()=>{sessionStorage.clear();sessionStorage.setItem('mewcode:selected:demo','s1');history.replaceState(null,'','/');});afterEach(()=>vi.unstubAllGlobals());
function backend(){
    let state:ServerState={...structuredClone(fixture_state),runtime:{...fixture_state.runtime,session_id:'s1',generation:1,state_version:1,phase:'running',busy:true,run_id:'r'},projection:{session_id:'s1',messages:[{id:'answer',role:'assistant',text:'旧正文',complete:false}],runs:[],warnings:[]}};
    let stream:ReadableStreamDefaultController<Uint8Array>,fail_list=false,list_calls=0,hold_history=false,fail_state=0,state_calls=0;
    let old_history:(response:Response)=>void=()=>{};const writes:Array<{url:string;body:any}>=[];
    vi.stubGlobal('fetch',async(input:RequestInfo|URL,init?:RequestInit)=>{
        const url=String(input);
        if(url.includes('/events'))return new Response(new ReadableStream<Uint8Array>({start(controller){stream=controller;init?.signal?.addEventListener('abort',()=>{try{controller.close();}catch{}});}}));
        if(url.endsWith('/state')){state_calls++;if(fail_state){fail_state--;return Response.json({error:{code:'offline',message:'暂时断开'}},{status:503});}return Response.json(state);}
        if(url.endsWith('/clients'))return Response.json({client_id:'c',next_sequence:1,server_instance_id:state.server_instance_id});
        if(init?.method==='POST'){const body=JSON.parse(String(init.body));writes.push({url,body});return Response.json({operation_id:'o',operation:{client_id:'c',sequence:body.sequence,next_sequence:body.sequence+1}});}
        if(url.endsWith('/sessions')){list_calls++;return fail_list?Response.json({error:{code:'list_failed',message:'列表暂不可用'}},{status:503}):Response.json({items:fixture_sessions,next_cursor:null});}
        if(url.includes('/history')){if(hold_history&&url.includes('/s1/'))return new Promise<Response>(resolve=>{old_history=resolve;});return Response.json({items:[{id:'history',role:'user',text:url.includes('/s1/')?'A 历史':'B 历史'}],next_cursor:null,warnings:[]});}
        return Response.json({error:{code:'missing',message:'不存在'}},{status:404});
    });
    return {writes,get state(){return state;},get lists(){return list_calls;},get reads(){return state_calls;},set(next:ServerState){state=next;},emit(kind='state',payload:unknown={}){const event:Envelope={server_instance_id:state.server_instance_id,seq:state.seq,session_id:'s1',runtime_generation:state.runtime.generation,run_id:'r',kind,payload};stream!.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`));},fail_list(){fail_list=true;},fail_state(count=1){fail_state=count;},hold_history(){hold_history=true;},finish_history(){old_history(Response.json({items:[{id:'old',role:'user',text:'晚到 A 旧历史'}],next_cursor:null,warnings:[]}));}};
}
async function ready(){await screen.findByText('已连接');await screen.findByRole('button',{name:/理解项目的启动流程/});}
it('正文合并期间停止使用最新权威版本，列表失败单独提示且不重发已确认操作',async()=>{
    const server=backend();render(<App/>);await ready();server.set({...server.state,seq:2,runtime:{...server.state.runtime,state_version:9},projection:{...server.state.projection,messages:[{id:'answer',role:'assistant',text:'新正文',complete:false}]}});
    await act(async()=>{server.emit();await new Promise(resolve=>setTimeout(resolve,0));});
    server.fail_list();fireEvent.click(screen.getByRole('button',{name:'停止'}));await waitFor(()=>expect(server.writes).toHaveLength(1));expect(server.writes[0].body.state_version).toBe(9);
    await screen.findByText(/会话列表刷新失败/);expect(screen.getByRole('button',{name:'停止'})).toBeEnabled();expect(server.writes).toHaveLength(1);expect(screen.queryByText('原操作尚未确认')).toBeNull();
});
it('浏览 B 仍同步 A 的占用和审批，晚到 A 历史不串入 B，两个草稿保持',async()=>{
    const server=backend();server.hold_history();render(<App/>);await ready();fireEvent.change(screen.getByRole('textbox',{name:'任务输入'}),{target:{value:'A 草稿'}});
    fireEvent.click(screen.getByRole('button',{name:/另一个会话/}));await screen.findByText('B 历史');fireEvent.change(screen.getByRole('textbox',{name:'任务输入'}),{target:{value:'B 草稿'}});
    server.set({...server.state,seq:2,runtime:{...server.state.runtime,phase:'awaiting_approval'},approvals:[{id:'approval',session_id:'s1',generation:1,run_id:'r',tool:'edit',reason:'写文件',expires_at:Date.now()+60000}]});
    await act(async()=>{server.emit('approval');server.finish_history();await new Promise(resolve=>setTimeout(resolve,0));});
    await screen.findByText('其他会话有待审批操作');expect(screen.getByRole('textbox',{name:'任务输入'})).toHaveValue('B 草稿');expect(screen.queryByText('晚到 A 旧历史')).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:/理解项目的启动流程/}));expect(screen.getByRole('textbox',{name:'任务输入'})).toHaveValue('A 草稿');
});
it('连续替换基线后最终正文仍提交，旧格式化和旧投影定时器不能吞掉最后版本',async()=>{
    const server=backend();render(<App/>);await ready();
    await act(async()=>{
        const first={...server.state,seq:2,projection:{...server.state.projection,messages:[{id:'answer',role:'assistant',text:'中间正文',complete:false}]}};
        server.set(first);server.emit('snapshot_replace',first);
        const final={...first,seq:3,projection:{...first.projection,messages:[{id:'answer',role:'assistant',text:'最终正文',complete:true}]}};
        server.set(final);server.emit('snapshot_replace',final);await new Promise(resolve=>setTimeout(resolve,150));
    });
    await screen.findByText('最终正文');expect(screen.queryByText('中间正文')).toBeNull();
});
it('事件连接仍在时状态读取失败后恢复，停止入口随真实连接恢复可用',async()=>{
    const server=backend();render(<App/>);await ready();server.set({...server.state,seq:2});server.fail_state();server.emit();
    await screen.findByText('连接中断');expect(screen.getByRole('button',{name:'停止'})).toBeDisabled();
    await waitFor(()=>expect(screen.getByText('已连接')).toBeInTheDocument(),{timeout:2500});expect(screen.getByRole('button',{name:'停止'})).toBeEnabled();
});
it('三次状态失败后同一 SSE 后续通知恢复控制，不重发任务',async()=>{
    const server=backend();render(<App/>);await ready();server.fail_state(3);server.set({...server.state,seq:2});server.emit();
    await waitFor(()=>expect(server.reads).toBe(4),{timeout:3500});expect(screen.getByRole('button',{name:'停止'})).toBeDisabled();
    server.set({...server.state,seq:3,runtime:{...server.state.runtime,state_version:11}});server.emit('approval');
    await waitFor(()=>expect(screen.getByText('已连接')).toBeInTheDocument(),{timeout:2000});
    fireEvent.click(screen.getByRole('button',{name:'停止'}));await waitFor(()=>expect(server.writes).toHaveLength(1));expect(server.writes[0].body.state_version).toBe(11);
},7000);
