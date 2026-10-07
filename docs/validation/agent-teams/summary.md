# add-agent-teams 本轮实施与验收

已在用户指定的当前工作区实施持久 Team Lead、固定成员与独立 Worktree、inprocess／tmux 完整实例、共享任务 DAG／原子领取、点对点邮箱与协议、审批、磁盘接续、私有 Git 整合与冲突租约、coordinator 双开关。创建／显式恢复保持空闲；成员模型结束、idle、提交、独立接纳、整合及发布分别记录。

2026-10-06 已按用户选择同步主规格后归档，保留 `spec-driven` 工作流元数据及全部 49 项已完成任务。归档见 [tasks.md](../../../openspec/changes/archive/2026-10-06-add-agent-teams/tasks.md)。本次同步新增 5 个主规格、更新 6 个已有主规格，合计新增 40 个需求、修改 1 个需求；11 份增量规格的 81 个场景均已核对，已有 Purpose 和无关需求保持。同步后 `openspec validate --specs --strict --no-interactive` 为 **30 passed、0 failed**，归档前当前变更严格校验通过。

## 最终验证

| 验证 | 本次结果 |
| --- | --- |
| 完整 pytest，真实 tmux／HTTP／子进程 | **1823 passed，0 failed、0 skipped，354.16s**；[本轮最终输出](pytest-final.txt) |
| `uv build` | 通过；[本轮构建输出](build-final.txt)，生成 `dist/mewcode-0.1.0.tar.gz` 和 `dist/mewcode-0.1.0-py3-none-any.whl`，wheel 内 13 个 teams 模块齐全 |
| `openspec validate add-agent-teams --strict` | 通过 |
| `git diff --check` | 通过 |
| 11 份规格的 81 个场景 | 已逐行映射到实际测试；限定范围和未注入窗口继续列明，见 [spec-audit.md](spec-audit.md) |
| 本次真实已授权模型双后端与 Git 产物 | 通过；成员并行、审批、直接消息、同任务唯一 claim、依赖同步、复用／恢复、双开关、冲突解决／回滚、活动工具取消均有终端和独立状态证据，见 [e2e.md](e2e.md) |

最终命令使用项目已有依赖、临时独立短 pytest 根和公开构建依赖；测试日志仅复制本轮最终输出。全量包含普通子任务、会话、权限、规划模式及团队新功能。源码与测试冻结后执行，最终源码摘要见 [source-snapshot.json](source-snapshot.json)，273 个 Python 文件在运行期间未变，不将验收途中编辑的旧进程当作热更新能力。

## 修复及失败记录

真实验收和独立审查发现并修复：终态任务晚到审计覆盖成果、预算跨段与取消绑定、请求边界取消消费、全局规划成员门禁、审批前 Hook、默认 Team 工具授权、coordinator 模型声明旁路、新目标开工基线同步、缺结果恢复隔离、零预算通知自动排队、停止未确认的错误 UI，以及新 claim 发布前提前解除审核。相应测试先复现再通过。

第一轮最后回归快照为 **1814 passed、3 skipped，366.96s**，三项真实 tmux 继承测试被默认长 Unix socket 路径跳过；随后修复测试 socket 目录，并修复最终恢复审查发现的错误重新指派状态边界。该结果只记历史快照，不能当最终源码全部通过。构建首次在受限网络下解析 setuptools 遇 DNS 错误，允许访问公开依赖后成功；未降低 TLS 校验。

## 验收限制

- 首轮 tmux Jerry 曾缺失工具结果／工作进程异常，底层原因未定位；明确停下、重新指派新 claim 后完成旧目标，后续同一身份新目标正常。保留原失败事实，不声明已修复未知根因。现恢复隔离已用真实 SIGKILL 与保存失败验证：不重放旧工具、不因普通消息继续旧 claim，核查及明确新领取后才恢复。
- 真实模型使用 bypass，仍保留 deny 和计划门禁；默认 ask／deny、MCP／Hook／Skill、精准竞锁、存档与 Git 发布故障由实际本地运行器和可控注入验证，不声称替代所有真实授权或任意崩溃窗口。
- Git 回滚只覆盖当前受管 Git 操作；不承诺撤销外部 shell、Hook 或远端副作用。Git 9.6 的未注入持久窗口见 [integration.md](integration.md)。
- tmux 和 inprocess 均有既定工具／权限边界，shell、共享 Git 与依赖不构成操作系统沙箱；不实现跨机器、实时流式成员通信或复杂 DAG 约束。

正常退出已停止本次受管成员与模型实例。专用临时 tmux server 在确认只剩本任务 shell 后由验收者清理；临时用户域、成员分支、原失败目标及全部磁盘证据保留。已比对本地配置的实际密钥值，仓库验收材料未包含真实密钥；只导出必要公开状态字段，不导出 nonce／租约令牌。
