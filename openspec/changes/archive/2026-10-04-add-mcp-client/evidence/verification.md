# add-mcp-client 实施验收

2026-10-04，按用户确认在当前工作目录实现并保留已有改动。用户批准补充生命周期适配后，继续保留 15 秒完整发现、30 秒单调用、5 秒关闭、单次取消不关闭共享连接、不隐式重发工具的原合同。

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| 修改前基线 | 449 passed in 34.57s | SDK 合同记录中的历史基线 |
| 官方 SDK 合同 | 10 项通过；两传输 × 两代协议、ID 配对、分页、取消及恢复 | test_mcp_sdk_contract.py、sdk-contract.md |
| 审查后专项回归 | 109 passed in 23.37s | review-green.txt |
| 最终全套 | **545 passed in 71.24s** | pytest-final.txt |
| 构建 | sdist 与 wheel 均成功，包含 MCP 模块与内部启动器 | build.txt |
| 规格校验 | `openspec validate add-mcp-client --strict` 通过 | openspec-validation.txt |
| 真实模型 tmux | 双传输、两轮复用、批准／拒绝、plan/do、故障隔离、取消、EOF、重启与正常／强制关闭通过 | tmux-report.md、终端捕获及 JSONL 日志 |
| 独立代码审查 | 4 个 Important 经反例复现后修复，1 个 Minor 已补齐 | code-review.md、review-red.txt |

配置测试覆盖两层整项覆盖、严格 YAML、环境单遍展开、有效环境快照、URL／字段校验、指纹及 venv 启动语义。工具测试覆盖稳定别名、冲突、Schema 引用与合法递归、统一参数错误、共享 64 KiB 预算、结构化与非文本内容、秘密脱敏。连接测试覆盖真实 SDK 调用、无半注册、零工具、业务失败、永久失效、延迟取消通知、进程组后代、卡住 owner 的总关闭预算及取消后复用。

权限与应用测试覆盖 exact/glob 规范 JSON、完整模式矩阵、批准绑定／保存／撤销、离线记录、安全展示、未经批准不发送、六内置工具仍可 spawn、plan 强制拒绝、两 Provider 的消息配对、Schema 故障结果配对后继续、启动 SIGINT 和 Provider 收尾。

对照 9 份 delta specs，实施范围覆盖 mcp-client、mcp-configuration、mcp-tools 及 agent-loop、interactive-chat、plan-mode、runtime-context、tool-permissions、tool-system 的集成合同。没有增加资源／提示词／采样能力、健康检查、自动重连或工具重试。未完成验收项：无。

OpenSpec 任务与本次新增的 MCP checklist 全部完成；历史 checklist 保持原记录。未提交 Git commit，未归档本变更，既有权限归档、AGENTS 和终端交互工作保持原样。
