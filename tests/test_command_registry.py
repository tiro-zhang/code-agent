"""注册冲突、共享元数据及框架无关分发的行为。"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from conftest import async_test


async def handle(args, context):
    from mewcode.commands import CommandResult
    context.messages.append(args)
    return CommandResult()


def definition(**options):
    from mewcode.commands import CommandSpec
    return CommandSpec('Inspect', ('I',), '检查', '/inspect [目标]', 'local', handle,
                       accepts_arguments=True, **options)


def test_registry_normalizes_aliases_and_freezes():
    from mewcode.commands import CommandRegistry, RegistrationError
    registry = CommandRegistry([definition()]).freeze()
    assert registry.find('INSPECT') is registry.find('i')
    assert registry.find('i').name == 'inspect'
    assert registry.find('missing') is None
    with pytest.raises(RegistrationError):
        registry.register(replace(definition(), name='next', aliases=()))


@pytest.mark.parametrize('name,aliases,hidden', [
    ('INSPECT', (), False), ('second', ('inspect',), False),
    ('I', (), True), ('second', ('i',), True),
])
def test_registry_rejects_every_shared_namespace_collision(name, aliases, hidden):
    from mewcode.commands import CommandRegistry, RegistrationError
    with pytest.raises(RegistrationError, match='冲突'):
        CommandRegistry([definition(), replace(definition(), name=name, aliases=aliases, hidden=hidden)])


@pytest.mark.parametrize('fields', [
    {'name': ''}, {'name': '/bad'}, {'name': 'has space'}, {'aliases': ('I', 'i')},
    {'name': 'i'}, {'kind': 'unknown'}, {'handler': None},
])
def test_invalid_definition_is_rejected_before_dispatch(fields):
    from mewcode.commands import CommandRegistry, RegistrationError
    with pytest.raises(RegistrationError):
        CommandRegistry([replace(definition(), **fields)])


@async_test
async def test_alias_and_hidden_command_dispatch_through_same_handler():
    from mewcode.commands import CommandRegistry, dispatch, parse_command
    registry = CommandRegistry([definition(hidden=True)]).freeze()
    context = SimpleNamespace(messages=[], show_message=lambda text: None)
    for text in ['/INSPECT Some  Text', '/i Other']:
        result = await dispatch(parse_command(text), registry, context)
        assert result.kind == 'handled'
    assert context.messages == ['Some  Text', 'Other']
    assert registry.completions('/i') == []
    assert 'inspect' not in registry.help_text(enhanced=False)


@async_test
async def test_unknown_and_extra_arguments_never_call_handler():
    from mewcode.commands import CommandRegistry, dispatch, parse_command
    shown = []
    context = SimpleNamespace(messages=[], show_message=shown.append)
    registry = CommandRegistry([replace(definition(), accepts_arguments=False)]).freeze()
    await dispatch(parse_command('/missing'), registry, context)
    await dispatch(parse_command('/inspect extra'), registry, context)
    assert context.messages == []
    assert '/help' in shown[0] and '/inspect' in shown[1]


@pytest.mark.parametrize('draft,kind,name,text', [
    (' \t\n', 'empty', '', ''), ('  正文 \t', 'message', '', '  正文 \t'),
    ('/exit\n正文', 'message', '', '/exit\n正文'),
    ('  //tmp/File ', 'message', '', '  /tmp/File '),
    (' /ReViEw\tSrc/A.py  "Name" ', 'command', 'review', 'Src/A.py  "Name"'),
    ('/UNKNOWN', 'command', 'unknown', ''),
])
def test_parser_preserves_text_and_only_normalizes_name(draft, kind, name, text):
    from mewcode.commands import parse_command
    parsed = parse_command(draft)
    assert (parsed.kind, parsed.name, parsed.text) == (kind, name, text)


def test_metadata_drives_help_and_completion_without_second_table():
    from mewcode.commands import CommandRegistry
    spec = replace(definition(), argument_hint='文件目标', argument_choices=(('list',), ('show', 'project')))
    registry = CommandRegistry([spec]).freeze()
    assert registry.completions('/IN') == ['/inspect']
    assert registry.completions(' /i s') == [' /i show']
    shown = registry.help_text(enhanced=True)
    assert all(word in shown for word in ['/inspect', '/i', '检查', '本地处理', '文件目标', 'Esc', 'EOF'])
