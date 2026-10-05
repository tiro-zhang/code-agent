"""命令入口只在需要时提交真实工作任务。"""
from dataclasses import replace
from io import StringIO
import pytest
from conftest import ScriptedProvider
from test_agent_loop import answer
from test_app import invoke, config_file
from test_memory_store import note_value, write_note


def test_local_commands_do_not_submit_or_create_memory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider([])
    code, shown = invoke(tmp_path, provider,
        '/HELP\n/CLEAR\n/SESSION\n/session list\n/MEMORY\n/memory list\n/PLAN\n/DO\n/do\n'
        '/PERMISSION mode strict\n/PeRmIsSiOnS\n/status\n/unknown\n/exit\n')
    assert code == 0 and provider.requests == []
    assert '清屏不可用' in shown and '\x1b' not in shown
    assert '未启用存档' in shown and '没有会话存档' in shown
    assert '自动维护：关闭' in shown and '没有笔记' in shown
    assert '[DEFAULT]' in shown and '待执行计划' not in shown
    assert 'strict' in shown and '未知命令' in shown
    assert not (tmp_path / '.mewcode' / 'memory').exists()


def test_review_expands_once_and_preserves_target(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider([answer('审查一'), answer('审查二'), answer('计划')])
    target = '/clear  "A B.py"  X'
    _, shown = invoke(tmp_path, provider, '/permission mode bypass\n/review\n/review ' + target + '\n/plan 任务\n/do\n/exit\n')
    assert len(provider.requests) == 3
    first = [m.content for m in provider.requests[0][0] if m.role == 'user'][-1]
    second = [m.content for m in provider.requests[1][0] if m.role == 'user'][-1]
    assert first.startswith('执行 Skill review')
    first_context = provider.requests[0][0][-1].content
    assert 'Git' in first_context and '未提交' in first_context and '不自动修改' in first_context
    assert second.endswith(target)
    assert target in provider.requests[1][0][-1].content
    assert [m.content for m in provider.requests[2][0] if m.role == 'user'][-1] == '任务'
    assert len(provider.requests[2][1]['tools']) == 5


def test_disabled_memory_show_is_complete_and_safe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / '.mewcode' / 'memory'
    write_note(root, note_value(content='起始\x1b[31m\n' + '正文' * 1500 + '\n结束'))
    provider = ScriptedProvider([])
    _, shown = invoke(tmp_path, provider, '/memory list project\n/memory show project ' + 'a' * 32 + '\n/exit\n')
    assert 'project/' + 'a' * 32 in shown
    assert '正文' * 1500 in shown and '结束' in shown and '\x1b' not in shown
    assert not (root / '.lock').exists() and not provider.requests


@pytest.mark.parametrize('command', ['/do extra', '/clear extra', '/memory show project ../x',
    '/memory list global', '/session resume latest', '/permission mode invalid'])
def test_bad_arguments_have_no_model_effect(tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider([])
    _, shown = invoke(tmp_path, provider, command + '\n/exit\n')
    assert '用法' in shown and not provider.requests
    if command.startswith('/session'): assert '--resume' in shown


def test_conflicting_registry_fails_before_resources(tmp_path, monkeypatch):
    from mewcode import app
    from mewcode.commands import CommandRegistry
    from mewcode.commands.builtins import builtin_definitions
    definitions = builtin_definitions()
    def broken(catalog=None):
        return CommandRegistry((*definitions, replace(definitions[0], name='hidden', aliases=('HELP',), hidden=True)))
    monkeypatch.setattr(app, 'build_registry', broken)
    def forbidden(*args, **kwargs):
        pytest.fail('不应创建交互资源')
    monkeypatch.setattr(app, 'ChatSession', forbidden)
    monkeypatch.setattr(app, 'MCPManager', forbidden)
    errors = StringIO()
    assert app.run(config_file(tmp_path), stdin=StringIO(), stdout=StringIO(), stderr=errors,
                   provider_factory=forbidden) == 2
    assert '冲突' in errors.getvalue() and 'help' in errors.getvalue()


def test_history_keeps_escaped_user_input_and_excludes_command_prompts(tmp_path, monkeypatch):
    from mewcode.terminal.controller import TerminalController
    from mewcode.commands import parse_command
    monkeypatch.chdir(tmp_path)
    remembered = []
    monkeypatch.setattr(TerminalController, 'remember', lambda self, text: remembered.append(text))
    provider = ScriptedProvider([answer('普通'), answer('计划'), answer('审查'), answer('重用')])
    invoke(tmp_path, provider, '//permission mode bypass\n/plan /exit\n/review Src/A.py\n/exit\n')
    assert remembered == ['//permission mode bypass']
    assert parse_command(remembered[0]).kind == 'message'
    assert parse_command(remembered[0]).text == '/permission mode bypass'
