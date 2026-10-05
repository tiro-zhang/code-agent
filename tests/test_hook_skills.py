"""Skill 复用运行时和顶层任务边界；直接入口也统一收尾。"""

import pytest
from conftest import ScriptedProvider, async_test, collect
from mewcode.types import ToolCall
from test_hook_lifecycle import configure, observe
from test_skills_session import session, response
from test_skills_isolated import isolated
from test_skills_catalog import entry


@pytest.mark.parametrize('mode', ['shared', 'isolated'])
@async_test
async def test_top_skill_has_one_root_turn_and_user_with_child_association(tmp_path, mode):
    path = tmp_path / '.mewcode/skills/test.md'
    (isolated if mode == 'isolated' else entry)(path, 'test')
    configure(tmp_path, [
        {'event': 'message.before_request', 'once': True, 'action': {'type': 'prompt', 'text': 'shared-once'}},
        {'event': 'turn.start', 'action': {'type': 'subagent', 'agent': 'placeholder', 'prompt': 'unused'}}])
    provider = ScriptedProvider([response(calls=(ToolCall('child-read', 'glob_files', '{"pattern":"*"}'),)), response()])
    chat = session(tmp_path, provider)
    events = observe(chat)
    await collect(chat.run_skill('test', '参数', user_text='/test 参数'))
    await chat.aclose()
    names = [item['event'] for item in events]
    assert names.count('session.start') == names.count('session.end') == 1
    assert names.count('turn.start') == names.count('turn.end') == names.count('message.user') == 1
    assert next(item for item in events if item['event'] == 'message.user')['message']['text'] == '/test 参数'
    before = [item for item in events if item['event'] == 'message.before_request']
    assert len(before) == 2 and len(chat.hooks.once) == 1 and len(provider.requests) == 2
    assert 'shared-once' in provider.requests[0][0][-1].content
    assert 'shared-once' not in provider.requests[1][0][-1].content
    if mode == 'isolated':
        root = next(item for item in events if item['event'] == 'turn.start')['run_id']
        assert all(item['parent_run_id'] == root and item['run_id'] != root and item['turn_id'] == root for item in before)
        assert next(item for item in events if item['event'] == 'tool.after' and item['tool']['call_id'] == 'child-read')['parent_run_id'] == root


@async_test
async def test_closing_direct_skill_at_accepted_call_reports_after_once_not_started(tmp_path):
    entry(tmp_path / '.mewcode/skills/test.md', 'test')
    chat = session(tmp_path, ScriptedProvider([]))
    events = observe(chat)
    source = chat.run_skill('test')
    async for event in source:
        if event.kind == 'tool_call':
            call_id = event.tool_call_id
            break
    await source.aclose()
    after = [item for item in events if item['event'] == 'tool.after']
    assert len(after) == 1 and after[0]['tool']['call_id'] == call_id
    assert after[0]['tool']['result']['error']['details']['not_started']
    assert next(item for item in events if item['event'] == 'turn.end')['reason'] == 'cancelled'
    assert not chat.provider.requests
    await chat.aclose()
