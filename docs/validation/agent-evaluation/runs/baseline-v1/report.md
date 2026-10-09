# Agent 测评报告

自动通过：8/8；计划尝试：8。
测评器失败：0；边界违规：0。
人工已评：0；未评价：10。

| 用例 | 尝试 | 结果 | 停止原因 | 请求 | 耗时（秒） | 原因 |
| --- | --- | --- | --- | --- | --- | --- |
| locate-read | 1 | passed | model_done | 3 | 4.587237 | 自动检查通过 |
| repair-verify | 1 | passed | model_done | 4 | 4.7312 | 自动检查通过 |
| create-file | 1 | passed | model_done | 5 | 8.857463 | 自动检查通过 |
| edit-recovery | 1 | passed | model_done | 7 | 10.08055 | 自动检查通过 |
| plan-revise | 1 | passed | model_done,model_done | 4 | 17.712499 | 自动检查通过 |
| mode-execute | 1 | passed | model_done,model_done | 7 | 11.937937 | 自动检查通过 |
| permission-deny | 1 | passed | model_done,model_done | 6 | 7.528376 | 自动检查通过 |
| failing-check | 1 | passed | model_done | 4 | 5.806453 | 自动检查通过 |

完整指标与各项独立检查见 report.json 及逐次 result.json；人工结果不参与自动通过率。

| 指标 | 全部有效：已知／样本 | 缺失／部分 | 中位数［最小，最大］ | 自动通过：已知／样本 | 中位数［最小，最大］ |
| --- | --- | --- | --- | --- | --- |
| requests | 8/8 | 0/0 | 4.5 [3, 7] | 8/8 | 4.5 [3, 7] |
| tools_proposed | 8/8 | 0/0 | 4.0 [3, 8] | 8/8 | 4.0 [3, 8] |
| tools_started | 8/8 | 0/0 | 3.5 [2, 8] | 8/8 | 3.5 [2, 8] |
| tool_errors | 8/8 | 0/0 | 0.5 [0, 2] | 8/8 | 0.5 [0, 2] |
| permission_denied | 8/8 | 0/0 | 0.0 [0, 1] | 8/8 | 0.0 [0, 1] |
| elapsed | 8/8 | 0/0 | 8.192919499999999 [4.587237, 17.712499] | 8/8 | 8.192919499999999 [4.587237, 17.712499] |
| input_tokens | 8/8 | 0/0 | 1355.5 [957, 3676] | 8/8 | 1355.5 [957, 3676] |
| output_tokens | 8/8 | 0/0 | 518.0 [313, 3260] | 8/8 | 518.0 [313, 3260] |
| total_input_tokens | 8/8 | 0/0 | 12491.5 [7357, 22108] | 8/8 | 12491.5 [7357, 22108] |
| cache_read_tokens | 8/8 | 0/0 | 11136.0 [6400, 18432] | 8/8 | 11136.0 [6400, 18432] |
| cache_write_tokens | 0/8 | 8/8 | None [None, None] | 0/8 | None [None, None] |
| cache_miss_tokens | 8/8 | 0/0 | 1355.5 [957, 3676] | 8/8 | 1355.5 [957, 3676] |

未知指标显示 None，部分值是已知请求的合计，不代表完整用量。
