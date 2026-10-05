"""创建只含合成输入的真实模型验收夹具，不复制模型凭据。"""

from pathlib import Path
import json
import sys

import yaml


def prepare(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / '.mewcode').mkdir(exist_ok=True)
    python = str(Path(sys.executable).absolute())
    command = lambda name: f'{python} hook_actions.py {name}'
    atom = lambda field, value: {'field': field, 'match': 'exact', 'value': value}
    rules = [
        {'event': 'session.start', 'once': True, 'action': {'type': 'command', 'command': command('startup')}},
        {'event': 'message.before_request', 'once': True, 'action': {'type': 'prompt', 'text': '本次回复末尾加上标记 HOOK_CONTEXT_OK。'}},
        {'event': 'tool.before', 'if': {'all': [atom('tool.name', 'write_file'), atom('tool.target_path', 'protected.txt')]},
         'action': {'type': 'command', 'command': command('before')}},
        {'event': 'tool.after', 'if': {'all': [atom('tool.name', 'write_file'), atom('tool.result.ok', True)]},
         'action': {'type': 'command', 'command': command('format')}},
        {'event': 'turn.end', 'once': True, 'async': True, 'action': {'type': 'command', 'command': command('background')}},
        {'event': 'tool.after', 'if': {'all': [atom('tool.name', 'read_file'), atom('tool.arguments.path', 'slow.txt')]},
         'action': {'type': 'command', 'command': command('timeout'), 'timeout_seconds': 1}},
        {'event': 'tool.before', 'if': {'all': [atom('tool.name', 'read_file'), atom('tool.arguments.path', 'cancel.txt')]},
         'action': {'type': 'command', 'command': command('cancel')}},
        {'event': 'mode.changed', 'action': {'type': 'command', 'command': command('mode')}},
    ]
    (root / '.mewcode/hooks.yaml').write_text(yaml.safe_dump({'version': 1, 'hooks': rules}, allow_unicode=True))
    allowed = [command(name) for name in ('before', 'format', 'background', 'timeout', 'cancel')]
    (root / '.mewcode/permissions.yaml').write_text(yaml.safe_dump({'version': 1, 'rules': [
        {'effect': 'allow', 'rule': f'Bash({text})', 'match': 'exact'} for text in allowed]}))
    for name in ('slow.txt', 'cancel.txt'):
        (root / name).write_text('controlled input\n')
    (root / 'hook_actions.py').write_text('''import json, os, pathlib, sys, time
event = json.load(sys.stdin)
kind = sys.argv[1]
with pathlib.Path('actions.jsonl').open('a') as output:
    output.write(json.dumps({'action': kind, 'event': event['event'], 'mode': event['mode'],
        'run_id': event.get('run_id'), 'tool': event.get('tool', {}).get('call_id')}) + '\\n')
if kind == 'startup':
    pathlib.Path('startup.done').touch()
elif kind == 'before':
    print(json.dumps({'decision': 'deny', 'reason': 'protected.txt 受保护，请改为创建 alternative.txt'}))
elif kind == 'format':
    path = pathlib.Path(event['tool']['target_path'])
    path.write_text(path.read_text().upper().rstrip() + '\\n')
elif kind == 'background':
    pathlib.Path('background.started').touch()
    time.sleep(2)
    pathlib.Path('background.done').touch()
elif kind in {'timeout', 'cancel'}:
    pathlib.Path(kind + '.started').write_text(str(os.getpid()))
    time.sleep(20)
    pathlib.Path(kind + '.leaked').touch()
elif kind == 'mode':
    pathlib.Path('mode.started').touch()
    if pathlib.Path('control.delay').exists(): time.sleep(20)
    pathlib.Path('mode.done').touch()
''')
    # 只观察真实执行；禁用记忆用于控制请求数量，通知为 UI 合同测试输入。
    (root / 'sitecustomize.py').write_text('''import asyncio, json, os, pathlib
root = pathlib.Path(os.environ['MEWCODE_HOOK_E2E_ROOT'])
from mewcode import app
from mewcode.hooks.runtime import HookRuntime
from mewcode.terminal.controller import TerminalController
from mewcode.types import AgentEvent
from mewcode.app import _Renderer
run = app._run
async def controlled_run(*args, **kwargs):
    kwargs.update(memory_enabled=False, user_root=root / 'empty-user')
    return await run(*args, **kwargs)
app._run = controlled_run
def record(name, value):
    with (root / name).open('a') as output:
        output.write(json.dumps(value, ensure_ascii=False) + '\\n')
dispatch = HookRuntime.dispatch
async def capture(self, event, **options):
    data = event.data
    tool = data.get('tool', {})
    record('events.jsonl', {'event': event.event, 'session_id': data['session_id'],
        'run_id': data.get('run_id'), 'turn_id': data.get('turn_id'), 'mode': data['mode'],
        'request_id': data.get('request_id'), 'tool': tool.get('call_id'),
        'name': tool.get('name'), 'path': tool.get('target_path'),
        'ok': tool.get('result', {}).get('ok'), 'error': tool.get('result', {}).get('error')})
    return await dispatch(self, event, **options)
HookRuntime.dispatch = capture
factory = app.make_provider
def observed_provider(config):
    provider = factory(config)
    stream = provider.stream
    async def observed(messages, **options):
        record('requests.jsonl', {'model': config.model, 'choice': options.get('tool_choice'),
            'marker': 'HOOK_CONTEXT_OK' in messages[-1].content, 'tools': [t.name for t in options.get('tools', ())]})
        async for item in stream(messages, **options):
            yield item
    provider.stream = observed
    return provider
app.make_provider = observed_provider
start, close = TerminalController.start, TerminalController.close
phase = TerminalController.set_phase
def observed_phase(self, value):
    record('terminal.jsonl', {'phase': value})
    return phase(self, value)
TerminalController.set_phase = observed_phase
async def observed_start(self):
    await start(self)
    async def notify():
        while True:
            path = root / 'notify.request'
            if path.exists():
                path.unlink()
                self.show(_Renderer(self.stream, ''), AgentEvent('hook_notice', run_id='e2e-background',
                    hook_source='e2e-notification-driver', hook_event='turn.end', text='后台 Hook 通知测试'))
            await asyncio.sleep(.05)
    self._e2e_notification = asyncio.create_task(notify())
TerminalController.start = observed_start
async def observed_close(self):
    task = getattr(self, '_e2e_notification', None)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await close(self)
TerminalController.close = observed_close
''')
    print(json.dumps({'root': str(root), 'rules': len(rules)}, ensure_ascii=False))


if __name__ == '__main__':
    prepare(Path(sys.argv[1]).resolve())
