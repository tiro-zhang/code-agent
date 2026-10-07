import {test,expect,type Page,type BrowserContext} from '@playwright/test';
import {fixture_state,fixture_sessions} from '../fixtures';
import type {ServerState} from '../../src/types';

async function fixture(context:BrowserContext,initial:ServerState=structuredClone(fixture_state)){
    let state=initial;let inputs=0,activations=0,decisions=0,created=0;let delay_reply=0;let client=0;
    const writes:Array<{path:string;body:any}>=[];
    const receipts=new Map<string,unknown>();
    let approval_active=state.approvals.length>0;
    await context.route('**/api/v1/**',async route=>{
        const request=route.request(),url=new URL(request.url()),path=url.pathname.replace('/api/v1','');
        const reply=(body:unknown,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
        if(path==='/events')return reply({},429);
        if(path==='/state')return reply(state);
        if(path==='/clients')return reply({client_id:`client-${++client}`,next_sequence:1,server_instance_id:state.server_instance_id});
        if(path==='/auth')return reply({server_instance_id:state.server_instance_id});
        if(path.startsWith('/operations/')){const value=receipts.get(path.slice('/operations/'.length));return value?reply(value):reply({error:{code:'unknown',message:'操作未收到'}},404);}
        if(path.startsWith('/approvals/')&&request.method()==='GET'){
            if(!approval_active)return reply({error:{code:'resolved',message:'审批已由另一标签页处理'}},409);
            const section=url.searchParams.get('section')||'targets';const offset=Number(url.searchParams.get('offset')||0);
            return reply({...initial.approvals[0],root:state.project.root,sections:['targets','arguments','content','scope'],section,content:section==='content'?`拟修改内容第 ${offset?2:1} 块`:section==='targets'?'/workspace/demo/example.py':`${section} 完整快照`,next_offset:section==='content'&&!offset?32768:null,total_bytes:section==='content'?32780:50});
        }
        if(request.method()==='POST'){
            const body=request.postDataJSON();writes.push({path,body});
            const operation={client_id:body.client_id,sequence:body.sequence,next_sequence:body.sequence+1};
            let response:any={operation_id:`o-${writes.length}`,operation};let status=202;
            if(path.endsWith('/activate')){activations++;state={...state,seq:state.seq+1,runtime:{...state.runtime,session_id:'s1',generation:1,state_version:1,phase:'idle',busy:false},projection:{...state.projection,session_id:'s1'}};}
            if(path.endsWith('/inputs')){inputs++;state={...state,seq:state.seq+1,runtime:{...state.runtime,phase:'running',busy:true,run_id:'r1'},projection:{...state.projection,messages:[...state.projection.messages,{id:`u${inputs}`,role:'user',text:body.text,kind:'user_input'},{id:`a${inputs}`,role:'assistant',text:'正在读取项目…',complete:false}]}};response.run_id='r1';}
            if(path.endsWith('/controls')){state={...state,seq:state.seq+1,runtime:{...state.runtime,phase:body.action==='stop'?'cancelling':state.runtime.phase,mode:body.action==='plan'?'plan':state.runtime.mode}};}
            if(path.endsWith('/decision')){if(!approval_active){response={error:{code:'resolved',message:'审批已由另一标签页处理'},operation};status=409;}else{decisions++;approval_active=false;state={...state,seq:state.seq+1,approvals:[]};}}
            if(path==='/sessions'){created++;response.session_id='s2';}
            receipts.set(`${body.client_id}/${body.sequence}`,{status_code:status,body:response});
            if(path.endsWith('/inputs')&&delay_reply)await new Promise(resolve=>setTimeout(resolve,delay_reply));
            return reply(response,status);
        }
        if(path.endsWith('/history'))return reply({items:[{id:'m1',role:'user',kind:'user_input',text:'读取 README.md，概括项目结构与启动步骤。'},{id:'m2',role:'assistant',text:'## 先从项目地图开始\n\n这是一个 Python 终端 AI 编程助手。入口位于 `src/mewcode/cli.py`。\n\n```bash\nuv sync\nuv run mewcode --config .env\n```\n\n接下来可以查看工具执行与权限模块。'}],next_cursor:null,warnings:[]});
        if(path==='/sessions')return reply({items:fixture_sessions,next_cursor:null});
        return reply({error:{code:'missing',message:'不存在'}},404);
    });
    return {get inputs(){return inputs;},get activations(){return activations;},get decisions(){return decisions;},get created(){return created;},writes,set_state(next:ServerState){state=next;},get state(){return state;},delay(ms:number){delay_reply=ms;}};
}
async function open_session(page:Page){await page.goto('/');await page.getByRole('button',{name:/理解项目的启动流程/}).click();await expect(page.getByText('读取 README.md，概括项目结构与启动步骤。')).toBeVisible();}
async function read_approval(page:Page){await page.getByRole('button',{name:/参数/}).click();await expect(page.getByText('arguments 完整快照')).toBeVisible();await page.getByRole('button',{name:/内容/}).click();await page.getByRole('button',{name:'下一块'}).click();await expect(page.getByText('拟修改内容第 2 块')).toBeVisible();await page.getByRole('button',{name:/范围/}).click();await expect(page.getByText('scope 完整快照')).toBeVisible();}

test('历史只读、草稿隔离、组合输入、发送后继续编辑与刷新不重发',async({page,context})=>{
    const backend=await fixture(context);await open_session(page);
    expect(backend.activations).toBe(0);expect(backend.inputs).toBe(0);
    const input=page.getByRole('textbox',{name:'任务输入'});await input.fill('    print("你好")\n');
    await page.getByRole('button',{name:/另一个会话/}).click();await expect(input).toHaveValue('');
    await page.getByRole('button',{name:/理解项目的启动流程/}).click();await expect(input).toHaveValue('    print("你好")\n');
    await page.getByRole('button',{name:'继续会话',exact:true}).click();await expect(page.getByRole('button',{name:'发送任务'})).toBeEnabled();
    await input.dispatchEvent('compositionstart');await input.press('Control+Enter');expect(backend.inputs).toBe(0);await input.dispatchEvent('compositionend');
    backend.delay(400);await input.press('Control+Enter');await input.fill('回执到达前的新草稿');
    await expect.poll(()=>backend.inputs).toBe(1);await expect(input).toHaveValue('回执到达前的新草稿');
    await expect(page.getByText('正在读取项目…')).toBeVisible();
    expect(backend.writes.find(write=>write.path.endsWith('/inputs'))?.body.text).toBe('    print("你好")\n');
    await page.reload();await expect(input).toHaveValue('回执到达前的新草稿');await expect(page.getByText('正在读取项目…')).toHaveCount(1);expect(backend.inputs).toBe(1);
    await page.screenshot({path:'test-results/workbench-desktop.png',fullPage:true});
});

test('窄屏键盘抽屉和详情控制保持可达，忙碌只禁发送',async({page,context})=>{
    await fixture(context);await page.setViewportSize({width:390,height:844});await page.goto('/');
    await page.getByRole('button',{name:'打开会话导航'}).click();await page.getByRole('button',{name:/理解项目的启动流程/}).click();
    await page.getByRole('textbox',{name:'任务输入'}).fill('窄屏草稿');
    await page.getByRole('button',{name:'收起或打开任务详情'}).click();await expect(page.getByRole('complementary',{name:'任务详情'})).toBeVisible();
    await page.keyboard.press('Escape');await expect(page.getByRole('textbox',{name:'任务输入'})).toHaveValue('窄屏草稿');
    await expect(page.locator('body')).toHaveJSProperty('scrollWidth',390);
    await page.screenshot({path:'test-results/workbench-mobile.png',fullPage:true});
});

test('两标签页审批完整分页且至多一次决定，聊天草稿保持',async({page,context})=>{
    const initial=structuredClone(fixture_state);initial.runtime={...initial.runtime,session_id:'s1',generation:1,state_version:1,phase:'awaiting_approval',busy:true,run_id:'r1'};initial.projection.session_id='s1';initial.approvals=[{id:'approval-1',session_id:'s1',generation:1,run_id:'r1',tool:'edit',reason:'修改 example.py',expires_at:Date.now()+600000}];
    const backend=await fixture(context,initial);const second=await context.newPage();await open_session(page);await open_session(second);
    await page.getByRole('textbox',{name:'任务输入'}).fill('审批期间的独立草稿');
    await expect(page.getByRole('button',{name:'仅本次允许'})).toBeDisabled();await read_approval(page);await read_approval(second);
    await page.getByRole('button',{name:'仅本次允许'}).click();await expect.poll(()=>backend.decisions).toBe(1);
    const late=second.getByRole('button',{name:'本会话允许'});if(await late.isVisible())await late.click();
    await expect(page.getByRole('textbox',{name:'任务输入'})).toHaveValue('审批期间的独立草稿');expect(backend.inputs).toBe(0);expect(backend.decisions).toBe(1);
});

test('安全Markdown不加载远程图像或执行HTML，代码复制可用',async({page,context})=>{
    const initial=structuredClone(fixture_state);initial.projection.session_id='s1';initial.projection.messages=[{id:'danger',role:'assistant',text:'<script>window.hacked=true</script>\n\n![泄漏](https://invalid.example/x)\n\n```python\n    print("<x>")\n```'}];
    await fixture(context,initial);let external=0;page.on('request',request=>{if(request.url().includes('invalid.example'))external++;});await open_session(page);
    await expect(page.getByText('泄漏 · 图片未加载')).toBeVisible();expect(await page.evaluate(()=>Object.hasOwn(window,'hacked'))).toBe(false);expect(external).toBe(0);
    await context.grantPermissions(['clipboard-read','clipboard-write']);await page.getByRole('button',{name:'复制代码'}).last().click();expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe('    print("<x>")\n');
});

test('向上阅读保持滚动位置，新流内容只显示提示，停止在占用会话上执行',async({page,context})=>{
    const initial=structuredClone(fixture_state);initial.runtime={...initial.runtime,session_id:'s1',phase:'running',busy:true,run_id:'r'};initial.projection.session_id='s1';initial.projection.messages=Array.from({length:30},(_,i)=>({id:`long${i}`,role:'assistant',text:`段落 ${i}\n\n这是已有的长对话内容，阅读时应保持当前位置。`.repeat(3)}));
    const backend=await fixture(context,initial);await open_session(page);const timeline=page.getByLabel('聊天记录');await timeline.evaluate(el=>{el.scrollTop=60;el.dispatchEvent(new Event('scroll'));});
    backend.set_state({...initial,seq:2,projection:{...initial.projection,messages:[...initial.projection.messages,{id:'new',role:'assistant',text:'新来的内容'}]}});
    await expect(page.getByRole('button',{name:'有新内容'})).toBeVisible();expect(await timeline.evaluate(el=>el.scrollTop)).toBe(60);
    await page.getByRole('button',{name:'停止',exact:true}).click();await expect(page.getByRole('button',{name:'正在停止',exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'发送任务'})).toBeDisabled();
    expect(backend.writes.at(-1)?.body.action).toBe('stop');
});

test('快照替换与重复SSE不会重复回复，临时缺失提示不阻塞停止',async({page,context})=>{
    const initial=structuredClone(fixture_state);initial.runtime={...initial.runtime,session_id:'s1',phase:'running',busy:true};initial.projection.session_id='s1';
    await fixture(context,initial);
    const next={...initial,seq:2,projection:{...initial.projection,messages:[{id:'answer',role:'assistant',text:'流式内容的一半',complete:false}]}};
    const final={...next,seq:3,projection:{...next.projection,messages:[{id:'answer',role:'assistant',text:'完整替换后的回复',complete:true}],warnings:['部分临时片段已超出事件保留范围，无法恢复。']}};
    const event=(state:ServerState)=>({server_instance_id:state.server_instance_id,seq:state.seq,session_id:'s1',runtime_generation:0,run_id:'r',kind:'snapshot_replace',payload:state});
    await context.route('**/api/v1/events?*',route=>route.fulfill({contentType:'text/event-stream',body:[event(next),event(next),event(final)].map(e=>`id: ${e.seq}\ndata: ${JSON.stringify(e)}\n\n`).join('')}));
    await open_session(page);await expect(page.getByText('完整替换后的回复')).toHaveCount(1);await expect(page.getByText('流式内容的一半')).toHaveCount(0);
    await expect(page.getByText('部分临时片段已超出事件保留范围，无法恢复。')).toBeVisible();await expect(page.getByRole('button',{name:'停止',exact:true})).toBeEnabled();
});

test('历史中登记的超大结果分块只读，明确标注来源截断',async({page,context})=>{
    const backend=await fixture(context);
    await context.route('**/api/v1/sessions/*/history',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({items:[{id:'saved-tool',role:'tool',kind:'tool_result',text:'长输出预览',call:{id:'call-1'},result:{result_id:'registered-result'},truncated:true}],next_cursor:null,warnings:[]})}));
    await context.route('**/api/v1/results/registered-result*',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({id:'registered-result',session_id:'s1',content:route.request().url().includes('cursor=')?'结果的后续保留片段':'结果的第一块',next_cursor:route.request().url().includes('cursor=')?null:'next',truncated:true,warnings:['原始工具输出已截断']})}));
    await page.goto('/');await page.getByRole('button',{name:/理解项目的启动流程/}).click();await page.getByText('查看工具记录').click();await page.getByRole('button',{name:'读取已保留详情'}).click();await expect(page.getByText('结果的第一块')).toBeVisible();await page.getByRole('button',{name:'下一块结果'}).click();await expect(page.getByText('结果的后续保留片段')).toBeVisible();await expect(page.getByText('原始工具输出已截断')).toBeVisible();expect(backend.inputs).toBe(0);expect(backend.activations).toBe(0);
});

test('复制标签页保留草稿并在身份冲突后独立登记，刷新也不会自动重发',async({page,context})=>{
    const initial=structuredClone(fixture_state);initial.runtime={...initial.runtime,session_id:'s1',generation:1,phase:'idle'};initial.projection.session_id='s1';await fixture(context,initial);
    const requests:Array<{client_id:string;sequence:number;text:string}>=[],accepted=new Map<string,string>();
    await context.route('**/api/v1/sessions/s1/inputs',route=>{
        const body=route.request().postDataJSON();requests.push(body);const key=`${body.client_id}:${body.sequence}`;
        if(accepted.has(key)&&accepted.get(key)!==body.text)return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:{code:'operation_conflict',message:'相同操作身份不能携带不同内容'}})});
        accepted.set(key,body.text);return route.fulfill({status:202,contentType:'application/json',body:JSON.stringify({run_id:`r${accepted.size}`,operation:{client_id:body.client_id,sequence:body.sequence,next_sequence:body.sequence+1}})});
    });
    await open_session(page);const copied=await page.evaluate(()=>Object.entries(sessionStorage));const duplicate=await context.newPage();
    await duplicate.addInitScript(items=>{if(!sessionStorage.getItem('fixture_cloned_once')){for(const [key,value]of items)sessionStorage.setItem(key,value);sessionStorage.setItem('fixture_cloned_once','yes');}},copied);
    await open_session(duplicate);await page.getByRole('textbox',{name:'任务输入'}).fill('原标签页任务');await page.getByRole('button',{name:'发送任务'}).click();await expect.poll(()=>accepted.size).toBe(1);
    const draft=duplicate.getByRole('textbox',{name:'任务输入'});await draft.fill('复制页自己的草稿');await duplicate.getByRole('button',{name:'发送任务'}).click();
    await expect(duplicate.getByRole('alert')).toContainText('独立身份');await expect(draft).toHaveValue('复制页自己的草稿');expect(requests).toHaveLength(2);expect(accepted.size).toBe(1);
    await duplicate.reload();await expect(draft).toHaveValue('复制页自己的草稿');await expect(duplicate.getByRole('button',{name:'发送任务'})).toBeEnabled();expect(requests).toHaveLength(2);
    await duplicate.getByRole('button',{name:'发送任务'}).click();await expect.poll(()=>accepted.size).toBe(2);expect(requests[2].client_id).not.toBe(requests[0].client_id);expect(requests[2].sequence).toBe(1);
});

test('过期回执在刷新后仍保留草稿，核查历史后明确登记才可再次发送',async({page,context})=>{
    const initial=structuredClone(fixture_state);initial.runtime={...initial.runtime,session_id:'s1',generation:1,phase:'idle'};initial.projection.session_id='s1';await fixture(context,initial);
    let inputs=0;
    await context.route('**/api/v1/sessions/s1/inputs',route=>{inputs++;return route.fulfill({status:410,contentType:'application/json',body:JSON.stringify({error:{code:'operation_expired',message:'回执已过期，不能重新执行'}})});});
    await open_session(page);const draft=page.getByRole('textbox',{name:'任务输入'});await draft.fill('先核查结果再决定是否发送');await page.getByRole('button',{name:'发送任务'}).click();
    await expect(page.getByRole('button',{name:'已核查历史，登记新身份'})).toBeVisible();await expect(page.getByRole('button',{name:'发送任务'})).toBeDisabled();expect(inputs).toBe(1);
    await page.reload();await expect(draft).toHaveValue('先核查结果再决定是否发送');await expect(page.getByRole('button',{name:'已核查历史，登记新身份'})).toBeVisible();expect(inputs).toBe(1);
    await page.getByRole('button',{name:'已核查历史，登记新身份'}).click();await expect(page.getByRole('button',{name:'发送任务'})).toBeEnabled();await expect(draft).toHaveValue('先核查结果再决定是否发送');expect(inputs).toBe(1);
});

test('复制页丢失拒绝响应后核查同身份正文，不能用另一页回执清除草稿',async({page,context})=>{
    const initial=structuredClone(fixture_state);initial.runtime={...initial.runtime,session_id:'s1',generation:1,phase:'idle'};initial.projection.session_id='s1';await fixture(context,initial);
    let first_conflict=true;const requests:any[]=[];let accepted:any;
    await context.route('**/api/v1/sessions/s1/inputs',route=>{
        const body=route.request().postDataJSON();requests.push(body);
        if(body.text==='A已执行任务'){accepted=body;return route.fulfill({status:202,contentType:'application/json',body:JSON.stringify({operation:{client_id:body.client_id,sequence:1,next_sequence:2},run_id:'A-run'})});}
        if(first_conflict){first_conflict=false;return route.abort('failed');}
        return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:{code:'operation_conflict',message:'回执属于A的不同正文'}})});
    });
    await context.route('**/api/v1/operations/**',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({status_code:202,body:{operation:{client_id:accepted.client_id,sequence:1,next_sequence:2},run_id:'A-run'}})}));
    await open_session(page);const copied=await page.evaluate(()=>Object.entries(sessionStorage));const duplicate=await context.newPage();
    await duplicate.addInitScript(items=>{if(!sessionStorage.getItem('fixture_cloned_once')){for(const [key,value]of items)sessionStorage.setItem(key,value);sessionStorage.setItem('fixture_cloned_once','yes');}},copied);
    await open_session(duplicate);await page.getByRole('textbox',{name:'任务输入'}).fill('A已执行任务');await page.getByRole('button',{name:'发送任务'}).click();await expect.poll(()=>requests.length).toBe(1);
    const draft=duplicate.getByRole('textbox',{name:'任务输入'});await draft.fill('B必须保留的草稿');await duplicate.getByRole('button',{name:'发送任务'}).click();await expect(duplicate.getByRole('button',{name:'查询原回执'})).toBeVisible();
    await duplicate.reload();await expect(duplicate.getByRole('alert')).toContainText('独立身份');await expect(draft).toHaveValue('B必须保留的草稿');await expect(duplicate.getByRole('button',{name:'发送任务'})).toBeEnabled();
    expect(requests.map(body=>body.text)).toEqual(['A已执行任务','B必须保留的草稿','B必须保留的草稿']);expect(requests[2].client_id).toBe(requests[1].client_id);expect(requests[2].sequence).toBe(requests[1].sequence);
});

test('忙碌转空闲只刷新一次历史首页，清理旧警告并保留分页游标与阅读位置',async({page,context})=>{
    const initial=structuredClone(fixture_state);initial.runtime={...initial.runtime,session_id:'s1',generation:1,phase:'running',busy:true};initial.projection.session_id='s1';
    const backend=await fixture(context,initial);let first_reads=0;const cursors:string[]=[];const warning='工具交互未完整提交，读取时快照暂不完整';
    const first_items=Array.from({length:20},(_,i)=>({id:`saved-${i}`,role:'assistant',text:`持久历史段落 ${i}。`.repeat(15)}));
    await context.route('**/api/v1/sessions/s1/history*',route=>{
        const cursor=new URL(route.request().url()).searchParams.get('cursor');
        if(cursor){cursors.push(cursor);return route.fulfill({contentType:'application/json',body:JSON.stringify({items:[{id:`page-${cursors.length}`,role:'assistant',text:`用户已加载的历史页 ${cursors.length}`}],next_cursor:cursors.length===1?'old-page-3':null,warnings:[]})});}
        first_reads++;return route.fulfill({contentType:'application/json',body:JSON.stringify({items:first_items,next_cursor:first_reads===1?'old-page-2':'new-page-2',warnings:first_reads===1?[warning]:[]})});
    });
    await page.goto('/');await page.getByRole('button',{name:/理解项目的启动流程/}).click();await expect(page.getByText(warning)).toBeVisible();
    await page.getByRole('button',{name:'继续读取历史'}).click();await expect(page.getByText('用户已加载的历史页 1')).toBeVisible();
    const timeline=page.getByLabel('聊天记录');await timeline.evaluate(el=>{el.scrollTop=240;el.dispatchEvent(new Event('scroll'));});
    const anchor=page.locator('.message').nth(3);const before=await anchor.evaluate(el=>el.getBoundingClientRect().top-el.closest('.timeline')!.getBoundingClientRect().top);
    backend.set_state({...initial,seq:2,runtime:{...initial.runtime,phase:'idle',busy:false}});
    await expect(page.getByText(warning)).toHaveCount(0);expect(first_reads).toBe(2);await expect(page.getByText('用户已加载的历史页 1')).toHaveCount(1);
    expect(Math.abs((await anchor.evaluate(el=>el.getBoundingClientRect().top-el.closest('.timeline')!.getBoundingClientRect().top))-before)).toBeLessThan(2);
    await page.getByRole('button',{name:'继续读取历史'}).click();await expect(page.getByText('用户已加载的历史页 2')).toBeVisible();expect(cursors).toEqual(['old-page-2','old-page-3']);
    backend.set_state({...backend.state,seq:3});await page.waitForTimeout(1800);expect(first_reads).toBe(2);
});

test('结束后的历史刷新不会被晚到的旧首页响应覆盖',async({page,context})=>{
    const initial=structuredClone(fixture_state);initial.runtime={...initial.runtime,session_id:'s1',generation:1,phase:'running',busy:true};initial.projection.session_id='s1';
    const backend=await fixture(context,initial);let first_reads=0;let release_old!:()=>void;
    const old_wait=new Promise<void>(resolve=>{release_old=resolve;});
    await context.route('**/api/v1/sessions/s1/history',async route=>{
        first_reads++;const old=first_reads===1;if(old)await old_wait;
        return route.fulfill({contentType:'application/json',body:JSON.stringify({items:[{id:'same-id',role:'assistant',text:old?'尚未提交的旧内容':'合法已保存的新内容'}],next_cursor:null,warnings:old?['旧请求：工具交互未完整提交']:[]})});
    });
    await page.goto('/');await page.getByRole('button',{name:/理解项目的启动流程/}).click();await expect.poll(()=>first_reads).toBe(1);
    backend.set_state({...initial,seq:2,runtime:{...initial.runtime,phase:'idle',busy:false}});
    await expect(page.getByText('合法已保存的新内容')).toBeVisible();release_old();await page.waitForTimeout(250);
    await expect(page.getByText('合法已保存的新内容')).toBeVisible();await expect(page.getByText('尚未提交的旧内容')).toHaveCount(0);await expect(page.getByText('旧请求：工具交互未完整提交')).toHaveCount(0);expect(first_reads).toBe(2);
});
