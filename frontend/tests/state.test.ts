import { describe, it, expect } from 'vitest';
import { accept_snapshot, should_send, clear_accepted_draft, input_error, merge_messages, empty_state } from '../src/state';

describe('用户输入与回执边界', () => {
    it('组合输入与普通 Enter 不发送，明确组合快捷键才发送', () => {
        expect(should_send({key:'Enter',ctrlKey:true,metaKey:false,isComposing:true})).toBe(false);
        expect(should_send({key:'Enter',ctrlKey:false,metaKey:false,isComposing:false})).toBe(false);
        expect(should_send({key:'Enter',ctrlKey:true,metaKey:false,isComposing:false})).toBe(true);
        expect(should_send({key:'Enter',ctrlKey:false,metaKey:true,isComposing:false})).toBe(true);
    });
    it('回执只清除提交时同版本草稿，即使新版本文字相同也保留', () => {
        expect(clear_accepted_draft({text:'后来的编辑',version:2},{text:'原文',version:1})).toEqual({text:'后来的编辑',version:2});
        expect(clear_accepted_draft({text:'原文',version:2},{text:'原文',version:1}).text).toBe('原文');
        expect(clear_accepted_draft({text:'原文',version:1},{text:'原文',version:1}).text).toBe('');
    });
    it('按UTF-8字节拒绝过大输入且保留合法的缩进末尾换行', () => {
        expect(input_error('  \n ')).toMatch(/内容/);
        expect(input_error('中'.repeat(87382))).toMatch(/256/);
        expect(input_error('    print(1)\n')).toBe('');
    });
});

describe('服务端权威投影', () => {
    it('重复及倒序快照不重复追加，新的完整快照替换临时正文', () => {
        const first = {...empty_state,server_instance_id:'a',seq:3,projection:{...empty_state.projection,messages:[{id:'m',role:'assistant',text:'一半'}]}};
        expect(accept_snapshot(first,{...first,seq:2})).toBe(first);
        expect(accept_snapshot(first,{...first})).toBe(first);
        const next = accept_snapshot(first,{...first,seq:4,projection:{...first.projection,messages:[{id:'m',role:'assistant',text:'完成'}]}});
        expect(next.projection.messages).toEqual([{id:'m',role:'assistant',text:'完成'}]);
    });
    it('新实例的小序号替换旧实例，不复活旧回复', () => {
        const state = accept_snapshot({...empty_state,server_instance_id:'old',seq:100},{...empty_state,server_instance_id:'new',seq:1});
        expect(state.server_instance_id).toBe('new'); expect(state.seq).toBe(1);
    });
    it('分页消息按身份合并并保留最新完整正文', () => {
        expect(merge_messages([{id:'a',role:'user',text:'old'}],[{id:'a',role:'user',text:'new'},{id:'b',role:'assistant',text:'ok'}]).map(x=>x.text)).toEqual(['new','ok']);
    });
});

describe('有界页面历史缓存',()=>{
    it('过多或过大的历史页释放较早正文并明确标注缺失，保留后续游标',async()=>{
        const {bound_history}=await import('../src/state');
        const page=bound_history({items:Array.from({length:4},(_,i)=>({id:String(i),role:'assistant',text:'长'.repeat(30)})),next_cursor:'next',warnings:[]},undefined,250);
        expect(page.items.map(item=>item.id)).toEqual(['3']);
        expect(page.next_cursor).toBe('next');expect(page.warnings.join('')).toMatch(/缓存/);
    });
});

describe('完成后的历史首页刷新',()=>{
    it('替换读取时警告和同身份正文，保留已分页内容及原快照游标',async()=>{
        const {refresh_history_page}=await import('../src/state');
        const previous={items:[{id:'m1',role:'assistant',text:'旧正文'},{id:'m2',role:'user',text:'已读第二页'}],next_cursor:'old-page-3',warnings:['工具交互未完整提交']};
        const fresh={items:[{id:'m1',role:'assistant',text:'已保存正文'},{id:'m3',role:'tool',text:'真实已提交结果'}],next_cursor:'new-page-2',warnings:[]};
        const updated=refresh_history_page(previous,fresh,true);
        expect(updated.items.map(item=>item.text)).toEqual(['已保存正文','已读第二页','真实已提交结果']);
        expect(updated.next_cursor).toBe('old-page-3');expect(updated.warnings).toEqual([]);
        expect(refresh_history_page(previous,fresh,false).next_cursor).toBe('new-page-2');
    });
});
