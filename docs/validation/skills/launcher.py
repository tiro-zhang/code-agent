"""真实服务验收：只观察请求，不替换 Provider、工具或权限。"""

import json
import os
from pathlib import Path
import sys

from mewcode.app import run
from mewcode.providers import make_provider

ROOT = Path(os.environ['MEWCODE_E2E_ROOT'])


class ObservedProvider:
    def __init__(self, config):
        self.config = config
        self.inner = make_provider(config)

    async def stream(self, messages, **options):
        row = {'model': self.config.model, 'tools': [t.name for t in options.get('tools', ())],
               'roles': [m.role for m in messages],
               'users': [m.content for m in messages if m.role == 'user'],
               'contexts': [{'kind': m.context_kind, 'text': m.content} for m in messages if m.role == 'context']}
        with (ROOT / 'requests.jsonl').open('a') as output:
            output.write(json.dumps(row, ensure_ascii=False) + '\n')
        source = self.inner.stream(messages, **options)
        try:
            async for event in source:
                yield event
        finally:
            await source.aclose()

    async def aclose(self):
        await self.inner.aclose()


if __name__ == '__main__':
    raise SystemExit(run('.env', provider_factory=ObservedProvider, memory_enabled=False,
                        user_root=ROOT / 'user', resume='latest' if '--resume' in sys.argv else None))
