import {render,screen,fireEvent} from '@testing-library/react';
import {expect,it} from 'vitest';
import {ToolCard,RunDetails} from '../src/Details';
import {Api} from '../src/api';
const api=new Api(async()=>Response.json({}),sessionStorage);
it('JSON字符串参数中的edit差异清楚标为尚未执行的局部预览',()=>{
    render(<ToolCard call={{id:'call',run_id:'r',iteration:2,name:'edit',arguments:'{"old_text":"before","new_text":"after"}',status:'proposed'}} session_id="s" api={api}/>);
    fireEvent.click(screen.getByText('edit'));
    expect(screen.getByText('拟执行预览 · 尚未确认执行')).toBeInTheDocument();
    expect(screen.getByText('− before')).toBeInTheDocument();expect(screen.getByText('+ after')).toBeInTheDocument();
    expect(screen.queryByText('实际已知结果')).toBeNull();
});
it('空用量与不完整用量不显示成已知零用量，运行说明和思考可区分',()=>{
    render(<RunDetails run={{id:'r',phase:'completed',reason:'cancelled',text:'清理已结束',thinking:'供应商公开片段',calls:[],usage:{},usage_by_purpose:{maintenance:{input_tokens:10,complete:false}}}} session_id="s" api={api}/>);
    expect(screen.getByText('未知 / 不完整')).toBeInTheDocument();
    expect(screen.getByText('清理已结束')).toBeInTheDocument();
    expect(screen.queryByText('供应商公开片段')).toBeNull();fireEvent.click(screen.getByText('查看供应商思考片段'));expect(screen.getByText('供应商公开片段')).toBeVisible();
});
