"""终端输入与异步事件展示；活动任务统一接收取消信号。"""

import asyncio
import signal
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from .async_utils import protected
from .config import ConfigError, ProviderConfig, load_config
from .providers import make_provider
from .permissions.terminal import InputReader
from .session import ChatSession
from .tools.base import ToolError
from .types import AgentEvent, Provider, ProviderError, TokenUsage
from .mcp.config import load_config as load_mcp_config
from .mcp.manager import MCPManager
from .commands import parse_command, dispatch, RegistrationError
from .commands.builtins import build_registry
from .commands.adapter import SessionCommandContext
from .terminal.controller import TerminalController
from .terminal.text import terminal_text
from .runtime import RuntimeResources, BackgroundPump



from .terminal.render import _Renderer


async def _run(config, input_stream, output_stream, error_stream, factory, *,
               permission_mode="default", approval_responder=None, input_reader=None, resume=None, team=None,
               persistent=True, memory_enabled=True, user_root=None, config_path=None):
    if resume is not None and team is not None:
        error_stream.write('启动失败：--resume 与 --team 不能同时使用。\n')
        return 2
    resources = RuntimeResources(config, root=Path.cwd(), provider_factory=factory,
        permission_mode=permission_mode, approval_responder=approval_responder,
        persistent=persistent, resume=resume, team=team, memory_enabled=memory_enabled,
        user_root=user_root, config_path=config_path, registry_factory=build_registry,
        session_factory=ChatSession, mcp_factory=MCPManager, mcp_config_loader=load_mcp_config)
    try:
        registry = resources.validate()
    except (ValueError, ToolError) as error:
        error_stream.write(f"启动失败：{terminal_text(str(error), config.api_key)}\n")
        return 2
    loop = asyncio.get_running_loop()
    active_cancel = asyncio.Event()
    idle_cancel = asyncio.Event()
    has_handler = threading.current_thread() is threading.main_thread()
    previous_handler = signal.getsignal(signal.SIGINT) if has_handler else None

    def interrupt(signum=None, frame=None):
        cancel = active_cancel if active_cancel is not None else idle_cancel
        loop.call_soon_threadsafe(cancel.set)
        if terminal is not None and active_cancel is not None:
            loop.call_soon_threadsafe(lambda: terminal.cancelling(force=True))

    if has_handler:
        signal.signal(signal.SIGINT, interrupt)
    renderer = _Renderer(output_stream, config.api_key)
    mcp, terminal, session = None, None, None
    try:
        try:
            resources.create_provider()
            reader = input_reader or InputReader(input_stream)
            def maintenance(event):
                # 独立通道不写入当前任务统计，也不改变输入阶段或草稿。
                if terminal is not None:
                    terminal.show(renderer, event, maintenance=True)
                else:
                    from .terminal.projection import TerminalProjection
                    for visible in TerminalProjection.maintenance_events(event):
                        renderer.show(visible)
            resources.notify = maintenance
            session = resources.construct()
            await resources.resume_team()
            terminal = TerminalController(reader, output_stream, secret=config.api_key,
                root=session.executor.context.root, on_interrupt=interrupt, allow_enhanced=input_reader is None, registry=registry)
            terminal.sync_session(session)
            renderer = _Renderer(terminal.stream, config.api_key)
            session.permissions.responder = approval_responder or terminal.approve
            await terminal.start()
        except (ValueError, ToolError, OSError) as error:
            error_stream.write(f"启动失败：{renderer.safe(error)}\n")
            return 2
        def show_mcp(diagnostic):
            server = renderer.safe(diagnostic.server, 100) or "全部 Server"
            renderer.line(f"MCP> {server} · {renderer.safe(diagnostic.stage)} · {renderer.safe(diagnostic.message)}")
        resources.mcp_notify = show_mcp
        mcp = await resources.bind_mcp(cancel_event=active_cancel)
        if active_cancel.is_set():
            return 0
        terminal.state.bind_tools(mcp.tools)
        try:
            await resources.prepare(cancel_event=active_cancel)
        except (ValueError, OSError) as error:
            error_stream.write(f'恢复失败：{renderer.safe(error, 400)}\n')
            return 2
        if active_cancel.is_set():
            return 0
        for warning in session.warnings:
            renderer.line(f'提示> {renderer.safe(warning, 400)}')
        active_cancel = None
        renderer.line(f"MewCode · {renderer.safe(config.name, 100)} · 权限 {session.permissions.mode} · /help 查看帮助")
        if session.session_id:
            count = sum(message.role != 'context' for message in session.history)
            status = f'已恢复 · {count} 条工作消息 · 等待新输入' if session.resumed else '新建存档'
            renderer.line(f'会话> {session.session_id} · {status}')
        context = SessionCommandContext(registry, session, terminal, renderer, config)
        def notice(event):
            terminal.write(f'后台> {event.run_id} · 父 {event.parent_run_id} · '
                f'{event.text} · {event.phase}（/tasks show 查看结果与用量）\n')
        notice_task = asyncio.create_task(resources.background.notices(notice))
        while True:
            terminal.sync_session(session)
            try:
                question, resume_task = await _idle_input(terminal, session, idle_cancel)
            except (KeyboardInterrupt, asyncio.CancelledError):
                renderer.line("")
                return 0
            except (OSError, ValueError, UnicodeError) as error:
                error_stream.write(f"输入通道失效：{renderer.safe(error)}\n")
                return 2
            if resume_task:
                terminal.begin_task(title='自动接续', source='自动接续', parent_id=str(resume_task))
                active_cancel = asyncio.Event()
                source = session.resume_parent(resume_task, cancel_event=active_cancel)
                try:
                    async for event in source:
                        terminal.show(renderer, event, managed_approval=approval_responder is None)
                finally:
                    await protected(source.aclose(), cancel_event=active_cancel)
                    terminal.finish_task(reason='cancelled' if active_cancel.is_set() else 'interrupted')
                    active_cancel = None
                    terminal.restore_draft()
                continue
            if question is None:
                renderer.line("")
                return 0
            if question.strip():
                try:
                    updated, diagnostics = session.refresh_skills()
                    if updated is not None:
                        registry = context.registry = updated
                        terminal.replace_registry(updated)
                    for diagnostic in diagnostics:
                        renderer.line('提示> ' + renderer.safe(diagnostic, 500))
                except OSError as error:
                    renderer.line('提示> ' + renderer.safe(error, 400))
                    continue
            parsed = parse_command(question)
            control = parsed.kind == 'command' and parsed.name in {'plan', 'do', 'reset', 'tasks', 'team'}
            if control:
                terminal.set_phase('control')
            active_cancel = asyncio.Event()
            context.cancel_event = active_cancel
            try:
                result = await dispatch(parsed, registry, context)
                cancelled_control = active_cancel.is_set()
            finally:
                if active_cancel.is_set():
                    terminal.discard_pending()
                if control or active_cancel.is_set():
                    terminal.set_phase('idle')
                active_cancel = context.cancel_event = None
            if cancelled_control:
                continue
            if result.kind == 'exit':
                return 0
            if result.kind == 'handled':
                continue
            if result.kind != 'summary':
                terminal.echo(renderer, question)
            if parsed.kind == 'message':
                terminal.remember(question)
            maintenance = result.kind == 'summary'
            if maintenance:
                terminal.set_phase('summary')
            else:
                terminal.begin_task(title=question)
            terminal.sync_session(session)
            active_cancel = asyncio.Event()
            source = (session.compact(cancel_event=active_cancel) if maintenance else
                      session.run_skill(result.skill_name, result.text, cancel_event=active_cancel, user_text=question) if result.kind == 'skill' else
                      session.ask(result.text, cancel_event=active_cancel))
            try:
                async for event in source:
                    terminal.show(renderer, event, managed_approval=approval_responder is None, maintenance=maintenance)
            except (ProviderError, OSError) as error:
                renderer.line(f"提示> {renderer.safe(error, 300)}")
            finally:
                await protected(source.aclose(), cancel_event=active_cancel)
                if active_cancel.is_set():
                    terminal.discard_pending()
                if not maintenance:
                    terminal.finish_task(reason='cancelled' if active_cancel.is_set() else 'interrupted')
                active_cancel = None
                if maintenance:
                    terminal.set_phase("idle")
            if terminal.eof:
                return 0
    finally:
        if 'notice_task' in locals():
            notice_task.cancel()
            await asyncio.gather(notice_task, return_exceptions=True)
        active_cancel = asyncio.Event()
        try:
            if terminal is not None:
                terminal.set_phase("closing")
            report = await resources.aclose(cancel_event=active_cancel)
            for diagnostic in report.errors:
                renderer.line('提示> ' + diagnostic)
            if report.pending:
                renderer.line('提示> 运行资源收尾超过期限；存档及资源状态待核查')
        finally:
            try:
                if terminal is not None:
                    await terminal.close()
            finally:
                if has_handler:
                    signal.signal(signal.SIGINT, previous_handler)


async def _idle_input(terminal, session, cancel):
    """唯一输入拥有者与结果唤醒竞争；已提交输入优先，草稿暂存。"""
    async def read_question():
        try:
            return await terminal.readline(cancel)
        except KeyboardInterrupt:
            raise asyncio.CancelledError from None
    read = asyncio.create_task(read_question())
    wake = asyncio.create_task(BackgroundPump(session).wait_ready())
    try:
        await asyncio.wait((read, wake), return_when=asyncio.FIRST_COMPLETED)
        # 终端后台 future 可能先于输入包装任务完成，给已提交控制保留优先权。
        await asyncio.sleep(0)
        if read.done() or terminal.input_submitted():
            return await read, None
        parent = wake.result()
        terminal.save_draft()
        return '', parent
    finally:
        read.cancel()
        wake.cancel()
        await asyncio.gather(read, wake, return_exceptions=True)


def run(config_path: str | Path, *, stdin: TextIO | None = None, stdout: TextIO | None = None,
        stderr: TextIO | None = None, provider_factory: Callable[[ProviderConfig], Provider] | None = None,
        permission_mode: str = "default", approval_responder=None, input_reader=None, resume=None,
        team=None, list_sessions=False, persistent=True, memory_enabled=True, user_root=None) -> int:
    """保留同步启动接口，由单个事件循环管理会话与客户端。"""
    input_stream = stdin if stdin is not None else sys.stdin
    output_stream = stdout if stdout is not None else sys.stdout
    error_stream = stderr if stderr is not None else sys.stderr
    if list_sessions:
        from .sessions import scan_sessions
        try:
            rows = scan_sessions(Path.cwd())
            output_stream.write('会话 ID · 标题 · 消息数 · 最后活动 · 状态\n')
            for row in rows:
                status = '活动中' if row.active else '可恢复' if row.recoverable else '不可恢复'
                output_stream.write(terminal_text(f'{row.id} · {row.title} · {row.message_count} · {row.last_activity.isoformat()} · {status}', '', limit=500) + '\n')
            if not rows:
                output_stream.write('没有会话存档\n')
            return 0
        except (ValueError, OSError) as error:
            error_stream.write(f'扫描失败：{terminal_text(str(error), "", limit=400)}\n')
            return 2
    try:
        config = load_config(config_path)
    except ConfigError as error:
        error_stream.write(f"配置错误：{error}\n")
        return 2
    return asyncio.run(_run(config, input_stream, output_stream, error_stream, provider_factory or make_provider,
                           permission_mode=permission_mode, approval_responder=approval_responder,
                           input_reader=input_reader, resume=resume, persistent=persistent,
                           team=team, config_path=Path(config_path).expanduser().resolve(),
                           memory_enabled=memory_enabled, user_root=user_root))
