"""固定动作执行器；不经模型工具入口，副作用与资源各自受控。"""

import asyncio
import json
import logging
import os
from pathlib import Path
import signal

import httpx2

from ..async_utils import protected
from ..tools.base import ToolError
from .models import ActionResult
from .prompts import PromptQueue

LOGGER = logging.getLogger("mewcode.hooks")


def diagnostic(rule, event, message):
    """日志故障也必须隔离；不记录动作输入、输出或异常原文。"""
    try:
        LOGGER.warning("Hook %s event=%s: %s", rule.identity, event.event, message)
    except Exception:
        pass


def decision_output(raw: bytes, source: str) -> ActionResult:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        decision = value.get("decision", "")
        if "decision" in value and decision not in ("allow", "deny"):
            raise ValueError
        reason = value.get("reason", "")
        if "reason" in value and not isinstance(reason, str):
            raise ValueError
        if decision == "deny" and not reason.strip():
            raise ValueError
        return ActionResult(decision=decision, reason=reason, source=source)
    except (ValueError, TypeError, UnicodeError):
        return ActionResult("failed")


async def stop_group(process):
    """始终清理进程组，即使 shell 已退出但后代仍持有管道。"""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            if process.returncode is None:
                raise
        if sig == signal.SIGTERM:
            await asyncio.sleep(.2)
    await process.wait()


class ActionRunner:
    def __init__(self, root: Path, *, permissions, prompts=None, current_mode=None,
                 http_timeout: float = 30, response_limit: int = 64 * 1024):
        self.root = Path(root).resolve()
        self.permissions = permissions
        self.prompts = prompts if prompts is not None else PromptQueue()
        self.current_mode = current_mode
        self.http_timeout, self.response_limit = http_timeout, response_limit
        self._client = None

    def can_submit(self, rule, event, *, background=False, cancel_event=None):
        if cancel_event is not None and cancel_event.is_set():
            return False
        mode = self.current_mode() if self.current_mode else event.data["mode"]
        if mode == "plan" and rule.action.type in {"command", "http"}:
            return False
        if background and rule.action.type == "command":
            try:
                self.permissions.authorize_noninteractive("execute_command", {"command": rule.action.command}, cancel_event=cancel_event)
            except ToolError as error:
                if error.code in {"approval_required", "cancelled"}:
                    return False
        return True

    async def run(self, rule, event, *, cancel_event=None, background=False, allow_approval=True):
        cancel = cancel_event if cancel_event is not None else asyncio.Event()
        if not self.can_submit(rule, event, background=background, cancel_event=cancel):
            diagnostic(rule, event, "安全状态或后台许可跳过")
            return ActionResult("skipped")
        action = rule.action
        try:
            if action.type == "prompt":
                self.prompts.add(action.text, rule.identity, event.event)
                return ActionResult()
            if action.type == "subagent":
                diagnostic(rule, event, "子 Agent 动作尚未实现")
                return ActionResult("unimplemented")
            if action.type == "command":
                arguments = {"command": action.command}
                if background or not allow_approval:
                    self.permissions.authorize_noninteractive("execute_command", arguments, cancel_event=cancel)
                else:
                    await self.permissions.authorize("execute_command", arguments, cancel_event=cancel,
                                                     origin=(rule.identity, event.event))
                # 授权可能让出执行；再次检查当前模式和取消。
                if not self.can_submit(rule, event, cancel_event=cancel):
                    return ActionResult("skipped")
                result = await self._wait(self._command(rule, event, cancel), cancel, action.timeout_seconds)
            else:
                result = await self._wait(self._http(rule, event), cancel, self.http_timeout)
            if result.status == "failed":
                diagnostic(rule, event, "动作失败或无效决策")
            return result
        except ToolError as error:
            if error.code == "cancelled":
                return ActionResult("cancelled")
            diagnostic(rule, event, "命令许可失败")
            return ActionResult("failed")
        except asyncio.CancelledError:
            cancel.set()
            raise
        except Exception as error:
            diagnostic(rule, event, type(error).__name__)
            return ActionResult("failed")

    async def _wait(self, operation, cancel, timeout):
        running = asyncio.create_task(operation)
        watcher = asyncio.create_task(cancel.wait())
        try:
            done, _ = await asyncio.wait((running, watcher), timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if cancel.is_set():
                return ActionResult("cancelled")
            if running in done:
                return running.result()
            return ActionResult("failed")
        finally:
            watcher.cancel()
            if not running.done():
                running.cancel()
            await protected(asyncio.gather(running, watcher, return_exceptions=True), cancel_event=cancel)

    async def _command(self, rule, event, cancel):
        operation_cancel = asyncio.Event()
        process = await protected(asyncio.create_subprocess_exec(
            "/bin/sh", "-c", rule.action.command, cwd=self.root, start_new_session=True,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE),
            cancel_event=operation_cancel)
        if cancel.is_set() or operation_cancel.is_set():
            # 创建进程时取消仍须取得句柄；取得后不能继续运行动作。
            await protected(stop_group(process), cancel_event=operation_cancel)
            return ActionResult('cancelled')
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        truncated = False

        async def read(name):
            nonlocal truncated
            stream = getattr(process, name)
            while chunk := await stream.read(8192):
                buffer = buffers[name]
                available = max(0, 32 * 1024 - len(buffer))
                buffer.extend(chunk[:available])
                truncated |= len(chunk) > available

        async def write():
            try:
                process.stdin.write(event.to_json().encode())
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()
                try:
                    await process.stdin.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        tasks = [asyncio.create_task(read(name)) for name in buffers]
        tasks.append(asyncio.create_task(write()))
        try:
            await asyncio.gather(*tasks)
            code = await process.wait()
            if code != 0 or truncated:
                return ActionResult("failed")
            return decision_output(bytes(buffers["stdout"]), rule.identity) if event.event == "tool.before" else ActionResult()
        finally:
            await protected(stop_group(process), cancel_event=operation_cancel)
            for task in tasks:
                task.cancel()
            await protected(asyncio.gather(*tasks, return_exceptions=True), cancel_event=operation_cancel)

    async def _http(self, rule, event):
        if self._client is None:
            self._client = httpx2.AsyncClient(timeout=self.http_timeout, follow_redirects=False, trust_env=False)
        action = rule.action
        async with self._client.stream(action.method, action.url, headers=dict(action.headers), json=event.data) as response:
            if not 200 <= response.status_code < 300:
                return ActionResult("failed")
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=8192):
                if len(body) + len(chunk) > self.response_limit:
                    return ActionResult("failed")
                body.extend(chunk)
            return decision_output(bytes(body), rule.identity) if event.event == "tool.before" else ActionResult()

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
        self.prompts.clear()
