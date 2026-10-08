import { render, screen, fireEvent } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { SafeMarkdown } from '../src/Markdown';
import { Composer } from '../src/Composer';

it('不渲染原始HTML和外部图片，代码保留安全可复制文本', () => {
    const {container} = render(<SafeMarkdown text={'<script>alert(1)</script>\n\n<img src="https://bad.test/leak">\n\n![外部图片](https://bad.test/x)\n\n```python\n    print("<x>")\n```'}/>);
    expect(container.querySelector('script, img')).toBeNull();
    expect(screen.getByText(/外部图片.*未加载/)).toBeInTheDocument();
    expect(screen.getByRole('button',{name:'复制代码'})).toBeInTheDocument();
    expect(container.querySelector('code')?.textContent).toBe('    print("<x>")\n');
});

it('输入法组合确认不会发送，粘贴多行及末尾换行原样交给提交', () => {
    const send = vi.fn();
    const text = '    const x = 1;\n/reset\n';
    render(<Composer text={text} on_change={()=>{}} on_send={send} disabled={false}/>);
    const input = screen.getByRole('textbox',{name:'任务输入'});
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input,{key:'Enter',ctrlKey:true});
    expect(send).not.toHaveBeenCalled();
    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input,{key:'Enter'});
    expect(send).not.toHaveBeenCalled();
    fireEvent.keyDown(input,{key:'Enter',metaKey:true});
    expect(send).toHaveBeenCalledTimes(1);
    expect(input).toHaveValue(text);
});

it('超限草稿仍可编辑但不能发送', () => {
    render(<Composer text={'中'.repeat(87382)} on_change={()=>{}} on_send={()=>{}} disabled={false}/>);
    expect(screen.getByRole('button',{name:'发送任务'})).toBeDisabled();
    expect(screen.getByRole('textbox',{name:'任务输入'})).not.toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('256');
});

it('应用上下文默认折叠并保留来源，主动展开后可完整阅读',async()=>{
    const {MessageView}=await import('../src/App');const {Api}=await import('../src/api');
    const api=new Api(async()=>Response.json({}),sessionStorage);
    render(<MessageView message={{id:'ctx',role:'context',kind:'runtime',text:'技能目录与原始环境上下文'}} api={api} session_id="s"/>);
    expect(screen.queryByText('技能目录与原始环境上下文')).toBeNull();
    fireEvent.click(screen.getByText('查看运行上下文'));
    expect(screen.getByText('技能目录与原始环境上下文')).toBeVisible();
});

it('工具历史正文默认折叠，仍能主动查看实际结果',async()=>{
    const {MessageView}=await import('../src/App');const {Api}=await import('../src/api');
    const api=new Api(async()=>Response.json({}),sessionStorage);
    render(<MessageView message={{id:'tool',role:'tool',kind:'tool_result',text:'工具原始输出',result:{ok:true,output:'完整结果'}}} api={api} session_id="s"/>);
    expect(screen.queryByText('工具原始输出')).toBeNull();
    fireEvent.click(screen.getByText('查看工具记录'));
    expect(screen.getByText('工具原始输出')).toBeVisible();
    fireEvent.click(screen.getByText('已保存的结果'));expect(screen.getByText(/完整结果/)).toBeVisible();
});
