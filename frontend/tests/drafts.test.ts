import {beforeEach,it,expect,vi} from 'vitest';
import {DraftStore} from '../src/drafts';
beforeEach(()=>sessionStorage.clear());
it('草稿按项目会话隔离且同标签页刷新恢复，不匹配文字或版本的回执不能清除',()=>{
    const store=new DraftStore(()=>sessionStorage);
    store.edit('p','a','原稿');const submitted=store.read('p','a');
    store.edit('p','b','B 的草稿');store.edit('q','a','另一个项目');
    store.confirm('p','a',{...submitted,text:'等长'});expect(store.read('p','a').text).toBe('原稿');
    store.edit('p','a','后续编辑');store.confirm('p','a',submitted);
    expect(new DraftStore(()=>sessionStorage).read('p','a')).toEqual({text:'后续编辑',version:2});
    expect(store.read('p','b').text).toBe('B 的草稿');expect(store.read('q','a').text).toBe('另一个项目');
    const latest=store.read('p','a');store.confirm('p','a',latest);expect(store.read('p','a')).toEqual({text:'',version:3});
});
it('仅通知所属草稿订阅，存储失败仍保留可编辑文本并提示',()=>{
    const storage={getItem:()=>null,setItem:()=>{throw Error('full');}} as unknown as Storage;
    const store=new DraftStore(()=>storage);const a=vi.fn(),b=vi.fn();
    const unsubscribe=store.subscribe('p','a',a);store.subscribe('p','b',b);
    store.edit('p','a','不会丢失');expect(a).toHaveBeenCalledTimes(1);expect(b).not.toHaveBeenCalled();
    expect(store.read('p','a').text).toBe('不会丢失');expect(store.warning).toContain('复制');
    unsubscribe();store.edit('p','a','继续编辑');expect(a).toHaveBeenCalledTimes(1);
});
