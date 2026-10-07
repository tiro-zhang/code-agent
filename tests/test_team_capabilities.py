"""团队工具的身份边界与 coordinator 双开关。"""
import json
from dataclasses import replace

import pytest

from mewcode.config import ConfigError, ProviderConfig, load_config
from mewcode.tools import default_registry
from mewcode.tools.base import ToolError
from mewcode.teams.capabilities import TeamScope, allowed_tools, guard_action


def config(**values):
    return ProviderConfig('测试', 'openai', '模型', 'https://example.test', '秘密', False,
                          context_window=32768, **values)


def test_registry_scope_is_explicit_even_for_system_tools():
    registry = default_registry()
    names = {tool.name for tool in registry.definitions(allowed_tools=frozenset({'team'}))}
    assert names == {'load_skill', 'agent', 'team'}
    with pytest.raises(ToolError) as error:
        registry.prepare('team_task', json.dumps({'action': 'list'}), allowed_tools=frozenset())
    assert error.value.code == 'tool_not_allowed'


def test_identity_and_coordinator_are_independent_of_delegation(monkeypatch):
    monkeypatch.setenv('MEWCODE_COORDINATOR', '1')
    scope = TeamScope('alpha', 'lead', True)
    settings = config(team_coordinator_enabled=True)
    names = default_registry().names()
    own = allowed_tools(names, scope, settings)
    assert {'team_member', 'team_integrate', 'execute_command'} <= own
    assert not {'edit_file', 'write_file', 'agent'} & own
    delegated = allowed_tools(names, scope, settings, delegation=True)
    assert {'edit_file', 'write_file'} <= delegated
    member = allowed_tools(names, TeamScope('alpha', 'alice', False), settings)
    assert 'edit_file' in member
    assert not {'team', 'agent', 'team_member', 'team_integrate'} & member


@pytest.mark.parametrize('enabled,env,expected', [(False,'0',False),(False,'1',False),(True,'0',False),(True,'1',True)])
def test_two_locks(monkeypatch, enabled, env, expected):
    monkeypatch.setenv('MEWCODE_COORDINATOR', env)
    scope = TeamScope('alpha', 'lead', True)
    assert scope.coordinator(config(team_coordinator_enabled=enabled)) is expected


def test_member_cannot_accept_or_mutate_in_plan():
    member = TeamScope('alpha', 'alice', False)
    with pytest.raises(ToolError):
        guard_action(member, 'team_task', {'action':'accept'}, mode='execute')
    with pytest.raises(ToolError):
        guard_action(member, 'team_task', {'action':'claim'}, mode='plan')
    guard_action(member, 'team_task', {'action':'list'}, mode='plan')
    with pytest.raises(ToolError):
        guard_action(member, 'team_message', {'action':'send', 'type':'assignment'}, mode='plan')


@pytest.mark.parametrize('line', ['team_backend=wrong','team_max_running=0','team_max_queued=true',
                                'team_coordinator_enabled=1','team_max_running='])
def test_config_rejects_team_values(tmp_path, line):
    path = tmp_path / '.env'
    path.write_text('name=测试\nprotocol=openai\nmodel=test\nbase_url=https://example.test\n'
                    'api_key=secret\ncontext_window=32768\n' + line)
    with pytest.raises(ConfigError, match='team_'):
        load_config(path)


def test_legacy_config_has_team_defaults(tmp_path):
    path = tmp_path / '.env'
    path.write_text('name=测试\nprotocol=openai\nmodel=test\nbase_url=https://example.test\n'
                    'api_key=secret\ncontext_window=32768\n')
    settings = load_config(path)
    assert (settings.team_backend, settings.team_max_running, settings.team_max_queued,
            settings.team_coordinator_enabled) == ('auto', 4, 32, False)


def test_dotenv_cannot_enable_process_coordinator_or_expand_restored_identity(tmp_path,monkeypatch):
    monkeypatch.delenv('MEWCODE_COORDINATOR',raising=False)
    path=tmp_path/'.env'
    path.write_text('name=测试\nprotocol=openai\nmodel=test\nbase_url=https://example.test\n'
                    'api_key=secret\ncontext_window=32768\nteam_coordinator_enabled=true\nMEWCODE_COORDINATOR=1\n')
    settings=load_config(path)
    scope=TeamScope('alpha','lead',True)
    assert not scope.coordinator(settings)
    monkeypatch.setenv('MEWCODE_COORDINATOR','1')
    assert scope.coordinator(settings)
    assert not TeamScope('alpha','alice',False).coordinator(settings)
    monkeypatch.delenv('MEWCODE_COORDINATOR')
    assert not scope.coordinator(settings)
