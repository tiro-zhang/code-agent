import {useCallback,useRef,useSyncExternalStore} from 'react';
import {ArrowUp,Command} from 'lucide-react';
import {input_error,should_send} from './state';
import type {Command as CommandType} from './types';
import type {DraftStore} from './drafts';
export function DraftComposer({store,project,session,on_send,disabled,commands}:{store:DraftStore;project:string;session:string;on_send:()=>void;disabled:boolean;commands?:CommandType[]}){
    const subscribe=useCallback((listener:()=>void)=>store.subscribe(project,session,listener),[store,project,session]);
    const read=useCallback(()=>store.read(project,session),[store,project,session]);
    const draft=useSyncExternalStore(subscribe,read);
    return <><Composer text={draft.text} on_change={text=>store.edit(project,session,text)} on_send={on_send} disabled={disabled} commands={commands}/>{store.warning&&<div className="notice-inline" role="alert">{store.warning}</div>}</>;
}
interface Props {text:string;on_change:(text:string)=>void;on_send:()=>void;disabled:boolean;commands?:CommandType[];}
export function Composer({text,on_change,on_send,disabled,commands=[]}:Props){
    const composing=useRef(false);const error=input_error(text);
    const matches=text.startsWith('/')&&!text.includes('\n')&&!text.includes(' ')?commands.filter(c=>`/${c.name}`.startsWith(text)).slice(0,5):[];
    return <div className="composer-wrap">
        {matches.length>0&&<div className="help-list" aria-label="命令补全">{matches.map(c=><button key={c.name} className="quiet" onClick={()=>on_change(c.usage.startsWith('/')?`/${c.name} `:`/${c.name} `)} title={c.description}><code>/{c.name}</code> {c.description}</button>)}</div>}
        <div className="composer">
            <textarea aria-label="任务输入" placeholder="描述你的任务，或输入 / 查看命令…" value={text} onChange={event=>on_change(event.target.value)} onCompositionStart={()=>{composing.current=true;}} onCompositionEnd={()=>{composing.current=false;}} onKeyDown={event=>{
                if(should_send({key:event.key,ctrlKey:event.ctrlKey,metaKey:event.metaKey,isComposing:composing.current||event.nativeEvent.isComposing,keyCode:event.nativeEvent.keyCode})){
                    event.preventDefault();if(!disabled&&!error)on_send();
                }
            }}/>
            <div className="composer-footer"><span className="composer-hint"><Command size={11}/> / Ctrl + Enter 发送 · Enter 换行</span><button className="primary send" aria-label="发送任务" disabled={disabled||Boolean(error)} onClick={on_send}>发送 <ArrowUp size={15}/></button></div>
        </div>
        {text.trim()&&error&&<div className="notice-inline" role="alert">{error}</div>}
        <div className="composer-caption">草稿保留在此标签页 · 只有明确发送才会开始任务</div>
    </div>;
}
