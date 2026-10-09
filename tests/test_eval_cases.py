"""内置任务与可信检查器的正反例校准不需要真实模型。"""
from pathlib import Path
import shutil
from conftest import async_test

ROOT = Path(__file__).resolve().parents[1]/'evals/core-v1'


def test_core_suite_covers_eight_cases_and_preserves_scope():
    from mewcode.evals.suite import load_suite
    suite = load_suite(ROOT)
    assert {case.id for case in suite.cases} == {'locate-read','repair-verify','create-file','edit-recovery','plan-revise','mode-execute','permission-deny','failing-check'}
    assert all(case.manual for case in suite.cases)
    assert [step for case in suite.cases if case.id=='mode-execute' for step in case.steps if 'mode' in step] == [{'mode':'plan'},{'mode':'execute'}]


@async_test
async def test_normal_builtin_cases_authorize_declared_reading(tmp_path):
    from mewcode.evals.suite import load_suite
    from mewcode.evals.runtime import make_session
    from test_eval_runtime import config
    from conftest import ScriptedProvider
    import json
    suite = load_suite(ROOT)
    for case in suite.cases:
        if case.permission_mode=='strict':
            continue
        workspace = tmp_path/case.id
        shutil.copytree(case.fixture,workspace)
        session = make_session(ScriptedProvider([]),config(),case,workspace,tmp_path/(case.id+'-user'))
        try:
            path = next(p for p in case.fixture.rglob('*') if p.is_file())
            result = await session.executor.execute('read_file',json.dumps({'path':path.relative_to(case.fixture).as_posix()}))
            assert result.ok, case.id
        finally:
            await session.aclose()


@async_test
async def test_builtin_functional_checkers_reject_bad_accept_reference_and_alternative(tmp_path):
    from mewcode.evals.suite import load_suite
    from mewcode.evals.graders import grade, snapshot
    suite = load_suite(ROOT)
    for name,target,reference,alternative in [('repair-verify','limits.py','clamp.py','def clamp(value, lower, upper):\n    if value < lower: return lower\n    if value > upper: return upper\n    return value\n'),('mode-execute','limits.py','clamp.py','def clamp(value, lower, upper):\n    return sorted([value,lower,upper])[1]\n'),('create-file','slug.py','slug.py','import re\ndef slugify(text):\n    return re.sub(r"\\s+", "-", text.strip().lower())\n')]:
        case = next(case for case in suite.cases if case.id==name)
        workspace = tmp_path/name
        shutil.copytree(case.fixture,workspace)
        if target=='slug.py':
            (workspace/target).write_text('def slugify(text): return text\n')
        initial = snapshot(workspace)
        bad = await grade(suite,case,workspace,initial,[],['model_done'],tmp_path/(name+'-bad'))
        assert bad[0]['status']=='fail'
        for index,source in enumerate([(ROOT/'references'/reference).read_text(),alternative]):
            (workspace/target).write_text(source)
            good = await grade(suite,case,workspace,initial,[],['model_done'],tmp_path/f'{name}-good-{index}')
            assert good[0]['status']=='pass'
        (workspace/'unrelated.txt').write_text('无关修改')
        boundary = await grade(suite,case,workspace,initial,[],['model_done'],tmp_path/(name+'-boundary'))
        assert any(check['boundary'] and check['status']=='fail' for check in boundary)


@async_test
async def test_grader_rejects_early_exit_in_module_and_function(tmp_path):
    from mewcode.evals.suite import load_suite
    from mewcode.evals.graders import grade, snapshot
    suite=load_suite(ROOT)
    case=next(case for case in suite.cases if case.id=='create-file')
    for index,source in enumerate(['raise SystemExit(0)\n','def slugify(text):\n    raise SystemExit(0)\n']):
        workspace=tmp_path/str(index)
        shutil.copytree(case.fixture,workspace)
        (workspace/'slug.py').write_text(source)
        checks=await grade(suite,case,workspace,snapshot(workspace),[],['model_done'],tmp_path/f'grade{index}')
        assert checks[0]['status']=='fail'
