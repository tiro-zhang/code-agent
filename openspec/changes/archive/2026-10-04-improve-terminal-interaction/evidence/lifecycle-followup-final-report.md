# 追加真实模型验收：取消复用、并发审批与最终窄屏复验

日期：2026-10-04。所有场景使用独立 `tmux -L mewcode-ui-lifecycle` 和独立 `/private/tmp/mewcode-ui-lifecycle-20261004/<场景名>` 项目。入口为仓库 `.venv/bin/mewcode --config <仓库>/.env.claude`，使用已有已授权真实模型配置，未复制或输出凭据。本验收代理不改产品代码、tasks 或 checklist；修复由根代理及开发代理完成，验收在收到落地通知后开启全新应用进程。

## 本次实际结果

| 场景 | 本次证据与观察 | 结果 |
| --- | --- | --- |
| 慢 MCP 已发送后 raw Ctrl+C | `cancelreuse` 的 wire 已记录 id4 echo，参数 `{"text":"LIFECYCLE-SENT-CANCEL-20261004","delay":20}`。本次批准后确认 wire，再按 Ctrl+C；直接读取 PTY echo=false/icanon=false。界面返回 cancelled，并明确“远端可能仍在执行或已有副作用”。 | 通过 |
| 同应用同连接下一轮成功 | 下一轮 echo id5 参数 `{"text":"LIFECYCLE-AFTER-CANCEL-REUSE-20261004"}`，新授权后真实成功；模型回显新文本及PID18285。整个 wire 只有一次 PROCESS_START(PID18285)、一次 server/discover、一次两页 tools/list；id4/id5分别只发送一次，取消通知对应 requestId4。 | 通过 |
| 最终真实同批并发审批 | 全新 `concurrentfinal` 中真实模型一次批次同时生成 #1/#2 两个read_file，请求1/20，两个工具在第一个授权页即同时可见。临时项目 ask规则逐次审批；批准#1后，#2独立请求等待授权，#1真实工具已完成。增强区域即时显示“旁路 1> 工具> [#1] 成功 · read_file”，下一行显示“工具> [#2] 等待授权 · read_file”。 | 通过 |
| 45×15 窄屏旁路及待审身份 | #2未决定时resize到45×15。在targets/arguments/all每个实际页面，都同时看见“旁路 1> 工具> [#1] 成功 · read_file”和“工具> [#2] 等待授权 · read_file”，两种状态未被长路径遮挡，旁路结果未把审批选项插断。 | 通过 |
| 45×15 窄屏页码及长详情阅全 | 审阅标题实际显示“审阅 1/2 页 · arguments · [4db85806e4fb4a2…]”，页码及section可读。targets1页、arguments2页、all6页逐页next，最后页back正文与前一页一致；所有页均保存。将正文折行拼接后，完整长中文真实绝对路径与完整参数中的相对路径均与请求文件完全相等。查看期间未批准#2。 | 通过 |
| 决定后完整日志及真实结果保留 | 恢复140×45后批准#2；#1/#2两条完整成功终态日志均刷入，真实模型返回文件内独特标记 `READ-CONCURRENT-FINAL-1-20261004`、`READ-CONCURRENT-FINAL-2-20261004`。观察到新model_done、请求2/20、空闲后才发送/exit，没有使用旧空闲footer判完成。 | 通过 |
| 最终恢复和清理 | `concurrentfinal` /exit约0.175秒、exit_code0，echo/icanon均true；应用PID21840与fixturePID21846都已不存在。所有本次创建的会话/fixture均清理，专用socket无服务器。 | 通过 |

## 证据导航

- 取消复用：`lifecycle-cancelreuse.json`含完整wire数组、按键前termios和退出后进程检查；`lifecycle-cancelreuse-after-cancel.txt`、`lifecycle-cancelreuse-reused-result.txt`含真实对话。
- 最终并发：`lifecycle-concurrentfinal-wait.txt`是第一项授权页，明确同批#1/#2；`lifecycle-concurrentfinal-second-approval-with-first-result.txt`是第二项待审时第一项成功旁路摘要；`lifecycle-concurrentfinal-final-model-result.txt`保存两个完整结果及模型新结束标记。
- 最终窄屏：`lifecycle-concurrentfinal-pages.json`记录45×15完整页面、back页面和全文拼接检查；`lifecycle-concurrentfinal-narrow-*.txt`为实际可见viewport，不用滚动记录伪称当前页面可见。
- 最终恢复：`lifecycle-concurrentfinal.json`含退出code、完整termios和PID存在性检查；`lifecycle-followup-final-manifest.json`保存本次被验收app/terminal/fixture文件SHA256。

## 修前和中间复验记录

`lifecycle-followup-pre-fix-report.md`及`lifecycle-pre-fix-concurrent-*`保留修前：日志延迟后可保留，但审批活动区height1让旁路结果被#2待审项遮挡；45列标题长request ID裁掉页码。两项当时均明确未通过，未用修后结果覆盖历史证据。

第一轮修复的`lifecycle-concurrentfixed-*`证明宽屏旁路摘要立即可见、45列页码已可见；但旁路前缀“旁路结果1条（决定后保留完整日志）>”太长，窄屏只剩“工具>…”，编号/成功状态仍不可读。这项实测失败已回报根代理并保留为中间状态。窄屏断言失败后已排队的独立批准动作继续了该轮，其真实两项结果成功与最终清理同样保留，未称该轮窄屏通过。

根代理最终缩短旁路前缀，并把状态移到工具编号后、工具名称前；收到落地通知后新建`concurrentfinal`重跑上述全套真实模型+真实工具+真实PTY场景，所有列出的最终判据本次已通过。

初次修前多行tmux驱动输入曾截在换行处，随后改用单行JSON路径数组；正式最终会话从一开始使用单行完整请求，没有这一驱动问题。所有实际对话均为真实模型，无额外受控AgentEvent重放。MCP工具按副作用串行边界调度，因此并发场景使用真实内置只读read_file，不把两个外部MCP调用伪称并发。
