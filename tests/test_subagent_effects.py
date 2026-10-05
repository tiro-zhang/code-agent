"""实际后台副作用、范围转换及 MCP 执行边界。"""

import asyncio
from types import SimpleNamespace
from conftest import ScriptedProvider, async_test, collect
from mewcode.tools.base import ToolResult
from mewcode.types import ToolCall
from test_skills_session import session, response
from test_subagent_runtime import submit
from test_mcp_permissions import TOOL
from test_parent_tasks import wait_until


@async_test
async def test_authorized_background_write_read_edit_and_command(tmp_path):
    provider = ScriptedProvider([
        response(calls=(ToolCall('write', 'write_file', '{"path":"result","content":"old"}'),)),
        response(calls=(ToolCall('read', 'read_file', '{"path":"result"}'),)),
        response(calls=(ToolCall('edit', 'edit_file', '{"path":"result","old_text":"old","new_text":"new"}'),)),
        response(calls=(ToolCall('command', 'execute_command', '{"command":"printf verified > command-result"}'),)),
        response('实际完成')])
    chat = session(tmp_path, provider)
    manager, parent, record = submit(chat, {'type':'defined', 'role':'general', 'prompt':'修改并验证', 'background':True}, chat.roles.get('general'))
    try:
        await asyncio.wait_for(manager.wait_terminal(record.task_id), 5)
        assert (tmp_path / 'result').read_text() == 'new'
        assert (tmp_path / 'command-result').read_text() == 'verified'
        assert all(item['result']['ok'] for item in record.outcome.evidence)
        assert record.outcome.side_effects == 'possible'
        assert parent.budget.used == 0 and len(record.outcome.request_usage) == 5
    finally:
        await chat.aclose()


@async_test
async def test_switch_keeps_started_command_and_blocks_subsequent_tool(tmp_path):
    provider = ScriptedProvider([
        response(calls=(ToolCall('command', 'execute_command', '{"command":"printf x >> started; sleep 0.3; printf done > finished"}'),)),
        response(calls=(ToolCall('write', 'write_file', '{"path":"forbidden","content":"bad"}'),)), response('受限结果')])
    config = SimpleNamespace(**vars(ScriptedProvider.config), agent_background_tools=frozenset())
    chat = session(tmp_path, provider, config=config)
    manager, parent, record = submit(chat, {'type':'defined', 'role':'general', 'prompt':'工作'}, chat.roles.get('general'))
    try:
        await wait_until(lambda: (tmp_path / 'started').exists())
        assert manager.detach(record.task_id)
        await manager.wait_terminal(record.task_id)
        assert (tmp_path / 'started').read_text() == 'x'
        assert (tmp_path / 'finished').read_text() == 'done'
        assert not (tmp_path / 'forbidden').exists()
        assert record.outcome.evidence[1]['result']['error']['code'] == 'tool_not_allowed'
    finally:
        await chat.aclose()


@async_test
async def test_fork_keeps_mcp_declaration_but_only_explicit_background_scope_calls_it(tmp_path):
    for allow in (False, True):
        calls = []
        class Remote:
            async def call(self, tool, arguments, **options):
                calls.append(arguments)
                return ToolResult.success({'text':'远端结果'})
        provider = ScriptedProvider([response('父完成'), response(calls=(ToolCall('remote', TOOL.name, '{"value":1}'),)), response('子完成')])
        config = SimpleNamespace(**vars(ScriptedProvider.config), agent_background_tools=frozenset({TOOL.name}) if allow else None)
        chat = session(tmp_path, provider, config=config)
        chat.executor.registry.register(TOOL)
        chat.permissions.bind_mcp_tools((TOOL,))
        chat.executor.mcp = Remote()
        try:
            await collect(chat.ask('父目标'))
            manager, parent, record = submit(chat, {'type':'fork', 'prompt':'调用外部工具'})
            await manager.wait_terminal(record.task_id)
            assert TOOL.name in {tool.name for tool in provider.requests[1][1]['tools']}
            result = record.outcome.evidence[0]['result']
            assert result['ok'] == allow and len(calls) == int(allow)
            if not allow:
                assert result['error']['code'] == 'tool_not_allowed'
        finally:
            await chat.aclose()
