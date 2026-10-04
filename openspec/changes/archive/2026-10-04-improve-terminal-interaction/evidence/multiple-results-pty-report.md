# 多个旁路结果的真实 PTY 专项复验

本次结果：**通过**。使用独立 tmux socket、80×16 真实 PTY，以及实际 `TerminalController`、`EnhancedTerminal` 和 `_Renderer`。注入受控 `AgentEvent`，不请求模型、不调用工具、不产生永久授权；此材料仅证明终端展示与审批边界，不能代替真实模型验收。

| 检查 | 本次实际结果 |
| --- | --- |
| 第三个调用等待审批，先到达 #1 超时、后到达 #2 成功 | 首屏持续显示 `FIRST-TIMEOUT`，并显示“异常／受限 1 条” |
| 随后再到达 17 条成功结果 | 首屏保持早先超时摘要，旁路总数为 19 |
| 决定前输入 `results` | 打开第 1/3 页，仍等待审批 |
| 连续输入 `next`、`next` | 实际展示第 2/3、3/3 页；所有 19 个结果标识均在可见正文中出现，无隐藏末行 |
| 输入 `back` | 回到第 2/3 页，页码和正文一致，仍未批准 |
| 最后输入 `1` | 第三请求返回 `deny`，其余旁路日志统一提交；19 个旁路终态及第三请求拒绝日志各提交一次 |

日志计数采用独立转发记录器：`EnhancedTerminal` 持续使用真实 stdout，控制器的完整日志同时原样转发至同一 PTY 并记录，从而区分审批预览与正式日志。第一次使用整个 scrollback 计数的断言失败，因为结束审阅时保留的预览快照也含有工具摘要；这不代表终态日志被重复提交。原始 [scrollback 记录](multiple-results-pty-failure.txt) 保留，正式日志计数已使用上述记录器复验通过。

材料：

- [可复现驱动](multiple-results-pty-driver.py)
- [断言、可见页内容、每个标识的日志计数](multiple-results-pty-result.json)
- [首屏：异常优先与总数](multiple-results-pty-00-summary.txt)
- [results 第 1 页](multiple-results-pty-01-results.txt)、[第 2 页](multiple-results-pty-02-next.txt)、[第 3 页](multiple-results-pty-03-next.txt)、[back 返回第 2 页](multiple-results-pty-04-back.txt)
- [最终实际提交日志](multiple-results-pty-committed-log.txt)
- [最终完整 PTY scrollback](multiple-results-pty-final-scroll.txt)

复现命令：`.venv/bin/python openspec/changes/improve-terminal-interaction/evidence/multiple-results-pty-driver.py`。驱动使用含进程 ID 的独立 socket，结束后仅清理本次创建的 server。未修改产品代码。
