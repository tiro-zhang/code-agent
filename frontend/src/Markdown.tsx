import {memo,startTransition,useEffect,useRef,useState,type ComponentPropsWithoutRef,type ReactNode} from 'react';
import Markdown,{type Components,type Options} from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import {Copy,Check} from 'lucide-react';
function CodeBlock({children,...props}:ComponentPropsWithoutRef<'pre'>){
    const ref=useRef<HTMLPreElement>(null);const [copied,set_copied]=useState(false);const [failed,set_failed]=useState(false);
    return <div className="code-block"><div className="code-toolbar"><span>CODE</span><button aria-label="复制代码" onClick={async()=>{try{await navigator.clipboard.writeText(ref.current?.textContent||'');set_copied(true);setTimeout(()=>set_copied(false),1800);}catch{set_failed(true);}}}>{copied?<Check/>:<Copy/>}{copied?'已复制':failed?'复制失败，请手动选择':'复制'}</button></div><pre ref={ref} {...props}>{children}</pre></div>;
}
const remark_plugins=[remarkGfm];
const rehype_plugins:Options['rehypePlugins']=[[rehypeHighlight,{detect:false}]];
const components:Components={
    pre:({node:_,...props})=><CodeBlock {...props}/>,
    img:({alt})=><span className="blocked-image">{alt||'外部图片'} · 图片未加载</span>,
    a:({href,children})=><a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
};
export const SafeMarkdown=memo(function SafeMarkdown({text,streaming=false}:{text:string;streaming?:boolean}){
    const [formatted,set_formatted]=useState<string|null>(streaming?null:text);
    const parsed=useRef<{text:string;content:ReactNode}|null>(null);
    useEffect(()=>{
        if(streaming){set_formatted(null);return;}
        if(formatted===text)return;
        // 先提交完整原文与完成状态，下一绘制机会后再增强；取消旧版本的调度。
        let alive=true,timer:ReturnType<typeof setTimeout>|undefined;
        const frame=requestAnimationFrame(()=>{timer=setTimeout(()=>{if(alive)startTransition(()=>set_formatted(text));},0);});
        return ()=>{alive=false;cancelAnimationFrame(frame);clearTimeout(timer);};
    },[text,streaming,formatted]);
    if(streaming||formatted!==text)return <pre className="stream-text">{text}</pre>;
    // react-markdown 的同步入口是无 Hook 的纯转换。缓存实际转换结果，
    // 避免低优先级挂载被输入打断后再次解析；缓存仅随此正文组件保留一版。
    if(parsed.current?.text!==text)parsed.current={text,content:Markdown({skipHtml:true,remarkPlugins:remark_plugins,rehypePlugins:rehype_plugins,components,children:text})};
    return <div className="markdown">{parsed.current.content}</div>;
});
