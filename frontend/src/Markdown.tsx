import {useRef,useState,type ComponentPropsWithoutRef} from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import {Copy,Check} from 'lucide-react';
function CodeBlock({children,...props}:ComponentPropsWithoutRef<'pre'>){
    const ref=useRef<HTMLPreElement>(null);const [copied,set_copied]=useState(false);const [failed,set_failed]=useState(false);
    return <div className="code-block"><div className="code-toolbar"><span>CODE</span><button aria-label="复制代码" onClick={async()=>{try{await navigator.clipboard.writeText(ref.current?.textContent||'');set_copied(true);setTimeout(()=>set_copied(false),1800);}catch{set_failed(true);}}}>{copied?<Check/>:<Copy/>}{copied?'已复制':failed?'复制失败，请手动选择':'复制'}</button></div><pre ref={ref} {...props}>{children}</pre></div>;
}
export function SafeMarkdown({text}:{text:string}){
    return <div className="markdown"><Markdown skipHtml remarkPlugins={[remarkGfm]} rehypePlugins={[[rehypeHighlight,{detect:false}]]} components={{
        pre:({node:_,...props})=><CodeBlock {...props}/>,
        img:({alt})=><span className="blocked-image">{alt||'外部图片'} · 图片未加载</span>,
        a:({href,children})=><a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
    }}>{text}</Markdown></div>;
}
