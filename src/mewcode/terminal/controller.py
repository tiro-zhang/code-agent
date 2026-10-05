"""统一终端生命周期、流式输出与审批阶段，兼容非交互输入。"""

import asyncio
from contextlib import suppress
import os

from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input.defaults import create_input
from prompt_toolkit.output.defaults import create_output

from ..permissions.terminal import TerminalApproval
from .approval import ApprovalView
from .input import EnhancedTerminal
from .state import TerminalState
from .text import terminal_text


class _Output:
    """所有终端输出进入同一队列；审批正文有独立的安全写入入口。"""

    def __init__(self, owner, *, review=False):
        self.owner, self.review = owner, review

    def write(self, text):
        self.owner.write(text, review=self.review)
        return len(text)

    def flush(self):
        self.owner.output.flush()


class TerminalController:
    def __init__(self, reader, output, *, secret, root, on_interrupt, allow_enhanced=True, registry=None):
        from ..commands.builtins import build_registry
        self.registry = registry if registry is not None else build_registry()
        self.reader, self.output = reader, output
        self.secret, self.root, self.on_interrupt = secret, root, on_interrupt
        self.state = TerminalState(secret)
        self.state.set_phase("starting")
        self.stream, self.review_stream = _Output(self), _Output(self, review=True)
        self.backend = None
        self.phase = "starting"
        self.approval_active = False
        self._deferred = []
        self._deferred_alerts = []
        self._queue = []
        self._partial = ""
        self._writer = None
        self._approval_lock = asyncio.Lock()
        self._decisions = {}
        self._allow_enhanced = allow_enhanced
        self._plain_approval = TerminalApproval(reader, self.review_stream, root=root, secret=secret)

    @property
    def enhanced(self):
        return self.backend is not None

    @property
    def eof(self):
        return self.backend.eof if self.backend else self.reader.eof

    def _can_enhance(self):
        try:
            return (self._allow_enhanced and self.reader.interactive and self.reader.stream.isatty()
                    and self.output.isatty() and os.environ.get("TERM", "dumb") not in {"", "dumb"})
        except (AttributeError, OSError, ValueError):
            return False

    async def start(self):
        if not self._can_enhance():
            return
        backend = None
        try:
            backend = EnhancedTerminal(create_input(self.reader.stream, always_prefer_tty=False),
                create_output(self.output, always_prefer_tty=False), on_interrupt=self.on_interrupt,
                status=self.state.summary, active=self._active_lines, secret=self.secret,
                results=lambda: "".join(self._deferred), registry=self.registry)
            await backend.start()
            self.backend = backend
            self.set_phase("starting")
        except (OSError, ValueError, RuntimeError, EOFError) as error:
            if backend:
                with suppress(OSError, ValueError, RuntimeError, EOFError):
                    await backend.close()
            self.output.write("提示> 增强终端初始化失败，已恢复纯文本模式：" +
                              terminal_text(str(error), self.secret) + "\n")
            self.output.flush()

    def _active_lines(self):
        lines = self.state.active_lines()
        if self._deferred:
            records = [line for line in "".join(self._deferred).splitlines() if line.strip()]
            if records:
                latest = self._deferred_alerts[-1] if self._deferred_alerts else records[-1]
                summary = [f"旁路 {len(records)}> {latest}"]
                if self._deferred_alerts:
                    summary.append(f"异常／受限 {len(self._deferred_alerts)} 条 · results 查看全部旁路")
                lines = summary + lines
        return lines

    def set_phase(self, phase):
        self.phase = phase
        self.state.set_phase(phase)
        if self.backend:
            self.backend.set_phase(phase)
        else:
            self.reader.discard_pending()

    def sync_session(self, session):
        self.state.mode = session.mode
        self.state.permission_mode = session.permissions.mode
        self.state.max_iterations = session.agent.max_iterations
        if self.backend:
            self.backend.application.invalidate()

    def replace_registry(self, registry):
        """发布整代命令；帮助、分发和当前补全共用同一对象。"""
        self.registry = registry
        if self.backend:
            from .input import CommandCompleter
            self.backend.registry = registry
            self.backend.chat.completer = CommandCompleter(registry)
            self.backend.chat.cancel_completion()
            self.backend.application.invalidate()

    def write(self, text, *, review=False):
        if self.approval_active and not review:
            self._deferred.append(text)
            if self.backend:
                self.backend.application.invalidate()
            return
        if not self.backend:
            self.output.write(text)
            self.output.flush()
            return
        text = self._partial + text
        boundary = text.rfind("\n") + 1
        self._partial = text[boundary:]
        self.backend.set_live_text(self._partial)
        if boundary:
            self._queue.append(text[:boundary])
            if self._writer is None or self._writer.done():
                self._writer = asyncio.create_task(self._flush())

    def finish_line(self):
        if self._partial:
            self.write("\n")

    async def _flush(self):
        while self._queue:
            text = "".join(self._queue)
            self._queue.clear()
            def emit():
                self.output.write(text)
                self.output.flush()
            with set_app(self.backend.application):
                await run_in_terminal(emit)

    async def drain(self):
        while self._writer is not None:
            writer = self._writer
            await writer
            if self._writer is writer:
                self._writer = None

    def _release_output(self):
        self.approval_active = False
        self._deferred_alerts.clear()
        if self._deferred:
            text = "".join(self._deferred)
            self._deferred.clear()
            self.write(text)

    async def clear_screen(self):
        """清除终端显示并重绘；保留输入历史、任务状态及会话。"""
        if not self.backend:
            self.write("提示> 当前纯文本输出清屏不可用；会话上下文保持。\n")
            return
        self.finish_line()
        await self.drain()
        with set_app(self.backend.application):
            def clear():
                output = self.backend.application.output
                output.erase_screen()
                output.cursor_goto(0, 0)
                output.flush()
            await run_in_terminal(clear)
        self.backend.application.invalidate()

    async def readline(self, cancel):
        # 等输出恢复后再开放草稿，保证提示符出现时已有等待者接收提交。
        await self.drain()
        self.phase = "idle"
        self.state.set_phase("idle")
        if self.backend:
            return await self.backend.readline(cancel)
        self.reader.discard_pending()
        self.write("你> ")
        line = await self.reader.readline(cancel)
        if not line:
            return None
        # 非交互模式的一次换行是协议分隔符，不是消息正文的尾部空白。
        return line[:-2] if line.endswith("\r\n") else line[:-1] if line.endswith("\n") else line

    def remember(self, text):
        if self.backend:
            self.backend.remember(text)

    def begin_task(self):
        self._decisions.clear()
        self.state.begin_task(mode=self.state.mode, permission_mode=self.state.permission_mode,
                              max_iterations=self.state.max_iterations)
        self.set_phase("running")

    async def approve(self, request, cancel):
        async with self._approval_lock:
            if request.id in self._decisions:
                return self._decisions[request.id]
            if cancel.is_set():
                return "deny"
            # 此前队列先输出，然后建立当前唯一审批区域。
            self.finish_line()
            await self.drain()
            self.approval_active = True
            self.phase = "approval"
            self.state.set_phase("approval")
            try:
                if self.backend:
                    decision = await self.backend.approve(ApprovalView(request, root=self.root,
                                                                       secret=self.secret), cancel)
                else:
                    decision = await self._plain_approval(request, cancel)
                self._decisions[request.id] = decision
                return decision
            finally:
                self.phase = "running"
                self.state.set_phase("running")
                self._release_output()
                await self.drain()

    def show(self, renderer, event, *, managed_approval=True, maintenance=False):
        if maintenance:
            renderer.show(event)
            if self.backend:
                self.backend.application.invalidate()
            return
        lines = self.state.update(event)
        if self.backend:
            self.backend.application.invalidate()
        if event.kind == "permission_requested" and managed_approval:
            return
        if not self.enhanced:
            renderer.show(event)
            return
        if event.kind in {"progress", "usage", "tool_call", "tool_started", "tool_result",
                          "permission_requested", "permission_resolved"}:
            result = event.result
            needs_attention = bool(event.warning) or (result is not None and (
                not result.ok or result.truncated or
                isinstance(result.data, dict) and result.data.get("permission_limited")))
            if self.approval_active and needs_attention:
                self._deferred_alerts.extend(lines)
            for line in lines:
                renderer.line(line)
        elif event.kind == "finished":
            label = "结束" if event.reason == "model_done" else "本轮未完成"
            renderer.line(f"{label}> {event.reason} · {renderer.safe(event.text, 300)} · 请求 {event.iteration}/{event.max_iterations}")
            renderer.line(self.state.summary())
        else:
            renderer.show(event)

    def discard_pending(self):
        if self.backend:
            self.backend.discard_pending()
        else:
            self.reader.discard_pending()

    async def close(self):
        self._release_output()
        self.finish_line()
        await self.drain()
        if self.backend:
            await self.backend.close()
