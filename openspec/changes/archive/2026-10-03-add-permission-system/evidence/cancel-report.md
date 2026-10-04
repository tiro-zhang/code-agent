# 权限8.6真实tmux验收

项目：`/private/tmp/mewcode-permissions-e2e-cancel`。独立 socket：`mewcode-perm-cancel`。真实启动：repo `.venv/bin/python -m mewcode --config repo/.env.claude`，显示 claude-demo，default。未修改HOME、用户级配置或其他项目。

脱敏记录：`evidence/cancel-tmux.txt`，按标签分段；保存时读取本地已授权配置密钥仅用于替换，已断言记录不含该密钥。全部六个临时fixture最终内容与初始一致。

| 场景 | 结果 | 证据标签与实际观察 |
| --- | --- | --- |
| 授权等待Ctrl+C | 首次失败，修复后通过 | `ctrl-c-delayed-before-fix`：仅C-c未立即恢复，额外输入才唤醒；`ctrl-c-fixed-without-extra-input`：重启修复CLI，仅C-c立即cancelled未启动并回到你> |
| 取消后真实下一问 | 通过 | `ctrl-c-next-question-and-empty-deny-success`：新问回复“取消后正常。”并model_done，无遗留授权输入变成用户问题 |
| 授权Ctrl+D EOF | 通过 | `approval-eof-exit`：read_file cancelled未启动，未发第2模型请求，CLI_EXIT=0 |
| 空答拒绝续接 | 通过 | `ctrl-c-next-question-and-empty-deny-success`：对empty.txt直接Enter，permission_denied无开始，模型第2请求正常说明拒绝并结束 |
| 等待期间外部新增deny | 通过 | `waiting-before-new-deny`先确认changed.txt等待；此时项目YAML新增exact deny，随后输入2；`new-deny-after-user-once-prevents-start`显示permission_denied、无tool_started，模型正常第2轮解释，正文未出现 |
| 同轮两个read请求 | 通过 | `two-parallel-reads-serial-approval-success`：模型第1响应同时发出one.txt/two.txt两调用；one的独立request ID展示后输入2，随后才对two的新ID输入2；开始/成功均正确绑定调用，最终答案分别是“并行一号海棠”和“并行二号星河” |
| 等待不计执行timeout | 自动化通过 | 真实验收不额外等待30秒；`test_approval_wait_is_outside_execution_timeout`实际timeout设0.5秒，审批等待0.6秒仍未启动，批准后write成功 |

真实取消缺陷根因是 signal.signal handler直接Event.set不会唤醒无timer的selector。按root授权新增外部os.kill(SIGINT)+真实PTY+无timer子进程回归，旧code 2秒未恢复RED；app handler改为当前loop.call_soon_threadsafe(Event.set)后GREEN。没有抛KeyboardInterrupt。真实CLI重启后复验如上。

定向复验：`test_approval_wait_is_outside_execution_timeout`与`test_external_sigint_wakes_idle_child_event_loop_at_approval`，2 passed in2.18s。root已记录最终全套449 passed in34.52s。

会话清理：正常退出所有仍在空闲提示符的CLI后关闭本独立tmux server；未保留活动工具或验收会话。
