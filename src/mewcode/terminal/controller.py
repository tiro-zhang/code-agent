"""统一终端生命周期、流式输出与审批阶段，兼容非交互输入。"""

import asyncio
from contextlib import suppress
import os

from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input.defaults import create_input
from prompt_toolkit.output.defaults import create_output
from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import FormattedText

from ..permissions.terminal import TerminalApproval
from .approval import ApprovalView
from .input import EnhancedTerminal
from .state import TerminalState
from .text import terminal_text
from .projection import TerminalProjection
from .markdown import MarkdownStyle


class _Output:
    """所有终端输出进入同一队列；审批正文有独立的安全写入入口。"""

    def __init__(self, owner, *, review=False):
        self.owner, self.review = owner, review

    def write(self, text):
        self.owner.write(text, review=self.review)
        return len(text)

    def flush(self):
        self.owner.output.flush()

    def response_style(self, enabled):
        self.owner._markdown = MarkdownStyle() if enabled else None


class TerminalController:
    def __init__(self, reader, output, *, secret, root, on_interrupt, allow_enhanced=True, registry=None):
        from ..commands.builtins import build_registry
        self.registry = registry if registry is not None else build_registry()
        self.reader, self.output = reader, output
        self.secret, self.root, self.on_interrupt = secret, root, on_interrupt
        self.state = TerminalState(secret)
        self.projection = TerminalProjection(self.state)
        self.state.set_phase("starting")
        self.stream, self.review_stream = _Output(self), _Output(self, review=True)
        self.backend = None
        self.phase = "starting"
        self.approval_active = False
        self._deferred = []
        self._deferred_alerts = []
        self._queue = []
        self._partial = ""
        self._markdown = None
        self._plain_phase = ''
        self._plain_starts = set()
        self._writer = None
        self._approval_lock = asyncio.Lock()
        self._decisions = {}
        self._allow_enhanced = allow_enhanced
        self._plain_approval = TerminalApproval(reader, self.review_stream, root=root, secret=secret)
        self.tasks = None

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
            if getattr(self.reader, 'interactive', False):
                self.write('提示> 纯文本模式的 Ctrl+B 不可用；前台子 Agent 等待 30 秒会自动转后台\n')
            return
        backend = None
        try:
            backend = EnhancedTerminal(create_input(self.reader.stream, always_prefer_tty=False),
                create_output(self.output, always_prefer_tty=False), on_interrupt=self.on_interrupt,
                status=self.state.compact_status, active=self._active_lines, secret=self.secret,
                details=self.state.details_text,
                on_background=self.background,
                results=lambda: "".join(self._deferred) + '\n'.join(
                    event.text for event in self.projection.flush(consume=False)), registry=self.registry)
            await backend.start()
            self.backend = backend
            self.set_phase("starting")
        except (OSError, ValueError, RuntimeError, EOFError) as error:
            if backend:
                with suppress(OSError, ValueError, RuntimeError, EOFError):
                    await backend.close()
            self.output.write("提示> 增强终端初始化失败，已恢复纯文本模式：" +
                              terminal_text(str(error), self.secret) + "\n")
            self.output.write('提示> 纯文本模式的 Ctrl+B 不可用；前台子 Agent 等待 30 秒会自动转后台\n')
            self.output.flush()

    def _active_lines(self):
        lines = self.state.active_lines()
        finished = sum(tool.result is not None for tool in self.state._tools.values())
        if finished and not self.state._finished:
            lines.insert(0, f'调用进度> 已结束 {finished}/{len(self.state._tools)} 次调用')
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
        self.tasks = session.tasks
        self.state.mode = session.mode
        self.state.permission_mode = session.permissions.mode
        self.state.max_iterations = session.agent.max_iterations
        if self.backend:
            self.backend.application.invalidate()

    def background(self):
        task_id = self.tasks.foreground_task_id if self.tasks else None
        if self.enhanced and self.phase == 'running' and task_id and self.tasks.detach(task_id):
            self.write(f'后台> {task_id} 已切换，执行不会重启\n')
        else:
            self.write('提示> Ctrl+B 仅增强终端等待前台子 Agent 时可用；当前阶段不可用\n')

    def save_draft(self):
        if self.backend:
            self.backend.save_draft()
        else:
            self.reader.save_draft()

    def input_submitted(self):
        backend = self.backend or self.reader
        return backend.input_submitted()

    def restore_draft(self):
        # 实际恢复留在下一聊天输入边界，先清除运行／审批残留。
        pass

    def replace_registry(self, registry):
        """发布整代命令；帮助、分发和当前补全共用同一对象。"""
        self.registry = registry
        if self.backend:
            from .input import CommandCompleter
            self.backend.registry = registry
            self.backend.chat.completer = CommandCompleter(registry, self.secret)
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
        # 在入队时固定样式，避免稍后的角色／阶段切换改变已生成的正文。
        fragments = self._markdown.render(text[:boundary]) if self._markdown else [('', text[:boundary])]
        live = self._markdown.render(self._partial, commit=False) if self._markdown else [('', self._partial)]
        self.backend.set_live_text(self._partial, fragments=live)
        if boundary:
            self._queue.append((text[:boundary], fragments))
            if self._writer is None or self._writer.done():
                self._writer = asyncio.create_task(self._flush())

    def finish_line(self):
        if self._partial:
            self.write("\n")

    async def _flush(self):
        while self._queue:
            queued = self._queue[:]
            self._queue.clear()
            def emit():
                if getattr(self.output, 'isatty', lambda: False)():
                    fragments = [part for _, fragments in queued for part in fragments]
                    print_formatted_text(FormattedText(fragments), output=self.backend.application.output, end='')
                else:
                    self.output.write(''.join(text for text, _ in queued))
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
        self.reader.restore_draft()
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
        self.projection.reset()
        self._plain_phase = ''
        self._plain_starts.clear()

    def cancelling(self, *, force=False):
        """取消已提出但清理尚未完成；不提前恢复输入。"""
        if self.phase not in {'closing', 'cancelling'} and (force or self.phase != 'idle'):
            self.set_phase('cancelling')
            if not self.enhanced:
                self.write('状态> 正在停止，等待清理完成\n')

    def echo(self, renderer, text):
        """只回显实际提交的原始问题，保留正文换行和缩进。"""
        if not self.enhanced:
            # plain 已写出提示符；交互 TTY 由终端行规程回显，管道需显式保留问题。
            if not (getattr(self.reader.stream, 'isatty', lambda: False)() and
                    getattr(self.output, 'isatty', lambda: False)()):
                renderer.line(terminal_text(text, self.secret, multiline=True))
            return
        renderer.line('')
        renderer.line('你> ' + terminal_text(text, self.secret, multiline=True))

    async def approve(self, request, cancel):
        async with self._approval_lock:
            if request.id in self._decisions:
                return self._decisions[request.id]
            if cancel.is_set():
                return "deny"
            for event in self.projection.flush():
                self.write(event.text + '\n')
            # 此前队列先输出，然后建立当前唯一审批区域。
            self.finish_line()
            await self.drain()
            owning_phase = self.phase
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
                self.phase = ("closing" if self.phase == 'closing' else
                              "cancelling" if cancel.is_set() or self.phase == 'cancelling' else owning_phase)
                self.state.set_phase(self.phase)
                if self.backend:
                    self.backend.set_phase(self.phase)
                self._release_output()
                await self.drain()

    def show(self, renderer, event, *, managed_approval=True, maintenance=False):
        # 手动压缩保留其独立结果与用量，后台维护走安静通道。
        if maintenance and event.purpose == 'summary':
            renderer.show(event)
            return
        visible = self.projection.accept(event, maintenance=maintenance)
        if self.phase in {'cancelling', 'approval'}:
            self.state.set_phase(self.phase)
        if self.backend:
            self.backend.application.invalidate()
        elif not maintenance and event.kind in {'progress', 'thinking_delta'}:
            if self.state.phase != self._plain_phase:
                self._plain_phase = self.state.phase
                renderer.line('状态> ' + self.state.compact_status())
        actual = event.child_event if event.kind == 'skill_event' else event
        if not self.enhanced and not maintenance and actual and actual.kind == 'tool_started':
            identity = (actual.run_id, actual.tool_call_id)
            if identity not in self._plain_starts:
                self._plain_starts.add(identity)
                renderer.line('状态> 正在执行 ' + renderer.safe(actual.tool_name))
        if self.approval_active and actual and self.projection.needs_attention(actual):
            self._deferred_alerts.extend(item.text for item in visible if item.kind == 'display_line')
        for item in visible:
            renderer.show(item)

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
