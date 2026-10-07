import { render,screen,fireEvent,waitFor } from '@testing-library/react';
import {expect,it} from 'vitest';
import {ApprovalCard} from '../src/Approval';
import {Api} from '../src/api';
import type {Approval} from '../src/types';

const approval:Approval={id:'a1',session_id:'s1',generation:1,run_id:'r1',tool:'edit',reason:'写入项目文件',expires_at:Date.now()+60000};
it('必须读完全部分区分页才可批准，拒绝随时可用且决定不进入聊天',async()=>{
    const decisions:string[]=[];
    const seen:string[]=[];
    const api=new Api(async url=>{
        const q=new URL(url,'http://localhost').searchParams;const section=q.get('section')||'targets';const offset=Number(q.get('offset')||0);seen.push(`${section}:${offset}`);
        return Response.json({...approval,root:'/root',sections:['targets','arguments','content','scope'],section,content:`${section}-${offset}`,next_offset:section==='content'&&offset===0?4:null,total_bytes:8});
    },sessionStorage);
    render(<ApprovalCard approval={approval} api={api} disabled={false} on_decide={async decision=>{decisions.push(decision);}}/>);
    await screen.findByText('targets-0');
    expect(screen.getByRole('button',{name:'仅本次允许'})).toBeDisabled();
    expect(screen.getByRole('button',{name:'拒绝'})).not.toBeDisabled();
    fireEvent.click(screen.getByRole('button',{name:/参数/}));await screen.findByText('arguments-0');
    fireEvent.click(screen.getByRole('button',{name:/内容/}));await screen.findByText('content-0');
    expect(screen.getByRole('button',{name:'仅本次允许'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button',{name:'下一块'}));await screen.findByText('content-4');
    fireEvent.click(screen.getByRole('button',{name:/范围/}));await screen.findByText('scope-0');
    await waitFor(()=>expect(screen.getByRole('button',{name:'仅本次允许'})).not.toBeDisabled());
    fireEvent.click(screen.getByRole('button',{name:'仅本次允许'}));await waitFor(()=>expect(decisions).toEqual(['once']));
    expect(seen).toEqual(['targets:0','arguments:0','content:0','content:4','scope:0']);
});
it('过期请求不能再回答',async()=>{
    const api=new Api(async()=>Response.json({...approval,sections:['targets'],section:'targets',content:'目标',next_offset:null,total_bytes:3}),sessionStorage);
    render(<ApprovalCard approval={{...approval,expires_at:Date.now()-1}} api={api} disabled={false} on_decide={async()=>{throw new Error('不得执行');}}/>);
    expect(screen.getByRole('button',{name:'拒绝'})).toBeDisabled();
    expect(screen.getByText(/审批已过期/)).toBeInTheDocument();
});
