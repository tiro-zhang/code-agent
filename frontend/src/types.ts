export type Phase = 'unloaded'|'preparing'|'idle'|'running'|'awaiting_approval'|'background'|'maintenance'|'cancelling'|'blocked'|'closing';
export interface Message { id:string; role:string; text:string; kind?:string; run_id?:string; seq?:number; complete?:boolean; truncated?:boolean; call?:unknown; result?:unknown; result_id?:string; }
export interface ToolCall { id:string;run_id?:string;parent_run_id?:string;iteration?:number;name:string;arguments:unknown;status:string;result?:Record<string,unknown>;result_id?:string;truncated?:boolean; }
export interface Run { id:string;parent_run_id?:string;title?:string;phase:string;reason?:string;text?:string;thinking?:string;calls:ToolCall[];usage?:Record<string,unknown>|null;usage_by_purpose?:Record<string,unknown>; }
export interface Approval {id:string;session_id:string;generation:number;run_id:string;tool:string;reason:string;expires_at:string|number;}
export interface ApprovalPage extends Approval {root?:string;mode?:string;sections:string[];section:string;content:string;next_offset:number|null;total_bytes:number;}
export interface SessionItem {id:string;title:string;last_activity:string;message_count:number;active:boolean;recoverable:boolean;warnings:string[];}
export interface Command {name:string;aliases:string[];description:string;usage:string;kind:string;}
export interface ServerState {
    server_instance_id:string;seq:number;
    project:{root:string;key:string;model:string};
    runtime:{session_id:string|null;generation:number;state_version:number;phase:Phase;busy:boolean;run_id:string|null;mode:string;permission_mode:string;reason?:string;error?:string|null;activities:unknown[]};
    projection:{session_id:string|null;messages:Message[];runs:Run[];warnings:string[]};
    approvals:Approval[];commands:Command[];
}
export interface Envelope {server_instance_id:string;seq:number;session_id:string|null;runtime_generation:number;run_id:string|null;kind:string;payload:unknown;}
export interface Draft {text:string;version:number;}
export interface SubmittedDraft extends Draft {session_id:string;}
export interface HistoryPage {items:Message[];next_cursor:string|null;warnings:string[];}
export interface ResultPage {id:string;session_id:string;content:string;next_cursor:string|null;truncated:boolean;warnings:string[];}
