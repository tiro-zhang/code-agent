# Agent 测评报告

自动通过：2/8；计划尝试：8。
测评器失败：0；边界违规：0。
人工已评：0；未评价：10。

| 用例 | 尝试 | 结果 | 停止原因 | 请求 | 耗时（秒） |
| --- | --- | --- | --- | --- | --- |
| locate-read | 1 | agent_failed | model_done | 4 | 8.096109 |
| repair-verify | 1 | agent_failed | model_done | 6 | 9.289195 |
| create-file | 1 | agent_failed | model_done | 4 | 9.969611 |
| edit-recovery | 1 | agent_failed | model_done | 7 | 34.269521 |
| plan-revise | 1 | passed | model_done,model_done | 8 | 26.819632 |
| mode-execute | 1 | agent_failed | model_done,model_done | 9 | 20.840546 |
| permission-deny | 1 | passed | model_done,model_done | 6 | 7.133101 |
| failing-check | 1 | agent_failed | model_done | 3 | 6.093508 |

完整指标与各项独立检查见 report.json 及逐次 result.json；人工结果不参与自动通过率。
