import type {Draft} from './types';
import {clear_accepted_draft} from './state';
export class DraftStore {
    warning='';
    private values=new Map<string,Draft>();
    private listeners=new Map<string,Set<()=>void>>();
    constructor(private storage:()=>Storage){}
    private key(project:string,session:string){return `mewcode:draft:${project}:${session}`;}
    read(project:string,session:string):Draft{
        const key=this.key(project,session),cached=this.values.get(key);if(cached)return cached;
        let draft:Draft={text:'',version:0};
        try{const saved=JSON.parse(this.storage().getItem(key)||'null');if(saved&&typeof saved.text==='string'&&Number.isSafeInteger(saved.version)&&saved.version>=0)draft=saved;}
        catch{this.warning='标签页存储不可用或已满；草稿仍在当前页面，请在刷新前复制。';}
        this.values.set(key,draft);return draft;
    }
    private save(project:string,session:string,draft:Draft){
        const key=this.key(project,session);this.values.set(key,draft);
        try{this.storage().setItem(key,JSON.stringify(draft));}catch{this.warning='标签页存储不可用或已满；草稿仍在当前页面，请在刷新前复制。';}
        this.listeners.get(key)?.forEach(listener=>listener());
    }
    edit(project:string,session:string,text:string){this.save(project,session,{text,version:this.read(project,session).version+1});}
    confirm(project:string,session:string,submitted:Draft){
        const current=this.read(project,session),next=clear_accepted_draft(current,submitted);if(next!==current)this.save(project,session,next);
    }
    subscribe(project:string,session:string,listener:()=>void){
        const key=this.key(project,session),listeners=this.listeners.get(key)||new Set();listeners.add(listener);this.listeners.set(key,listeners);
        return ()=>{listeners.delete(listener);if(!listeners.size)this.listeners.delete(key);};
    }
}
