"""可信独立评分必须拒绝错误产物与边界违规。"""
from pathlib import Path
import shutil
from conftest import async_test
from test_eval_suite import suite_file


@async_test
async def test_independent_checker_accepts_different_valid_implementations(tmp_path):
    from mewcode.evals.suite import load_suite
    from mewcode.evals.graders import grade, snapshot
    (tmp_path/'verify.py').write_text("import sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nimport a\nassert a.add(2,3)==5 and a.add(-2,3)==1\n")
    suite = load_suite(suite_file(tmp_path, checks=[{'type':'command','asset':'verify.py'}]))
    workspace = tmp_path/'work'
    shutil.copytree(suite.cases[0].fixture, workspace)
    (workspace/'a.py').write_text('def add(a,b): return a-b\n')
    initial = snapshot(workspace)
    bad = await grade(suite, suite.cases[0], workspace, initial, [], ['model_done'], tmp_path/'grade1')
    assert bad[0]['status'] == 'fail'
    for i, implementation in enumerate(['def add(a,b): return a+b\n','def add(a,b): return sum([a,b])\n']):
        (workspace/'a.py').write_text(implementation)
        good = await grade(suite, suite.cases[0], workspace, initial, [], ['model_done'], tmp_path/f'grade{i+2}')
        assert good[0]['status'] == 'pass'


@async_test
async def test_protection_includes_permissions_but_excludes_registered_runtime_files(tmp_path):
    from mewcode.evals.graders import snapshot, grade
    from mewcode.evals.suite import load_suite
    suite = load_suite(suite_file(tmp_path, checks=[{'type':'changes','allowed':['a.py']}]))
    workspace = tmp_path/'work'
    workspace.mkdir()
    (workspace/'.mewcode').mkdir()
    (workspace/'.mewcode/permissions.yaml').write_text('version: 1\n')
    initial = snapshot(workspace, excluded=['.mewcode/context'])
    (workspace/'.mewcode/context').mkdir()
    (workspace/'.mewcode/context/cache').write_text('runtime')
    assert snapshot(workspace, excluded=['.mewcode/context']) == initial
    (workspace/'.mewcode/permissions.yaml').write_text('version: 2\n')
    result = await grade(suite, suite.cases[0], workspace, initial, [], ['model_done'], tmp_path/'grading', excluded=['.mewcode/context'])
    assert result[0]['status'] == 'fail' and result[0]['boundary']


@async_test
async def test_grader_timeout_is_undetermined(tmp_path):
    from mewcode.evals.graders import grade, snapshot
    from mewcode.evals.suite import load_suite
    (tmp_path/'hang.py').write_text('import time\ntime.sleep(10)\n')
    suite = load_suite(suite_file(tmp_path, checks=[{'type':'command','asset':'hang.py','timeout':.05}]))
    workspace = tmp_path/'work'
    workspace.mkdir()
    result = await grade(suite, suite.cases[0], workspace, snapshot(workspace), [], ['model_done'], tmp_path/'grading')
    assert result[0]['status'] == 'undetermined'


@async_test
async def test_escaped_grader_child_does_not_block_cleanup(tmp_path):
    import os
    import signal
    import time
    from mewcode.evals.graders import grade, snapshot
    from mewcode.evals.suite import load_suite
    pid=tmp_path/'child.pid'
    (tmp_path/'escape.py').write_text('import subprocess,sys\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,"-c","import time;time.sleep(2)"],start_new_session=True)\nPath('+repr(str(pid))+').write_text(str(p.pid))\n')
    suite=load_suite(suite_file(tmp_path,checks=[{'type':'command','asset':'escape.py','timeout':.05}]))
    workspace=tmp_path/'work'
    workspace.mkdir()
    started=time.monotonic()
    try:
        checks=await grade(suite,suite.cases[0],workspace,snapshot(workspace),[],['model_done'],tmp_path/'grade')
        assert time.monotonic()-started<.8
        assert checks[0]['status']=='undetermined' and checks[0]['side_effects']=='unknown'
    finally:
        if pid.exists():
            try: os.kill(int(pid.read_text()),signal.SIGKILL)
            except ProcessLookupError: pass
