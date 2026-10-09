"""只从已保存证据进行描述性比较。"""
from .report import summarize
from .suite import EvaluationError


def compare_runs(baseline, candidate, *, kind):
    if kind not in {'code','model','strategy'}:
        raise EvaluationError('比较类型无效')
    before, after = baseline['manifest'], candidate['manifest']
    controls_a, controls_b = before['controls'], after['controls']
    differences = sorted(k for k in set(controls_a)|set(controls_b) if controls_a.get(k)!=controls_b.get(k))
    if before.get('execution') != after.get('execution'):
        differences.append('execution')
    changes = sorted(k for k in set(before['variants'])|set(after['variants']) if before['variants'].get(k)!=after['variants'].get(k))
    allowed = {'code':{'source','prompt','tools'},'model':{'model'},'strategy':{'source','prompt','tools'}}[kind]
    comparable = not differences
    overall = None
    rows = []
    if comparable:
        overall = {'baseline':summarize(baseline['results'],baseline.get('reviews',())),
                   'candidate':summarize(candidate['results'],candidate.get('reviews',()))}
        identities = sorted({r['case_id'] for r in baseline['results']}|{r['case_id'] for r in candidate['results']})
        for identity in identities:
            a = summarize([r for r in baseline['results'] if r['case_id']==identity],[r for r in baseline.get('reviews',()) if r['case_id']==identity])
            b = summarize([r for r in candidate['results'] if r['case_id']==identity],[r for r in candidate.get('reviews',()) if r['case_id']==identity])
            rates = [a['automatic']['rate'],b['automatic']['rate']]
            rows.append({'case_id':identity,'baseline':a,'candidate':b,'delta':rates[1]-rates[0] if all(v is not None for v in rates) else None})
    return {'format_version':1,'kind':kind,'comparable':comparable,'differences':differences,
            'variant_changes':changes,'attribution':'controlled' if comparable and set(changes)<=allowed else 'descriptive',
            'uncontrolled_changes':sorted(set(changes)-allowed),'overall':overall,'cases':rows,
            'note':'结果用于描述已保存样本，不构成统计显著性或服务端权重证明。'}


def markdown(report):
    lines = ['# Agent 测评比较','',f"可直接比较：{report['comparable']}；比较目的：{report['kind']}。",
             f"控制条件变化：{', '.join(report['differences']) or '无'}。",
             f"版本／配置变化：{', '.join(report['variant_changes']) or '无'}。",
             f"归因条件：{report['attribution']}。",'', '| 用例 | 基线通过 | 候选通过 | 成功率差值 |', '| --- | --- | --- | --- |']
    for row in report['cases']:
        a,b = row['baseline']['automatic'],row['candidate']['automatic']
        lines.append(f"| {row['case_id']} | {a['passed']}/{a['denominator']} | {b['passed']}/{b['denominator']} | {row['delta']} |")
    if report['overall']:
        lines += ['','| 维度 | 基线 | 候选 |','| --- | --- | --- |']
        for name in ('counts','boundary_violations','functional_checks','boundary_checks','manual','failure_reasons'):
            lines.append(f"| {name} | {report['overall']['baseline'][name]} | {report['overall']['candidate'][name]} |")
        lines += ['','| 指标 | 基线全部有效：中位数／已知／缺失 | 候选全部有效：中位数／已知／缺失 | 基线成功：中位数／样本 | 候选成功：中位数／样本 |','| --- | --- | --- | --- | --- |']
        for name in report['overall']['baseline']['metrics']['all']:
            a,b = (report['overall'][side]['metrics'] for side in ('baseline','candidate'))
            lines.append(f"| {name} | {a['all'][name]['median']} / {a['all'][name]['known']} / {a['all'][name]['missing']} | {b['all'][name]['median']} / {b['all'][name]['known']} / {b['all'][name]['missing']} | {a['passed'][name]['median']} / {a['passed'][name]['samples']} | {b['passed'][name]['median']} / {b['passed'][name]['samples']} |")
    return '\n'.join([*lines,'',report['note'],''])
