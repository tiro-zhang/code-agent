import {fixture_state,fixture_sessions} from './fixtures';
import type {ServerState,Envelope,Message} from '../src/types';

// 固定语料供计数回归和生产浏览器验收共用。
const block=Array.from({length:10},(_,i)=>`说明 ${i}：固定历史正文。\n\n\`\`\`python\n    print("history ${i}")\n\`\`\`\n`).join('');
export const performance_history:Message[]=Array.from({length:100},(_,i)=>({id:`h${i}`,role:'assistant',text:block+'x'.repeat(Math.max(0,1229-new TextEncoder().encode(block).length)),complete:true}));
const long_block='当前回答\n\n```python\n    print("stream")\n```\n\n';
export const performance_text=long_block.repeat(Math.ceil(131072/new TextEncoder().encode(long_block).length));
export const performance_sessions=fixture_sessions;
export function performance_state():ServerState{return {...structuredClone(fixture_state),runtime:{...fixture_state.runtime,session_id:'s1',generation:1,state_version:1,phase:'running',busy:true,run_id:'r1'},projection:{session_id:'s1',messages:[...performance_history,{id:'current',role:'assistant',text:performance_text,complete:false}],runs:[],warnings:[]}};}
export function performance_event(seq:number,kind='agent',payload:unknown={kind:'text_delta'}):Envelope{return {server_instance_id:'test-instance',seq,session_id:'s1',runtime_generation:1,run_id:'r1',kind,payload};}
