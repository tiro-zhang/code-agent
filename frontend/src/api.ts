import type {ServerState,SubmittedDraft} from './types';
type Fetcher=(url:string,init?:RequestInit)=>Promise<Response>;
interface Client {client_id:string;next_sequence:number;server_instance_id:string;}
interface Pending {path:string;body:Record<string,unknown>;draft?:SubmittedDraft;}
export interface IdentityIssue {code:string;message:string;uncertain:boolean;}
interface Saved {client:Client;pending:Pending|null;identity_issue?:IdentityIssue|null;}
export class ApiError extends Error {
    constructor(public code:string,message:string,public status=0){super(message);this.name='ApiError';}
}
export class Api {
    client:Client|null=null;
    pending:Pending|null=null;
    storage_error='';
    identity_issue:IdentityIssue|null=null;
    private storage_key='';
    private storage:Storage;
    constructor(private network:Fetcher=(url,init)=>fetch(url,init),storage?:Storage) {
        try {this.storage=storage??window.sessionStorage;}
        catch {
            this.storage_error='标签页存储不可用，草稿仅保留在当前页面。';
            const values=new Map<string,string>();
            this.storage={get length(){return values.size;},clear:()=>values.clear(),getItem:key=>values.get(key)??null,key:index=>[...values.keys()][index]??null,removeItem:key=>{values.delete(key);},setItem:(key,value)=>{values.set(key,value);}};
        }
    }
    async get<T>(path:string,signal?:AbortSignal):Promise<T>{
        const response=await this.network(`/api/v1${path}`,{credentials:'same-origin',cache:'no-store',signal});
        const body=await response.json();
        if(!response.ok)throw this.error(response.status,body);
        return body;
    }
    async authenticate(token:string){
        const response=await this.network('/api/v1/auth',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});
        if(!response.ok)throw this.error(response.status,await response.json());
    }
    async connect(state:ServerState):Promise<boolean>{
        this.storage_key=`mewcode:client:${state.project.key}`;
        let saved:Saved|null=null;
        try{saved=JSON.parse(this.storage.getItem(this.storage_key)||'null');}catch{this.storage_error='标签页存储不可用，草稿仅保留在当前页面。';}
        const changed=Boolean(saved?.client && saved.client.server_instance_id!==state.server_instance_id);
        if(saved?.client.server_instance_id===state.server_instance_id){this.client=saved.client;this.pending=saved.pending;this.identity_issue=saved.identity_issue??null;return false;}
        await this.register_client(state.server_instance_id);return changed;
    }
    private async register_client(expected_instance:string){
        const response=await this.network('/api/v1/clients',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:'{}'});
        const body=await response.json();if(!response.ok)throw this.error(response.status,body);
        if(body.server_instance_id!==expected_instance)throw new ApiError('instance_changed','登记期间服务身份已变化，请先同步状态。');
        this.client=body;this.pending=null;this.identity_issue=null;this.save();
    }
    private save(){
        try{this.storage.setItem(this.storage_key,JSON.stringify({client:this.client,pending:this.pending,identity_issue:this.identity_issue}));}
        catch{this.storage_error='标签页存储不可用，刷新后不能恢复未确认操作；当前草稿仍可编辑。';}
    }
    async replace_identity(acknowledge_unknown=false):Promise<void>{
        if(!this.identity_issue){
            if(this.pending)throw new ApiError('unconfirmed','旧操作结果尚未确认，请先查询原回执，不能通过新身份重发。');
            throw new ApiError('identity_available','当前标签页身份可用，无需重新登记。');
        }
        if(this.identity_issue.uncertain&&!acknowledge_unknown)throw new ApiError('review_required','旧操作结果无法确认，请先核查当前状态与已保存历史，再明确登记新身份。');
        if(!this.client)throw new ApiError('instance_changed','请先连接当前服务。');
        await this.register_client(this.client.server_instance_id);
    }
    private mark_identity_issue(code:string){
        const uncertain=code==='operation_expired';
        this.identity_issue={code,uncertain,message:uncertain
            ?'原操作回执已过期，无法确认是否执行；草稿已保留。请先核查当前状态与已保存历史，登记新身份不会重新执行旧操作。'
            :'当前提交因操作身份或序号冲突被拒绝，未执行；草稿已保留。请登记独立身份后再次明确发送。'};
        if(!uncertain)this.pending=null;
        this.save();
    }
    private error(status:number,body:any){return new ApiError(body?.error?.code||'request_failed',body?.error?.message||`请求失败 (${status})`,status);}
    private settle(body:any){
        if(body.operation && this.client){this.client.next_sequence=body.operation.next_sequence;this.pending=null;this.save();}
    }
    async mutate(path:string,data:Record<string,unknown>,state:ServerState,draft?:SubmittedDraft){
        if(!this.client || this.client.server_instance_id!==state.server_instance_id)throw new ApiError('instance_changed','服务身份已变化，请先同步状态。');
        if(this.identity_issue)throw new ApiError('identity_recovery_required',this.identity_issue.message);
        if(this.pending)throw new ApiError('unconfirmed','上一个操作尚未确认，请先查询原回执。');
        this.pending={path,body:{server_instance_id:state.server_instance_id,client_id:this.client.client_id,sequence:this.client.next_sequence,generation:state.runtime.generation,state_version:state.runtime.state_version,...data},draft};
        this.save();
        return this.submit_pending();
    }
    private async submit_pending(return_outcome=false){
        const pending=this.pending!;
        const response=await this.network(`/api/v1${pending.path}`,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending.body)});
        const body=await response.json();this.settle(body);
        if(!response.ok){
            if(body.operation&&return_outcome)return {body,accepted:false};
            if(!body.operation){
                const code=body.error?.code;
                if(['operation_conflict','sequence_conflict','operation_expired'].includes(code)){
                    this.mark_identity_issue(code);
                    if(code==='operation_conflict'){
                        // 此响应确认当前不同内容未执行；只换身份，等待用户再次发送。
                        let message='当前提交因复制标签页的身份冲突被拒绝，未执行。已登记独立身份；草稿保留，请再次明确发送。';
                        try{await this.replace_identity();}
                        catch(error){message=`当前提交未执行，草稿已保留。独立身份登记失败：${error instanceof Error?error.message:'连接失败'}。请使用重新登记入口。`;}
                        throw new ApiError(code,message,response.status);
                    }
                    throw new ApiError(code,this.identity_issue!.message,response.status);
                }
                // 明确未入账的格式或认证拒绝不会消费序号。
                this.pending=null;this.save();
            }
            throw this.error(response.status,body);
        }
        if(this.pending)throw new ApiError('missing_receipt','响应缺少操作回执，请查询原提交结果。');
        return return_outcome?{body,accepted:true}:body;
    }
    async recover():Promise<{body:any;draft?:SubmittedDraft;accepted:boolean}|null>{
        if(!this.pending || !this.client)return null;
        const pending=this.pending;
        let receipt:{status_code?:number;body?:any;pending?:boolean};
        try{receipt=await this.get(`/operations/${encodeURIComponent(this.client.client_id)}/${pending.body.sequence}`);}
        catch(error){
            if(error instanceof ApiError&&error.code==='operation_expired'){this.mark_identity_issue(error.code);throw new ApiError(error.code,this.identity_issue!.message,error.status);}
            throw error;
        }
        if(receipt.pending)return null;
        const identity=receipt.body?.operation;
        if(!identity||identity.client_id!==pending.body.client_id||identity.sequence!==pending.body.sequence)throw new ApiError('missing_receipt','操作回执身份不完整，请稍后再查询。');
        // 复制标签页可能共享身份；GET 只证明回执存在，原请求复核才能证明正文归属。
        // 账本已有该序号，原身份 POST 只返回回执或冲突，不会创建第二次执行。
        const verified=await this.submit_pending(true);
        return {...verified,draft:pending.draft};
    }
    async retry_pending(){
        if(this.identity_issue)throw new ApiError('identity_recovery_required',this.identity_issue.message);
        if(!this.pending)return null;
        const pending=this.pending;
        const body=await this.submit_pending();return {body,draft:pending.draft,accepted:true};
    }
}
