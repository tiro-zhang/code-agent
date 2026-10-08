import {useState,type ReactNode} from 'react';
// 首次展开才构建正文，之后保留子组件的已读页和复制反馈。
export function LazyDetails({summary,children,className}:{summary:ReactNode;children:()=>ReactNode;className?:string}){
    const [loaded,set_loaded]=useState(false);
    return <details className={className} onToggle={event=>{if(event.currentTarget.open)set_loaded(true);}}><summary onClick={()=>set_loaded(true)}>{summary}</summary>{loaded&&children()}</details>;
}
