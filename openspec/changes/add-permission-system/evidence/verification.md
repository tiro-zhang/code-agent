# 权限系统验收记录

日期：2026-10-03。Python 3.11.14，macOS，使用项目 `.venv`；不自动提交或归档。

## 真实对话环境

- 实际启动命令：`/Users/tiro/workspace/code/vibecoding/mewcode-demo/.venv/bin/python -m mewcode --config /Users/tiro/workspace/code/vibecoding/mewcode-demo/.env.claude`；重启附加 `--permission-mode strict`。
- 供应商：Anthropic 兼容接口，模型 `deepseek-v4-pro`，使用已有配置；证据不保存密钥。
- 主验收项目：`/private/tmp/mewcode-permissions-e2e/project`，含 `notes.txt`、public／restricted 目录和越界链接。供应商配置放在仓库，不改变工具项目根。
- tmux 使用独立 socket `mewcode-perm-e2e`。另外两个独立临时项目用于搜索／shell 和取消验收，避免相互修改规则。
- 首次使用 `.env` 的 OpenAI 兼容服务出现 TLS 连接失败；记录于 [openai-tls-failure.txt](openai-tls-failure.txt)。后续改用已有的 `.env.claude` 完成真实请求，不将失败请求当作通过。

## 授权与规则

证据：[首次授权及拒绝恢复](approvals-and-deny-tmux.txt)、[重启与撤销](restart-and-revoke-tmux.txt)、[三来源冲突配置](three-sources.yaml)。

| 验收 | 真实结果 |
| --- | --- |
| 本次读取 | 第一次 read_file 选择 2；编辑后再次读取仍出现新授权请求。 |
| 会话编辑 | edit_file 选择 3；下一任务同路径内容改变仍可复用，没有扩大到 read_file。 |
| 永久读取 | 第二次 read_file 选择 4；本地 YAML 保留已有 rules，仅新增 read_file／真实 notes.txt 路径批准。 |
| plan／do | strict 下 plan 只读；撤销 session 后 /do 保留 strict 与永久读取批准，但 edit_file 重新询问。 |
| 重启 | read_file 永久批准恢复，edit_file 会话批准失效；重新批准一次后实际修改并读取验证。 |
| 永久撤销 | `/permissions revoke permanent` 后同文件 read_file 重新询问；空答产生普通 permission_denied，模型继续报告拒绝。 |
| 三来源冲突 | 用户 deny、项目 allow、本地 ask 对同目标冲突；bypass 仍拒绝创建 blocked.txt，无开始事件、文件不存在。 |
| Agent 调整 | 收到拒绝后模型请求创建 alternative.txt，实际内容 fallback；没有中止 Agent Loop。 |
| 实际文件 | notes.txt 依次从 hello cat 变为 hello mew、hello purr、hello calm、hello quiet；最后独立读取一致。 |

为三来源验收临时创建的 `~/.mewcode/permissions.yaml` 原先不存在，内容仅含 `write_file(blocked.txt)` 的 deny；验收后核对内容未变化并移除，恢复原状态。未改动用户原有权限文件。

## 自动化与复核

最终自动化回归：**449 passed in 34.52s**，见 [pytest-final.txt](pytest-final.txt)。OpenSpec 严格校验通过，见 [openspec-validation.txt](openspec-validation.txt)。`git diff --check` 与 `compileall` 通过。

- 配置／规则／批准：严格 YAML、三来源优先级、十二种模式组合、原子存储、冲突、撤销与失败降级。
- 执行入口：授权前无目标工作进程和副作用；等待不扣执行 timeout；实际开始事件与等待分开；拒绝结果配对回灌，同批合法调用继续。
- 搜索：仅元数据先枚举，rg 内容搜索实参只含获准文件；覆盖空集合正则校验、二进制检测、截断与取消回收。
- 终端：真实 fd／PTY 检查取消、EOF、无通道与提前输入；发现旧输入误批后已补回归修复。
- 原基线的两项 TLS 提示测试经用户明确授权修复，沿包装异常链按类型分类且不回显凭据；原测试文件未修改。

真实 tmux 发现审批等待的外部 SIGINT 没有立即唤醒 selector。现已改用事件循环线程安全调度唤醒：子进程／PTY 外部 SIGINT 测试先失败后通过；真实 tmux 仅按 Ctrl+C 即返回提示符，随后真实对话正常。详见 [取消验收记录](cancel-tmux.txt)。

## 搜索、shell 与最终审查

[搜索与 shell 报告](search-shell-report.md)记录9轮真实对话、18次模型请求：部分和全部内容／路径搜索受限提示正确；受限文件名和内容探针没有进入记录；无害复合命令审批前无副作用，子命令deny阻止整条执行，完整exact允许正确生效。黑名单仅通过纯解析及即使误执行也只输出字面文本的printf载体验证。

[独立代码审查](code-review.md)记录四类已修复问题：动态参数遮蔽静态命令词、静态重定向磁盘写入漏判、提前输入误批准、外部SIGINT未唤醒。新增回归和真实终端复验均用于闭环，当前没有未修复的Critical／Important问题。

## 取消、排队与收尾

[取消验收报告](cancel-report.md)与[完整终端记录](cancel-tmux.txt)覆盖真实Ctrl+C、EOF、空答拒绝、审批等待期间新增deny、同一模型响应中的两个read请求逐一批准。每项均核对请求／调用标识和实际文件，无提前执行；取消后下一轮真实对话正常。等待时间不扣执行预算由独立自动化测试验证。

新增权限checklist逐项通过。所有专用tmux会话已退出，临时用户级规则已移除。证据已再次检查不含两份模型配置的密钥。未修改用户原有供应商配置、未提交Git、未归档本变更。

范围限制保留：文件／搜索使用项目真实路径边界；Bash只受已知黑名单、可见结构规则与人工授权控制，不是OS文件系统沙箱。网络限制、资源配额与审计日志不在本章。
