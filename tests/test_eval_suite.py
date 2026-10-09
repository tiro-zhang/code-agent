"""测评资产必须在任何真实请求前完成校验。"""
from pathlib import Path
import pytest
import yaml


def suite_file(root, **case_options):
    (root / 'fixture').mkdir(exist_ok=True)
    (root / 'fixture/a.py').write_text('value = 1\n')
    case = dict(id='repair', fixture='fixture', steps=[{'message': '修复'}],
                checks=[{'type': 'file', 'path': 'a.py', 'contains': 'value = 2'}])
    case.update(case_options)
    path = root / 'suite.yaml'
    path.write_text(yaml.safe_dump(dict(version=1, id='example', cases=[case])))
    return path


def test_fixture_change_changes_fingerprint(tmp_path):
    from mewcode.evals.suite import load_suite
    path = suite_file(tmp_path)
    first = load_suite(path)
    (tmp_path / 'fixture/a.py').write_text('value = 3\n')
    second = load_suite(path)
    assert first.fingerprint != second.fingerprint
    assert first.cases[0].timeout == 600


@pytest.mark.parametrize('options', [
    {'extra': True}, {'timeout': True}, {'timeout': 0},
    {'steps': [{'mode': 'other'}]}, {'steps': [{'message': 'x', 'mode': 'plan'}]},
    {'steps': []}, {'checks': [{'type': 'unknown'}]},
    {'checks': [{'type': 'file', 'path': '../escape', 'exists': True}]},
    {'approvals': [{'tool': 'read_file', 'decision': 'permanent'}]},
])
def test_rejects_invalid_cases_before_execution(tmp_path, options):
    from mewcode.evals.suite import load_suite, EvaluationError
    with pytest.raises(EvaluationError):
        load_suite(suite_file(tmp_path, **options))


def test_rejects_duplicate_ids_and_yaml_keys(tmp_path):
    from mewcode.evals.suite import load_suite, EvaluationError
    path = suite_file(tmp_path)
    doc = yaml.safe_load(path.read_text())
    doc['cases'] *= 2
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(EvaluationError, match='重复'):
        load_suite(path)
    path.write_text('version: 1\nversion: 1\nid: a\ncases: []\n')
    with pytest.raises(EvaluationError, match='重复'):
        load_suite(path)


def test_rejects_asset_symlink_and_missing_grader(tmp_path):
    from mewcode.evals.suite import load_suite, EvaluationError
    path = suite_file(tmp_path)
    (tmp_path / 'fixture/leak').symlink_to('/etc/hosts')
    with pytest.raises(EvaluationError, match='链接'):
        load_suite(path)
    (tmp_path / 'fixture/leak').unlink()
    path = suite_file(tmp_path, checks=[{'type': 'command', 'asset': 'absent.py'}])
    with pytest.raises(EvaluationError, match='不存在'):
        load_suite(path)


def test_preflight_checks_dependencies_and_repeat(tmp_path, monkeypatch):
    from mewcode.evals.suite import load_suite, preflight, EvaluationError
    suite = load_suite(suite_file(tmp_path))
    with pytest.raises(EvaluationError):
        preflight(suite, repeat=True)
    monkeypatch.setattr('mewcode.evals.suite.shutil.which', lambda name: None)
    with pytest.raises(EvaluationError, match='rg'):
        preflight(suite)
