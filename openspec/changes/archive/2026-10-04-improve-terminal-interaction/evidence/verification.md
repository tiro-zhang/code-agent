# improve-terminal-interaction 本轮验收汇总

OpenSpec 25/25 任务已完成。

本轮实现独立增强终端后端，多行草稿／括号粘贴／进程内历史、严格本地命令与补全、只读 `/status`、稳定调用状态和真实用量、单一审批审阅、完整分页及旁路结果隔离。保留 plain 管道协议、既有权限决定与 MCP 共享连接／取消合同。

## 自动化与构建

- 最终完整测试 **691 passed in 82.99s**，见 `pytest-final.txt`；包含终端、权限、计划、Agent Loop、缓存统计、两种 Provider 和 MCP 新旧协议／双传输测试。
- `build.txt` 记录源码包和 wheel 构建。
- `openspec-validation.txt` 记录本轮严格校验；`diff-check.txt` 记录格式检查。
- 首次基线的 13 个 HTTP 测试因沙箱禁止 bind localhost 失败；在允许本地测试端口后全部通过。最终全套使用同样必要权限，无跳过替代。
- 审查发现的输入残留、粘贴部分标记、启动取消、运行期 EOF、未换行流式正文、分页缺行、窄屏页码、旁路结果及累计用量边界已修复。见 `code-review.md`，当前无未解决审查项。

## 任务与证据对应

| OpenSpec 任务 | 主要验证 |
| --- | --- |
| 1.1–1.2 后端与兼容 | prompt_toolkit 依赖和 wheel；test_terminal_controller / test_terminal_app；TERM=dumb 与增强 PTY；StringIO、管道预读队列、重定向与初始化失败退化 |
| 1.3 生命周期 | test_permission_app 的 plain/enhanced SIGINT；test_terminal_input 的 EOF／取消；lifecycle-report.md 的启动 Ctrl+C、审批 Ctrl+D、主子进程收尾与实际 termios |
| 2.1–2.2 多行／历史 | test_terminal_input 真实解析字节、Esc+Enter、CRLF、上下键与修改后发送；命令解析空白；tmux-report.md 的实际用户文本逐字符和一次请求断言 |
| 2.3 输入隔离 | input-isolation-pty-report.md：8 项真实 tmux PTY 边界；test_terminal_input 部分 opener、超时、新旧 paste 与连续答案；plain 真实 PTY 提前输入回归 |
| 3.1–3.3 本地命令／状态 | test_terminal_commands / app / state；tmux-report.md 的帮助、严格错误、Tab、plan修订/do、计划模式多行及完整状态查询 |
| 4.1–4.3 状态／用量 | test_terminal_state 的乱序、去重、迟到与旧run事件、elapsed、未知/部分缓存、逐请求覆盖和运行期累计；test_terminal_controller 的完成前流式正文可见 |
| 4.4 安全与窄屏 | test_terminal_approval/input/state 的中文宽字符、控制字符、敏感字段、动态尺寸；lifecycle-followup-final-report.md 的45×15实屏；lifecycle-isolation-report.md 的单Server失败隔离 |
| 5.1–5.3 审批合同 | test_terminal_approval/controller、现有权限测试；默认拒绝、四期限、完整快照、无预览读文件、永久保存失败、deny优先、外部参数身份；真实读/编辑/shell/MCP审批 |
| 5.4 并发旁路结果 | test_terminal_controller 的 plain安全边界、重复回调、先失败后成功；lifecycle-followup-final-report.md 同批真实读取；multiple-results-pty-report.md 审批期间全部旁路结果分页与异常保留 |
| 6.1–6.2 应用和MCP | 完整pytest；主规格mcp-client/mcp-tools/mcp-configuration对照；tmux双传输、精确参数改变拒绝、同连接取消复用、初始化取消、EOF、故障隔离及正常/卡住关闭 |
| 7.1 文档 | README与/help一致；本章独立checklist保留历史验收，结果基于本轮实际证据 |
| 7.2–7.4 真实对话 | tmux-report.md 及 tmux-*；含一次真实服务stream_error和随后独立成功的产物验证，未将失败轮次伪称全程成功 |
| 7.5 生命周期与布局 | lifecycle-report.md、lifecycle-followup-final-report.md、lifecycle-isolation-report.md、input-isolation-pty-report.md、multiple-results-pty-report.md |
| 7.6 最终收尾 | pytest-final.txt、build.txt、openspec-validation.txt、diff-check.txt、code-review.md、当前checklist和tasks |

## MCP 合同对照

- 初始化、分页、别名注册、协议和工具 Schema 仍由既有 MCP 实现负责；终端仅接管通知与事件展示，不增加调用重试。
- 取消已发送请求后同一个 stdio PID 和连接成功复用，通知请求 ID 与原请求对应；增强界面明确远端可能仍执行或有副作用，不声称回滚。
- 拒绝与授权 EOF 均有 wire“未发送”证据；改变参数重新审批；永久批准展示完整参数持久化说明；外部 path 不展示成本地真实目标。
- 单 Server 启动失败不影响另一个实际调用成功；异常关闭有界且主子进程被回收。
- HTTP取消、超时后同连接复用及新旧协议交叉组合由本轮自动化真实本地Server合同测试验证；真实模型tmux的双传输成功调用与独立stdio取消复用分别记录，不冒称所有交叉组合均人工实测。

## 范围与限制

真实模型使用已授权 DeepSeek Anthropic 兼容配置，未实测官方两家服务端；两种Provider协议均通过自动化回归。真实会话一度出现服务stream_error，状态准确、后续独立验证成功，记录保留。Alt+Enter以终端能发送等效Esc+Enter为前提。终端恢复直接验证ECHO/ICANON；macOS PENDIN位差异如实记录，不声称完整termios位级相同。

未自动归档、同步本变更到主规格、提交或合并。本轮保留工作区已有MCP实现、已完成归档和其他既有修改；MCP历史验收不替代本报告的新证据。
