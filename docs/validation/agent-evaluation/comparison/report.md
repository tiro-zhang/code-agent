# Agent 测评比较

可直接比较：True；比较目的：code。
控制条件变化：无。
版本／配置变化：无。
归因条件：controlled。

| 用例 | 基线通过 | 候选通过 | 成功率差值 |
| --- | --- | --- | --- |
| create-file | 1/1 | 1/1 | 0.0 |
| edit-recovery | 1/1 | 1/1 | 0.0 |
| failing-check | 1/1 | 1/1 | 0.0 |
| locate-read | 1/1 | 1/1 | 0.0 |
| mode-execute | 1/1 | 1/1 | 0.0 |
| permission-deny | 1/1 | 1/1 | 0.0 |
| plan-revise | 1/1 | 1/1 | 0.0 |
| repair-verify | 1/1 | 1/1 | 0.0 |

| 维度 | 基线 | 候选 |
| --- | --- | --- |
| counts | {'agent_failed': 0, 'cancelled': 0, 'harness_error': 0, 'not_run': 0, 'passed': 8, 'provider_failed': 0} | {'agent_failed': 0, 'cancelled': 0, 'harness_error': 0, 'not_run': 0, 'passed': 8, 'provider_failed': 0} |
| boundary_violations | 0 | 0 |
| functional_checks | {'pass': 33, 'fail': 0, 'undetermined': 0} | {'pass': 33, 'fail': 0, 'undetermined': 0} |
| boundary_checks | {'pass': 21, 'fail': 0, 'undetermined': 0} | {'pass': 21, 'fail': 0, 'undetermined': 0} |
| manual | {'expected': 10, 'reviewed': 0, 'unreviewed': 10, 'counts': {}, 'dimensions': {'answer_accuracy': {}, 'failure_reporting': {}, 'plan_quality': {}, 'revision_incorporation': {}}} | {'expected': 10, 'reviewed': 0, 'unreviewed': 10, 'counts': {'unreviewed': 3}, 'dimensions': {'answer_accuracy': {'unreviewed': 1}, 'failure_reporting': {'unreviewed': 1}, 'plan_quality': {'unreviewed': 1}, 'revision_incorporation': {}}} |
| failure_reasons | {} | {} |

| 指标 | 基线全部有效：中位数／已知／缺失 | 候选全部有效：中位数／已知／缺失 | 基线成功：中位数／样本 | 候选成功：中位数／样本 |
| --- | --- | --- | --- | --- |
| requests | 5.0 / 8 / 0 | 4.5 / 8 / 0 | 5.0 / 8 | 4.5 / 8 |
| tools_proposed | 4.5 / 8 / 0 | 4.0 / 8 / 0 | 4.5 / 8 | 4.0 / 8 |
| tools_started | 4.0 / 8 / 0 | 3.5 / 8 / 0 | 4.0 / 8 | 3.5 / 8 |
| tool_errors | 0.5 / 8 / 0 | 0.5 / 8 / 0 | 0.5 / 8 | 0.5 / 8 |
| permission_denied | 0.0 / 8 / 0 | 0.0 / 8 / 0 | 0.0 / 8 | 0.0 / 8 |
| elapsed | 7.678026 / 8 / 0 | 6.585983499999999 / 8 / 0 | 7.678026 / 8 | 6.585983499999999 / 8 |
| input_tokens | 1467.0 / 8 / 0 | 1318.5 / 8 / 0 | 1467.0 / 8 | 1318.5 / 8 |
| output_tokens | 473.5 / 8 / 0 | 545.0 / 8 / 0 | 473.5 / 8 | 545.0 / 8 |
| total_input_tokens | 13698.0 / 8 / 0 | 12646.5 / 8 / 0 | 13698.0 / 8 | 12646.5 / 8 |
| cache_read_tokens | 12096.0 / 8 / 0 | 11328.0 / 8 / 0 | 12096.0 / 8 | 11328.0 / 8 |
| cache_write_tokens | None / 0 / 8 | None / 0 / 8 | None / 8 | None / 8 |
| cache_miss_tokens | 1467.0 / 8 / 0 | 1318.5 / 8 / 0 | 1467.0 / 8 | 1318.5 / 8 |

结果用于描述已保存样本，不构成统计显著性或服务端权重证明。
