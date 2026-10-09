"""只替换网络，真实会话、授权与工具仍参与测评器测试。"""
import asyncio
import json
from pathlib import Path
from conftest import ScriptedProvider, async_test
from test_eval_suite import suite_file
from mewcode.config import ProviderConfig
from mewcode.types import ProviderEvent, Message, ToolCall, TokenUsage


def config():
    return ProviderConfig('test', 'openai', 'test-model', 'https://example.invalid',
                          'fixture-secret', False, context_window=128000)


def response(text='完成'):
    return [ProviderEvent('completed', message=Message('assistant', text))]


def call(name, args, identity='a'):
    return [ProviderEvent('completed', message=Message('assistant', tool_calls=(ToolCall(identity, name, json.dumps(args)),)))]


@async_test
async def test_core_scope_blocks_system_entrypoints_and_keeps_plan_readonly(tmp_path):
    from mewcode.evals.runtime import make_session
    from mewcode.evals.suite import load_suite
    case = load_suite(suite_file(tmp_path)).cases[0]
    root = tmp_path / 'work'
    root.mkdir()
    user = tmp_path / 'user'
    user.mkdir()
    provider = ScriptedProvider([])
    session = make_session(provider, config(), case, root, user)
    try:
        assert session.effective_tools() == frozenset({'read_file','write_file','edit_file','execute_command','glob_files','search_code'})
        await session.set_mode('plan')
        assert session.effective_tools() == frozenset({'read_file','glob_files','search_code'})
        for name in ['load_skill', 'agent', 'team', 'mcp__x__y', 'write_file']:
            result = await session.executor.execute(name, '{}', allowed_tools=session.effective_tools())
            assert not result.ok
        assert session.skills.catalog.skills == () and session.roles.roles == ()
        assert session.memory is None and session.journal is None
        assert session.permissions.config.paths[0].is_relative_to(user)
    finally:
        await session.aclose()


@async_test
async def test_trial_preserves_denial_and_does_not_grade_model_claim_as_success(tmp_path):
    from mewcode.evals.runner import run_trial
    from mewcode.evals.suite import load_suite
    suite = load_suite(suite_file(tmp_path, rules=[{'effect':'deny','rule':'edit_file(*)','match':'glob'}]))
    provider = ScriptedProvider([call('edit_file', {'path':'a.py','old_text':'1','new_text':'2'}), response('已修复')])
    result = await run_trial(suite, suite.cases[0], config(), tmp_path/'trial', 'run', '1', provider=provider)
    assert result['status'] == 'agent_failed'
    assert result['metrics']['tools_started'] == 0
    assert result['metrics']['permission_denied'] == 1
    assert provider.closed
    assert (tmp_path/'trial/workspace/a.py').read_text() == 'value = 1\n'


@async_test
async def test_multistep_mode_change_does_not_send_and_usage_not_double_counted(tmp_path):
    from mewcode.evals.runner import run_trial
    from mewcode.evals.suite import load_suite
    steps = [{'mode':'plan'}, {'message':'规划'}, {'mode':'execute'}, {'message':'说明'}]
    suite = load_suite(suite_file(tmp_path, steps=steps, checks=[{'type':'protected','paths':['a.py']}]))
    usage = TokenUsage(input_tokens=9, output_tokens=3)
    provider = ScriptedProvider([[ProviderEvent('usage', usage=TokenUsage(input_tokens=5)), ProviderEvent('usage', usage=usage), *response()], response()])
    result = await run_trial(suite, suite.cases[0], config(), tmp_path/'trial', 'run', '1', provider=provider)
    assert result['status'] == 'passed'
    assert result['metrics']['requests'] == 2
    assert result['metrics']['input_tokens'] == 9
    assert 'input_tokens' in result['metrics']['incomplete_fields']
    assert len(result['answers']) == 2


@async_test
async def test_timeout_cleans_real_command_before_snapshot(tmp_path):
    from mewcode.evals.runner import run_trial
    from mewcode.evals.suite import load_suite
    command = 'sleep 2; touch late'
    suite = load_suite(suite_file(tmp_path, timeout=1, approvals=[{'tool':'execute_command','arguments':{'command':command},'decision':'once'}]))
    provider = ScriptedProvider([call('execute_command', {'command':command})])
    result = await run_trial(suite, suite.cases[0], config(), tmp_path/'trial', 'run', '1', provider=provider)
    assert result['status'] == 'cancelled'
    await asyncio.sleep(1.2)
    assert not (tmp_path/'trial/workspace/late').exists()


@async_test
async def test_close_timeout_marks_harness_error_and_skips_grading(tmp_path):
    from mewcode.evals.runner import run_trial
    from mewcode.evals.suite import load_suite
    from mewcode.evals.runtime import make_session
    suite = load_suite(suite_file(tmp_path))
    provider = ScriptedProvider([response()])
    def factory(*args):
        session = make_session(*args)
        async def blocked():
            await asyncio.Event().wait()
        session.aclose = blocked
        return session
    result = await run_trial(suite, suite.cases[0], config(), tmp_path/'trial', 'run', '1',
                             provider=provider, session_factory=factory, close_timeout=.05)
    assert result['status'] == 'harness_error'
    assert result['side_effects'] == 'unknown'
    assert not (tmp_path/'trial/snapshot').exists()


@async_test
async def test_permission_session_grant_is_tool_specific_and_personal_domain_is_empty(tmp_path, monkeypatch):
    from mewcode.evals.runtime import make_session
    from mewcode.evals.suite import load_suite
    personal = tmp_path/'personal'
    (personal/'.mewcode').mkdir(parents=True)
    (personal/'.mewcode/MEWCODE.md').write_text('个人秘密指令')
    monkeypatch.setenv('HOME',str(personal))
    suite = load_suite(suite_file(tmp_path, permission_mode='strict', approvals=[{'tool':'read_file','path':'a.py','decision':'session'}]))
    work,user = tmp_path/'work',tmp_path/'user'
    import shutil
    shutil.copytree(suite.cases[0].fixture,work)
    session = make_session(ScriptedProvider([]),config(),suite.cases[0],work,user)
    try:
        assert '个人秘密' not in session.prompt_state.custom_instructions
        assert (await session.executor.execute('read_file','{"path":"a.py"}')).ok
        assert (await session.executor.execute('read_file','{"path":"a.py"}')).ok
        assert not (await session.executor.execute('edit_file','{"path":"a.py","old_text":"1","new_text":"2"}')).ok
        assert not (user/'permissions.local.yaml').exists()
    finally:
        await session.aclose()


@async_test
async def test_persistent_work_files_are_redacted_after_scoring(tmp_path):
    from mewcode.evals.runner import run_trial
    from mewcode.evals.suite import load_suite
    suite_file(tmp_path,checks=[{'type':'protected','paths':['a.py']}])
    (tmp_path/'fixture/a.py').write_text('value = 1\n# fixture-secret\n')
    suite=load_suite(tmp_path)
    result=await run_trial(suite,suite.cases[0],config(),tmp_path/'trial','run','1',provider=ScriptedProvider([response()]))
    assert result['status']=='passed'
    assert result['redacted_files']
    assert all(b'fixture-secret' not in path.read_bytes() for path in (tmp_path/'trial').rglob('*') if path.is_file())
