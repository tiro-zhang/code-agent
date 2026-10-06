# Worktree 隔离本次验收

本次实施位于 `.worktrees/add-worktree-isolation`，分支 `add-worktree-isolation`，开发基线 `62b99e8`。验收时间为 2026-10-05 至 2026-10-06（Asia/Shanghai）。本报告只使用本次执行的证据。实施已于 2026-10-06 合并到本地 `main`，整合验证记录见文末。

最终完整回归 **1423 passed，157.25 秒**；源码包和 wheel 构建成功，wheel 含 8 个 Worktree 模块及 explore/general 内置角色；`git diff --check` 和 `openspec validate add-worktree-isolation --strict` 通过。独立只读代码复审无剩余 Critical／Important。日志见 [pytest](worktree-isolation/pytest.txt)、[构建](worktree-isolation/build.txt)、[评审修复回归](worktree-isolation/review-fixes.txt)；10 份增量规格的 106 个场景逐项对应见 [规格核对](worktree-isolation/spec-audit.md)。场景数不等于独立测试数。

## 基线失败及本次修复

用户要求“记录这次失败后继续 Worktree 实施”：实施前完整回归为 1262 通过、1 失败。失败为 `test_mcp_sdk_contract.py::test_recovery_stops_on_deadline_or_cancel_and_connection_survives[legacy-True]`，取消后 HTTP GET 数从 4 增为 5；单独重跑 1 passed（1.12 秒）。沙箱内的 HTTP bind 失败通过获批本地监听环境复跑，未作为功能故障或静默跳过测试。

中途完整回归还出现同一 MCP 用例的 `[modern-False]` 计数竞态，单独重跑通过。没有改动 MCP 实现；最终完整回归全部通过，记录上述失败而不宣称已修复其竞态。原日志保留于 `/tmp/mewcode-worktree-baseline*.log` 和 `/tmp/mewcode-worktree-full-tests-final.log` 等本次临时文件。

本次引入并修复的问题包括非隔离 ChildRuntime 的延迟初始化兼容性、同步关闭的存档锁释放时机，以及首次真实大结果回流重复携带原文导致缓存引用被截断。评审还发现可再生产物规则过宽、目录枚举预算、规划已有目录的恢复、退出状态写入失败、取消展示早于真实收尾、长答复截掉引用索引、回流确认早于主历史提交等边界；均有本次失败复现及通过回归。回流确认现在关联已持久保存的主 `history_commit`，旧过早记录不会掩盖缺失回流。

## 真实模型与终端设置

临时 Git 项目为 `/private/tmp/mewcode-worktree-e2e`，tmux 使用专用 socket `mewcode-worktree-e2e` 和会话 `mewcode-e2e`。模型为已授权配置中的 OpenAI 兼容 `ark-code-latest`；真实密钥及端点未复制到临时项目、日志或本报告。临时 `.env` 仅含无密钥的合成初始化值，模型启动配置来自项目外。

从临时项目启动实际程序，使用实施目录的 `PYTHONPATH=src` 和已有 uv 环境：`uv run --project <实施目录> --no-sync mewcode --config <授权配置>`。启动及恢复均由真实 CLI 完成。操作阶段曾误用不存在的 uv 路径、fixture 中不支持的 `match: regex`；修正为实际 `/opt/homebrew/bin/uv` 和支持的 glob 后启动成功。这些是验收配置错误，不计为产品通过。

父 `shared.py` 已提交版本为 `value = 'frozen'`，工作副本有未提交的 `value = 'parent dirty'`。editor 和 reader 角色均显式声明 `isolation: worktree`。父 README 读取由终端批准本次调用；新根子任务没有继承该批准。过程中观察到真实工具调用、流式文本、结束原因及恢复提示。终端证据见 [脱敏记录](worktree-isolation/terminal-evidence.txt)，实际文件、Git、租约及存档核验见 [结构化审计](worktree-isolation/artifact-audit.json)。

## 本次 checklist 结果

| 项目 | 结果 | 实际证据与限制 |
| --- | --- | --- |
| 原生 Git、多目录与独立分支 | 通过 | 真实两个 editor 子目录共享版本库；对应独立分支，父 dirty 保持。 |
| 冻结 HEAD、目录说明、显式 cwd | 通过 | 两个 editor 基线 `87a9800`，读取 frozen，各自 pwd 与目录一致；分别执行 `python -m unittest -q`，退出 0、Ran 1 test、OK。 |
| 父与两个子分别落盘 | 通过 | `agent_c5721c791ea946329954f28dfa6a286a` 为 one，`agent_c3cf73f0c6cf471e9c54fbb0aca6265a` 为 two；父为 parent dirty，两个子因修改保留。人工审批使本次两个真实任务没有重叠执行，实际并发由 `test_two_children_edit_same_name_with_independent_read_state` 验证。 |
| 新根批准及非交互缺许可 | 通过 | reader 读取 README 返回 `approval_required`、`not_started=true`；不请求人工，父先前批准未重绑。空提交任务添加组合命令曾被拒绝，改为独立原命令后成功。 |
| 大结果、来源、无成果清理 | 通过（修复后复验） | 首次 reader 回流过大、父定位缓存失败并人工取消，明确记为失败。复验 `agent_ac3c91227ec54556bf8a6dc7e1c57e12` 大结果归档 complete，目录及分支 removed；父随后只读其归档前两行并核对 provenance。内部 truncated 事实保留，不声称工具截断前全文存在。 |
| 真实取消及受控进程收尾 | 通过 | `agent_d0942b0d8f91417688ae78d85413669e` 已创建 cancel-start；执行 `/tasks cancel <id>` 后 cancel-end 不存在，目标命令进程已退出，终态 cancelled，目录因未追踪成果保留；实际租约已释放。观察超过原 sleep 时间后仍无结束标记。 |
| 取消后下一任务可运行 | 通过 | 随后的 reader 空提交任务完整结束，父终端仍可继续读取。 |
| 干净文件树的新提交保护 | 通过 | `agent_dffec7575f8a466984713c1fce259376` 创建空提交 `b24240f`，Git 文件树干净但 HEAD 不等于基线 `3dbad39`，目录和分支 retained；未推送。 |
| 恢复后的归档可读且无重放 | 通过 | 正常退出后 `--resume latest` 恢复 50 条工作消息；恢复本身未重建子队列或工具执行。新请求仅读取已删除 reader 子目录对应的父缓存第 1 行，provenance.task_id 正确。新取消子任务的 returned 事件关联实际主历史提交，恢复没有其缺失回流警告。修复前运行的旧记录缺关联仍保留警告，不伪造历史确认。 |
| 名称、链接逃逸、零 Git 只读恢复 | 通过（自动化） | 路径与管理器测试包括长度／深度、跨进程锁、祖先替换、双向 Git 关联、半初始化、零 Git／写入快速路径及删除前重查。未要求模型制造恶意目录。 |
| 有界初始化及 Git hooks | 通过（自动化） | 配置／初始化测试覆盖单项／总预算、枚举上限、依赖链接、复制基线、hooks 专属配置和不兼容布局；不复制批准、会话、记忆或启动密钥。真实创建复制的是合成 `.env`。 |
| 指令、Skill、记忆、提示和缓存根 | 通过（自动化） | 专项 runtime 与既有回归覆盖绝对目录身份、子项目资源、用户域合同、短提醒、同路径版本检查及原 Fork／未声明行为。CodeGraph 用于已有调用关系定位；索引指向父 checkout 时以实施目录源码核对，不把旧源码视为实现结果。 |
| 子权限与共享 Provider／MCP | 通过（自动化） | 父当前规则上限、deny／ask、热更新故障、会话／永久批准不重绑、模式收紧、共享客户端生命周期均覆盖。未伪造共享 stdio MCP 的逐调用 cwd。 |
| 异步 Hook、展示终态与实际租约 | 通过（自动化） | 阻塞 Hook、取消、关闭等待真实 handle、父 dispatcher 继续可用；证据保护与句柄释放分离。 |
| 归档部分失败、退出写入失败 | 通过（自动化） | 原目录和引用保留，租约可释放；清理受阻不覆盖模型结束原因。长答复的独立缓存索引及取消后的真实证据、用量、请求帐本回填均通过。 |
| 删除成果及部分失败 | 通过（自动化） | 暂存／未暂存、未追踪／忽略、初始化内容／权限位／链接改变、新提交（包括已推送 fixture）、查询失败、目录移除后分支删除失败均覆盖。没有 force、全局 prune 或自动合并。 |
| TTL 三层过滤、规划、关闭竞争 | 通过（自动化）；真实定时竞争未执行 | 真实服务等待 30 天及精确抢锁不可稳定控制，使用本次可控时钟、真实锁、活动刷新和失败注入测试验证；规划暂停及关闭收尾覆盖，不假称真实 TTL 已触发。 |
| 官方 Anthropic、多服务与远端 MCP | 未执行 | 本次只有已授权 OpenAI 兼容模型；既有协议／SDK 测试在本次完整回归重跑，不能证明真实远端副作用停止。 |
| README、checklist、构建及规格 | 通过 | 默认值、角色声明、共享边界、成果路径、删除条件已同步；本次新证据完整，不复用旧章节的验收结论。 |

本次隔离表示 Git 工作副本及默认工具根。shell、依赖软链、共享 Git、LLM 和 MCP 的边界在 README 明示；未增加操作系统沙箱、自动合并、跨目录同步或团队编排。验收程序正常退出，保留临时项目中的受保护成果和可恢复证据供复核。

## 主干整合（2026-10-06）

本地 `main` 从 `62b99e8` 快进至实施提交 `4eb7b02`，没有冲突。合并前主干的未追踪旧方案（任务清单尚未完成）完整备份到 `/private/tmp/mewcode-main-planning-backup-tm6wwc2h/add-worktree-isolation`，开发过程记录也已备份。远端 SSH 连接关闭，`git pull --ff-only` 未完成，因此本次仅确认本地整合，远端最新状态未核实。

合并前首次完整回归 1422 通过、1 个已记录 MCP 竞态失败（`legacy-False`，GET 4→5）；单独复验 1 通过（1.15 秒），完整重跑 **1423 passed，168.12 秒**。合并后首次回归 1422 通过、1 个 Git Hook 取消测试的准备等待超时；失败发生于 3 秒启动窗口，单独复验 1 通过（1.10 秒）。将准备窗口调整为 10 秒，并在创建提前结束时立即失败，取消后的 3 秒收尾断言保持；生命周期专项 **22 passed，4.35 秒**。没有修改运行代码或 MCP 实现。

主干最终完整回归 **1423 passed，151.73 秒**，严格规格校验及差异检查通过。记录见 [本次合并验证日志](worktree-isolation/merge-verification.txt)。本次开发 Worktree 已非强制移除，已合并功能分支已安全删除；其他工作树和真实验收项目成果保留。

## 主规格同步与归档

2026-10-06 按用户选择同步 10 份主规格：新增 14 条需求、修改 14 条需求，无删除或重命名。逐项核对全部增量的描述和场景，保留未涉及的现有需求及场景，重复同步结果一致；新建的 Worktree 主规格沿用增量中的 Purpose，没有 TBD 占位。主规格严格校验 **25 passed，0 failed**。

变更已归档至 [2026-10-06-add-worktree-isolation](../../openspec/changes/archive/2026-10-06-add-worktree-isolation/)，schema 为 `spec-driven`，规划材料全部完成，任务 **44/44** 完成。移动前后 14 个文件的 SHA-256 完全一致，包含 `.openspec.yaml`。本次只变更规格、归档路径和文档；运行时回归与真实模型验收仍对应上述实施和合并记录。归档校验见 [本次归档日志](worktree-isolation/archive-verification.txt)。
