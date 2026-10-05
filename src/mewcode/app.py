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



from .terminal.render import _Renderer


async def _run(config, input_stream, output_stream, error_stream, factory, *,
               permission_mode="default", approval_responder=None, input_reader=None, resume=None,
               persistent=True, memory_enabled=True, user_root=None):
    try:
        from .skills.catalog import discover_skills
        catalog = discover_skills(Path.cwd(), user_root=user_root)
        registry = build_registry(catalog)
    except (ValueError, ToolError) as error:
        error_stream.write(f"启动失败：{terminal_text(str(error), config.api_key)}\n")
        return 2
    provider = factory(config)
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
            reader = input_reader or InputReader(input_stream)
            def maintenance(notification):
                kind = notification.get('kind')
                event = AgentEvent('usage' if kind in {'usage', 'restore_usage'} else 'memory_update',
                    purpose=notification.get('purpose', 'memory'), text=notification.get('text', ''),
                    usage=notification.get('usage'), phase=notification.get('status', ''))
                # 独立通道不写入当前任务统计，也不改变输入阶段或草稿。
                if terminal is not None:
                    terminal.show(renderer, event, maintenance=True)
                else:
                    from .terminal.projection import TerminalProjection
                    for visible in TerminalProjection.maintenance_events(event):
                        renderer.show(visible)
            session = ChatSession(provider, max_iterations=config.max_iterations, permission_mode=permission_mode,
                config=config, persistent=persistent, resume=resume, memory_enabled=memory_enabled,
                user_root=user_root, notify=maintenance, skill_catalog=catalog, provider_factory=factory)
            session.permissions.config.load()
            terminal = TerminalController(reader, output_stream, secret=config.api_key,
                root=session.executor.context.root, on_interrupt=interrupt, allow_enhanced=input_reader is None, registry=registry)
            terminal.sync_session(session)
            renderer = _Renderer(terminal.stream, config.api_key)
            session.permissions.responder = approval_responder or terminal.approve
            await terminal.start()
        except (ValueError, ToolError, OSError) as error:
            error_stream.write(f"启动失败：{renderer.safe(error)}\n")
            return 2
        snapshot = load_mcp_config(session.executor.context.root)
        def show_mcp(diagnostic):
            server = renderer.safe(diagnostic.server, 100) or "全部 Server"
            renderer.line(f"MCP> {server} · {renderer.safe(diagnostic.stage)} · {renderer.safe(diagnostic.message)}")
        mcp = MCPManager(snapshot, session.executor.registry, notify=show_mcp)
        session.executor.mcp = mcp
        await mcp.start(cancel_event=active_cancel)
        if active_cancel.is_set():
            return 0
        session.permissions.bind_mcp_tools(mcp.tools)
        terminal.state.bind_tools(mcp.tools)
        try:
            session.validate_skills()
            await session.prepare_restore(cancel_event=active_cancel)
            await session.start(cancel_event=active_cancel)
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
        while True:
            terminal.sync_session(session)
            try:
                question = await terminal.readline(idle_cancel)
            except (KeyboardInterrupt, asyncio.CancelledError):
                renderer.line("")
                return 0
            except (OSError, ValueError, UnicodeError) as error:
                error_stream.write(f"输入通道失效：{renderer.safe(error)}\n")
                return 2
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
            control = parsed.kind == 'command' and parsed.name in {'plan', 'do'}
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
                terminal.begin_task()
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
                active_cancel = None
                if maintenance:
                    terminal.set_phase("idle")
            if terminal.eof:
                return 0
    finally:
        active_cancel = asyncio.Event()
        try:
            if terminal is not None:
                terminal.set_phase("closing")
            if session is not None:
                try:
                    await protected(session.aclose(), cancel_event=active_cancel)
                except OSError:
                    renderer.line('提示> 会话句柄关闭失败，请检查 .mewcode；存档缓存保留')
            try:
                if mcp is not None:
                    await protected(mcp.close(), cancel_event=active_cancel)
            finally:
                await protected(provider.aclose(), cancel_event=active_cancel)
        finally:
            try:
                if terminal is not None:
                    await terminal.close()
            finally:
                if has_handler:
                    signal.signal(signal.SIGINT, previous_handler)


def run(config_path: str | Path, *, stdin: TextIO | None = None, stdout: TextIO | None = None,
        stderr: TextIO | None = None, provider_factory: Callable[[ProviderConfig], Provider] | None = None,
        permission_mode: str = "default", approval_responder=None, input_reader=None, resume=None,
        list_sessions=False, persistent=True, memory_enabled=True, user_root=None) -> int:
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
                           memory_enabled=memory_enabled, user_root=user_root))
