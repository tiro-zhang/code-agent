"""真实 tmux PTY 中验证多个旁路结果；受控事件，不请求模型或执行工具。"""

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace


EVIDENCE = Path(__file__).resolve().parent
REPOSITORY = EVIDENCE.parents[3]


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


async def child(root):
    from mewcode.app import _Renderer
    from mewcode.permissions.terminal import InputReader
    from mewcode.terminal.controller import TerminalController
    from mewcode.tools.base import ToolResult
    from mewcode.types import AgentEvent

    terminal = TerminalController(InputReader(sys.stdin), sys.stdout, secret='', root=root, on_interrupt=lambda: None)
    renderer = _Renderer(terminal.stream, '')
    checks = []
    committed = []

    class CompletedOutput:
        """只记录控制器提交的完整日志；UI 仍使用启动时的真实 stdout。"""

        def write(self, text):
            committed.append(text)
            return sys.stdout.write(text)

        def flush(self):
            sys.stdout.flush()

    def show(kind, identity, name, **fields):
        terminal.show(renderer, AgentEvent(kind, run_id='multiple-results-pty', iteration=1,
                                          tool_call_id=identity, tool_name=name, **fields))

    def state(stage, pending, **data):
        write_json(root / 'state.json', {'stage': stage, 'pending': not pending.done(), **data})

    try:
        await terminal.start()
        assert terminal.enhanced
        terminal.output = CompletedOutput()
        terminal.begin_task()
        show('tool_started', 'first', 'SIDE-01')
        show('tool_started', 'second', 'SIDE-02')
        request = SimpleNamespace(id='review-third', tool='write_file', arguments={'path': 'controlled.txt', 'content': '仅输入验收'},
                                  targets=(str(root / 'controlled.txt'),), reason='仅验证旁路结果展示，不执行写入', mode='default')
        show('permission_requested', 'third', 'write_file', permission_request=request)
        pending = asyncio.create_task(terminal.approve(request, asyncio.Event()))
        await asyncio.sleep(.15)
        show('tool_result', 'first', 'SIDE-01', result=ToolResult.failure('timeout', 'FIRST-TIMEOUT'))
        show('tool_result', 'second', 'SIDE-02', result=ToolResult.success({}))
        for number in range(4, 21):
            show('tool_result', f'side-{number}', f'SIDE-{number:02}', result=ToolResult.success({}))
        await asyncio.sleep(.15)
        assert not pending.done() and len(terminal._deferred_alerts) == 1
        assert 'FIRST-TIMEOUT' in terminal._active_lines()[0]
        assert not committed
        checks.append({'case': '先超时后成功并继续到达 17 条结果', 'result': 'pass', 'deferred_records': len(terminal._deferred), 'priority': terminal._active_lines()[0]})
        state('ready', pending)
        seen_commands = set()
        async with asyncio.timeout(45):
            while not pending.done():
                command_file = root / 'command.json'
                if command_file.exists():
                    command = json.loads(command_file.read_text())
                    serial = command['serial']
                    if serial not in seen_commands:
                        await asyncio.sleep(.12)
                        assert not pending.done()
                        body, page, total = terminal.backend._review_parts()
                        if command.get('section'):
                            assert terminal.backend._section == command['section']
                        if 'page' in command:
                            assert page == command['page']
                        seen_commands.add(serial)
                        state(f'command-{serial}', pending, section=terminal.backend._section, page=page, total=total, body=body)
                await asyncio.sleep(.01)
        assert await pending == 'deny'
        assert not terminal._deferred and not terminal._deferred_alerts
        show('tool_result', 'third', 'write_file', result=ToolResult.failure('permission_denied', 'THIRD-DENIED'))
        terminal.show(renderer, AgentEvent('finished', run_id='multiple-results-pty', iteration=1, reason='model_done'))
        await terminal.drain()
        checks.append({'case': '浏览过程不批准；明确 1 后拒绝第三请求', 'result': 'pass', 'decision': 'deny', 'browsing_commands': len(seen_commands)})
    finally:
        await terminal.close()
    write_json(root / 'result.json', {'checks': checks, 'committed_log': ''.join(committed)})
    write_json(root / 'state.json', {'stage': 'done'})


def driver():
    root = Path(tempfile.mkdtemp(prefix='mewcode-multiple-results-'))
    socket = f'mewcode-ui-results-{os.getpid()}'
    started = False
    pages = []

    def tmux(*arguments):
        return subprocess.run(['tmux', '-L', socket, *arguments], check=True, capture_output=True, text=True).stdout

    def wait(stage):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            path = root / 'state.json'
            if path.exists() and (value := json.loads(path.read_text())).get('stage') == stage:
                return value
            time.sleep(.02)
        raise TimeoutError(stage)

    def send(text):
        tmux('send-keys', '-t', 'results', '-l', '--', text)

    def capture(name, *, scroll=False):
        time.sleep(.1)
        arguments = ['capture-pane', '-p', '-t', 'results']
        if scroll:
            arguments.extend(['-S', '-300'])
        text = tmux(*arguments)
        (EVIDENCE / f'multiple-results-pty-{name}.txt').write_text(text)
        return text

    def browse(text, serial, **expected):
        send(text + '\r')
        write_json(root / 'command.json', {'serial': serial, **expected})
        state = wait(f'command-{serial}')
        visible = capture(f'{serial:02}-{text}')
        assert state['pending'] and 'results' in visible
        assert f"{state['page'] + 1}/{state['total']}" in visible
        for line in state['body'].splitlines():
            assert line in visible
        pages.append({**state, 'visible': visible})
        return state

    command = shlex.join([str(REPOSITORY / '.venv/bin/python'), str(Path(__file__).resolve()), '--child', str(root)])
    try:
        tmux('new-session', '-d', '-s', 'results', '-x', '80', '-y', '16', command)
        started = True
        tmux('set-option', '-t', 'results', 'remain-on-exit', 'on')
        assert wait('ready')['pending']
        first = capture('00-summary')
        assert 'FIRST-TIMEOUT' in first and '异常／受限 1 条' in first
        page = browse('results', 1, section='results', page=0)
        assert page['total'] > 1
        serial = 2
        for index in range(1, page['total']):
            browse('next', serial, section='results', page=index)
            serial += 1
        browse('back', serial, section='results', page=page['total'] - 2)
        all_bodies = '\n'.join(item['body'] for item in pages)
        expected = ['SIDE-01', 'SIDE-02', *[f'SIDE-{n:02}' for n in range(4, 21)]]
        assert all(marker in all_bodies for marker in expected)
        send('1\r')
        wait('done')
        final = capture('final-scroll', scroll=True)
        result = json.loads((root / 'result.json').read_text())
        committed = result.pop('committed_log')
        (EVIDENCE / 'multiple-results-pty-committed-log.txt').write_text(committed)
        counts = {marker: committed.count(marker) for marker in [*expected, 'THIRD-DENIED', 'FIRST-TIMEOUT']}
        assert all(value == 1 for value in counts.values()), counts
        result.update({'scope': '真实 TerminalController + EnhancedTerminal + tmux PTY，受控 AgentEvent；无模型请求或工具执行',
                       'verified_at': datetime.now(timezone.utc).isoformat(), 'result': 'pass', 'terminal': '80x16',
                       'pages': pages, 'final_log_counts': counts, 'socket': socket, 'runtime_directory': str(root)})
        write_json(EVIDENCE / 'multiple-results-pty-result.json', result)
        print(json.dumps({'result': 'pass', 'pages': len(pages), 'unique_terminal_logs': len(counts), 'checks': result['checks']}, ensure_ascii=False, indent=2))
    except BaseException:
        if started:
            capture('failure', scroll=True)
        raise
    finally:
        if started:
            subprocess.run(['tmux', '-L', socket, 'kill-server'], capture_output=True)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        asyncio.run(child(Path(sys.argv[2])))
    else:
        driver()
