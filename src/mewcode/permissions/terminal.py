"""单一可取消输入拥有者与完整操作审批展示。"""

import asyncio
import codecs
import difflib
from io import StringIO
import json
import os
from pathlib import Path
import termios
import textwrap


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
    def __init__(self, reader: InputReader, output, *, root: str | Path = "",
                 secret: str = "", page_lines: int = 24):
        self.reader, self.output = reader, output
        self.root, self.secret = str(root), secret
        self.page_lines = page_lines
        self._lock = asyncio.Lock()

    def _line(self, text):
        self.output.write(_safe(str(text), self.secret) + "\n")
        self.output.flush()

    def _details(self, request):
        lines = ["完整参数：", json.dumps(request.arguments, ensure_ascii=False, indent=2)]
        arguments = request.arguments
        if request.tool == "write_file":
            lines += ["完整写入内容：", arguments.get("content", "")]
        elif request.tool == "edit_file":
            lines += ["编辑差异（仅当前调用提供的原文与新文）：", "\n".join(difflib.unified_diff(
                arguments.get("old_text", "").splitlines(), arguments.get("new_text", "").splitlines(),
                fromfile="old_text", tofile="new_text", lineterm=""))]
        text = _safe("\n".join(lines), self.secret)
        return [part for line in text.splitlines() for part in
                (textwrap.wrap(line, width=100, replace_whitespace=False, drop_whitespace=False) or [""])]

    async def __call__(self, request, cancel_event: asyncio.Event) -> str:
        if not self.reader.interactive:
            self._line("权限> 没有可用决策通道，拒绝未获授权操作")
            return "deny"
        async with self._lock:
            if cancel_event.is_set():
                return "deny"
            # 新请求不能消费上一阶段的提前输入；请求内翻页和重问不清输入。
            self.reader.discard_pending()
            self._line(f"权限> [{request.id}] {request.tool} · 等待授权 · {request.mode}")
            self._line(f"原因> {request.reason}")
            self._line(f"工作根目录> {self.root}")
            target_label = "精确命令授权对象" if request.tool == "execute_command" else "真实目标"
            self._line(target_label + "> " + ("\n".join(request.targets) or "无文件目标"))
            self._line("范围> 本次绑定当前完整参数及目标，仅使用一次。")
            if request.tool == "execute_command":
                self._line("范围> 会话／永久仅绑定此根目录、工具与以下完整精确命令：")
                self._line(request.arguments.get("command", ""))
            else:
                self._line("范围> 会话／永久仅绑定此根目录、工具与列出的真实文件；允许未来同一路径使用不同内容或搜索表达式，不扩展目录或其他工具。")
            self._line("期限> 会话在本次运行有效；永久保存于项目本地权限文件，可用 /permissions revoke 撤销。")
            details = self._details(request)
            page = 0
            def show_page():
                total = max(1, (len(details) + self.page_lines - 1) // self.page_lines)
                self._line(f"详情> 第 {page + 1}/{total} 页（next 下一页，back 上一页，all 查看全部）")
                self._line("\n".join(details[page * self.page_lines:(page + 1) * self.page_lines]))
            show_page()
            while not cancel_event.is_set():
                self._line("授权> 1 拒绝 [默认] / 2 本次 / 3 会话 / 4 永久；也可 next/back/all 查看详情")
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
                decisions = {"": "deny", "1": "deny", "deny": "deny", "2": "once", "once": "once",
                             "3": "session", "session": "session", "4": "permanent", "permanent": "permanent"}
                if choice in decisions:
                    return decisions[choice]
                if choice == "all":
                    self._line("\n".join(details))
                elif choice in {"next", "n", "back", "b"}:
                    last = max(0, (len(details) - 1) // self.page_lines)
                    page = min(last, page + 1) if choice in {"next", "n"} else max(0, page - 1)
                    show_page()
                else:
                    self._line("提示> 无效选项，请选择 1/2/3/4 或查看详情；尚未批准")
            return "deny"
