"""测评入口与结果比较具有明确退出码且不会隐式调用模型。"""
import json
import pytest
from conftest import async_test, ScriptedProvider
from test_eval_suite import suite_file
from test_eval_runtime import config, response, call


def test_cli_rejects_missing_config_without_provider_call(tmp_path, capsys):
    from mewcode.evals.cli import main
    assert main(['run','--config',str(tmp_path/'absent'), '--suite',str(suite_file(tmp_path)), '--output',str(tmp_path/'out')]) == 2
    assert not (tmp_path/'out/manifest.json').exists()


@async_test
async def test_suite_keeps_each_attempt_and_marks_test_double(tmp_path):
    from mewcode.evals.suite import load_suite
    from mewcode.evals.runner import run_suite
    from mewcode.evals.artifacts import load_run
    suite = load_suite(suite_file(tmp_path, approvals=[{'tool':'edit_file','path':'a.py','decision':'once'}]))
    attempts = iter([False, True, True])
    def provider_factory(cfg):
        good = next(attempts)
        return ScriptedProvider(([call('edit_file',{'path':'a.py','old_text':'1','new_text':'2'})] if good else [])+[response()])
    result = await run_suite(suite, config(), tmp_path/'out', repeat=3, provider_factory=provider_factory)
    assert result['automatic']['rate'] == 2/3
    run = load_run(tmp_path/'out')
    assert [r['status'] for r in run['results']] == ['agent_failed','passed','passed']
    assert run['manifest']['execution'] == 'test-double'
    assert run['manifest']['controls']['evaluator']
    assert len({r['trial_id'] for r in run['results']}) == 3


@async_test
async def test_offline_compare_has_no_provider_or_tool_side_effect(tmp_path, monkeypatch):
    from mewcode.evals.suite import load_suite
    from mewcode.evals.runner import run_suite
    from mewcode.evals.cli import main
    suite = load_suite(suite_file(tmp_path))
    for name in ['a','b']:
        await run_suite(suite, config(), tmp_path/name, provider_factory=lambda cfg: ScriptedProvider([response()]))
    def forbidden(*args, **kwargs):
        raise AssertionError('离线操作不得调用执行边界')
    monkeypatch.setattr('mewcode.providers.make_provider', forbidden)
    monkeypatch.setattr('mewcode.tools.executor.ToolExecutor.execute', forbidden)
    assert main(['compare','--baseline',str(tmp_path/'a'),'--candidate',str(tmp_path/'b'), '--kind','code','--output',str(tmp_path/'comparison')]) == 0
    report = json.loads((tmp_path/'comparison/report.json').read_text())
    assert report['comparable']


def test_cli_requires_explicit_inputs():
    from mewcode.evals.cli import main
    with pytest.raises(SystemExit) as error:
        main(['run'])
    assert error.value.code == 2


@async_test
async def test_cancel_keeps_active_and_not_run_attempts(tmp_path):
    import asyncio
    from mewcode.evals.suite import load_suite
    from mewcode.evals.runner import run_suite
    from mewcode.evals.artifacts import load_run
    cancel = asyncio.Event()
    async def slow():
        cancel.set()
        await asyncio.sleep(10)
        yield response()[0]
    suite = load_suite(suite_file(tmp_path))
    report = await run_suite(suite, config(), tmp_path/'out', repeat=2,
                             provider_factory=lambda cfg: ScriptedProvider([slow]),cancel_event=cancel)
    assert report['counts']['cancelled'] == 1 and report['counts']['not_run'] == 1
    rows = load_run(tmp_path/'out')['results']
    assert rows[0]['reason'] == 'user' and rows[1]['started'] is False


@async_test
async def test_provider_failure_retained_without_retry(tmp_path):
    from mewcode.evals.suite import load_suite
    from mewcode.evals.runner import run_suite
    from mewcode.evals.artifacts import load_run
    from mewcode.types import ProviderError
    async def failing():
        raise ProviderError('服务失败 fixture-secret')
        yield
    suite = load_suite(suite_file(tmp_path))
    report = await run_suite(suite, config(), tmp_path/'out',provider_factory=lambda cfg: ScriptedProvider([failing]))
    assert report['counts']['provider_failed'] == 1
    assert load_run(tmp_path/'out')['results'][0]['metrics']['requests'] == 1
    assert all('fixture-secret' not in p.read_text() for p in (tmp_path/'out').rglob('*.json*'))


@async_test
async def test_unconfirmed_close_stops_remaining_attempts(tmp_path, monkeypatch):
    import asyncio
    from mewcode.evals.suite import load_suite
    from mewcode.evals.runner import run_suite
    from mewcode.evals.runtime import make_session
    suite = load_suite(suite_file(tmp_path))
    providers=[]
    def factory(cfg):
        value = ScriptedProvider([response()])
        providers.append(value)
        return value
    def blocked(*args):
        session = make_session(*args)
        async def never_close():
            await asyncio.Event().wait()
        session.aclose=never_close
        return session
    monkeypatch.setattr('mewcode.evals.runner.make_session',blocked)
    report = await run_suite(suite,config(),tmp_path/'out',repeat=2,provider_factory=factory,close_timeout=.05)
    assert report['counts']['harness_error']==1 and report['counts']['not_run']==1
    assert len(providers)==1 and providers[0].closed


def test_cli_exits_when_a_close_task_ignores_cancellation(tmp_path):
    import subprocess
    import sys
    suite_file(tmp_path)
    script='''import asyncio,sys
from mewcode.evals import cli,runner
from mewcode import config
config.load_config=lambda _:config.ProviderConfig('test','openai','test','https://example.invalid','secret',False,context_window=128000)
async def stubborn():
    while True:
        try: await asyncio.Event().wait()
        except asyncio.CancelledError: pass
async def fake(*args,**kwargs):
    await runner.bounded(stubborn(),.02)
    return {'automatic':{'passed':0,'denominator':0},'planned':1,'counts':{'harness_error':1}}
runner.run_suite=fake
raise SystemExit(cli.main(['run','--config','unused','--suite',sys.argv[1],'--output',sys.argv[2]]))
'''
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path/'suite.yaml'),str(tmp_path/'out')],capture_output=True,text=True,timeout=2)
    assert result.returncode==2
