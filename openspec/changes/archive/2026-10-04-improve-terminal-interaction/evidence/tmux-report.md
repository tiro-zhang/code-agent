# 真实模型 tmux 交互验收

日期：2026-10-04。独立临时项目 `/private/tmp/mewcode-ui-e2e-mboz7_7u`，专用 socket `mewcode-ui-e2e`，常规窗口 110×36、编辑审阅窗口 60×12。使用仓库已授权 `.env.claude` 对应的真实 DeepSeek Anthropic 兼容服务。未复制配置或凭据。透明 Provider 包装只记录最后一条用户消息和请求计数，所有响应来自实际服务；最终入口有 multiprocessing 主入口保护。

| 场景 | 本次实际结果 | 证据 |
| --- | --- | --- |
| 多行粘贴 | 含首尾空白、缩进、空行、正文 `/exit` 和尾部换行的括号粘贴只进入草稿；等待后模型请求数不变；Enter 后增量恰为 1，记录的用户文本与原始草稿逐字符相等；模型回复“多行收到”。 | tmux-01-paste-draft.txt、tmux-01-paste-result.txt、tmux-expected-draft.txt、tmux-model-requests.jsonl |
| 本地帮助和严格命令 | `/help`、`/hepl`、`/do extra`、非法权限值及 `/status` 均无额外模型请求；语法错误清楚。 | tmux-02-help-status-errors.txt |
| 补全 | `/sta` + Tab 变为 `/status` 草稿，尚未提交且请求数不变；Enter 查询仍为零模型请求。 | tmux-06-completion-draft.txt、tmux-06-completion-status.txt |
| 计划及普通修订 | `/plan` 产生完整计划，下一条普通消息修订第二行保持与 Python 断言；两轮分别完成。`/status` 显示计划可用，错误 `/do extra` 不消费；合法 `/do` 切换执行并实际调用文件工具。 | tmux-03-plan-revision.txt、tmux-04-execution-result.txt |
| 计划模式中的多行正文 | 先单独 `/plan` 零请求，再粘贴含空行、缩进和 `/exit` 的多行任务；提交一次、供应商最后用户文本完全相等，模式仍规划且计划可用。 | tmux-07-plan-multiline-draft.txt、tmux-07-plan-multiline-result.txt |
| 读取、编辑、分页和授权期限 | glob/read 选择本次批准；edit 选择会话批准。修改审阅在 60×12 使用 content、next/back/all，页码及差异可见；编辑工具实际成功。之后 shell 精确命令选择永久批准并执行 Python 全文断言。 | tmux-04-approval-*.txt、tmux-04-edit-narrow-*.txt、tmux-05-verify-review-1.txt |
| 产物与验证 | 独立读取临时 notes.txt，全文为 `新内容\n第二行保持\n`；真实 shell 退出 0、输出 UI-VERIFY-PASS，模型据此报告。 | tmux-file-verification.json、tmux-05-verify-result.txt |
| 状态详情 | `/status` 显示各请求实际 Token/缓存数据、完整调用 ID 和停止原因；一次真实 stream_error 的缺失字段显示未知、累计为部分、命中率未知。 | tmux-04-execution-result.txt、tmux-06-completion-status.txt |
| MCP 双传输与授权 | stdio local_demo、新协议；Streamable HTTP http_demo、旧协议。启动均注册 2 工具，真实调用 echo 参数均为 `{"text":"UI-MCP-ONE"}`。两项永久审批前展示安全连接、真实外部身份、精确 JSON 参数范围与完整参数持久化提醒；wire 各有实际 tools/call，实际结果带各自进程 PID。 | tmux-00-ready.txt、tmux-05-mcp-review-*.txt、tmux-05-mcp-result.txt、tmux-stdio.jsonl、tmux-http.jsonl |
| MCP 拒绝与不扩大范围 | 改为参数 UI-MCP-DENIED 后重新审批；选择拒绝，界面显示 permission_denied／未启动，模型不重试。wire 无该参数 tools/call。 | tmux-05-deny-review-1.txt、tmux-05-deny-result.txt、tmux-stdio.jsonl |

并发授权、中文长路径、45×15 完整分页、另一工具结果即时可见、慢 MCP 取消后同连接复用、启动取消、授权 EOF 与卡住关闭，见同目录 `lifecycle-report.md` 与 `lifecycle-followup-final-report.md`。跨阶段分块粘贴、连续数字不串审批及取消后新输入，见 `input-isolation-pty-report.md`；该组明确使用真实 PTY 和受控请求，不称为模型验收。

## 本轮发现与复验

1. 初始实现中未换行的流式正文会被重绘覆盖，小窗口每页末行也可能不可见。已补回归测试、修复，并用最终多行回复／小窗口分页复验。
2. 真实并发审阅揭示旁路结果被单行状态区挡住、窄屏页码及过长旁路前缀遮挡状态。最终 45×15 复验同时看到 `旁路 1> 工具> [#1] 成功 · read_file`、`[#2] 等待授权` 与页码，所有页完整可达；详见 lifecycle 最终报告，修前材料也保留。
3. `/do` 已完成真实编辑后，第 4 次模型请求遇到服务 stream_error。该轮如实标为未完成，不声称连续整个任务成功；随后发起独立验证请求，真实 shell 全文断言与模型回复成功。错误与成功记录分别保存。
4. 早期验收驱动将短暂重绘露出的旧空闲提示误判为新任务完成；最终用观测到的 finished 事件与稳定画面判断。早期透明入口缺少 `if __name__ == "__main__"`，文件工具 spawn 子进程时重复运行验收应用，造成超时；修正入口、重新启动后重跑全部文件流程成功。两项属于验收驱动问题，不作为产品成功或产品缺陷证据。
5. 原始 model-requests/stdio wire 包含早期驱动轮次，不能用全文件计数直接代表最后会话；`tmux-events.jsonl` 仅记录修正入口后的 8 个任务（7 个 model_done、1 个 stream_error）。每步请求增量断言在 `tmux-driver.txt` 中；文本正文及实际 fixture 请求保留便于复核。

## 验证边界

真实模型服务是上述已配置的兼容服务，未实测官方 OpenAI/Anthropic 服务端；两种 Provider 协议与工具 Schema 通过本轮完整自动化回归。Alt+Enter 只验证了终端发送等效 Esc+Enter 字节的合同，不保证所有终端快捷键映射。缺失 Token 字段和完整 termios PENDIN 位差异均不伪装为完整统计／位级相等。全部验收只修改独立临时项目，未将批准保存到仓库项目。
