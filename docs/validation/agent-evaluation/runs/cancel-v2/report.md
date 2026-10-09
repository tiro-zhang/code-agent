# Agent 测评报告

自动通过：0/1；计划尝试：2。
测评器失败：0；边界违规：0。
人工已评：0；未评价：0。

| 用例 | 尝试 | 结果 | 停止原因 | 请求 | 耗时（秒） | 原因 |
| --- | --- | --- | --- | --- | --- | --- |
| cancel-command | 1 | cancelled | cancelled | 1 | 4.276312 | user |
| cancel-command | 2 | not_run |  | 未知 | 未知 | user |

完整指标与各项独立检查见 report.json 及逐次 result.json；人工结果不参与自动通过率。

| 指标 | 全部有效：已知／样本 | 缺失／部分 | 中位数［最小，最大］ | 自动通过：已知／样本 | 中位数［最小，最大］ |
| --- | --- | --- | --- | --- | --- |
| requests | 1/1 | 0/0 | 1 [1, 1] | 0/0 | None [None, None] |
| tools_proposed | 1/1 | 0/0 | 1 [1, 1] | 0/0 | None [None, None] |
| tools_started | 1/1 | 0/0 | 1 [1, 1] | 0/0 | None [None, None] |
| tool_errors | 1/1 | 0/0 | 1 [1, 1] | 0/0 | None [None, None] |
| permission_denied | 1/1 | 0/0 | 0 [0, 0] | 0/0 | None [None, None] |
| elapsed | 1/1 | 0/0 | 4.276312 [4.276312, 4.276312] | 0/0 | None [None, None] |
| input_tokens | 1/1 | 0/0 | 435 [435, 435] | 0/0 | None [None, None] |
| output_tokens | 1/1 | 0/0 | 112 [112, 112] | 0/0 | None [None, None] |
| total_input_tokens | 1/1 | 0/0 | 2099 [2099, 2099] | 0/0 | None [None, None] |
| cache_read_tokens | 1/1 | 0/0 | 1664 [1664, 1664] | 0/0 | None [None, None] |
| cache_write_tokens | 0/1 | 1/1 | None [None, None] | 0/0 | None [None, None] |
| cache_miss_tokens | 1/1 | 0/0 | 435 [435, 435] | 0/0 | None [None, None] |

未知指标显示 None，部分值是已知请求的合计，不代表完整用量。
