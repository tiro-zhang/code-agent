"""公共命令业务通过实际会话保留模式、权限和重置合同。"""

import pytest

from conftest import async_test, ScriptedProvider, collect
from test_agent_loop import answer
from test_app import config_file
from mewcode.config import load_config
from test_skills_session import session


@async_test
async def test_headless_service_changes_real_session_and_keeps_local_queries_model_free(tmp_path):
    from mewcode.commands.service import SessionCommandService
    chat = session(tmp_path, ScriptedProvider([answer('旧答复')]))
    service = SessionCommandService(chat, load_config(config_file(tmp_path)))
    try:
        await collect(chat.ask('旧任务'))
        requests = len(chat.provider.requests)
        await service.set_mode('plan')
        assert chat.mode == 'plan'
        assert 'strict' in service.permission_text('mode strict')
        assert chat.permissions.mode == 'strict'
        assert '只读' in service.status_text() or '[PLAN]' in service.status_text()
        assert '未启用存档' in service.session_text(listing=False)
        assert 'review' in service.skills_text('list')
        assert 'general' in service.agents_text('list')
        assert '未绑定团队' in await service.team_text('status')
        assert len(chat.provider.requests) == requests
        await service.reset_session()
        assert not chat.history and chat.mode == 'plan' and chat.permissions.mode == 'strict'
    finally:
        await chat.aclose()


@async_test
async def test_headless_service_preserves_validation_and_team_plan_guard(tmp_path):
    from mewcode.commands.service import SessionCommandService
    chat = session(tmp_path, ScriptedProvider([]))
    service = SessionCommandService(chat, load_config(config_file(tmp_path)))
    try:
        with pytest.raises(ValueError):
            service.permission_text('mode wrong')
        await service.set_mode('plan')
        with pytest.raises(ValueError):
            await service.team_text('create', 'forbidden')
        assert not chat.provider.requests
        assert await chat.teams.control({'action': 'list'}) == []
    finally:
        await chat.aclose()
