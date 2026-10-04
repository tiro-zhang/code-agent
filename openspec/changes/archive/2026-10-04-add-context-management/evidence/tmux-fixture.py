"""真实服务验收夹具：仅预装合成历史并记录脱敏事件，不替换模型或工具。"""
import json
import os
from pathlib import Path
from mewcode.session import ChatSession
from mewcode.types import Message

original_init = ChatSession.__init__
original_ask = ChatSession.ask
original_compact = ChatSession.compact

def initialize(self, *args, **kwargs):
    original_init(self, *args, **kwargs)
    self.history[:] = [Message('user', '历史验收约束：只修改 demo.py；保留 KEEP 原样；未运行测试不得说通过。'),
        Message('assistant', '合成历史背景，只用于制造上下文压力，不代表真实执行记录。\n' + '历史资料用于压力测试，没有文件修改和验证事实。' * 1550)]
    for i in range(3):
        self.history.extend([Message('user', f'最近讨论 {i}：只做上下文压力数据，不执行。'),
            Message('assistant', '以下是合成背景数据：' + '保留近期原文，历史内容不能当作当前文件事实。' * 160)])

def record(event):
    data = {'kind':event.kind,'purpose':event.purpose,'phase':event.phase,'iteration':event.iteration,
            'reason':event.reason,'before':event.estimated_before,'after':event.estimated_after,
            'spilled':event.spilled,'failures':event.failures,'tool':event.tool_name}
    if event.usage:
        data['usage'] = {'total_input_tokens':event.usage.total_input_tokens,'output_tokens':event.usage.output_tokens}
    if event.kind == 'tool_result':
        data['ok'] = event.result.ok
    with open('events.jsonl','a') as file:
        file.write(json.dumps(data, ensure_ascii=False)+'\n')

async def ask(self, *args, **kwargs):
    async for event in original_ask(self,*args,**kwargs):
        record(event)
        yield event
    Path('history-metadata.json').write_text(json.dumps([
        {'role':m.role,'kind':m.context_kind,'cache_path':m.cache_path,
         'content':m.content if m.context_kind in {'summary','boundary'} else ''} for m in self.history], ensure_ascii=False, indent=2))

async def compact(self,*args,**kwargs):
    async for event in original_compact(self,*args,**kwargs):
        record(event)
        yield event

ChatSession.__init__ = initialize
ChatSession.ask = ask
ChatSession.compact = compact
