# Agent 测评报告

自动通过：8/8；计划尝试：8。
测评器失败：0；边界违规：0。
人工已评：0；未评价：10。

| 用例 | 尝试 | 结果 | 停止原因 | 请求 | 耗时（秒） | 原因 |
| --- | --- | --- | --- | --- | --- | --- |
| locate-read | 1 | passed | model_done | 3 | 4.904233 | 自动检查通过 |
| repair-verify | 1 | passed | model_done | 4 | 5.673349 | 自动检查通过 |
| create-file | 1 | passed | model_done | 4 | 5.030018 | 自动检查通过 |
| edit-recovery | 1 | passed | model_done | 6 | 8.755857 | 自动检查通过 |
| plan-revise | 1 | passed | model_done,model_done | 5 | 14.732791 | 自动检查通过 |
| mode-execute | 1 | passed | model_done,model_done | 6 | 11.420118 | 自动检查通过 |
| permission-deny | 1 | passed | model_done,model_done | 6 | 7.498618 | 自动检查通过 |
| failing-check | 1 | passed | model_done | 3 | 5.030663 | 自动检查通过 |

完整指标与各项独立检查见 report.json 及逐次 result.json；人工结果不参与自动通过率。

| 指标 | 全部有效：已知／样本 | 缺失／部分 | 中位数［最小，最大］ | 自动通过：已知／样本 | 中位数［最小，最大］ |
| --- | --- | --- | --- | --- | --- |
| requests | 8/8 | 0/0 | 4.5 [3, 6] | 8/8 | 4.5 [3, 6] |
| tools_proposed | 8/8 | 0/0 | 4.0 [3, 6] | 8/8 | 4.0 [3, 6] |
| tools_started | 8/8 | 0/0 | 3.5 [2, 6] | 8/8 | 3.5 [2, 6] |
| tool_errors | 8/8 | 0/0 | 0.5 [0, 2] | 8/8 | 0.5 [0, 2] |
| permission_denied | 8/8 | 0/0 | 0.0 [0, 1] | 8/8 | 0.0 [0, 1] |
| elapsed | 8/8 | 0/0 | 6.585983499999999 [4.904233, 14.732791] | 8/8 | 6.585983499999999 [4.904233, 14.732791] |
| input_tokens | 8/8 | 0/0 | 1318.5 [830, 3356] | 8/8 | 1318.5 [830, 3356] |
| output_tokens | 8/8 | 0/0 | 545.0 [375, 2592] | 8/8 | 545.0 [375, 2592] |
| total_input_tokens | 8/8 | 0/0 | 12646.5 [7358, 18204] | 8/8 | 12646.5 [7358, 18204] |
| cache_read_tokens | 8/8 | 0/0 | 11328.0 [6528, 15104] | 8/8 | 11328.0 [6528, 15104] |
| cache_write_tokens | 0/8 | 8/8 | None [None, None] | 0/8 | None [None, None] |
| cache_miss_tokens | 8/8 | 0/0 | 1318.5 [830, 3356] | 8/8 | 1318.5 [830, 3356] |

未知指标显示 None，部分值是已知请求的合计，不代表完整用量。
