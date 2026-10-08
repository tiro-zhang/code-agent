import type {ServerState,Envelope} from './types';
export class SyncScheduler {
    received_seq:number;snapshot_seq:number;
    private instance:string;
    private target_seq:number;
    private epoch=0;private disposed=false;private urgent=false;
    private timer:ReturnType<typeof setTimeout>|undefined;
    private controller:AbortController|null=null;
    private in_flight:Promise<void>|null=null;
    private last_start=Date.now();private failures=0;private retry_at=0;
    private forced=0;private completed_force=0;
    private waiters:Array<{force:number;resolve:(state:ServerState)=>void;reject:(error:unknown)=>void}>=[];
    constructor(initial:ServerState,private read:(signal:AbortSignal)=>Promise<ServerState>,private apply:(next:ServerState,current:()=>boolean)=>Promise<void>,private lists:()=>void,private error:(error:unknown)=>void){
        this.instance=initial.server_instance_id;this.received_seq=this.snapshot_seq=this.target_seq=initial.seq;
    }
    notify(event:Envelope){
        if(this.disposed)return;
        if(event.server_instance_id!==this.instance){void this.synchronize().catch(()=>{});return;}
        if(event.seq<=this.received_seq)return;
        this.received_seq=event.seq;
        // 列表是独立领域，不能因 /state 已覆盖该序号而跳过。
        if(event.kind==='sessions_changed')this.lists();
        this.target_seq=Math.max(this.target_seq,event.seq);
        // 一轮有限重试耗尽后，新的真实通知可重新核对；仍遵守失败退避。
        if(this.failures>=3)this.failures=2;
        const kind=(event.payload as {kind?:string}|null)?.kind;
        const normal=event.kind==='agent'&&['text_delta','thinking_delta'].includes(kind||'');
        this.urgent ||= !normal;
        this.schedule();
    }
    synchronize():Promise<ServerState>{
        if(this.disposed)return Promise.reject(new Error('同步已关闭'));
        const force=++this.forced;this.urgent=true;this.failures=0;this.retry_at=0;
        const promise=new Promise<ServerState>((resolve,reject)=>this.waiters.push({force,resolve,reject}));this.schedule();return promise;
    }
    private needed(){return this.target_seq>this.snapshot_seq||this.forced>this.completed_force;}
    private schedule(){
        if(this.disposed||this.in_flight||!this.needed()||this.failures>=3)return;
        clearTimeout(this.timer);this.timer=undefined;
        const wait=Math.max(this.retry_at-Date.now(),this.urgent?0:this.last_start+100-Date.now());
        if(wait>0)this.timer=setTimeout(()=>{this.timer=undefined;this.pump();},wait);else this.pump();
    }
    private pump(){
        if(this.disposed||this.in_flight||!this.needed())return;
        clearTimeout(this.timer);this.timer=undefined;
        const epoch=this.epoch,force=this.forced,controller=new AbortController();this.controller=controller;
        const current=()=>!this.disposed&&epoch===this.epoch&&!controller.signal.aborted;
        this.last_start=Date.now();this.urgent=false;
        const work=Promise.resolve().then(async()=>{
            try{
                const next=await this.read(controller.signal);if(!current())return;
                const changed=next.server_instance_id!==this.instance;
                if(changed||next.seq>this.snapshot_seq){await this.apply(next,current);if(!current())return;}
                if(changed){this.instance=next.server_instance_id;this.received_seq=this.target_seq=next.seq;this.lists();}
                this.snapshot_seq=changed?next.seq:Math.max(this.snapshot_seq,next.seq);this.failures=0;this.retry_at=0;this.completed_force=force;
                const done=this.waiters.filter(waiter=>waiter.force<=force);this.waiters=this.waiters.filter(waiter=>waiter.force>force);done.forEach(waiter=>waiter.resolve(next));
            }catch(error){
                if(!current())return;this.failures++;this.retry_at=Date.now()+1000;this.error(error);
                const failed=this.waiters.filter(waiter=>waiter.force<=force);this.waiters=this.waiters.filter(waiter=>waiter.force>force);failed.forEach(waiter=>waiter.reject(error));
                // 显式核对失败不能返回旧快照冒充成功，也不能无间隔重试。
                this.completed_force=force;
            }
        }).finally(()=>{if(this.in_flight===work){this.in_flight=null;this.schedule();}});
        this.in_flight=work;
    }
    async replace(next:ServerState){
        if(this.disposed)return;
        const changed=next.server_instance_id!==this.instance,covered=this.snapshot_seq;
        const epoch=++this.epoch;this.controller?.abort();clearTimeout(this.timer);this.timer=undefined;this.in_flight=null;
        const current=()=>!this.disposed&&epoch===this.epoch;
        if(changed||next.seq>covered)await this.apply(next,current);if(!current())return;
        this.instance=next.server_instance_id;this.received_seq=changed?next.seq:Math.max(this.received_seq,next.seq);this.snapshot_seq=changed?next.seq:Math.max(covered,next.seq);this.target_seq=changed?next.seq:Math.max(this.target_seq,next.seq);this.failures=0;this.retry_at=0;this.completed_force=this.forced;
        this.waiters.splice(0).forEach(waiter=>waiter.resolve(next));this.lists();
    }
    dispose(){this.disposed=true;this.epoch++;clearTimeout(this.timer);this.controller?.abort();this.waiters.splice(0).forEach(waiter=>waiter.reject(new Error('同步已关闭')));}
}
