import { expect, it } from 'vitest';
import { parse_sse } from '../src/stream';
it('跨网络块的中文与多行SSE只在完整消息到达后解析', async () => {
    const bytes = new TextEncoder().encode('id: 7\ndata: {"seq":7,\ndata: "kind":"state","字":"你"}\n\n:ping\n\nid: 8\ndata: {"seq":8}\n\n');
    const stream = new ReadableStream<Uint8Array>({start(c){for(let i=0;i<bytes.length;i+=3)c.enqueue(bytes.slice(i,i+3));c.close();}});
    const events:unknown[]=[];
    for await(const event of parse_sse(stream)) events.push(event);
    expect(events).toEqual([{seq:7,kind:'state',字:'你'},{seq:8}]);
});
