import {beforeEach,afterEach,it,expect,vi} from 'vitest';
import {SyncScheduler} from '../src/sync';
import {fixture_state} from './fixtures';
import {performance_event} from './performance-fixtures';
import type {ServerState} from '../src/types';
beforeEach(()=>vi.useFakeTimers());afterEach(()=>vi.useRealTimers());
function setup(read:(signal:AbortSignal)=>Promise<ServerState>){
    const applied:ServerState[]=[],errors:unknown[]=[],lists=vi.fn();
    const scheduler=new SyncScheduler(fixture_state,read,async next=>{applied.push(next);},lists,error=>errors.push(error));
    return {scheduler,applied,errors,lists};
}
it('40Hz 普通通知每秒最多十次读取，无新通知不轮询，快照覆盖不越过接收游标',async()=>{
    let seq=1,reads=0;const {scheduler}=setup(async()=>{reads++;return {...fixture_state,seq};});
    for(let i=0;i<40;i++){seq++;scheduler.notify(performance_event(seq));await vi.advanceTimersByTimeAsync(25);}
    expect(reads).toBeLessThanOrEqual(10);await vi.advanceTimersByTimeAsync(100);const settled=reads;await vi.advanceTimersByTimeAsync(1000);expect(reads).toBe(settled);expect(scheduler.received_seq).toBe(41);scheduler.dispose();
});
it('100 个已覆盖事件合并为一次读取，已覆盖的列表通知仍刷新',async()=>{
    const read=vi.fn(async()=>({...fixture_state,seq:101}));const {scheduler,lists}=setup(read);
    for(let seq=2;seq<=101;seq++)scheduler.notify(performance_event(seq));await vi.advanceTimersByTimeAsync(100);
    expect(read).toHaveBeenCalledTimes(1);expect(scheduler.snapshot_seq).toBe(101);expect(scheduler.received_seq).toBe(101);
    scheduler.notify(performance_event(102,'sessions_changed'));await vi.advanceTimersByTimeAsync(100);expect(lists).toHaveBeenCalledTimes(1);
    scheduler.dispose();
});
it('已先取得更高快照后仍消费列表通知，接收游标只随实际事件推进',async()=>{
    const read=vi.fn(async()=>({...fixture_state,seq:101}));const {scheduler,lists}=setup(read);
    scheduler.notify(performance_event(2));await vi.advanceTimersByTimeAsync(100);expect(scheduler.received_seq).toBe(2);
    for(let seq=3;seq<50;seq++)scheduler.notify(performance_event(seq));scheduler.notify(performance_event(50,'sessions_changed'));
    await vi.advanceTimersByTimeAsync(100);expect(read).toHaveBeenCalledTimes(1);expect(lists).toHaveBeenCalledTimes(1);expect(scheduler.received_seq).toBe(50);scheduler.dispose();
});
it('操作后的显式核对不会复用操作之前开始的在途响应',async()=>{
    const pending:Array<(state:ServerState)=>void>=[];const {scheduler}=setup(()=>new Promise(resolve=>pending.push(resolve)));
    scheduler.notify(performance_event(2));await vi.advanceTimersByTimeAsync(100);
    let resolved=false;const confirmed=scheduler.synchronize().then(()=>{resolved=true;});pending[0]({...fixture_state,seq:2});await vi.advanceTimersByTimeAsync(0);
    expect(pending).toHaveLength(2);expect(resolved).toBe(false);pending[1]({...fixture_state,seq:3});await confirmed;expect(resolved).toBe(true);scheduler.dispose();
});
it('旧的同实例替换基线不倒退快照覆盖，卸载后不接受迟到响应',async()=>{
    let finish:(state:ServerState)=>void=()=>{};const {scheduler,applied}=setup(()=>new Promise(resolve=>{finish=resolve;}));
    await scheduler.replace({...fixture_state,seq:10});await scheduler.replace({...fixture_state,seq:8});expect(scheduler.snapshot_seq).toBe(10);
    scheduler.notify(performance_event(11));await vi.advanceTimersByTimeAsync(100);scheduler.dispose();finish({...fixture_state,seq:11});await vi.advanceTimersByTimeAsync(100);expect(applied.some(state=>state.seq===11)).toBe(false);
});
it('读取期间最后的新通知会补读，优先审批不等待普通窗口且同实例只有一个在途请求',async()=>{
    let finish:(state:ServerState)=>void=()=>{};let calls=0;
    const {scheduler}=setup(async()=>{calls++;return new Promise(resolve=>{finish=resolve;});});
    scheduler.notify(performance_event(2));await vi.advanceTimersByTimeAsync(100);expect(calls).toBe(1);
    scheduler.notify(performance_event(3,'approval'));expect(calls).toBe(1);finish({...fixture_state,seq:2});await vi.advanceTimersByTimeAsync(0);expect(calls).toBe(2);
    finish({...fixture_state,seq:3});await vi.advanceTimersByTimeAsync(0);expect(scheduler.snapshot_seq).toBe(3);scheduler.dispose();
});
it('明确基线替换丢弃旧请求并补列表，卸载不接受迟到结果',async()=>{
    let finish:(state:ServerState)=>void=()=>{};const {scheduler,applied,lists}=setup(()=>new Promise(resolve=>{finish=resolve;}));
    scheduler.notify(performance_event(2));await vi.advanceTimersByTimeAsync(100);
    await scheduler.replace({...fixture_state,server_instance_id:'new',seq:8});finish({...fixture_state,seq:100});await vi.advanceTimersByTimeAsync(100);
    expect(applied.map(state=>state.server_instance_id)).toEqual(['new']);expect(lists).toHaveBeenCalledTimes(1);expect(scheduler.received_seq).toBe(8);scheduler.dispose();
});
it('失败恢复有间隔且有限，不把失败当作完成，控制显式同步仍可重试',async()=>{
    const read=vi.fn(async()=>{throw Error('offline');});const {scheduler,errors}=setup(read);scheduler.notify(performance_event(2));
    await vi.advanceTimersByTimeAsync(100);expect(read).toHaveBeenCalledTimes(1);await vi.advanceTimersByTimeAsync(999);expect(read).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(10000);expect(read.mock.calls.length).toBeLessThanOrEqual(3);expect(scheduler.snapshot_seq).toBe(1);expect(errors.length).toBeGreaterThan(0);
    await expect(scheduler.synchronize()).rejects.toThrow('offline');scheduler.dispose();
});
it('三次失败后存活 SSE 的新通知能以退避恢复，停止通知不会永久失效',async()=>{
    let offline=true,calls=0;const {scheduler}=setup(async()=>{calls++;if(offline)throw Error('offline');return {...fixture_state,seq:9};});
    scheduler.notify(performance_event(2));await vi.advanceTimersByTimeAsync(2500);expect(calls).toBe(3);offline=false;scheduler.notify(performance_event(9,'approval'));
    // 首次读取在 100 ms，三次失败发生于 100/1100/2100 ms，下次最早 3100 ms。
    await vi.advanceTimersByTimeAsync(599);expect(calls).toBe(3);await vi.advanceTimersByTimeAsync(1);expect(calls).toBe(4);expect(scheduler.snapshot_seq).toBe(9);scheduler.dispose();
});
