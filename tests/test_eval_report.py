"""失败分母、未知统计和离线比较不能生成乐观分数。"""
import copy
import json
import pytest


def records():
    return [dict(case_id='repair', trial_id=str(i), status=status,
                 metrics={'requests': 2, 'input_tokens': 10, 'output_tokens': None, 'elapsed': float(i)},
                 checks=[], manual=['accuracy'])
            for i, status in enumerate(['passed', 'passed', 'provider_failed', 'harness_error'], 1)]


def test_report_keeps_service_failures_and_unknown_usage():
    from mewcode.evals.report import summarize
    report = summarize(records())
    assert report['automatic'] == {'passed': 2, 'denominator': 3, 'rate': 2/3}
    assert report['counts']['harness_error'] == 1
    assert report['metrics']['all']['output_tokens']['known'] == 0
    assert report['metrics']['passed']['requests']['samples'] == 2
    assert report['manual']['reviewed'] == 0


def test_zero_valid_trials_has_no_rate():
    from mewcode.evals.report import summarize
    rows = records()[3:]
    assert summarize(rows)['automatic']['rate'] is None


def test_compare_discloses_control_and_multiple_variant_changes():
    from mewcode.evals.compare import compare_runs
    manifest = dict(controls={'suite': 'x', 'budget': 20}, variants={'source': 'a', 'model': 'm', 'prompt': 'p'})
    baseline = {'manifest': manifest, 'results': records()}
    candidate = copy.deepcopy(baseline)
    candidate['manifest']['variants'].update(model='new', prompt='new')
    report = compare_runs(baseline, candidate, kind='model')
    assert report['comparable']
    assert set(report['variant_changes']) == {'model', 'prompt'}
    assert report['attribution'] == 'descriptive'
    candidate['manifest']['controls']['budget'] = 21
    report = compare_runs(baseline, candidate, kind='model')
    assert not report['comparable']
    assert report['differences'] == ['budget']
    assert report['overall'] is None


def test_manual_review_does_not_change_automatic_status(tmp_path):
    from mewcode.evals.report import read_reviews, summarize
    rows = records()
    row = dict(run_id='run', case_id='repair', trial_id='1', dimension='accuracy',
               status='fail', reviewer='tester', reason='遗漏失败', evidence=['proof.txt'])
    (tmp_path / 'proof.txt').write_text('exit 1')
    (tmp_path / 'reviews.jsonl').write_text(json.dumps(row)+'\n')
    reviews = read_reviews(tmp_path, 'run', rows)
    report = summarize(rows, reviews)
    assert report['automatic']['passed'] == 2
    assert report['manual']['reviewed'] == 1
    row['evidence'] = ['../escape']
    (tmp_path / 'reviews.jsonl').write_text(json.dumps(row)+'\n')
    with pytest.raises(ValueError):
        read_reviews(tmp_path, 'run', rows)


def test_load_run_rejects_incomplete_and_symlink_results(tmp_path):
    from mewcode.evals.artifacts import load_run, write_json
    manifest = dict(format_version=1, run_id='run', complete=False, trials=[])
    write_json(tmp_path / 'manifest.json', manifest)
    with pytest.raises(ValueError, match='完整'):
        load_run(tmp_path)
    manifest.update(complete=True, trials=[{'case_id': 'repair', 'trial_id': '1', 'result': 'result.json'}])
    write_json(tmp_path / 'manifest.json', manifest)
    (tmp_path / 'result.json').symlink_to('/etc/hosts')
    with pytest.raises(ValueError):
        load_run(tmp_path)


def test_trace_excludes_secrets_and_thinking():
    from mewcode.evals.artifacts import encode_event
    from mewcode.types import AgentEvent, ToolCall
    from mewcode.web.security import Redactor
    assert encode_event(AgentEvent('thinking_delta', text='secret'), Redactor('secret')) is None
    encoded = encode_event(AgentEvent('tool_call', call=ToolCall('a', 'read_file', '{"path":"secret"}')), Redactor('secret'))
    assert 'secret' not in json.dumps(encoded)
    assert encoded['redacted']


def test_code_identity_uses_actual_files_without_git_commit(tmp_path,monkeypatch):
    from mewcode.evals import artifacts
    package = tmp_path/'package'
    (package/'evals').mkdir(parents=True)
    monkeypatch.setattr(artifacts,'__file__',str(package/'evals/artifacts.py'))
    source = package/'agent.py'
    source.write_text('first')
    before = artifacts.code_identity()
    source.write_text('second')
    after = artifacts.code_identity()
    assert before['digest']!=after['digest']
    assert before['commit'] is None


def test_stream_fragments_cannot_reconstruct_a_secret():
    from mewcode.evals.artifacts import encode_event
    from mewcode.types import AgentEvent
    from mewcode.web.security import Redactor
    rows=[encode_event(AgentEvent('text_delta',text=part),Redactor('secret')) for part in ['sec','ret']]
    assert all(row.get('body_omitted') for row in rows)
    assert 'secret' not in ''.join(row['text'] for row in rows)
