import type {Envelope} from './types';
export async function* parse_sse(stream:ReadableStream<Uint8Array>):AsyncGenerator<Envelope>{
    const reader=stream.getReader();const decoder=new TextDecoder();let buffer='';
    try{
        while(true){
            const {value,done}=await reader.read();
            buffer+=decoder.decode(value,{stream:!done});
            let match:RegExpExecArray|null;
            while((match=/\r?\n\r?\n/.exec(buffer))){
                const block=buffer.slice(0,match.index);buffer=buffer.slice(match.index+match[0].length);
                const data=block.split(/\r?\n/).filter(line=>line.startsWith('data:')).map(line=>line.slice(5).replace(/^ /,'')).join('\n');
                if(data)yield JSON.parse(data);
            }
            if(done)break;
            if(buffer.length>16*1024*1024)throw new Error('事件超过显示限额，需要重新同步');
        }
    }finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
}
