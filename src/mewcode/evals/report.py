"""自动结果与人工复核分列，不掩盖服务或测评器失败。"""
from collections import Counter
import json
import math
from pathlib import Path
from statistics import median

from .artifacts import STATUSES
from .suite import EvaluationError, asset

VALID = {'passed','agent_failed','provider_failed','cancelled'}
METRICS = ('requests','tools_proposed','tools_started','tool_errors','permission_denied','elapsed',
           'input_tokens','output_tokens','total_input_tokens','cache_read_tokens','cache_write_tokens','cache_miss_tokens')


def metric_summary(rows):
    result = {}
    for name in METRICS:
        values = [row.get('metrics',{}).get(name) for row in rows]
        known = [v for v in values if type(v) in (int,float) and math.isfinite(v)]
        result[name] = {'samples':len(values),'known':len(known),'missing':len(values)-len(known),
                        'partial':sum(name in row.get('metrics',{}).get('incomplete_fields',[]) for row in rows),
                        'median':median(known) if known else None,'min':min(known) if known else None,'max':max(known) if known else None}
    return result


def summarize(rows, reviews=()):
    counts = {status:0 for status in sorted(STATUSES)}
    for row in rows:
        if row['status'] not in STATUSES:
            raise EvaluationError('结果状态未知')
        counts[row['status']] += 1
    valid = [row for row in rows if row['status'] in VALID]
    success = [row for row in rows if row['status'] == 'passed']
    expected = sum(len(row.get('manual',[])) for row in rows if row['status'] != 'not_run')
    review_counts = dict(Counter(row['status'] for row in reviews))
    reviewed = sum(row['status'] in {'pass','fail'} for row in reviews)
    def dimension(boundary):
        checks = [check for row in rows for check in row.get('checks',[]) if bool(check.get('boundary'))==boundary]
        return {status:sum(check.get('status')==status for check in checks) for status in ('pass','fail','undetermined')}
    return {'planned':len(rows),'started':sum(row.get('started',row['status']!='not_run') for row in rows),
            'counts':counts,'automatic':{'passed':len(success),'denominator':len(valid),'rate':len(success)/len(valid) if valid else None},
            'boundary_violations':sum(any(c.get('boundary') and c.get('status')=='fail' for c in row.get('checks',[])) for row in rows),
            'functional_checks':dimension(False),'boundary_checks':dimension(True),
            'failure_reasons':dict(Counter(row.get('reason','未记录') for row in rows if row['status']!='passed')),
            'metrics':{'all':metric_summary(valid),'passed':metric_summary(success)},
            'manual':{'expected':expected,'reviewed':reviewed,'unreviewed':max(0,expected-reviewed),'counts':review_counts,
                      'dimensions':{name:dict(Counter(row['status'] for row in reviews if row['dimension']==name)) for name in sorted({dim for row in rows for dim in row.get('manual',[])})}}}


def read_reviews(directory, run_id, results):
    root = Path(directory)
    path = root/'reviews.jsonl'
    if not path.exists():
        return []
    path = asset(root, 'reviews.jsonl')
    identities = {(row['case_id'],row['trial_id']):row for row in results}
    seen, rows = set(), []
    try:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            required = {'run_id','case_id','trial_id','dimension','status','reviewer','reason','evidence'}
            if not isinstance(row,dict) or not required <= set(row) or set(row)-required-{'time'}:
                raise EvaluationError('人工评价字段无效')
            key = (row['case_id'],row['trial_id'])
            identity = (*key,row['dimension'])
            if row['run_id']!=run_id or key not in identities or identity in seen:
                raise EvaluationError('人工评价身份未知或重复')
            if row['dimension'] not in identities[key].get('manual',[]) or row['status'] not in {'pass','fail','unreviewed'}:
                raise EvaluationError('人工评价维度或决定无效')
            if not all(isinstance(row[n],str) and row[n] for n in ('reviewer','reason')) or not isinstance(row['evidence'],list) or not row['evidence']:
                raise EvaluationError('人工评价必须说明理由与证据')
            for ref in row['evidence']:
                asset(root, ref)
            seen.add(identity)
            rows.append(row)
    except (ValueError, KeyError, TypeError) as error:
        raise EvaluationError(f'人工评价无效：{error}') from None
    return rows


def markdown(report, rows):
    automatic = report['automatic']
    rate = f"{automatic['passed']}/{automatic['denominator']}" if automatic['denominator'] else '不可计算（有效分母为 0）'
    lines = ['# Agent 测评报告','',f"自动通过：{rate}；计划尝试：{report['planned']}。",
             f"测评器失败：{report['counts']['harness_error']}；边界违规：{report['boundary_violations']}。",
             f"人工已评：{report['manual']['reviewed']}；未评价：{report['manual']['unreviewed']}。",'',
             '| 用例 | 尝试 | 结果 | 停止原因 | 请求 | 耗时（秒） | 原因 |','| --- | --- | --- | --- | --- | --- | --- |']
    for row in rows:
        metrics = row.get('metrics',{})
        reason = str(row.get('reason','')).replace('|','\\|').replace('\n',' ')
        lines.append(f"| {row['case_id']} | {row['trial_id']} | {row['status']} | {','.join(row.get('stops',[]))} | {metrics.get('requests','未知')} | {metrics.get('elapsed','未知')} | {reason} |")
    lines += ['','完整指标与各项独立检查见 report.json 及逐次 result.json；人工结果不参与自动通过率。']
    lines += ['','| 指标 | 全部有效：已知／样本 | 缺失／部分 | 中位数［最小，最大］ | 自动通过：已知／样本 | 中位数［最小，最大］ |',
              '| --- | --- | --- | --- | --- | --- |']
    for name in METRICS:
        a,b = report['metrics']['all'][name],report['metrics']['passed'][name]
        lines.append(f"| {name} | {a['known']}/{a['samples']} | {a['missing']}/{a['partial']} | {a['median']} [{a['min']}, {a['max']}] | {b['known']}/{b['samples']} | {b['median']} [{b['min']}, {b['max']}] |")
    lines += ['','未知指标显示 None，部分值是已知请求的合计，不代表完整用量。']
    return '\n'.join(lines)+'\n'
