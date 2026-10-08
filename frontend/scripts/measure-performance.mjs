import {createServer} from 'node:http';
import {readFileSync,writeFileSync} from 'node:fs';
import {resolve,extname} from 'node:path';
import {createRequire} from 'node:module';
import {cpus,platform,release} from 'node:os';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';

// 使用正式静态资源和本机模拟接口，不访问模型或执行工具。
const root=process.env.MEW_PERF_ROOT||resolve(fileURLToPath(new URL('../..',import.meta.url)));
const require=createRequire(resolve(root,'frontend/package.json'));
const {chromium}=require('playwright');
const static_dir=resolve(process.argv[2]||`${root}/src/mewcode/web/static`);
const output=process.argv[3]||'/private/tmp/mewcode-web-performance/result.json';
const {build}=require('esbuild');
const corpus=await build({entryPoints:[resolve(root,'frontend/tests/performance-fixtures.ts')],bundle:true,write:false,format:'esm',platform:'node'});
const {performance_history:history,performance_text:long_text,performance_sessions:sessions}=await import('data:text/javascript;base64,'+Buffer.from(corpus.outputFiles[0].text).toString('base64'));
let seq=1,generation=1,run_id='run',complete=false,phase='running',stage='loading',reads=0,bytes=0,notifications=0;
const state_requests=[];
let clients=new Set();
const snapshot=()=>({server_instance_id:'performance',seq,project:{root:'/workspace/performance',key:'performance',model:'mock'},runtime:{session_id:'s1',generation,state_version:seq,phase,busy:phase!=='idle',run_id,mode:'execute',permission_mode:'default',activities:[]},projection:{session_id:'s1',messages:[...history,{id:'current',role:'assistant',text:long_text+`\n片段 ${seq}`,complete}],runs:[],warnings:[]},approvals:[],commands:[]});
function emit(kind='agent',payload={kind:'text_delta'}){seq++;notifications++;for(const stream of clients)stream.write(`data: ${JSON.stringify({server_instance_id:'performance',seq,session_id:'s1',runtime_generation:generation,run_id,kind,payload})}\n\n`);}
const server=createServer(async(req,res)=>{
    const path=new URL(req.url,'http://localhost').pathname;
    function json(value,status=200){const body=JSON.stringify(value);if(path==='/api/v1/state'){reads++;bytes+=Buffer.byteLength(body);state_requests.push({at:Date.now(),stage,seq,bytes:Buffer.byteLength(body)});}res.writeHead(status,{'Content-Type':'application/json'});res.end(body);}
    if(path==='/api/v1/events'){res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});res.write(': connected\n\n');clients.add(res);req.on('close',()=>clients.delete(res));return;}
    if(path==='/api/v1/state')return json(snapshot());
    if(path==='/api/v1/clients')return json({client_id:'perf-client',next_sequence:1,server_instance_id:'performance'});
    if(path==='/api/v1/sessions')return json({items:sessions,next_cursor:null});
    if(path.endsWith('/history'))return json({items:history,next_cursor:null,warnings:[]});
    if(path.endsWith('/controls')){let body='';for await(const chunk of req)body+=chunk;const operation=JSON.parse(body);if(operation.generation!==generation||operation.state_version!==seq)return json({error:{code:'stale',message:'停止身份过期'}},409);phase='cancelling';emit('state',{});return json({operation_id:`stop-${seq}`,operation:{client_id:operation.client_id,sequence:operation.sequence,next_sequence:operation.sequence+1}},202);}
    if(path.startsWith('/api/'))return json({error:{code:'missing',message:'模拟接口不存在'}},404);
    try{const file=resolve(static_dir,path==='/'?'index.html':`.${path}`);if(!file.startsWith(static_dir+'/'))throw Error();const content=readFileSync(file);res.writeHead(200,{'Content-Type':{'.html':'text/html','.js':'application/javascript','.css':'text/css','.json':'application/json'}[extname(file)]||'application/octet-stream'});res.end(content);}catch{res.writeHead(404);res.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    await context.addInitScript(()=>{
        sessionStorage.setItem('mewcode:selected:performance','s1');
        window.perf_stage='loading';window.perf_stages=[{stage:'loading',start:0}];window.perf_samples={input:[],navigation:[],stop:[],finish:[],probe:[],feedback:[],long_tasks:[]};
        new PerformanceObserver(list=>{for(const entry of list.getEntries()){const stage=[...window.perf_stages].reverse().find(item=>item.start<=entry.startTime)?.stage;window.perf_samples.long_tasks.push({stage,start:entry.startTime,duration:entry.duration});}}).observe({type:'longtask',buffered:true});
        const original=window.fetch;window.fetch=(...args)=>{if(String(args[0]).endsWith('/controls')&&window.stop_started!==undefined){window.perf_samples.stop.push(performance.now()-window.stop_started);delete window.stop_started;}return original(...args);};
        // CDP 提供事件发生时刻，事件分发前主线程排队也包含在内。
        document.addEventListener('keydown',event=>{
            if(!event.isTrusted||event.key!=='x'||event.target.tagName!=='TEXTAREA')return;
            const input=event.target,start=event.timeStamp,prior=input.value,stage=window.perf_stage;
            requestAnimationFrame(()=>requestAnimationFrame(()=>{
                const duration=performance.now()-start;window.perf_samples[stage==='finish'?'finish':stage==='probe'?'probe':'input'].push(duration);
                window.perf_samples.feedback.push({kind:'input',stage,start,duration,changed:input.value!==prior,mode:document.querySelector('[data-message-id="current"] .stream-text')?'plain':'formatted'});
            }));
        },true);
        document.addEventListener('click',event=>{
            if(!event.isTrusted)return;const nav=event.target.closest('.session-button'),stop=event.target.closest('.occupancy button.danger');
            if(stop){if(stop.disabled)throw Error('停止不可用');window.stop_started=event.timeStamp;}
            if(nav){const start=event.timeStamp,stage=window.perf_stage;requestAnimationFrame(()=>requestAnimationFrame(()=>{const duration=performance.now()-start;window.perf_samples.navigation.push(duration);window.perf_samples.feedback.push({kind:'navigation',stage,start,duration,selected:nav.getAttribute('aria-current')==='page'});}));}
        },true);
    });
    const page=await context.newPage();const origin=`http://127.0.0.1:${server.address().port}`;
    await page.goto(origin);await page.waitForSelector('[data-message-id="current"]',{timeout:120000});
    await page.waitForFunction(()=>document.querySelector('.connection')?.textContent==='已连接');
    const cdp=await context.newCDPSession(page);await cdp.send('Performance.enable');
    const center=async selector=>{const box=await page.locator(selector).boundingBox();if(!box)throw Error('缺少采样目标');return {x:box.x+box.width/2,y:box.y+box.height/2};};
    const input_point=await center('textarea'),nav_points=[await center('.session-button >> nth=0'),await center('.session-button >> nth=1')];
    const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
    // 不等待前一输入处理完成；CDP 消息按顺序排队，保留外部到达的时间戳。
    const click=(point,timestamp=Date.now()/1000)=>Promise.all([
        cdp.send('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',clickCount:1,timestamp}),
        cdp.send('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',clickCount:1,timestamp})]);
    const type=()=>{const timestamp=Date.now()/1000;return Promise.all([click(input_point,timestamp),
        cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'x',code:'KeyX',windowsVirtualKeyCode:88,text:'x',timestamp}),
        cdp.send('Input.dispatchKeyEvent',{type:'keyUp',key:'x',code:'KeyX',windowsVirtualKeyCode:88,timestamp})]);};
    const set_stage=async value=>{stage=value;await page.evaluate(value=>{window.perf_stage=value;window.perf_stages.push({stage:value,start:performance.now()});},value);};
    await click(input_point);await set_stage('probe');
    const blocked=page.evaluate(()=>{const end=performance.now()+180;while(performance.now()<end){}});await sleep(30);await type();await blocked;
    await page.waitForFunction(()=>window.perf_samples.probe.length===1);
    const queue_probe=await page.evaluate(()=>window.perf_samples.probe[0]);if(queue_probe<100)throw Error(`输入排队校验未通过: ${queue_probe}`);
    await set_stage('streaming');const began=Date.now();const interval=setInterval(()=>emit(),25),arrivals=[];
    try{for(let i=0;i<50;i++){arrivals.push(type());if(i<20)arrivals.push(click(nav_points[i%2]));await sleep(200);}await Promise.all(arrivals);}finally{clearInterval(interval);}
    const stream_duration=Date.now()-began;
    await click(nav_points[0]);
    await page.waitForSelector('[data-message-id="current"]');
    const metrics_before=await cdp.send('Performance.getMetrics');
    const finish_started=await page.evaluate(()=>performance.now());
    await set_stage('finish');complete=true;phase='idle';emit('agent',{kind:'task_finished'});
    const finishing=[];for(let i=0;i<10;i++){finishing.push(type());await sleep(30);}await Promise.all(finishing);
    await page.waitForFunction(()=>document.querySelector('[data-message-id="current"] .markdown code')&&!document.querySelector('[data-message-id="current"] .stream-text'));
    const format_ready_at=await page.evaluate(()=>performance.now());
    for(let i=0;i<5;i++){await type();await sleep(50);}
    await click(nav_points[0]);await set_stage('controls');complete=false;
    for(let i=0;i<20;i++){
        generation++;run_id=`run-${i+1}`;phase='running';emit('state',{});
        await page.waitForFunction(()=>{const button=document.querySelector('.occupancy button.danger');return button&&!button.disabled&&button.textContent==='停止';});
        const old_count=await page.evaluate(()=>window.perf_samples.stop.length);await click(await center('.occupancy button.danger'));
        await page.waitForFunction(count=>window.perf_samples.stop.length>count,old_count);
        await page.waitForFunction(()=>{const button=document.querySelector('.occupancy button.danger');return button&&!button.disabled&&button.textContent==='正在停止';});
    }
    const metrics_after=await cdp.send('Performance.getMetrics');
    await page.waitForFunction(()=>window.perf_samples.input.length===50&&window.perf_samples.finish.length===15&&window.perf_samples.navigation.length===22&&window.perf_samples.stop.length===20);
    const samples=await page.evaluate(()=>window.perf_samples);
    const summary=Object.fromEntries(['input','navigation','stop','finish'].map(key=>{const values=[...samples[key],...(key==='input'?samples.finish:[])].sort((a,b)=>a-b);return [key,{count:values.length,p95:values[Math.ceil(values.length*.95)-1],max:Math.max(...values)}];}));
    const correctness={input_feedback:samples.feedback.filter(item=>item.kind==='input').every(item=>item.changed),navigation_feedback:samples.feedback.filter(item=>item.kind==='navigation').every(item=>item.selected),valid_stops:samples.stop.length===20,final_formatted:true};
    const report={date:new Date().toISOString(),measurement:{input:'原生 keyDown 时间戳至两次 RAF；包含焦点和主线程排队',navigation:'原生 click 时间戳至两次 RAF',stop:'原生 click 时间戳至 fetch 调用',queue_probe_ms:queue_probe,arrival_interval_ms:200,probe_excluded_from_reference:true},correctness,device:{os:`${platform()} ${release()}`,cpu:cpus()[0].model,browser:browser.version(),node:process.version},build_sha256:createHash('sha256').update(readFileSync(resolve(static_dir,'manifest.json'))).digest('hex'),scenario:{history_count:100,history_bytes_each:Buffer.byteLength(history[0].text),current_bytes:Buffer.byteLength(long_text),notifications_per_second:40,stream_duration_ms:stream_duration,viewport:[1440,1000]},finish_started,format_ready_at,metrics_before,metrics_after,summary,state_reads:reads,state_response_bytes:bytes,notifications,state_requests,samples};
    writeFileSync(output,JSON.stringify(report,null,2)+'\n');if(process.env.MEW_PERF_ASSERT==='1'&&(['input','navigation','stop'].some(key=>summary[key].p95>100)||Object.values(correctness).includes(false)))process.exitCode=1;console.log(JSON.stringify({...report,samples:undefined,state_requests:undefined,metrics_before:undefined,metrics_after:undefined}));
}finally{await browser.close();for(const stream of clients)stream.end();await new Promise(resolve=>server.close(resolve));}
