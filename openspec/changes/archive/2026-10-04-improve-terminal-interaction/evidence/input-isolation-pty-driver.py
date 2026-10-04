"""在独立 tmux PTY 中验收真实输入解析；不调用模型或执行工具。"""

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
from types import SimpleNamespace


EVIDENCE = Path(__file__).resolve().parent
REPOSITORY = EVIDENCE.parents[3]
SOCKET = "mewcode-ui-isolation"
SESSION = "input"


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


async def child(root):
    from prompt_toolkit.input.defaults import create_input
    from prompt_toolkit.output.defaults import create_output
    from mewcode.terminal.approval import ApprovalView
    from mewcode.terminal.input import EnhancedTerminal

    checks = []
    before = termios.tcgetattr(sys.stdin.fileno())
    terminal = EnhancedTerminal(create_input(), create_output(), on_interrupt=lambda: None)

    def report(stage, **data):
        write_json(root / "state.json", {"stage": stage, **data})

    async def gate(name):
        async with asyncio.timeout(30):
            while not (root / name).exists():
                await asyncio.sleep(.01)
        await asyncio.sleep(.12)

    def view(identity):
        request = SimpleNamespace(id=identity, tool="write_file", arguments={"path": "isolated.txt", "content": "input boundary"},
                                  targets=(str(root / "isolated.txt"),), reason="仅验证输入；不会调用任何工具", mode="default")
        return ApprovalView(request, root=root)

    try:
        await terminal.start()
        terminal.set_phase("running")
        report("running_ready")
        async with asyncio.timeout(30):
            while "".join(char for char, _ in terminal._paste_boundary._prefix) != "\x1b[20":
                await asyncio.sleep(.01)
        assert terminal.chat.text == "" and terminal.answer.text == ""
        report("partial_opener_received", phase=terminal.phase, ordinary_typeahead="discarded")
        await gate("open_first_review")
        cancel = asyncio.Event()
        first = asyncio.create_task(terminal.approve(view("isolation-first"), cancel))
        await asyncio.sleep(.12)
        report("approval_first_ready")
        await gate("inspect_stale_paste")
        assert not first.done() and terminal.answer.text == ""
        checks.append({"case": "跨阶段部分 opener 的数字和换行", "result": "pass", "pending": True, "answer": terminal.answer.text})
        checks.append({"case": "运行期普通提前输入", "result": "pass", "answer": terminal.answer.text})
        report("stale_paste_discarded")
        await gate("inspect_fresh_paste")
        assert not first.done() and terminal.answer.text == "2\n3\n"
        checks.append({"case": "本阶段数字粘贴只编辑草稿", "result": "pass", "pending": True, "answer": terminal.answer.text})
        report("fresh_paste_is_draft")
        await gate("inspect_multiline_enter")
        assert not first.done() and terminal.answer.text == ""
        checks.append({"case": "多行数字草稿按 Enter 不构成批准", "result": "pass", "pending": True})
        report("multiline_enter_rejected")
        decision = await asyncio.wait_for(first, 30)
        assert decision == "once"
        checks.append({"case": "明确输入 2 和 Enter", "result": "pass", "decision": decision})
        second = asyncio.create_task(terminal.approve(view("isolation-second"), asyncio.Event()))
        await asyncio.sleep(.25)
        assert not second.done() and terminal.answer.text == ""
        checks.append({"case": "同批残留 3 和 Enter 不批准下一请求", "result": "pass", "pending": True, "answer": terminal.answer.text})
        report("approval_second_pending")
        decision = await asyncio.wait_for(second, 30)
        assert decision == "deny" and not terminal.eof
        checks.append({"case": "Ctrl+C 取消审批", "result": "pass", "decision": decision, "eof": terminal.eof})
        reading = asyncio.create_task(terminal.readline())
        await asyncio.sleep(.12)
        assert not reading.done() and terminal.chat.text == ""
        report("next_chat_ready")
        question = await asyncio.wait_for(reading, 30)
        assert question == "取消后仍可输入"
        checks.append({"case": "取消后的下一条普通消息", "result": "pass", "question": question})
    finally:
        await terminal.close()
        after = termios.tcgetattr(sys.stdin.fileno())
        restoration = {"echo_before": bool(before[3] & termios.ECHO), "echo_after": bool(after[3] & termios.ECHO),
                       "canonical_before": bool(before[3] & termios.ICANON), "canonical_after": bool(after[3] & termios.ICANON)}
        result = {"scope": "单个 EnhancedTerminal 的真实 tmux PTY 输入边界；没有模型请求、工具执行或持久授权", "checks": checks,
                  "terminal_restoration": restoration}
        write_json(root / "result.json", result)
    assert len(checks) == 8 and restoration["echo_before"] == restoration["echo_after"] and restoration["canonical_before"] == restoration["canonical_after"]
    report("done")


def driver():
    root = Path(tempfile.mkdtemp(prefix="mewcode-input-isolation-"))
    events = []

    def tmux(*arguments):
        return subprocess.run(["tmux", "-L", SOCKET, *arguments], check=True, capture_output=True, text=True).stdout

    def wait(stage):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            path = root / "state.json"
            if path.exists() and (state := json.loads(path.read_text())).get("stage") == stage:
                events.append(state)
                return state
            time.sleep(.02)
        raise TimeoutError(stage)

    def send(text):
        tmux("send-keys", "-t", SESSION, "-l", "--", text)

    def gate(name):
        (root / name).touch()

    def capture(name):
        time.sleep(.1)
        text = tmux("capture-pane", "-p", "-S", "-200", "-t", SESSION)
        (EVIDENCE / f"input-isolation-pty-{name}.txt").write_text(text)

    command = shlex.join([str(REPOSITORY / ".venv/bin/python"), str(Path(__file__).resolve()), "--child", str(root)])
    try:
        tmux("new-session", "-d", "-s", SESSION, "-x", "100", "-y", "32")
        tmux("set-option", "-t", SESSION, "remain-on-exit", "on")
        send(command + "\r")
        wait("running_ready")
        send("queued while running\r\x1b[20")
        wait("partial_opener_received")
        capture("01-running-prefix")
        gate("open_first_review")
        wait("approval_first_ready")
        send("0~2\n3\n\x1b[201~")
        gate("inspect_stale_paste")
        wait("stale_paste_discarded")
        capture("02-stale-paste")
        send("\x1b[200~2\n3\n\x1b[201~")
        gate("inspect_fresh_paste")
        wait("fresh_paste_is_draft")
        capture("03-fresh-paste-draft")
        send("\r")
        gate("inspect_multiline_enter")
        wait("multiline_enter_rejected")
        capture("04-multiline-rejected")
        send("2\r3\r")
        wait("approval_second_pending")
        capture("05-second-pending")
        send("\x03")
        wait("next_chat_ready")
        capture("06-after-cancel")
        send("取消后仍可输入\r")
        wait("done")
        capture("07-done")
        result = json.loads((root / "result.json").read_text())
        result.update({"verified_at": datetime.now(timezone.utc).isoformat(), "driver_events": events, "socket": SOCKET,
                       "runtime_directory": str(root), "result": "pass"})
        write_json(EVIDENCE / "input-isolation-pty-result.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except BaseException:
        capture("failure")
        raise
    finally:
        subprocess.run(["tmux", "-L", SOCKET, "kill-server"], capture_output=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        asyncio.run(child(Path(sys.argv[2])))
    else:
        driver()
