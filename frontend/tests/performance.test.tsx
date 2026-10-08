import {beforeEach,afterEach,it,expect,vi} from 'vitest';
import {render,screen,fireEvent,waitFor,act} from '@testing-library/react';
import {App} from '../src/App';
import {performance_state,performance_history,performance_sessions,performance_event} from './performance-fixtures';
import type {ServerState,Envelope} from '../src/types';

const parsing=vi.hoisted(()=>({count:0}));
vi.mock('remark-gfm',async original=>{const module=await original<typeof import('remark-gfm')>();return {default:function(this: unknown,...args:unknown[]){parsing.count++;return module.default.apply(this,args as []);}};});
beforeEach(()=>{sessionStorage.clear();sessionStorage.setItem('mewcode:selected:demo','s1');history.replaceState(null,'','/');parsing.count=0;});
afterEach(()=>vi.unstubAllGlobals());

function network(initial=performance_state()){
    let state=initial,reads=0,lists=0,hold_list=false;
    let release_list:()=>void=()=>{};
    let stream:ReadableStreamDefaultController<Uint8Array>;
    const body=new ReadableStream<Uint8Array>({start(controller){stream=controller;}});
    const writes:Array<{url:string;body:any}>=[];
    vi.stubGlobal('fetch',async(input:RequestInfo|URL,init?:RequestInit)=>{
        const url=String(input);
        if(url.includes('/events'))return new Response(body,{headers:{'Content-Type':'text/event-stream'}});
        if(url.endsWith('/state')){reads++;return Response.json(state);}
        if(url.endsWith('/clients'))return Response.json({client_id:'c',next_sequence:1,server_instance_id:state.server_instance_id});
        if(init?.method==='POST'){
            const submitted=JSON.parse(String(init.body));writes.push({url,body:submitted});
            state={...state,seq:state.seq+1,runtime:{...state.runtime,phase:'running',busy:true}};
            return Response.json({operation_id:'o',operation:{client_id:'c',sequence:submitted.sequence,next_sequence:submitted.sequence+1}});
        }
        if(url.includes('/history'))return Response.json({items:performance_history,next_cursor:null,warnings:[]});
        if(url.includes('/sessions')){lists++;if(hold_list)await new Promise<void>(resolve=>{release_list=resolve;});return Response.json({items:performance_sessions,next_cursor:null});}
        return Response.json({error:{code:'missing',message:'不存在'}},{status:404});
    });
    return {get reads(){return reads;},get lists(){return lists;},writes,set_state(next:ServerState){state=next;},emit(...events:Envelope[]){for(const event of events)stream!.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`));},hold_list(){hold_list=true;},release(){hold_list=false;release_list();}};
}
async function ready(){await screen.findByRole('button',{name:/理解项目的启动流程/});await screen.findByText('已连接');await waitFor(()=>expect(document.querySelectorAll('[data-message-id]')).toHaveLength(101),{timeout:30000});}

it('长历史下编辑草稿不会再次解析已有 Markdown',async()=>{
    network();render(<App/>);await ready();const before=parsing.count;
    fireEvent.change(screen.getByRole('textbox',{name:'任务输入'}),{target:{value:'新的草稿'}});
    expect(screen.getByRole('textbox',{name:'任务输入'})).toHaveValue('新的草稿');expect(parsing.count-before).toBe(0);
},60000);
it('完整长正文格式化完成后编辑草稿仍不重解析历史和最终回答',async()=>{
    const initial=performance_state();initial.runtime={...initial.runtime,phase:'idle',busy:false};initial.projection.messages=initial.projection.messages.map(message=>({...message,complete:true}));network(initial);render(<App/>);await ready();
    const before=parsing.count;fireEvent.change(screen.getByRole('textbox',{name:'任务输入'}),{target:{value:'完成后的新草稿'}});expect(parsing.count-before).toBe(0);
},60000);
it('100 个已被首次状态快照覆盖的事件只读取一次额外状态',async()=>{
    const backend=network();render(<App/>);await ready();backend.set_state({...performance_state(),seq:101});
    await act(async()=>{backend.emit(...Array.from({length:100},(_,i)=>performance_event(i+2)));await new Promise(resolve=>setTimeout(resolve,350));});
    expect(backend.reads).toBe(2);
},60000);
it('操作已确认且状态已同步时，慢列表不继续禁用停止',async()=>{
    const initial=performance_state();initial.runtime={...initial.runtime,phase:'idle',busy:false};initial.projection.messages=[];
    const backend=network(initial);render(<App/>);await screen.findByRole('button',{name:/理解项目的启动流程/});await screen.findByText('已连接');
    fireEvent.change(screen.getByRole('textbox',{name:'任务输入'}),{target:{value:'明确发送'}});backend.hold_list();
    fireEvent.click(screen.getByRole('button',{name:'发送任务'}));await screen.findByRole('button',{name:'停止'});
    await waitFor(()=>expect(backend.lists).toBe(2));expect(screen.getByRole('button',{name:'停止'})).toBeEnabled();backend.release();
},60000);
