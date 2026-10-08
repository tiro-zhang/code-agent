import {it,expect,vi} from 'vitest';
import {render,screen,fireEvent,waitFor} from '@testing-library/react';
import {SafeMarkdown} from '../src/Markdown';
import {MessageView} from '../src/App';
import {ToolCard,ResultViewer} from '../src/Details';
import {Api} from '../src/api';
const parsing=vi.hoisted(()=>({count:0}));
vi.mock('remark-gfm',async original=>{const module=await original<typeof import('remark-gfm')>();return {default:function(this:unknown,...args:unknown[]){parsing.count++;return module.default.apply(this,args as []);}};});
const api=new Api(async()=>Response.json({}),sessionStorage);
it('同正文的新对象不会重解析或重置复制反馈，等长替换与元信息仍更新',async()=>{
    vi.stubGlobal('navigator',{clipboard:{writeText:vi.fn().mockResolvedValue(undefined)}});
    const message={id:'a',role:'assistant',text:'```python\nprint(1)\n```',complete:true};
    const view=render(<MessageView message={message} api={api} session_id="s"/>);
    fireEvent.click(screen.getByRole('button',{name:'复制代码'}));await screen.findByText('已复制');const count=parsing.count;
    view.rerender(<MessageView message={{...message}} api={api} session_id="s"/>);
    expect(parsing.count).toBe(count);expect(screen.getByText('已复制')).toBeInTheDocument();
    view.rerender(<MessageView message={{...message,text:'```python\nprint(2)\n```',truncated:true,result_id:'new-result'}} api={api} session_id="s"/>);
    await waitFor(()=>expect(view.container.querySelector('code')?.textContent).toBe('print(2)\n'));
    expect(screen.getByText(/显示不完整/)).toBeInTheDocument();expect(screen.getByText('读取已保留详情')).toBeInTheDocument();vi.unstubAllGlobals();
});
it('生成正文保留原文而不解析，完成先补齐最终正文再自动安全格式化',async()=>{
    const text='![图片](https://external.test/x)\n<script>bad()</script>\n```python\n    print("<x>")\n```';
    const count=parsing.count;const view=render(<MessageView message={{id:'a',role:'assistant',text,complete:false}} api={api} session_id="s"/>);
    expect(parsing.count).toBe(count);expect(view.container.querySelector('.stream-text')?.textContent).toBe(text);expect(view.container.querySelector('img,script')).toBeNull();
    view.rerender(<MessageView message={{id:'a',role:'assistant',text:text+'\n最后片段',complete:true}} api={api} session_id="s"/>);
    expect(view.container.querySelector('.stream-text')?.textContent).toBe(text+'\n最后片段');expect(screen.queryByText('生成中')).toBeNull();
    await screen.findByRole('button',{name:'复制代码'});expect(view.container.querySelector('code')?.textContent).toBe('    print("<x>")\n');expect(view.container.querySelector('img,script')).toBeNull();
});
it('折叠工具详情不构建参数和 diff，展开后保留已读结果且摘要更新',async()=>{
    const read=vi.fn(async()=>Response.json({id:'result',session_id:'s',content:'已读正文',next_cursor:null,truncated:false,warnings:[]}));const result_api=new Api(read,sessionStorage);
    const call={id:'c',name:'edit',arguments:{old_text:'before',new_text:'after'},status:'running',result_id:'result'};
    const view=render(<ToolCard call={call} session_id="s" api={result_api}/>);
    expect(view.container.querySelector('.diff')).toBeNull();expect(screen.queryByText('已捕获参数')).toBeNull();fireEvent.click(screen.getByText('edit'));
    expect(screen.getByText('− before')).toBeVisible();fireEvent.click(screen.getByText('读取已保留详情'));await screen.findByText('已读正文');
    view.rerender(<ToolCard call={{...call,status:'completed'}} session_id="s" api={result_api}/>);expect(screen.getByText('已读正文')).toBeVisible();expect(read).toHaveBeenCalledTimes(1);expect(view.container.querySelector('details')).toHaveAttribute('open');
});
it('结果身份变化后丢弃旧身份的迟到读取',async()=>{
    let finish:(value:Response)=>void=()=>{};const delayed=new Api(()=>new Promise(resolve=>{finish=resolve;}),sessionStorage);
    const view=render(<ResultViewer id="old" session_id="a" api={delayed}/>);fireEvent.click(screen.getByText('读取已保留详情'));
    view.rerender(<ResultViewer id="new" session_id="b" api={delayed}/>);finish(Response.json({id:'old',session_id:'a',content:'旧内容',next_cursor:null,truncated:false,warnings:[]}));
    await waitFor(()=>expect(screen.getByText('读取已保留详情')).toBeEnabled());expect(screen.queryByText('旧内容')).toBeNull();
});
