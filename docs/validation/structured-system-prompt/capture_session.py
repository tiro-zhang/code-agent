"""人工验收采集器：记录真实请求元数据，不替换模型或给行为自动评分。"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True, help='待验收版本的 src 目录')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--trace', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    from mewcode.app import run
    from mewcode.providers import make_provider

    def record(data):
        with args.trace.open('a') as output:
            output.write(json.dumps(data, ensure_ascii=False) + '\n')

    def factory(config):
        provider = make_provider(config)
        sequence = 0
        create = provider.client.messages.create

        async def traced_create(**request):
            stable = {'system': request.get('system'), 'tools': request.get('tools')}
            digest = hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            record({'kind': 'wire_prefix', 'request': sequence, 'sha256': digest})
            raw = await create(**request)

            class Raw:
                async def __aenter__(self):
                    await raw.__aenter__()
                    return self

                async def __aexit__(self, *exc):
                    return await raw.__aexit__(*exc)

                async def __aiter__(self):
                    async for event in raw:
                        if event.type == 'message_start':
                            source = getattr(event.message, 'usage', None)
                            record({'kind': 'response_model', 'request': sequence, 'model': event.message.model})
                        else:
                            source = getattr(event, 'usage', None)
                        if source is not None:
                            record({'kind': 'raw_usage', 'request': sequence, 'event': event.type,
                                    'usage': source.model_dump(exclude_unset=True)})
                        yield event

            return Raw()

        provider.client.messages.create = traced_create
        original = provider.stream

        async def stream(messages, **options):
            nonlocal sequence
            sequence += 1
            record({'kind': 'request', 'request': sequence, 'tools': [t.name for t in options.get('tools', ())],
                    'context': messages[-1].content if messages[-1].role == 'context' else None})
            start, first = time.monotonic(), False
            async for event in original(messages, **options):
                if event.kind in ('thinking_delta', 'text_delta') and event.text and not first:
                    record({'kind': 'first_fragment', 'request': sequence, 'seconds': round(time.monotonic() - start, 3)})
                    first = True
                if event.kind == 'completed':
                    record({'kind': 'response', 'request': sequence, 'text': event.message.content,
                            'tools': [asdict(call) for call in event.message.tool_calls]})
                    if not first:
                        record({'kind': 'first_fragment', 'request': sequence, 'seconds': None})
                yield event

        provider.stream = stream
        record({'kind': 'config', 'model': config.model, 'protocol': config.protocol,
                'base_url': config.base_url, 'thinking': config.thinking})
        return provider

    return run(args.config, provider_factory=factory)


if __name__ == '__main__':
    raise SystemExit(main())
