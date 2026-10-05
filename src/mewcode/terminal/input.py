"""单个非全屏终端应用；草稿、审批和取消共享输入所有权。"""

import asyncio
from collections import deque
from contextlib import suppress
import os
import termios

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion, CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import HSplit, Layout, Window, ConditionalContainer
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.utils import get_cwidth

from ..commands.builtins import build_registry
from .approval import ApprovalView
from .text import terminal_text


def _fit_width(text, width):
    """按终端显示列裁剪摘要，保留一列省略号。"""
    if width <= 0:
        return ""
    if get_cwidth(text) <= width:
        return text
    current, cells = [], 0
    for char in text:
        cells += get_cwidth(char)
        if cells >= width:
            break
        current.append(char)
    return "".join(current) + "…"


class CommandCompleter(Completer):
    def __init__(self, registry, secret=''):
        self.registry = registry
        self.secret = secret

    def get_completions(self, document, complete_event):
        if document.cursor_position != len(document.text):
            return
        for value in self.registry.completions(document.text):
            spec = self.registry.find(value.lstrip().split()[0][1:])
            yield Completion(value, start_position=-len(document.text),
                             display=terminal_text(value, self.secret),
                             display_meta=terminal_text(spec.description, self.secret) if spec else '')


class _PasteBoundary:
    """记录粘贴起始字节的代际，并保留尚未收全的边界序列。"""

    _START = "\x1b[200~"
    _END = "\x1b[201~"

    def __init__(self, parser, generation):
        self.parser, self.generation = parser, generation
        self._feed = parser.feed
        self._flush = parser.flush
        self._emit = parser.feed_key_callback
        self._feeding = False
        self._prefix = []
        self._paste_generation = None
        self._completed = deque()
        parser.feed, parser.flush, parser.feed_key_callback = self.feed, self.flush, self.emit

    @property
    def pending(self):
        return bool(self._prefix) or self._paste_generation is not None

    def feed(self, data):
        # 原解析器在一批字节中遇到粘贴边界时会递归 feed，只扫描外层原始输入。
        if self._feeding:
            self._feed(data)
            return
        for char in data:
            marker = self._START if self._paste_generation is None else self._END
            self._prefix.append((char, self.generation()))
            while self._prefix and not marker.startswith("".join(c for c, _ in self._prefix)):
                self._prefix.pop(0)
            if len(self._prefix) == len(marker):
                if self._paste_generation is None:
                    self._paste_generation = self._prefix[0][1]
                else:
                    self._completed.append(self._paste_generation)
                    self._paste_generation = None
                self._prefix.clear()
        self._feeding = True
        try:
            self._feed(data)
        finally:
            self._feeding = False

    def flush(self):
        # ESC 单键在本阶段仍按原超时处理；已跨阶段或更长的潜在粘贴开头
        # 必须等待后续字节，否则 payload 中的换行可能变成批准操作。
        if self._paste_generation is not None or (self._prefix and
                (len(self._prefix) > 1 or self._prefix[0][1] != self.generation())):
            return
        self._prefix.clear()
        self._flush()

    def emit(self, key):
        if key.key == Keys.BracketedPaste:
            key.paste_generation = self._completed.popleft()
        self._emit(key)

    def close(self):
        self.parser.feed, self.parser.flush, self.parser.feed_key_callback = self._feed, self._flush, self._emit


class EnhancedTerminal:
    """Application 从启动存活至资源收尾，不另开 stdin 或全屏缓冲。"""

    def __init__(self, input, output, *, on_interrupt, status=lambda: "", active=lambda: [], secret="",
                 results=lambda: "", registry=None, details=lambda section: '暂无详情', on_background=lambda: None):
        self.input, self.output = input, output
        self.secret = secret
        self._closing = False
        self._ready = False
        self._review_cache = None
        self.on_interrupt, self.status, self.active = on_interrupt, status, active
        self.on_background = on_background
        self._saved_draft = None
        self.results = results
        self.details = details
        self.details_open = False
        self._details_section, self._details_page = 'tools', 0
        self._discover_inhibited = False
        self._dismissed_text = None
        self.phase = "starting"
        self.eof = False
        self.generation = 0
        self.registry = registry if registry is not None else build_registry()
        self.chat = Buffer(multiline=True, completer=CommandCompleter(self.registry, secret), complete_while_typing=False,
                           read_only=Condition(lambda: self.phase != "idle" or self.details_open))
        self.chat.on_text_changed += self._discover
        self.answer = Buffer(multiline=True, read_only=Condition(lambda: self.phase != "approval"))
        self.live = Buffer(read_only=True)
        self._live_fragments = []
        self._history = []
        self._history_index = 0
        self._history_draft = ""
        self._pending = None
        self._cancel = None
        self._view = None
        self._section, self._page = "summary", 0
        self._notice = ""
        parser = getattr(input, "vt100_parser", None)
        self._paste_boundary = _PasteBoundary(parser, lambda: self.generation) if parser is not None else None
        self._task = None
        self._started = asyncio.Event()
        self._lock = asyncio.Lock()
        self._chat_control = BufferControl(self.chat)
        self._answer_control = BufferControl(self.answer)
        approval = Condition(lambda: self.phase == "approval")
        idle = Condition(lambda: self.phase == "idle" and not self.details_open)
        self._details_control = FormattedTextControl(self._details_text, focusable=True)
        layout = HSplit([
            ConditionalContainer(Window(FormattedTextControl(self._status_text),
                height=lambda: 1 if self.phase == "approval" or self.details_open else Dimension(min=1, max=3), wrap_lines=True),
                Condition(lambda: self.phase != "approval" or not self._tiny())),
            ConditionalContainer(Window(FormattedTextControl(lambda: self._live_fragments), wrap_lines=True,
                height=Dimension(min=1, max=8)), Condition(lambda: bool(self.live.text) and not self.details_open)),
            ConditionalContainer(Window(FormattedTextControl(self._active_text),
                height=lambda: self._active_height() if self.phase == "approval" else Dimension(min=1, max=4),
                wrap_lines=True), Condition(lambda: bool(self._active_height()) and not self.details_open)),
            ConditionalContainer(Window(FormattedTextControl(self._review_header), height=1),
                Condition(lambda: self.phase == "approval" and self.output.get_size().rows >= 6)),
            ConditionalContainer(Window(FormattedTextControl(self._review_text), wrap_lines=True,
                height=lambda: self._review_height()), approval),
            ConditionalContainer(Window(self._details_control, wrap_lines=True,
                height=lambda: max(2, min(20, self.output.get_size().rows - 1 - self._hint_height()))),
                Condition(lambda: self.details_open)),
            ConditionalContainer(Window(FormattedTextControl(lambda: self._notice), height=1),
                Condition(lambda: self.phase == "approval" and bool(self._notice) and not self._tiny())),
            ConditionalContainer(Window(self._answer_control, height=1,
                get_line_prefix=lambda line, wrap: "> " if self._tiny() else "授权> "), approval),
            ConditionalContainer(Window(self._chat_control, wrap_lines=True,
                height=Dimension(min=1, max=8),
                get_line_prefix=lambda line, wrap: "你> " if line == 0 else "… "), idle),
            ConditionalContainer(CompletionsMenu(max_height=6, scroll_offset=1), idle),
            ConditionalContainer(Window(FormattedTextControl(self._hint),
                height=lambda: self._hint_height() if self.phase == "approval" or self.details_open else Dimension(min=1, max=2), wrap_lines=True),
                Condition(lambda: True)),
        ])
        # 避免无响应的终端位置查询拖延退出；非全屏布局仍使用真实尺寸。
        if hasattr(output, "enable_cpr"):
            output.enable_cpr = False
        self.application = Application(layout=Layout(layout, focused_element=self._chat_control),
            key_bindings=self._bindings(), full_screen=False, input=input, output=output,
            refresh_interval=.25, min_redraw_interval=.03, erase_when_done=False)

    def _status_text(self):
        labels = {"starting": "启动中", "idle": "等待输入", "running": "运行中", "control": "处理控制指令",
                  "approval": "等待授权", "summary": "压缩上下文", "closing": "退出清理",
                  "cancelling": "正在停止"}
        text = self.status() or labels[self.phase]
        if self.details_open:
            return _fit_width(text.splitlines()[0], max(2, self.output.get_size().columns - 1))
        return text.splitlines()[0] if self.phase == "approval" else text

    def _active_text(self):
        lines = self.active()
        if self.phase != "approval":
            return "\n".join(lines)
        # 每个摘要只占一行，长路径和错误详情不能挤掉后续工具的结果。
        width = max(1, self.output.get_size().columns - 1)
        clipped = []
        for line in lines[:self._active_height()]:
            text = terminal_text(line, self.secret)
            clipped.append(_fit_width(text, width))
        return "\n".join(clipped)

    def _active_height(self):
        count = len(self.active())
        if self.phase != "approval":
            return min(4, count)
        # 小窗口仍保留优先结果，同时给审阅正文和答案各留至少一行。
        fixed = self._review_overhead() + 1
        available = max(0, self.output.get_size().rows - fixed)
        return min(count, 1 if self._tiny() else 3, available)

    def _hint(self):
        if self.details_open:
            if self.output.get_size().columns < 80:
                return 'Tab 切换 · ↑↓翻页\nEsc/F2 返回 · Ctrl+C 取消/清草稿'
            return 'Tab 调用／思考 · PgUp/PgDn 翻页 · Esc/F2 返回 · Ctrl+C 按当前任务状态处理'
        if self.phase == "approval":
            if self._tiny():
                return '1拒2次3会4久\n↑↓翻页 回车拒'
            if self.output.get_size().columns < 80:
                return ('1 拒绝 · 2 本次 · 3 会话 · 4 永久\n'
                        '↑↓ 翻页 · all 全文 · scope 范围\nCtrl+C 取消整轮')
            return ("授权> 1/回车 拒绝 · 2 本次 · 3 会话 · 4 永久 · Ctrl+C 取消整轮\n"
                    "targets/arguments/content/scope/summary/results · next/back/all · PgUp/PgDn")
        if self.phase == "idle":
            return "Enter 发送 · Esc+Enter 换行 · / 发现命令 · F2 详情 · Ctrl+C 清草稿／空草稿退出"
        if self.phase == 'cancelling':
            return '正在停止，等待流和工具清理；已完成操作可能保留'
        if self.phase == 'control':
            return 'Ctrl+C 取消当前控制指令，等待清理完成'
        return "Ctrl+C 取消启动" if self.phase == "starting" else "Ctrl+C 取消本轮" if self.phase in {"running", "summary"} else "正在清理资源"

    def _tiny(self):
        size = self.output.get_size()
        return size.rows < 8 or size.columns < 20

    def _review_height(self):
        # 各固定区域使用相同的精确高度，页面不能多于实际分配的窗口。
        rows = self.output.get_size().rows
        overhead = self._review_overhead() + self._active_height()
        return max(1, min(18, rows - overhead))

    def _hint_height(self):
        width = max(2, self.output.get_size().columns - 1)
        return sum(max(1, (get_cwidth(line) + width - 1) // width) for line in self._hint().split('\n'))

    def _review_overhead(self):
        return (1 + self._hint_height() + int(self.output.get_size().rows >= 6)
                + (int(bool(self._notice)) + 1 if not self._tiny() else 0))

    def _review_parts(self):
        if self._view is None:
            return "", 0, 1
        size = self.output.get_size()
        width = max(2, size.columns - 1)
        if size.rows < 6:
            width = max(2, width - min(10, width // 2))
        results = self.results() if self._section == "results" else ""
        key = (self._section, self._page, size.columns, self._review_height(), results)
        if self._review_cache is None or self._review_cache[0] != key:
            if self._section == "results":
                parts = ApprovalView.page_text(results or "暂无旁路结果", self._page,
                    width=width, height=self._review_height())
            else:
                parts = self._view.page(self._section, self._page, width=width,
                                        height=self._review_height())
            self._page = parts[1]
            self._review_cache = ((self._section, self._page, size.columns, self._review_height(), results), parts)
        return self._review_cache[1]

    def _review_header(self):
        _, page, total = self._review_parts()
        width = max(1, self.output.get_size().columns - 1)
        identity = self._view.identity() if self._view and hasattr(self._view, 'identity') else ''
        title = _fit_width(f"审阅 {page + 1}/{total} 页 · {self._section} · {identity}", width)
        remaining = width - get_cwidth(title) - 5
        if self._view and remaining > 0:
            identity = _fit_width(terminal_text(self._view.request_id, self.secret), remaining)
            title += f" · [{identity}]"
        return title

    def _review_text(self):
        body, page, total = self._review_parts()
        if self.output.get_size().rows < 6:
            reserve = min(10, max(2, self.output.get_size().columns - 1) // 2)
            return _fit_width(f'{page + 1}/{total}', reserve - 1).ljust(reserve) + body
        return body

    def set_live_text(self, text, *, fragments=None):
        """未换行的正文实时留在布局内，完整行再进入终端滚动记录。"""
        self.live.set_document(Document(text, len(text)), bypass_readonly=True)
        self._live_fragments = fragments if fragments is not None else [('', text)]
        self.application.invalidate()

    def _discover(self, buffer):
        """只用当前注册表发现命令；粘贴、详情和完整命令不拦截提交。"""
        if self._discover_inhibited or self.phase != 'idle' or self.details_open:
            return
        text = buffer.text
        content = text.lstrip()
        if (text == self._dismissed_text or '\n' in text or '\r' in text or
                not content.startswith('/') or content.startswith('//') or
                any(char.isspace() for char in content) or self.registry.find(content[1:])):
            buffer.cancel_completion()
            return
        if self.registry.completions(text):
            buffer.start_completion(select_first=False)

    def _details_text(self):
        size = self.output.get_size()
        body, page, total = ApprovalView.page_text(self.details(self._details_section), self._details_page,
            width=max(2, size.columns - 1), height=max(1, min(19, size.rows - 2 - self._hint_height())))
        self._details_page = page
        label = '调用' if self._details_section == 'tools' else '思考'
        return f'最近任务 · {label} · {page + 1}/{total} 页\n{body}'

    def _toggle_details(self):
        self.discard_pending()
        self.chat.cancel_completion()
        self.details_open = not self.details_open
        self.application.layout.focus(self._details_control if self.details_open else self._chat_control)
        self.application.invalidate()

    def _bindings(self):
        bindings = KeyBindings()
        idle = Condition(lambda: self.phase == "idle" and not self.details_open)
        approval = Condition(lambda: self.phase == "approval")

        @bindings.add('c-b', eager=True)
        def background(event):
            self.on_background()

        @bindings.add("enter", eager=True)
        def submit(event):
            if self.details_open:
                return
            if self.phase == "idle" and self.chat.complete_state:
                state = self.chat.complete_state
                self.chat.apply_completion(state.current_completion or state.completions[0])
                return
            if self.phase == "idle" and self._pending is not None:
                text = self.chat.text
                future = self._pending
                self.set_phase("running")
                if not future.done():
                    future.set_result(text)
            elif self.phase == "approval" and self._pending is not None:
                choice = self.answer.text.strip().lower()
                self.answer.set_document(Document(""), bypass_readonly=True)
                if choice in self._view.decisions:
                    future = self._pending
                    decision = self._view.decisions[choice]
                    self.set_phase("running")
                    if not future.done():
                        future.set_result(decision)
                elif choice in {"next", "n", "back", "b"}:
                    self._page = max(0, self._page + (1 if choice in {"next", "n"} else -1))
                    self._notice = ""
                elif choice in {"summary", "targets", "arguments", "content", "scope", "all", "results"}:
                    self._section, self._page, self._notice = choice, 0, ""
                else:
                    self._notice = "无效选项；尚未批准，请重新选择或浏览详情"
                self.application.invalidate()

        @bindings.add("escape", eager=True, filter=idle & Condition(lambda: self.chat.complete_state is not None))
        def dismiss_completion(event):
            self._dismissed_text = self.chat.text
            self.chat.cancel_completion()

        @bindings.add('f2', eager=True,
                      filter=Condition(lambda: self.phase in {'idle', 'running', 'summary'}))
        def details(event):
            self._toggle_details()

        @bindings.add('escape', eager=True, filter=Condition(lambda: self.details_open))
        def close_details(event):
            self._toggle_details()

        @bindings.add('tab', eager=True, filter=Condition(lambda: self.details_open))
        def details_tab(event):
            self._details_section = 'thinking' if self._details_section == 'tools' else 'tools'
            self._details_page = 0

        @bindings.add("escape", "enter", filter=idle)
        def newline(event):
            self.chat.insert_text("\n")

        @bindings.add(Keys.BracketedPaste, eager=True)
        def paste(event):
            if self.details_open:
                return
            if getattr(event.key_sequence[-1], "paste_generation", self.generation) != self.generation:
                return
            text = event.data.replace("\r\n", "\n").replace("\r", "\n")
            if self.phase == "idle":
                self._discover_inhibited = True
                try:
                    self.chat.cancel_completion()
                    self.chat.insert_text(text)
                finally:
                    self._discover_inhibited = False
            elif self.phase == "approval":
                # 粘贴只编辑当前答案；多行答案不拆成多次决定。
                self.answer.insert_text(text)

        @bindings.add("c-c", eager=True)
        def interrupt(event):
            if self.phase == 'idle' and self.chat.text:
                self.chat.cancel_completion()
                self.chat.set_document(Document(''), bypass_readonly=True)
                self.discard_pending()
                return
            self.on_interrupt()
            if self._cancel is not None:
                self._cancel.set()
            if self.phase == "idle" and self._pending is not None and not self._pending.done():
                self._pending.cancel()
            elif self.phase not in {'idle', 'closing'}:
                self.set_phase('cancelling')
            self.discard_pending()

        @bindings.add("c-d", eager=True)
        def eof(event):
            if self.phase == "idle" and self.chat.text:
                self.chat.delete()
                return
            self.eof = True
            if self._cancel is not None:
                self._cancel.set()
            self.on_interrupt()
            if self._pending is not None and not self._pending.done():
                self._pending.set_result(None)
            self.discard_pending()

        @bindings.add("up", filter=idle)
        def up(event):
            if self.chat.complete_state:
                self.chat.complete_previous()
            elif self.chat.document.cursor_position_row:
                self.chat.cursor_up()
            elif self._history and self._history_index > 0:
                if self._history_index == len(self._history):
                    self._history_draft = self.chat.text
                self._history_index -= 1
                self.chat.document = Document(self._history[self._history_index])

        @bindings.add("down", filter=idle)
        def down(event):
            if self.chat.complete_state:
                self.chat.complete_next()
            elif not self.chat.document.on_last_line:
                self.chat.cursor_down()
            elif self._history_index < len(self._history):
                self._history_index += 1
                text = self._history[self._history_index] if self._history_index < len(self._history) else self._history_draft
                self.chat.document = Document(text, len(text))

        @bindings.add("tab", filter=idle)
        def complete(event):
            if self.chat.complete_state:
                self.chat.complete_next()
            else:
                choices = list(self.chat.completer.get_completions(self.chat.document, CompleteEvent(completion_requested=True)))
                if len(choices) == 1:
                    self.chat.apply_completion(choices[0])
                elif choices:
                    self.chat.start_completion(select_first=True)

        @bindings.add("pageup", filter=approval | Condition(lambda: self.details_open))
        @bindings.add('up', filter=approval | Condition(lambda: self.details_open))
        def previous_page(event):
            if self.details_open:
                self._details_page = max(0, self._details_page - 1)
            else:
                self._page = max(0, self._page - 1)

        @bindings.add("pagedown", filter=approval | Condition(lambda: self.details_open))
        @bindings.add('down', filter=approval | Condition(lambda: self.details_open))
        def next_page(event):
            if self.details_open:
                self._details_page += 1
            else:
                self._page += 1

        @bindings.add(Keys.Any, filter=Condition(lambda: self.details_open or self.phase not in {"idle", "approval"}))
        def ignore_running(event):
            pass

        return bindings

    def discard_pending(self):
        """清除已解析提前输入；跨阶段未结束粘贴仍读到结束标记再丢弃。"""
        self.generation += 1
        if self._paste_boundary is None:
            self.input.flush_keys()
        self.application.key_processor.input_queue.clear()
        try:
            fd = self.input.fileno()
            if os.isatty(fd) and not (self._paste_boundary and self._paste_boundary.pending):
                termios.tcflush(fd, termios.TCIFLUSH)
        except (OSError, ValueError):
            pass

    def set_phase(self, phase):
        keep_details = self.details_open and self.phase in {'running', 'summary'} and phase == 'idle'
        self.discard_pending()
        self.phase = phase
        self.details_open = keep_details
        self.chat.set_document(Document(""), bypass_readonly=True)
        self.answer.set_document(Document(""), bypass_readonly=True)
        if phase in {"idle", "approval"}:
            self.application.layout.focus(self._details_control if keep_details else
                self._chat_control if phase == "idle" else self._answer_control)
        self.application.invalidate()

    async def start(self):
        self._task = asyncio.create_task(self.application.run_async(
            pre_run=self._started.set, handle_sigint=False, set_exception_handler=False))
        self._task.add_done_callback(self._application_done)
        ready = asyncio.create_task(self._started.wait())
        try:
            await asyncio.wait((ready, self._task), return_when=asyncio.FIRST_COMPLETED)
            if self._task.done():
                await self._task
            self._ready = True
        finally:
            ready.cancel()
            await asyncio.gather(ready, return_exceptions=True)

    def _application_done(self, task):
        if self._closing or task.cancelled():
            return
        error = task.exception()
        if not self._ready and not isinstance(error, EOFError):
            return
        if error is not None:
            # EOF 在启动／运行期同样必须取消资源所有者，不依赖下一次 readline。
            self.eof = True
            self.on_interrupt()
            if self._cancel is not None:
                self._cancel.set()
            if isinstance(error, EOFError) and self._pending is not None and not self._pending.done():
                self._pending.set_result(None)

    async def _wait(self, cancel=None):
        watcher = asyncio.create_task(cancel.wait()) if cancel is not None else None
        waiters = [self._pending, self._task] + ([watcher] if watcher else [])
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if watcher is not None and cancel.is_set():
                raise asyncio.CancelledError
            if self._task.done():
                try:
                    await self._task
                except EOFError:
                    self.eof = True
                    if cancel is not None:
                        cancel.set()
                    return None
            return await self._pending
        finally:
            if watcher:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            if self._pending is not None and not self._pending.done():
                self._pending.cancel()

    async def readline(self, cancel=None):
        async with self._lock:
            self._pending = asyncio.get_running_loop().create_future()
            self._cancel = cancel
            self._history_index, self._history_draft = len(self._history), ""
            self.set_phase("idle")
            if self._saved_draft is not None:
                self.chat.set_document(self._saved_draft, bypass_readonly=True)
                self._saved_draft = None
            try:
                return await self._wait(cancel)
            finally:
                self._pending, self._cancel = None, None

    def save_draft(self):
        """自动接续前保存正文和光标；运行及审批仍独占输入。"""
        self._saved_draft = self.chat.document

    def input_submitted(self):
        """提交 Future 比多层读取协程更早完成，供空闲调度原子检查。"""
        return self._pending is not None and self._pending.done() and not self._pending.cancelled()

    def remember(self, text):
        if text.strip() and (not self._history or self._history[-1] != text):
            self._history.append(text)

    async def approve(self, view, cancel):
        async with self._lock:
            if cancel.is_set():
                return "deny"
            self._pending = asyncio.get_running_loop().create_future()
            self._cancel, self._view = cancel, view
            self._section, self._page, self._notice = "summary", 0, ""
            self._review_cache = None
            self.set_phase("approval")
            try:
                return await self._wait(cancel) or "deny"
            except asyncio.CancelledError:
                cancel.set()
                return "deny"
            finally:
                self._pending, self._cancel, self._view = None, None, None
                self.set_phase("cancelling" if cancel.is_set() else "running")

    async def close(self):
        self._closing = True
        try:
            if self._task is not None:
                if not self._task.done():
                    self.application.exit()
                with suppress(EOFError, asyncio.CancelledError):
                    await self._task
        finally:
            self._history.clear()
            if self._paste_boundary is not None:
                self._paste_boundary.close()
