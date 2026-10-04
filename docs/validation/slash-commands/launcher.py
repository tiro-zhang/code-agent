"""真实服务验收：仅记录请求边界并隔离用户记忆；不替换模型或工具。"""
import json
import os
import sys
from pathlib import Path
from mewcode.app import run
from mewcode.providers import make_provider

ROOT = Path(os.environ['MEWCODE_E2E_ROOT'])

class ObservedProvider:
    def __init__(self, config):
        self.config = config
        self.inner = make_provider(config)
    async def stream(self, messages, **options):
        row = {'tools': [tool.name for tool in options.get('tools', ())],
               'users': [message.content for message in messages if message.role == 'user']}
        with (ROOT / 'requests.jsonl').open('a') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        source = self.inner.stream(messages, **options)
        try:
            async for event in source:
                yield event
        finally:
            await source.aclose()
    async def aclose(self):
        await self.inner.aclose()

if __name__ == '__main__':
    raise SystemExit(run('.env', provider_factory=ObservedProvider, memory_enabled=False, user_root=ROOT/'user', resume='latest' if '--resume' in sys.argv else None))
