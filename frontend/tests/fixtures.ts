import type { ServerState } from '../src/types';
export const fixture_state:ServerState = {
    server_instance_id:'test-instance',seq:1,project:{root:'/workspace/demo',key:'demo',model:'test-model'},
    runtime:{session_id:null,generation:0,state_version:0,phase:'unloaded',busy:false,run_id:null,mode:'execute',permission_mode:'default',activities:[]},
    projection:{session_id:null,messages:[],runs:[],warnings:[]},approvals:[],
    commands:[{name:'help',aliases:['?'],description:'显示帮助',usage:'/help',kind:'query'}],
};
export const fixture_sessions = [{id:'s1',title:'理解项目的启动流程',last_activity:'2026-10-07T12:00:00Z',message_count:2,active:false,recoverable:true,warnings:[]},{id:'s2',title:'另一个会话',last_activity:'2026-10-06T10:00:00Z',message_count:0,active:false,recoverable:true,warnings:[]}];
