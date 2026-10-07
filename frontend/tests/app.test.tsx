import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { App } from '../src/App';
import { fixture_state, fixture_sessions } from './fixtures';

beforeEach(()=>{sessionStorage.clear();history.replaceState(null,'','/');});
afterEach(()=>vi.unstubAllGlobals());
function network() {
    let state=structuredClone(fixture_state);
    const writes:Array<{url:string;body:any}>=[];
    vi.stubGlobal('fetch',async (input:RequestInfo|URL,init?:RequestInit)=>{
        const url=String(input);
        if(url.includes('/events')) return new Response('',{status:429});
        if(url.endsWith('/clients')) return Response.json({client_id:'c',next_sequence:1,server_instance_id:state.server_instance_id});
        if(init?.method==='POST') {
            const body=JSON.parse(String(init.body));writes.push({url,body});
            if(url.endsWith('/activate')) state={...state,seq:2,runtime:{...state.runtime,session_id:'s1',phase:'idle',generation:1,state_version:1},projection:{...state.projection,session_id:'s1'}};
            return Response.json({operation_id:'o',operation:{client_id:'c',sequence:body.sequence,next_sequence:body.sequence+1}});
        }
        if(url.endsWith('/state')) return Response.json(state);
        if(url.includes('/history')) return Response.json({items:[{id:'m1',role:'user',text:'已保存的请求'}],next_cursor:null,warnings:[]});
        if(url.includes('/sessions')) return Response.json({items:fixture_sessions,next_cursor:null});
        return Response.json({error:{code:'missing',message:'不存在'}},{status:404});
    });
    return writes;
}
it('打开历史只读，明确继续后才允许发送，切换保存各会话草稿',async()=>{
    const writes=network(); render(<App/>);
    await screen.findByRole('button',{name:/理解项目的启动流程/});
    fireEvent.click(screen.getByRole('button',{name:/理解项目的启动流程/}));
    await screen.findByText('已保存的请求');
    expect(writes).toHaveLength(0);
    fireEvent.change(screen.getByRole('textbox',{name:'任务输入'}),{target:{value:'    待发送\n'}});
    expect(screen.getByRole('button',{name:'发送任务'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button',{name:/另一个会话/}));
    expect(screen.getByRole('textbox',{name:'任务输入'})).toHaveValue('');
    fireEvent.click(screen.getByRole('button',{name:/理解项目的启动流程/}));
    expect(screen.getByRole('textbox',{name:'任务输入'})).toHaveValue('    待发送\n');
    fireEvent.click(screen.getByRole('button',{name:'继续会话'}));
    await waitFor(()=>expect(screen.getByRole('button',{name:'发送任务'})).not.toBeDisabled());
    expect(writes.map(x=>x.url)).toEqual(['/api/v1/sessions/s1/activate']);
    fireEvent.click(screen.getByRole('button',{name:'发送任务'}));
    await waitFor(()=>expect(writes).toHaveLength(2));
    expect(writes[1].body.text).toBe('    待发送\n');
});

it('撤销会话批准确认明确提交session范围',async()=>{
    const writes=network();render(<App/>);
    fireEvent.click(await screen.findByRole('button',{name:/理解项目的启动流程/}));
    fireEvent.click(screen.getByRole('button',{name:'继续会话'}));
    await waitFor(()=>expect(screen.getByRole('button',{name:'撤销会话批准'})).not.toBeDisabled());
    fireEvent.click(screen.getByRole('button',{name:'撤销会话批准'}));
    fireEvent.click(screen.getByRole('button',{name:'确认撤销'}));
    await waitFor(()=>expect(writes).toHaveLength(2));
    expect(writes[1].body.action).toBe('revoke');expect(writes[1].body.value).toBe('session');
});
