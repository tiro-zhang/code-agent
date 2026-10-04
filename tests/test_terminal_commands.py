"""本地命令识别、正文保留、帮助和补全的边界。"""

import pytest

from conftest import ScriptedProvider, async_test, collect, permission_bypass
from mewcode.session import ChatSession
from mewcode.tools import default_registry
from mewcode.tools.base import ToolContext
from mewcode.tools.executor import ToolExecutor
from test_agent_loop import answer


@pytest.mark.parametrize("draft, kind, text", [
    ("", "empty", ""),
    (" \t\n\r\n", "empty", ""),
    ("  问题 \t", "message", "  问题 \t"),
    ("第一行\n  第二行\n", "message", "第一行\n  第二行\n"),
    ("/exit\n", "message", "/exit\n"),
    ("/permissions mode bypass\n/plan", "message", "/permissions mode bypass\n/plan"),
    ("/exit\r\n正文", "message", "/exit\r\n正文"),
    ("//tmp/example", "message", "/tmp/example"),
    ("  //tmp/example \t", "message", "  /tmp/example \t"),
    ("///exit", "message", "//exit"),
    ("//exit\n正文", "message", "//exit\n正文"),
    ("正文 /exit", "message", "正文 /exit"),
    ("  /help \t", "help", ""),
    ("/status", "status", ""),
    ("/exit", "exit", ""),
    ("/do", "do", ""),
    ("/plan", "plan", ""),
    (" /plan  阅读 README 并  总结  ", "plan", "阅读 README 并  总结"),
    ("/plan\t读取 文件", "plan", "读取 文件"),
    ("/permissions", "permissions", "/permissions"),
    (" /permissions\tmode  strict  ", "permissions", "/permissions mode strict"),
    ("/permissions mode default", "permissions", "/permissions mode default"),
    ("/permissions mode bypass", "permissions", "/permissions mode bypass"),
    ("/permissions revoke session", "permissions", "/permissions revoke session"),
    ("/permissions revoke permanent", "permissions", "/permissions revoke permanent"),
])
def test_parse_preserves_messages_and_recognizes_valid_commands(draft, kind, text):
    from mewcode.terminal.commands import parse_command

    command = parse_command(draft)
    assert (command.kind, command.text) == (kind, text)


@pytest.mark.parametrize("draft, usage", [
    ("/help extra", "/help"),
    ("/status extra", "/status"),
    ("/exit extra", "/exit"),
    ("/do extra", "/do"),
    ("/permissions extra", "/permissions"),
    ("/permissions mode", "/permissions mode strict|default|bypass"),
    ("/permissions mode invalid", "/permissions mode strict|default|bypass"),
    ("/permissions mode strict extra", "/permissions mode strict|default|bypass"),
    ("/permissions revoke", "/permissions revoke session|permanent"),
    ("/permissions revoke invalid", "/permissions revoke session|permanent"),
    ("/permissions revoke session extra", "/permissions revoke session|permanent"),
    ("/hepl", "/help"),
    ("/HELP", "/help"),
    ("/", "/help"),
])
def test_invalid_commands_return_local_chinese_usage(draft, usage):
    from mewcode.terminal.commands import parse_command

    command = parse_command(draft)
    assert command.kind == "error"
    assert usage in command.text
    assert any("\u4e00" <= character <= "\u9fff" for character in command.text)


@pytest.mark.parametrize("enhanced", [False, True])
def test_help_lists_full_commands_and_phase_specific_controls(enhanced):
    from mewcode.terminal.commands import help_text

    shown = help_text(enhanced=enhanced)
    for syntax in ["/help", "/status", "/exit", "/plan [任务]", "/do",
                   "/permissions mode strict", "/permissions mode default",
                   "/permissions mode bypass", "/permissions revoke session",
                   "/permissions revoke permanent", "//", "Enter", "Ctrl+C", "EOF"]:
        assert syntax in shown
    assert "空闲" in shown and "授权" in shown and "取消整轮" in shown
    assert "拒绝" in shown
    if enhanced:
        assert "Esc" in shown and "Alt+Enter" in shown and "换行" in shown
    else:
        assert "逐行" in shown


@pytest.mark.parametrize("draft, expected", [
    ("/he", ["/help"]),
    ("/per", ["/permissions"]),
    (" /per", [" /permissions"]),
    ("/permissions ", ["/permissions mode", "/permissions revoke"]),
    ("/permissions m", ["/permissions mode"]),
    ("/permissions mode ", ["/permissions mode strict", "/permissions mode default", "/permissions mode bypass"]),
    ("/permissions mode b", ["/permissions mode bypass"]),
    ("/permissions revoke p", ["/permissions revoke permanent"]),
    ("/permissions revoke ", ["/permissions revoke session", "/permissions revoke permanent"]),
    ("", []),
    ("问题 /he", []),
    ("//he", []),
    ("/he\n", []),
    ("/plan 文件", []),
    ("/unknown", []),
    ("/permissions mode invalid", []),
    ("/permissions mode strict extra", []),
])
def test_command_completions_only_offer_known_command_positions(draft, expected):
    from mewcode.terminal.commands import command_completions

    assert command_completions(draft) == expected


@async_test
async def test_pending_plan_status_is_readonly_and_tracks_plan_lifecycle(tmp_path):
    provider = ScriptedProvider([answer("待执行计划"), answer("执行结果"), answer("新计划")])
    executor = ToolExecutor(default_registry(), ToolContext(tmp_path), permissions=permission_bypass(tmp_path))
    session = ChatSession(provider, executor=executor)
    assert session.has_pending_plan is False
    session.enter_plan()
    await collect(session.ask("规划任务"))
    before = tuple(session.history)
    assert session.has_pending_plan is True
    assert session.has_pending_plan is True
    assert tuple(session.history) == before and len(provider.requests) == 1
    with pytest.raises(AttributeError):
        session.has_pending_plan = False
    await collect(session.execute_plan())
    assert session.has_pending_plan is False
    session.enter_plan()
    await collect(session.ask("再次规划"))
    assert session.has_pending_plan is True
    session.enter_plan()
    assert session.has_pending_plan is False
