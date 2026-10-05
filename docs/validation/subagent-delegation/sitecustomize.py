"""本次真实验收的旁路采集；不保存请求正文、URL、请求头或密钥。"""

import hashlib
import itertools
import json
import os

_sequence = itertools.count(1)
_trace = os.environ.get('MEWCODE_VALIDATION_TRACE')


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _record(value):
    with open(_trace, 'a') as output:
        output.write(json.dumps(value, ensure_ascii=False) + '\n')


def _capture(original, protocol):
    async def create(self, *args, **options):
        number = next(_sequence)
        prefix = {key: options.get(key) for key in ('tools', 'system')}
        messages = options.get('messages', [])
        _record({'kind':'request', 'request':number, 'protocol':protocol, 'model':options.get('model'),
                 'prefix':_digest(prefix), 'messages':[
                     {'role':message['role'], 'digest':_digest(message),
                      'blocks':[_digest(block) for block in message['content']] if isinstance(message.get('content'), list) else []}
                     for message in messages],
                 'tool_names':[tool.get('name', tool.get('function', {}).get('name')) for tool in options.get('tools', ())]})
        raw = await original(self, *args, **options)
        class Stream:
            async def __aenter__(self):
                await raw.__aenter__()
                return self
            async def __aexit__(self, *exc):
                return await raw.__aexit__(*exc)
            def __getattr__(self, name):
                return getattr(raw, name)
            async def __aiter__(self):
                async for event in raw:
                    usage = getattr(event, 'usage', None) or getattr(getattr(event, 'message', None), 'usage', None)
                    if usage is not None:
                        _record({'kind':'raw_usage', 'request':number,
                                 'event':getattr(event, 'type', 'chunk'),
                                 'usage':usage.model_dump(exclude_unset=True)})
                    yield event
        return Stream()
    return create


if _trace:
    from openai.resources.chat.completions import AsyncCompletions
    from anthropic.resources.messages import AsyncMessages
    AsyncCompletions.create = _capture(AsyncCompletions.create, 'openai')
    AsyncMessages.create = _capture(AsyncMessages.create, 'anthropic')
