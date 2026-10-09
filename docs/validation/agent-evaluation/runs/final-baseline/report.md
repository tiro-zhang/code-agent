# Agent 测评报告

自动通过：8/8；计划尝试：8。
测评器失败：0；边界违规：0。
人工已评：0；未评价：10。

| 用例 | 尝试 | 结果 | 停止原因 | 请求 | 耗时（秒） | 原因 |
| --- | --- | --- | --- | --- | --- | --- |
| locate-read | 1 | passed | model_done | 3 | 4.701749 | 自动检查通过 |
| repair-verify | 1 | passed | model_done | 4 | 5.282955 | 自动检查通过 |
| create-file | 1 | passed | model_done | 5 | 8.223554 | 自动检查通过 |
| edit-recovery | 1 | passed | model_done | 6 | 8.480372 | 自动检查通过 |
| plan-revise | 1 | passed | model_done,model_done | 5 | 17.077268 | 自动检查通过 |
| mode-execute | 1 | passed | model_done,model_done | 7 | 13.234387 | 自动检查通过 |
| permission-deny | 1 | passed | model_done,model_done | 6 | 7.132498 | 自动检查通过 |
| failing-check | 1 | passed | model_done | 4 | 5.102004 | 自动检查通过 |

完整指标与各项独立检查见 report.json 及逐次 result.json；人工结果不参与自动通过率。

| 指标 | 全部有效：已知／样本 | 缺失／部分 | 中位数［最小，最大］ | 自动通过：已知／样本 | 中位数［最小，最大］ |
| --- | --- | --- | --- | --- | --- |
| requests | 8/8 | 0/0 | 5.0 [3, 7] | 8/8 | 5.0 [3, 7] |
| tools_proposed | 8/8 | 0/0 | 4.5 [3, 9] | 8/8 | 4.5 [3, 9] |
| tools_started | 8/8 | 0/0 | 4.0 [2, 9] | 8/8 | 4.0 [2, 9] |
| tool_errors | 8/8 | 0/0 | 0.5 [0, 2] | 8/8 | 0.5 [0, 2] |
| permission_denied | 8/8 | 0/0 | 0.0 [0, 1] | 8/8 | 0.0 [0, 1] |
| elapsed | 8/8 | 0/0 | 7.678026 [4.701749, 17.077268] | 8/8 | 7.678026 [4.701749, 17.077268] |
| input_tokens | 8/8 | 0/0 | 1467.0 [947, 4603] | 8/8 | 1467.0 [947, 4603] |
| output_tokens | 8/8 | 0/0 | 473.5 [316, 2860] | 8/8 | 473.5 [316, 2860] |
| total_input_tokens | 8/8 | 0/0 | 13698.0 [7347, 22651] | 8/8 | 13698.0 [7347, 22651] |
| cache_read_tokens | 8/8 | 0/0 | 12096.0 [6400, 18048] | 8/8 | 12096.0 [6400, 18048] |
| cache_write_tokens | 0/8 | 8/8 | None [None, None] | 0/8 | None [None, None] |
| cache_miss_tokens | 8/8 | 0/0 | 1467.0 [947, 4603] | 8/8 | 1467.0 [947, 4603] |

未知指标显示 None，部分值是已知请求的合计，不代表完整用量。
