"""单一可取消输入拥有者与完整操作审批展示。"""

import asyncio
import codecs
from io import StringIO
import os
from pathlib import Path
import termios
import shutil


def _safe(text: str, secret: str = "") -> str:
    text = text.replace(secret, "[已隐藏]") if secret else text
    return "".join(char if char.isprintable() or char in "\n\t" else repr(char)[1:-1]
                   for char in text)


class InputReader:
    """用事件循环监听 fd；取消后没有后台线程继续消费标准输入。"""

    def __init__(self, stream, *, interactive: bool | None = None):
        self.stream = stream
        self.interactive = bool(stream.isatty()) if interactive is None else interactive
        self.eof = False
        self._buffer = ""
        self._lock = asyncio.Lock()
        self._decoder = codecs.getincrementaldecoder(getattr(stream, "encoding", None) or "utf-8")()

    def discard_pending(self):
        """丢弃上一输入阶段的残留，终端留给当前提示符的新输入。"""
        # 非交互问题流预读的完整行是协议队列，不能在轮次切换时清空。
        if not self.interactive:
            return
        self._buffer = ""
        self._decoder.reset()
        try:
            fd = self.stream.fileno()
            if os.isatty(fd):
                termios.tcflush(fd, termios.TCIFLUSH)
        except (AttributeError, OSError, ValueError):
            pass

    async def readline(self, cancel_event: asyncio.Event | None = None) -> str:
        async with self._lock:
            if cancel_event is not None and cancel_event.is_set():
                self.discard_pending()
                raise asyncio.CancelledError
            # 内存输入不会阻塞；其它输入必须经可撤销的 fd 监听。
            if isinstance(self.stream, StringIO):
                line = self.stream.readline()
                self.eof |= not bool(line) or not line.endswith("\n")
                return line
            fd = self.stream.fileno()
            loop = asyncio.get_running_loop()
            while "\n" not in self._buffer and not self.eof:
                ready = loop.create_future()
                def receive():
                    if ready.done():
                        return
                    try:
                        data = os.read(fd, 4096)
                        self.eof |= not bool(data)
                        self._buffer += self._decoder.decode(data, final=not bool(data))
                        ready.set_result(None)
                    except Exception as error:
                        ready.set_exception(error)
                waiter = asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
                loop.add_reader(fd, receive)
                try:
                    await asyncio.wait((ready, waiter) if waiter else (ready,),
                                       return_when=asyncio.FIRST_COMPLETED)
                    if cancel_event is not None and cancel_event.is_set():
                        raise asyncio.CancelledError
                    await ready
                except asyncio.CancelledError:
                    self.discard_pending()
                    raise
                finally:
                    loop.remove_reader(fd)
                    if not ready.done():
                        ready.cancel()
                    if waiter:
                        waiter.cancel()
                        await asyncio.gather(waiter, return_exceptions=True)
            if "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                return line + "\n"
            line, self._buffer = self._buffer, ""
            return line


class TerminalApproval:
    """兼容后端也消费同一审批快照；仅当前请求读取决定。"""

    def __init__(self, reader: InputReader, output, *, root: str | Path = "",
                 secret: str = "", page_lines: int = 24):
        self.reader, self.output = reader, output
        self.root, self.secret = str(root), secret
        self.page_lines = page_lines
        self._lock = asyncio.Lock()

    def _line(self, text):
        self.output.write(_safe(str(text), self.secret) + "\n")
        self.output.flush()

    async def __call__(self, request, cancel_event: asyncio.Event) -> str:
        from ..terminal.approval import ApprovalView
        if not self.reader.interactive:
            self._line("权限> 没有可用决策通道，拒绝未获授权操作")
            return "deny"
        async with self._lock:
            if cancel_event.is_set():
                return "deny"
            self.reader.discard_pending()
            view = ApprovalView(request, root=self.root, secret=self.secret)
            section, page = "summary", 0
            def show_page():
                nonlocal page
                size = shutil.get_terminal_size(fallback=(100, self.page_lines + 8))
                body, page, total = view.page(section, page, width=size.columns,
                                              height=max(1, min(self.page_lines, size.lines - 6)))
                self._line(f"权限> [{view.request_id}] · 等待授权")
                self._line(f"详情> {section} 第 {page + 1}/{total} 页（next/back/all；targets/arguments/content）")
                self._line(body)
            show_page()
            while not cancel_event.is_set():
                self._line("授权> 1 拒绝 [默认] / 2 本次 / 3 会话 / 4 永久；回车拒绝，Ctrl+C 取消整轮")
                try:
                    line = await self.reader.readline(cancel_event)
                except asyncio.CancelledError:
                    cancel_event.set()
                    return "deny"
                except (OSError, ValueError, UnicodeError):
                    self._line("权限> 决策通道失效，拒绝当前操作")
                    return "deny"
                if not line or self.reader.eof:
                    cancel_event.set()
                    return "deny"
                choice = line.strip().lower()
                if choice in view.decisions:
                    return view.decisions[choice]
                if choice == "all":
                    self._line(view.detail("all"))
                elif choice in {"next", "n", "back", "b"}:
                    page = max(0, page + (1 if choice in {"next", "n"} else -1))
                    show_page()
                elif choice in {"summary", "targets", "arguments", "content"}:
                    section, page = choice, 0
                    show_page()
                else:
                    self._line("提示> 无效选项，请选择 1/2/3/4 或查看详情；尚未批准")
            return "deny"
