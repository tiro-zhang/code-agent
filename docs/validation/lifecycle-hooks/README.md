# 生命周期 Hook 本轮验收（2026-10-05）

`add-lifecycle-hooks` 在用户指定的原目录实现。[OpenSpec apply](openspec-apply.json) 为 **28/28、all_done**。最终 **1123 passed in 127.09s**；源码包、wheel 和 OpenSpec strict 通过，独立代码审查问题已修复并复核。真实模型 tmux 验收共 **23 次工作请求、3 个运行时、12 个用户任务**，增强及纯文本终端均正常退出。开发验收完成时未提交、合并或归档；后续归档记录见文末。

## 自动化与构建

| 检查 | 本轮实际结果 | 证据 |
| --- | --- | --- |
| `uv sync --offline` | 成功，47 个包解析及本地项目安装；httpx2 声明直接依赖 | 锁文件及 wheel 元数据见 [审计](request-audit.json) |
| `uv run pytest -q` | 1123 passed，127.09 秒，退出码 0 | [最终日志](pytest.txt) |
| `uv build` | sdist 与 wheel 成功；wheel 包含 8 个 Hook 模块和共用 matching 模块，96 个 Python 文件与最终源码逐字节一致 | [构建日志](build.txt)、[打包审计](request-audit.json) |
| `openspec validate add-lifecycle-hooks --strict` | valid，退出码 0 | [严格校验](openspec.txt) |
| 独立只读代码审查 | 进程创建窗口超时、控制审批恢复阶段、压缩取消配对三项已修复；无剩余 Critical / Important | [审查与 RED→GREEN 记录](review.txt) |
| README 示例与凭据 | 实际加载器接受 4 条示例；公开验收材料及变更文档真实密钥命中 0 | [独立审计程序](audit_e2e.py)、[结果](request-audit.json) |

完整测试覆盖：严格 YAML／重复键／错误聚合及整体停用，共用 exact/glob/regex 与既有权限兼容，字段缺失／反向／类型边界，快照故障及日志故障隔离，命令／HTTP 的成功、异常、截断、超时与取消，黑名单和明确拒绝，once 原子提交，4/32 后台资源限制，关闭／规划清理，工作意图／完整响应／压缩成对，注入预算及消费时点，共享与独立 Skill 关联，工具拒绝回流／结果唯一 after／未启动元数据，串行副作用及原四路只读并发，终端审批／通知／草稿／光标／详情／纯文本降级。

测试过程中曾有两次完整运行失败，保留原日志：

- [MCP 时序失败](intermediate-mcp-pytest.txt)：旧 `legacy-False` 恢复测试在取消窗口记录数从 4 变为 5；1 failed、1119 passed。该项单独重跑 1 passed in 1.12s。
- [终端时序失败](intermediate-ui-pytest.txt)：旧窄屏授权分页测试缺少所见行；1 failed、1122 passed。该项单独重跑 1 passed in 1.10s。

这两次运行与真实 tmux 同时进行；没有修改对应 MCP／分页实现或旧测试。关闭真实终端后的最终完整运行全部通过。以上是观察结果，不把并行运行当作已证明的唯一原因。[较早完整通过日志](initial-pytest.txt) 为 1119 passed，最终结论使用新增审查回归后的 1123 项。

## 真实模型与夹具边界

使用现有且已授权的 `.env.claude`，实际请求模型为 `deepseek-v4-pro`、Anthropic 兼容协议。配置以仓库绝对路径传给 CLI，没有复制配置或密钥到临时项目。增强终端根目录为 `/private/tmp/mewcode-hooks-e2e-20261005`，纯文本根目录为 `/private/tmp/mewcode-hooks-plain-e2e-20261005`。

[夹具](prepare_e2e.py) 声明 8 条合成规则及受控命令，命令读取真正的 JSON stdin，执行文件格式化、拒绝、延迟和标记写入。`sitecustomize.py` 只记录安全请求／事件元数据，关闭记忆以限定本次工作请求数量，并为通知验收输入独立 `hook_notice`。模型、流式响应、工具调度、权限、Hook 动作、终端及会话均执行实际实现，没有供应商替身。启动和控制命令保留 default 审批；普通临时文件任务显式使用该临时项目的 bypass，黑名单／明确拒绝边界另由本轮自动化验证。

通知来源明确为 `e2e-notification-driver`。产品目前没有自动 Hook 成功通知，本轮通知检查验证终端路由合同，不能解释为成功动作自行弹通知。事件观测记录不保存完整工具参数、提示词、响应或 headers；tmux 输出经安全文本处理、密钥脱敏，`/status` 中实际 API 思考正文省略。

## 本轮场景与独立核验

| 场景 | 结果与范围 | 本轮证据 |
| --- | --- | --- |
| 启动 Hook 审批 | 启动先等待授权，显示 `#hooks[0]`、`session.start` 和精确命令；不创建用户任务 | [首次启动](startup-approval.txt)、[最终代码启动](final-startup-approval.txt) |
| 同步格式化后读取 | 创建 `format.txt` 后 Hook 转为 `HELLO HOOKS\n`，真实读取已见大写；成功聚合只有 write/read 两次工具 | [流式结果](formatting-result.txt)、[F2](f2-recent-tools.txt)、[产物与 SHA256](request-audit.json) |
| 前置拒绝与模型调整 | `protected.txt` 没有创建；原调用得到 `hook_denied`、`not_started=true`；模型改建并读取 `alternative.txt`，实际内容 `DENIED TEST\n` | [拒绝与替代](denial-and-alternative.txt)、[实际事件](enhanced-events.jsonl)、[产物](request-audit.json) |
| 注入和 once | 每个运行时只有首个实际工作请求含待注入标记，后续为 false；同一运行时多轮仅一个 startup/background，重启再执行一次 | [请求审计](request-audit.json)、[原始安全请求元数据](enhanced-requests.jsonl)、[动作记录](enhanced-actions.jsonl) |
| 后台动作与通知隔离 | 真实后台标记完成；独立通知不新增工具／usage，不重置详情。草稿文字及逻辑光标保留；物理光标 X 保持 14，通知增加一行使 Y 从 21 到 22 | [草稿捕获](background-notice-draft.txt)、自动化 `test_terminal_projection` / `test_terminal_ui_keys` |
| 超时故障放行 | read_file 已成功，后置延迟命令 1 秒超时只记日志，模型仍完成；受控 PID 不存在、无 `timeout.leaked` | [超时输出](timeout-result.txt)、[独立进程审计](request-audit.json) |
| 工具前台取消 | 命令实际开始后 Ctrl+C，先显示正在停止，清理后生成唯一 cancelled / not_started 结果；无延迟产物，下一轮回复 `HOOK_CONTINUE_OK` | [停止阶段](cancel-cleaning.txt)、[终态](cancel-completed.txt)、[核验](cancel-verified.json)、[继续](after-cancel-continue.txt) |
| 规划副作用跳过 | 最终代码在 plan 真正读取 slow.txt 并给方案；`planfile.txt` 不存在，timeout 动作次数仍为 1，mode 命令在切入 plan 时未启动 | [方案输出](final-plan-result.txt)、[基线](final-baseline.json)、[核验](control-cancel-audit.json) |
| 控制 Hook 审批及通知 | `/do` 审批显示 `#hooks[7]`、`mode.changed`；通知期间已输入答案 2 保留，审批仍独占输入 | [审批与通知](control-approval-with-notice.txt) |
| 控制执行与 Ctrl+C | 批准后恢复 control，输入关闭；Ctrl+C 40ms 捕获正在停止，0.736s 后空闲；延迟 mode.done 未生成，工作请求／turn.start 均保持 17／9 | [执行](control-running.txt)、[停止](control-cancelling.txt)、[结束](control-cancelled.txt)、[核验](control-cancel-audit.json) |
| 控制成功及最近详情 | 后续短 Hook 成功，F2 和 `/status` 仍指向之前 slow.txt 的同一调用；本地控制／status 保持 17 次请求，随后真实回复 `FINAL_HOOK_OK` | [成功](control-success-completed.txt)、[F2](f2-after-control.txt)、[status](status-after-control.txt)、[核验](control-success-audit.json)、[继续](final-continue.txt) |
| 增强终端退出 | 最终代码正常 `/exit`，实际 pane 退出码 0，session.end 成对 | [退出审计](enhanced-exit-audit.json)、[安全事件](enhanced-events.jsonl) |
| TERM=dumb 降级 | 启动授权来源、固定格式化、真实读取、控制指令和独立通知正常；5 次工作请求，控制前后均为 3 次；实际 PTY 后续输出 984 字节，ESC 字节 0，退出码 0 | [启动](plain-startup-approval.txt)、[格式化](plain-formatting-result.txt)、[控制及通知](plain-control-and-notice.txt)、[读取](plain-read-result.txt)、[PTY 原文](plain-pty-output.txt)、[核验](plain-exit-audit.json) |

事件／请求／动作／终端阶段的安全 JSONL 分别以 `enhanced-` 和 `plain-` 前缀保存；[审计程序](audit_e2e.py) 从临时项目原文件核验：每个运行时 session.start/end 各一次，turn.start/end 与 message.user 数量相等，全部实际请求与 before_request 对应，每个运行时只注入一次，每个实际工具调用至多一个 after，Hook 命令不递归成为模型工具。

## 失败尝试和驱动器更正

- 初次工具取消驱动错过执行窗口，20 秒命令已完成，见 [未命中取消的捕获](cancel-before-interrupt.txt)。不计作取消通过；清除该受控产物后重新驱动，并在真实 started 标记出现后 40ms 取消，使用配对事件、超过延迟窗口后的产物和 PID 检查判定。
- [原始时间记录](cancel-timing.json) 的 cancelled=false 使用了错误的 `结束>` 文本判据；实际是 `本轮未完成>`。保留原数据，[独立核验](cancel-verified.json) 解释更正。控制取消原判据遗漏“当前”二字，也保留 false 和真实核验结果，见 [控制审计](control-cancel-audit.json)。
- 初次 F2 退出后发送 `/status` 太快，仍有草稿，意外成为普通模型消息，见 [失败尝试](status-after-hook.txt)；不作为本地 status 通过依据。[重新发送](status-after-local-input.txt) 和最终代码 [status](status-after-control.txt) 均独立核验本地零请求。
- 最终代码启动时首次 `/plan` 发送过早被启动阶段忽略，随后真实读取进入 default 审批。取消未启动调用，待空闲重新切换 plan，再执行真正规划任务；[进度](final-plan-progress.txt)、[已接受规划](final-plan-ready.txt) 和事件记录区分两次尝试。
- 纯文本正常退出后 tmux 已自动关闭 pipe，驱动再次关闭返回非零；独立读取 dead pane 的退出码 0 与原始 PTY 后完成核验，见 [退出审计](plain-exit-audit.json)。审计驱动初版把输入 `denied test` 的结果误写成无空格 `DENIEDTEST`，读取实际文件并对照原请求后修正为精确 `DENIED TEST\n`，没有放宽产物断言。

## 清单范围与未执行项

[逐项清单审计](checklist-audit.md) 覆盖当前 checklist 的每个复选项。本轮 Hook 章节 14 项通过；旧章节保留历史状态，但本轮没有逐一重造其独立真实服务／日期／产物场景，因此逐项列为未执行并解释原因。当前完整回归是新执行的 1123 项，不引用旧章节的历史测试数量作为本轮结果。

本轮没有重复真实官方 Anthropic／OpenAI、Ark 入口、远程 MCP、独立 Skill 真实子模型或真实远端 HTTP 回调。协议、Skill、MCP、HTTP 与压缩集成由本次完整自动化覆盖；HTTP 使用本地受控服务。真实模型不能被自动化覆盖替代的场景只以上表为准。大注入预算、队列饱和、HTTP 断连／重定向、压缩取消、reset／恢复 once 等故障边界使用自动化，不声称全部经过真实模型手工触发。

子 Agent 动作**只诊断占位**；once **仅本次运行时内存标记，无持久化**；规则**按声明顺序，无显式优先级**。这些是规格排除项，未误报为已实现。

## 后续归档（2026-10-05）

用户选择先同步主规格再归档。新增 [lifecycle-hooks 主规格](../../../openspec/specs/lifecycle-hooks/spec.md)，完整保留 15 条需求、40 个场景及 Purpose；主规格不含 delta 操作标题。`openspec validate --specs --strict` 为 **21 passed、0 failed**，逐条比较确认无剩余同步差异后，将变更移至 [2026-10-05-add-lifecycle-hooks](../../../openspec/changes/archive/2026-10-05-add-lifecycle-hooks)。

归档前后 5 个文件 SHA256 全部一致，包括 `.openspec.yaml`；4 份产物及 28 个任务全部完成，活动变更列表已无该变更。详见 [归档核验](archive.json)。未创建 Git 提交。前文测试、请求和 OpenSpec apply JSON 保留开发验收时的实际记录，不改写其历史路径。
