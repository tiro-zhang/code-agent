"""主会话真实导出与调用的团队身份一致。"""
import asyncio
import json
import subprocess

from conftest import ScriptedProvider, async_test
from mewcode.config import ProviderConfig
from mewcode.session import ChatSession
from mewcode.tools import default_registry
from mewcode.tools.base import ToolContext
from mewcode.tools.executor import ToolExecutor
from conftest import permission_bypass


def make_session(tmp_path):
    subprocess.run(['git','init','-q',str(tmp_path)], check=True)
    subprocess.run(['git','-C',str(tmp_path),'-c','user.name=测试','-c','user.email=test@example.test',
                    'commit','--allow-empty','-qm','基线'], check=True)
    config = ProviderConfig('test','openai','test','https://example.test','key',False, context_window=32768,
                            team_backend='inprocess')
    provider = ScriptedProvider([])
    executor = ToolExecutor(default_registry(), ToolContext(tmp_path), permissions=permission_bypass(tmp_path))
    return ChatSession(provider, executor=executor, config=config, user_root=tmp_path/'user', memory_enabled=False)


@async_test
async def test_ordinary_entry_hidden_then_create_lead(tmp_path):
    session = make_session(tmp_path)
    assert 'team' in session.effective_tools()
    assert 'team_task' not in session.effective_tools()
    forged = await session.executor.execute('team_task', '{"action":"list"}')
    assert not forged.ok and forged.error['code'] == 'tool_not_allowed'
    created = await session.executor.execute('team', '{"action":"create","name":"alpha"}')
    assert created.ok
    assert session.team_scope.lead
    assert {'team_member','team_task','team_message','team_integrate'} <= session.effective_tools()
    assert not session.provider.requests
    await session.aclose()
    assert session.teams.store.load('alpha').status == 'paused'


@async_test
async def test_plan_mode_blocks_create_even_when_system_passthrough(tmp_path):
    session = make_session(tmp_path)
    session.enter_plan()
    result = await session.executor.execute('team', '{"action":"create","name":"alpha"}')
    assert not result.ok and result.error['code'] == 'tool_not_allowed'
    assert not (tmp_path/'user'/'teams'/'alpha').exists()
    await session.aclose()
