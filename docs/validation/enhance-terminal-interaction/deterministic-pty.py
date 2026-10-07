"""真实 tmux PTY 的确定性输入验收；注入展示事件，不请求模型或执行工具。"""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import termios
import time
import traceback
from types import SimpleNamespace


EVIDENCE = Path(__file__).resolve().parent
REPOSITORY = EVIDENCE.parents[2]
SOCKET = 'mewcode-enhance-deterministic'
SESSION = 'input'


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


async def child(root):
    from prompt_toolkit.input.defaults import create_input
    from prompt_toolkit.output.defaults import create_output
    from mewcode.terminal.approval import ApprovalView
    from mewcode.terminal.browser import DetailsBrowser
    from mewcode.terminal.input import EnhancedTerminal
    from mewcode.terminal.state import TerminalState
    from mewcode.tools.base import ToolResult
    from mewcode.types import AgentEvent, ToolCall

    checks = []
    before = termios.tcgetattr(sys.stdin.fileno())
    state = TerminalState()
    browser = DetailsBrowser(state.history)
    interrupts = []
    terminal = EnhancedTerminal(create_input(), create_output(), browser=browser,
        status=lambda: state.compact_status() + ' · 模型 PTY展示事件（无模型请求） · /tmp/pty验收',
        active=state.active_lines, on_interrupt=lambda: interrupts.append(terminal.phase))
    tasks = []
    error = None

    def snapshot():
        return {'phase': terminal.phase, 'state_phase': state.phase, 'run_id': state.run_id,
            'draft': terminal.chat.text, 'cursor': terminal.chat.cursor_position,
            'pending': terminal._pending is not None and not terminal._pending.done(),
            'answer': terminal.answer.text, 'details_open': terminal.details_open,
            'browser_view': browser.view, 'searching': browser.searching,
            'turn_id': browser.turn_id, 'call_id': browser.call_id, 'anchor': browser.anchor,
            'turn_count': len(state.history.turns), 'body_bytes': state.history.details.body_bytes,
            'body_limit': state.history.details.body_limit,
            'size': list(terminal.output.get_size())}

    def report(stage):
        write_json(root / 'state.json', {'stage': stage, **snapshot()})

    def checked(case):
        checks.append({'case': case, 'result': 'pass', **snapshot()})

    async def gate(name):
        async with asyncio.timeout(40):
            while not (root / name).exists():
                await asyncio.sleep(.01)
        await asyncio.sleep(.2)

    async def until(predicate):
        async with asyncio.timeout(10):
            while not predicate():
                await asyncio.sleep(.01)

    def begin(run, title, **kwargs):
        state.begin_task(mode='execute', permission_mode='default', max_iterations=20,
                         title=title, **kwargs)
        state.update(AgentEvent('progress', run_id=run, phase='model', iteration=1))

    def tool(run, identity, text):
        state.update(AgentEvent('tool_call', run_id=run, tool_call_id=identity,
            tool_name='read_file', call=ToolCall(identity, 'read_file', '{"path":"PTY验收.txt"}')))
        state.update(AgentEvent('tool_result', run_id=run, tool_call_id=identity,
            tool_name='read_file', result=ToolResult.success({'content': text, 'path': 'PTY验收.txt'})))

    def finish(run):
        state.update(AgentEvent('finished', run_id=run, reason='model_done'))

    def approval(identity):
        request = SimpleNamespace(id=identity, tool='write_file',
            arguments={'path': 'isolated.txt', 'content': '仅验证输入，不执行'},
            targets=(str(root / 'isolated.txt'),), reason='真实PTY输入隔离；没有工具执行', mode='default')
        task = asyncio.create_task(terminal.approve(ApprovalView(request, root=root), asyncio.Event()))
        tasks.append(task)
        return task

    def readline(cancel=None):
        task = asyncio.create_task(terminal.readline(cancel))
        tasks.append(task)
        return task

    try:
        await terminal.start()
        original = readline()
        await until(lambda: terminal.phase == 'idle')
        report('initial_idle')
        question = await asyncio.wait_for(original, 40)
        assert question == '原任务'
        begin('original', question)
        tool('original', 'file-1', '\n'.join(f'第{i}行：真实PTY长正文' for i in range(120)))
        report('running_draft_ready')
        await gate('check_running_enter')
        assert terminal.phase == 'running' and terminal.chat.text == '下一条\n第二行'
        assert terminal.chat.cursor_position == len(terminal.chat.text) - 1
        assert terminal._pending is None and state.iteration == 1
        draft, cursor = terminal.chat.text, terminal.chat.cursor_position
        checked('运行期多行编辑和Enter不提交、不排队，当前段输入不变')
        finish('original')
        next_input = readline()
        await until(lambda: terminal.phase == 'idle')
        await asyncio.sleep(.3)
        assert not next_input.done() and terminal.chat.text == draft and terminal.chat.cursor_position == cursor
        assert len(state.history.turns) == 1 and not terminal._history
        checked('运行期收到Enter紧邻结束；idle Future仍未完成，草稿和光标保持')
        report('race_idle_pending')
        assert await asyncio.wait_for(next_input, 40) == draft
        assert terminal.phase == 'running' and terminal.chat.text == ''
        checked('只有新按Enter消费草稿一次')

        begin('auto', '自动接续', source='自动接续', parent_id='original')
        report('auto_draft_ready')
        await gate('save_auto_draft')
        assert terminal.chat.text == '接续前草稿' and terminal.chat.cursor_position == 5
        terminal.save_draft()
        report('auto_snapshot_saved')
        await gate('check_auto_new_edit')
        assert terminal.chat.text == '接续前草稿新编辑'
        live_document = terminal.chat.document
        finish('auto')
        auto_read = readline()
        await until(lambda: terminal.phase == 'idle')
        await asyncio.sleep(.2)
        assert not auto_read.done() and terminal.chat.document == live_document
        assert state.history.current.source == '自动接续' and state.history.current.parent_id == 'original'
        checked('自动接续save_draft之后的新编辑不被陈旧快照覆盖')
        report('auto_idle_pending')
        assert await asyncio.wait_for(auto_read, 40) == live_document.text
        report('paste_running')
        await until(lambda: ''.join(char for char, _ in terminal._paste_boundary._prefix) == '\x1b[20')
        report('paste_partial_opener')
        await gate('paste_enter_idle')
        paste_read = readline()
        await until(lambda: terminal.phase == 'idle')
        report('paste_idle')
        await gate('check_split_paste')
        assert not paste_read.done() and terminal.chat.text == '' and terminal.answer.text == ''
        assert len(state.history.turns) == 2
        checked('跨running→idle分裂paste opener；/reset和数字换行完整丢弃')
        report('split_paste_discarded')
        assert await asyncio.wait_for(paste_read, 40) == '保留审批草稿'
        terminal.set_phase('running')
        report('browser_ready')
        await gate('check_browser_search')
        assert terminal.details_open and browser.view == 'call_list' and browser.searching
        assert browser.search_text == '2' and terminal.chat.text == '审批期间保留草稿'
        draft_document = terminal.chat.document
        selected = browser.turn_id, browser.call_id, browser.anchor
        report('browser_search_checked')
        await until(lambda: ''.join(char for char, _ in terminal._paste_boundary._prefix) == '\x1b[20')
        first = approval('pty-first')
        await until(lambda: terminal.phase == 'approval')
        assert not terminal.details_open and not browser.searching and browser.search_text == ''
        report('approval_first_ready')
        await gate('check_approval_stale')
        assert not first.done() and terminal.answer.text == '' and terminal.chat.document == draft_document
        assert (browser.turn_id, browser.call_id, browser.anchor) == selected
        checked('F2搜索被审批抢占，搜索残留及跨阶段数字回车零批准')
        report('approval_stale_discarded')
        assert await asyncio.wait_for(first, 40) == 'once'
        second = approval('pty-second')
        await until(lambda: terminal.phase == 'approval')
        await asyncio.sleep(.3)
        assert not second.done() and terminal.answer.text == '' and terminal.chat.document == draft_document
        checked('本次明确2+Enter批准一次；同批3+Enter不能批准下一请求')
        report('approval_second_pending')
        assert await asyncio.wait_for(second, 40) == 'deny'
        report('approval_done')
        await gate('check_reopen')
        assert terminal.details_open and not browser.searching
        assert (browser.turn_id, browser.call_id, browser.anchor) == selected
        assert terminal.chat.document == draft_document
        checked('审批后用户F2重开恢复选择，不恢复搜索残留')
        report('reopen_checked')
        await gate('history_start')
        state.reset_task()
        begin('round-0', '第0轮：等待淘汰')
        tool('round-0', 'call-0', '最旧轮次正文')
        finish('round-0')
        browser.turn_id = browser.call_id = None
        browser.view = 'call_list'
        terminal.application.invalidate()
        report('retention_selected')
        await gate('check_retention_select')
        assert terminal.details_open and browser.view == 'detail'
        oldest = browser.turn_id
        for number in range(1, 11):
            begin(f'round-{number}', f'第{number}轮')
            tool(f'round-{number}', f'call-{number}', f'第{number}轮正文')
            finish(f'round-{number}')
        terminal.application.invalidate()
        await asyncio.sleep(.25)
        assert len(state.history.turns) == 10 and state.history.get(oldest) is None
        assert '所选轮次已移出' in browser.notice and browser.turn_id == state.history.current.id
        assert browser.view == 'call_list' and state.phase == 'idle' and state.run_id == 'round-10'
        checked('第11个已结束轮次淘汰最旧索引，选中轮次明确移出提示')
        report('retention_evicted')
        await gate('capacity_select_old')
        assert browser.turn_id == state.history.turns[0].id and browser.view == 'detail'
        capacity_selection = browser.turn_id, browser.call_id
        entry = browser._call().result
        begin('capacity-current', '容量验收当前运行段')
        for number in range(140):
            tool('capacity-current', f'large-{number}', '中' * 22000)
        terminal.application.invalidate()
        await asyncio.sleep(.2)
        assert entry.evicted and not entry.body
        assert state.history.details.body_bytes <= 8 * 1024 * 1024
        assert (browser.turn_id, browser.call_id) == capacity_selection
        assert '移出' in browser._body()
        assert state.phase == 'model' and state.run_id == 'capacity-current'
        assert not state.history.current.calls[-1].result.evicted
        checked('真实8MiB容量淘汰先移出已结束轮正文；选择不跳转、当前段状态不变')
        report('capacity_evicted')
        await gate('prepare_resize')
        # 新增轻量调用供宽窄窗口锚点验收；仍沿真实工具事件进入共享缓存。
        tool('capacity-current', 'resize-call', '\n'.join(f'第{i}行：resize中文长正文和逻辑锚点' for i in range(150)))
        browser.turn_id = state.history.current.id
        browser.call_id = state.history.current.calls[-1].id
        browser.view = 'detail'
        browser.anchor = (0, 0)
        terminal.application.invalidate()
        report('resize_ready')
        await gate('check_resize_anchor')
        assert browser.anchor != (0, 0)
        selection = browser.turn_id, browser.call_id, browser.anchor
        report('resize_anchor_checked')
        for label, columns, rows in [('narrow', 45, 15), ('tiny', 18, 6), ('wide', 110, 34)]:
            await gate('resize_' + label)
            await until(lambda: terminal.output.get_size().columns == columns and terminal.output.get_size().rows == rows)
            assert (browser.turn_id, browser.call_id, browser.anchor) == selection
            assert 'F2' in terminal._details_text()
            assert state.phase == 'model' and state.run_id == 'capacity-current'
            checked(f'resize {columns}×{rows}保持轮次/调用/逻辑锚点且关闭入口可达')
            report('resize_' + label + '_checked')
        await gate('check_resize_close')
        assert not terminal.details_open and state.phase == 'model'
        checked('极小窗口再恢复后真实F2关闭仍可达')
        assert len(checks) == 14
        report('finished_checks')
    except BaseException:
        error = traceback.format_exc()
        report('failed')
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await terminal.close()
        after = termios.tcgetattr(sys.stdin.fileno())
        restoration = {'echo_before': bool(before[3] & termios.ECHO), 'echo_after': bool(after[3] & termios.ECHO),
            'canonical_before': bool(before[3] & termios.ICANON), 'canonical_after': bool(after[3] & termios.ICANON)}
        assert restoration['echo_before'] == restoration['echo_after']
        assert restoration['canonical_before'] == restoration['canonical_after']
        write_json(root / 'result.json', {'result': 'fail' if error else 'pass', 'error': error,
            'scope': '真实tmux PTY、EnhancedTerminal、TerminalState和DetailsBrowser；确定性注入展示事件，无模型请求、工具执行或持久授权',
            'checks': checks, 'terminal_restoration': restoration})
        if not error:
            report('done')


def driver():
    root = Path(tempfile.mkdtemp(prefix='mewcode-enhance-deterministic-'))
    events = []
    captures = []

    def tmux(*arguments):
        return subprocess.run(['tmux', '-L', SOCKET, *arguments], check=True, capture_output=True, text=True).stdout

    def wait(stage):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            path = root / 'state.json'
            if path.exists():
                state = json.loads(path.read_text())
                if state.get('stage') == 'failed':
                    result_path = root / 'result.json'
                    if result_path.exists():
                        raise AssertionError(json.loads(result_path.read_text())['error'])
                if state.get('stage') == stage:
                    events.append(state)
                    return state
            time.sleep(.02)
        raise TimeoutError(stage)

    def send(text):
        tmux('send-keys', '-t', SESSION, '-l', '--', text)

    def key(name):
        tmux('send-keys', '-t', SESSION, name)

    def gate(name):
        (root / name).touch()

    def capture(name):
        time.sleep(.15)
        path = EVIDENCE / f'deterministic-{name}.txt'
        text = tmux('capture-pane', '-p', '-S', '-200', '-t', SESSION)
        # 捕获仍来自本次真实PTY；只替换个人主目录和临时运行目录。
        text = text.replace(str(Path.home()), '~').replace(str(root), '[PTY临时目录]')
        path.write_text(text)
        captures.append(path.name)

    command = shlex.join([str(REPOSITORY / '.venv/bin/python'), str(Path(__file__).resolve()), '--child', str(root)])
    try:
        tmux('new-session', '-d', '-s', SESSION, '-x', '110', '-y', '34')
        tmux('set-option', '-t', SESSION, 'remain-on-exit', 'on')
        send(command + '\r')
        wait('initial_idle')
        send('原任务\r')
        wait('running_draft_ready')
        send('下一条\x1b\r第二行\x1b[D\r')
        gate('check_running_enter')
        wait('race_idle_pending')
        capture('01-running-enter-idle-pending')
        send('\r')
        wait('auto_draft_ready')
        send('接续前草稿')
        gate('save_auto_draft')
        wait('auto_snapshot_saved')
        send('新编辑')
        gate('check_auto_new_edit')
        wait('auto_idle_pending')
        capture('02-auto-new-draft')
        send('\r')
        wait('paste_running')
        send('\x1b[20')
        wait('paste_partial_opener')
        gate('paste_enter_idle')
        wait('paste_idle')
        send('0~/reset\n2\n3\n\x1b[201~')
        gate('check_split_paste')
        wait('split_paste_discarded')
        capture('03-cross-phase-paste')
        send('保留审批草稿\r')
        wait('browser_ready')
        send('审批期间保留草稿\x1b[D')
        time.sleep(.2)
        key('F2')
        time.sleep(.2)
        send('/')
        time.sleep(.2)
        send('2')
        gate('check_browser_search')
        wait('browser_search_checked')
        capture('04-f2-search')
        send('\x1b[20')
        wait('approval_first_ready')
        send('0~2\n3\n\x1b[201~')
        gate('check_approval_stale')
        wait('approval_stale_discarded')
        capture('05-approval-zero-stale-decisions')
        send('2\r3\r')
        wait('approval_second_pending')
        capture('06-second-approval-pending')
        send('1\r')
        wait('approval_done')
        key('F2')
        gate('check_reopen')
        wait('reopen_checked')
        gate('history_start')
        wait('retention_selected')
        send('\r')
        gate('check_retention_select')
        wait('retention_evicted')
        capture('07-eleventh-turn-eviction')
        send('t')
        time.sleep(.15)
        for _ in range(9):
            key('Up')
        send('\r\r')
        gate('capacity_select_old')
        wait('capacity_evicted')
        capture('08-eight-mib-body-eviction')
        gate('prepare_resize')
        wait('resize_ready')
        key('PageDown')
        gate('check_resize_anchor')
        wait('resize_anchor_checked')
        capture('09-resize-110x34')
        for label, columns, rows in [('narrow', 45, 15), ('tiny', 18, 6), ('wide', 110, 34)]:
            tmux('resize-window', '-t', SESSION, '-x', str(columns), '-y', str(rows))
            gate('resize_' + label)
            wait('resize_' + label + '_checked')
            capture('10-resize-' + label)
        key('F2')
        gate('check_resize_close')
        wait('done')
        capture('11-restored')
        result = json.loads((root / 'result.json').read_text())
        assert result['result'] == 'pass'
        result.update({'verified_at': datetime.now(timezone.utc).isoformat(), 'driver_events': events,
            'socket': SOCKET, 'session': SESSION, 'runtime_directory': str(root), 'captures': captures})
        write_json(EVIDENCE / 'deterministic-result.json', result)
        print(json.dumps({'result': result['result'], 'checks': len(result['checks']), 'captures': len(captures),
            'evidence': str(EVIDENCE / 'deterministic-result.json')}, ensure_ascii=False))
    except BaseException:
        capture('failure')
        result_path = root / 'result.json'
        result = json.loads(result_path.read_text()) if result_path.exists() else {'result': 'fail', 'error': traceback.format_exc()}
        result.update({'driver_events': events, 'runtime_directory': str(root), 'captures': captures})
        write_json(EVIDENCE / 'deterministic-result.json', result)
        raise
    finally:
        subprocess.run(['tmux', '-L', SOCKET, 'kill-server'], capture_output=True)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        asyncio.run(child(Path(sys.argv[2])))
    else:
        driver()
