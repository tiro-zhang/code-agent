import { beforeEach, expect, it } from 'vitest';
import { Api, ApiError } from '../src/api';
import { empty_state } from '../src/state';

beforeEach(()=>sessionStorage.clear());
const state = {...empty_state,server_instance_id:'server',project:{root:'/project',key:'project',model:'test'},runtime:{generation:2,state_version:4}};

it('响应丢失后先查询再按原身份复核回执，不创建新任务身份，保留草稿版本', async () => {
    const calls: string[] = [];
    const network = async (url: string, init?: RequestInit): Promise<Response> => {
        calls.push(`${init?.method || 'GET'} ${url}`);
        if(url.endsWith('/clients')) return Response.json({client_id:'client',next_sequence:1,server_instance_id:'server'});
        if(url.includes('/inputs')) {if(calls.filter(call=>call.includes('/inputs')).length===1)throw new TypeError('连接断开');return Response.json({run_id:'run',operation:{client_id:'client',sequence:1,next_sequence:2}});}
        return Response.json({status_code:202,body:{run_id:'run',operation:{client_id:'client',sequence:1,next_sequence:2}}});
    };
    const api = new Api(network, sessionStorage);
    await api.connect(state as never);
    await expect(api.mutate('/sessions/s/inputs',{text:'  code\n'},state as never,{session_id:'s',text:'  code\n',version:3})).rejects.toThrow();
    const refreshed = new Api(network, sessionStorage);
    await refreshed.connect(state as never);
    const receipt = await refreshed.recover();
    expect(receipt?.draft).toEqual({session_id:'s',text:'  code\n',version:3});
    expect(calls.filter(x=>x.includes('/inputs'))).toHaveLength(2);
    expect(calls).toContain('GET /api/v1/operations/client/1');
    expect(refreshed.pending).toBeNull();
});

it('重启隔离旧操作，不用新身份自动重发，并报告服务变化', async () => {
    let inputs = 0;
    const network = async (url:string): Promise<Response> => {
        if(url.endsWith('/clients')) return Response.json({client_id:'newclient',next_sequence:1,server_instance_id:inputs?'new':'server'});
        inputs++; throw new TypeError('断连');
    };
    const api = new Api(network,sessionStorage);
    await api.connect(state as never);
    await expect(api.mutate('/sessions/s/inputs',{text:'任务'},state as never)).rejects.toThrow();
    const refreshed = new Api(network,sessionStorage);
    const changed = await refreshed.connect({...state,server_instance_id:'new'} as never);
    expect(changed).toBe(true); expect(refreshed.pending).toBeNull(); expect(inputs).toBe(1);
});

it('业务拒绝消费原序号、保留草稿且下一操作使用服务回执序号', async () => {
    const sequences:number[] = [];
    const network = async (url:string, init?:RequestInit):Promise<Response> => {
        if(url.endsWith('/clients')) return Response.json({client_id:'c',next_sequence:1,server_instance_id:'server'});
        const body=JSON.parse(String(init?.body));sequences.push(body.sequence);
        return Response.json({error:{code:'project_busy',message:'项目忙碌'},operation:{client_id:'c',sequence:body.sequence,next_sequence:body.sequence+1}},{status:409});
    };
    const api=new Api(network,sessionStorage);await api.connect(state as never);
    await expect(api.mutate('/sessions/s/inputs',{text:'任务'},state as never)).rejects.toBeInstanceOf(ApiError);
    await expect(api.mutate('/sessions/s/inputs',{text:'新任务'},state as never)).rejects.toThrow('项目忙碌');
    expect(sequences).toEqual([1,2]);expect(api.pending).toBeNull();
});

it('浏览器禁止访问标签页存储时仍可建立连接并报告草稿无法持久化', async () => {
    const descriptor=Object.getOwnPropertyDescriptor(window,'sessionStorage')!;
    Object.defineProperty(window,'sessionStorage',{configurable:true,get(){throw new DOMException('denied','SecurityError');}});
    try {
        const api=new Api(async()=>Response.json({client_id:'c',next_sequence:1,server_instance_id:'server'}));
        await api.connect(state as never);
        expect(api.storage_error).toMatch(/存储不可用/);
    } finally {Object.defineProperty(window,'sessionStorage',descriptor);}
});

function copied_storage():Storage {
    const values=new Map<string,string>();
    for(let i=0;i<sessionStorage.length;i++){const key=sessionStorage.key(i)!;values.set(key,sessionStorage.getItem(key)!);}
    return {get length(){return values.size;},clear:()=>values.clear(),getItem:key=>values.get(key)??null,key:index=>[...values.keys()][index]??null,removeItem:key=>{values.delete(key);},setItem:(key,value)=>{values.set(key,value);}};
}

it('复制标签页的不同正文冲突后登记独立身份，刷新后下一次明确发送才能执行',async()=>{
    let registered=0;const submitted:Array<{client_id:string;sequence:number;text:string}>=[];
    const network=async(url:string,init?:RequestInit)=>{
        if(url.endsWith('/clients'))return Response.json({client_id:`client-${++registered}`,next_sequence:1,server_instance_id:'server'});
        const body=JSON.parse(String(init?.body));submitted.push(body);
        if(body.client_id==='client-1'&&submitted.length>1)return Response.json({error:{code:'operation_conflict',message:'相同操作身份不能携带不同内容'}},{status:409});
        return Response.json({operation:{client_id:body.client_id,sequence:body.sequence,next_sequence:body.sequence+1},run_id:'r'});
    };
    const first=new Api(network,sessionStorage);await first.connect(state as never);
    const duplicate=copied_storage();const second=new Api(network,duplicate);await second.connect(state as never);
    await first.mutate('/sessions/s/inputs',{text:'A任务'},state as never);
    await expect(second.mutate('/sessions/s/inputs',{text:'B草稿'},state as never,{session_id:'s',text:'B草稿',version:2})).rejects.toMatchObject({code:'operation_conflict'});
    expect(registered).toBe(2);expect(submitted).toHaveLength(2);expect(second.pending).toBeNull();
    const refreshed=new Api(network,duplicate);await refreshed.connect(state as never);
    expect(refreshed.client?.client_id).toBe('client-2');
    await refreshed.mutate('/sessions/s/inputs',{text:'B确认后新任务'},state as never);
    expect(submitted.map(item=>[item.client_id,item.sequence,item.text])).toEqual([['client-1',1,'A任务'],['client-1',1,'B草稿'],['client-2',1,'B确认后新任务']]);
});

it.each(['sequence_conflict','operation_expired'])('%s刷新后提供明确重登记路径且绝不自动重发',async code=>{
    let registered=0,inputs=0;
    const network=async(url:string,init?:RequestInit)=>{
        if(url.endsWith('/clients'))return Response.json({client_id:`c${++registered}`,next_sequence:1,server_instance_id:'server'});
        inputs++;const body=JSON.parse(String(init?.body));
        if(body.client_id==='c1')return Response.json({error:{code,message:'旧身份不能使用'}},{status:code==='operation_expired'?410:409});
        return Response.json({operation:{client_id:'c2',sequence:1,next_sequence:2}});
    };
    const api=new Api(network,sessionStorage);await api.connect(state as never);
    await expect(api.mutate('/sessions/s/inputs',{text:'草稿'},state as never)).rejects.toThrow();
    const refreshed=new Api(network,sessionStorage);await refreshed.connect(state as never);
    expect(refreshed.identity_issue?.code).toBe(code);expect(registered).toBe(1);expect(inputs).toBe(1);
    if(code==='operation_expired')await expect(refreshed.replace_identity()).rejects.toMatchObject({code:'review_required'});
    await refreshed.replace_identity(true);expect(registered).toBe(2);expect(inputs).toBe(1);
    await refreshed.mutate('/sessions/s/inputs',{text:'核查后明确发送'},state as never);expect(inputs).toBe(2);
});

it('查询丢失响应遇到过期回执时要求先核查，不允许绕过未知提交直接登记',async()=>{
    let registered=0,inputs=0;
    const network=async(url:string)=>{
        if(url.endsWith('/clients'))return Response.json({client_id:`c${++registered}`,next_sequence:1,server_instance_id:'server'});
        if(url.includes('/operations/'))return Response.json({error:{code:'operation_expired',message:'回执已过期'}},{status:410});
        inputs++;throw new TypeError('断连');
    };
    const api=new Api(network,sessionStorage);await api.connect(state as never);
    await expect(api.mutate('/sessions/s/inputs',{text:'结果不明任务'},state as never)).rejects.toThrow();
    await expect(api.replace_identity(true)).rejects.toMatchObject({code:'unconfirmed'});
    await expect(api.recover()).rejects.toMatchObject({code:'operation_expired'});
    expect(api.identity_issue?.uncertain).toBe(true);expect(api.pending?.body.text).toBe('结果不明任务');
    await expect(api.replace_identity()).rejects.toMatchObject({code:'review_required'});
    await api.replace_identity(true);expect(inputs).toBe(1);expect(registered).toBe(2);
});

it('冲突后的身份登记失败可刷新后明确重试，不消耗或重发草稿',async()=>{
    let registrations=0,inputs=0;
    const network=async(url:string)=>{
        if(url.endsWith('/clients')){
            registrations++;if(registrations===2)throw new TypeError('登记连接中断');
            return Response.json({client_id:`client-${registrations}`,next_sequence:1,server_instance_id:'server'});
        }
        inputs++;return Response.json({error:{code:'operation_conflict',message:'身份冲突'}},{status:409});
    };
    const api=new Api(network,sessionStorage);await api.connect(state as never);
    await expect(api.mutate('/sessions/s/inputs',{text:'保留草稿'},state as never)).rejects.toThrow('登记失败');
    const refreshed=new Api(network,sessionStorage);await refreshed.connect(state as never);
    expect(refreshed.identity_issue?.code).toBe('operation_conflict');expect(inputs).toBe(1);
    await refreshed.replace_identity();expect(refreshed.client?.client_id).toBe('client-3');expect(inputs).toBe(1);
});

it('复制页不同正文响应丢失后，不能把原标签页回执当作自己的接受结果',async()=>{
    let registered=0,first_attempt=true;const inputs:Array<{client_id:string;sequence:number;text:string}>=[];
    const network=async(url:string,init?:RequestInit)=>{
        if(url.endsWith('/clients'))return Response.json({client_id:`c${++registered}`,next_sequence:1,server_instance_id:'server'});
        if(url.includes('/operations/'))return Response.json({status_code:202,body:{run_id:'A-run',operation:{client_id:'c1',sequence:1,next_sequence:2}}});
        const body=JSON.parse(String(init?.body));inputs.push(body);
        if(first_attempt){first_attempt=false;throw new TypeError('B的拒绝响应丢失');}
        return Response.json({error:{code:'operation_conflict',message:'该回执实际属于A的不同正文'}},{status:409});
    };
    const api=new Api(network,sessionStorage);await api.connect(state as never);
    await expect(api.mutate('/sessions/s/inputs',{text:'B独立草稿'},state as never,{session_id:'s',text:'B独立草稿',version:7})).rejects.toThrow();
    const refreshed=new Api(network,sessionStorage);await refreshed.connect(state as never);
    await expect(refreshed.recover()).rejects.toMatchObject({code:'operation_conflict'});
    expect(refreshed.client?.client_id).toBe('c2');expect(inputs.map(body=>[body.client_id,body.sequence,body.text])).toEqual([['c1',1,'B独立草稿'],['c1',1,'B独立草稿']]);
});

it('查询未知操作不会复核POST或登记新身份，原请求等待明确重试',async()=>{
    let writes=0;
    const network=async(url:string)=>{
        if(url.endsWith('/clients'))return Response.json({client_id:'c',next_sequence:1,server_instance_id:'server'});
        if(url.includes('/operations/'))return Response.json({error:{code:'operation_unknown',message:'尚未接收此操作'}},{status:404});
        writes++;throw new TypeError('断连');
    };
    const api=new Api(network,sessionStorage);await api.connect(state as never);
    await expect(api.mutate('/sessions/s/inputs',{text:'等待用户重试'},state as never)).rejects.toThrow();
    await expect(api.recover()).rejects.toMatchObject({code:'operation_unknown'});expect(writes).toBe(1);expect(api.pending?.body.text).toBe('等待用户重试');
});
