# 团队 Git 整合子系统验证

本记录对应 `openspec/changes/archive/2026-10-06-add-agent-teams/tasks.md` 的 9.1—9.6。验证通过 Python 调用真实本地 Git 仓库和 Worktree，覆盖整合服务的确定性合同；不代表已完成真实模型或 tmux 团队端到端验收。

## 本次命令与结果

```text
UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run pytest tests/test_team_integration.py -q
31 passed in 32.55s
```

测试使用 pytest 临时目录，创建独立仓库、目标分支、成员分支及整合／最终候选目录。不读取真实模型密钥，不修改用户仓库或远端。

## 规格对应证据

| 任务 | 本次验证内容 |
| --- | --- |
| 9.1 | 目标独立分支／目录和操作日志；完整 commit 输入；错误仓库、未知分支、未接纳成果拒绝；验收后成员继续提交，以及登记冻结凭据后成员分支 reset，均不改变整合输入；两个服务实例竞争同一 flock 时第二个拒绝。 |
| 9.2 | A 的接口提交整合后，B 安全同步并看到接口文件；用户目标分支保持原基线。未提交内容、忽略文件路径碰撞和同步冲突受到保护，成员已有提交保留。同步成功但日志发布中断时，只核查实际 Git 状态，不再次合并。 |
| 9.3 | 指定成员接收整合目录与 token；真实 workspace flock 拒绝第二写入者、未停止时的结束／收回操作、错误成员和错误 token。成员在同一整合目录解决冲突并提交，验证后发布。服务没有源码内容编辑接口。 |
| 9.4 | 第一次整合成功、第二次冲突撤销后，首份成果和两个成员分支保留。验证失败、验证程序异常和取消撤销当前操作。冲突之后未知编辑、忽略文件替换或 Git abort 失败进入 needs_review，不宣称已撤销，不继续整合。 |
| 9.5 | 必需任务接纳与代码整合门槛；非代码任务的明确证据；独立最终候选验证；脏目录和外部基线变化拒绝；验证期间用户提交保留。已检出目标 ref 在目录／索引更新期间锁定，竞争 Git 更新失败，发布后 HEAD、索引、工作区一致。未检出目标采用旧值比较更新。已发布目标拒绝进一步整合。 |
| 9.6 | Git 已完成但结果日志尚未保存、日志已发布但快照保存中断、最终发布后用户继续提交、操作记录损坏及同步发布中断的恢复。恢复不重跑 Git、不撤销用户后续操作；有完整发布证据时仅修复目标快照。 |

另外验证了规划模式的开始门禁，以及验证期间切换 `/plan` 时不能发布成果。所有应用受管 Git 命令禁用仓库 Git hooks；成员冲突处理自行执行的 Git、shell、hooks 和远端副作用另外记录，不承诺随受管 Git 撤销消失。

## 接入和端到端范围

任务验收、必需任务完整列表及调用者身份由 TeamService／TaskStore 提供可信来源。成员运行器必须在冲突处理期间持有服务发出的实际 Lease，确认工具和进程停止后释放；依赖只在整合及成员同步结果持久发布后解除。

本记录没有执行真实模型团队 tmux 端到端流程，没有验证 Git 回滚可撤销外部 Hook、shell 或远端副作用，也没有把普通成员目录重置为团队提交。真实模型、权限交互和 tmux 双后端的全流程结果应另行记录。

## 后续运行器接入验证（同轮）

`tests/test_team_conflict_runtime.py::test_conflict_member_uses_leased_directory_then_lead_publishes` 已随 7 模块复验运行通过（合计 51 passed，24.68s；完整命令见 [spec-audit.md](spec-audit.md)）。该测试创建真实成员 Worktree、真实 Git 冲突与独立整合目录，通过真实 ToolExecutor 读取、编辑、执行 Git 提交并验证发布；同时验证租约双写拒绝、成员实际工具根切换及退出后回到原根。模型使用 ScriptedProvider，不能记为真实模型团队端到端通过。

## 受管 Git 权限接入（同轮）

整合服务新增实际 cwd／参数的可选授权回调；生产 TeamService 接入当前真实权限。首次 Worktree、merge、sync、候选及目标发布均在操作发布前预检，并在实际变更前再检查。策略拒绝不冒充成功回滚；已启动当前操作的确定性取消／失败清理不等待新授权，显式 rollback 仍重新授权。权限与关联 Git 回归合计 **46 passed，52.23s**，完整命令及 14 个权限专项证据见 [permissions.md](permissions.md)。

## 独立复核：9.6 故障注入矩阵（2026-10-06）

本次关联回归包含本模块 31 个真实 Git 实例、权限 15 个实例和冲突运行器实例；总命令及 **502 passed、5 skipped，133.81s** 见 [spec-audit.md](spec-audit.md)。以下逐条读取了测试体和 `IntegrationService.recover`，不是仅按测试名称推断：

| 实際中断位置 | 本次可核查函数 | 恢复断言 |
| --- | --- | --- |
| merge 已修改真实 HEAD，保存 git_applied 操作记录抛错 | `test_recovery_reconciles_git_success_without_replaying_and_preserves_user_update` | 新服务读取真实 HEAD／状态；needs_review；整合 HEAD 原值保留、用户目标仍为基线；不再次合并。此测试本身没有创建后续用户提交，后续提交见下行。 |
| published 操作证据已经落盘，目标 ready 快照保存抛错 | `test_recover_published_operation_after_goal_state_save_failure_without_remerge` | 新服务只修复 head／ready／active_operation 快照；真实 HEAD 不变。 |
| finalize 已推进目标 ref，保存 published 操作记录抛错；用户随后真实新增提交 | `test_final_publish_crash_recovers_without_touching_later_user_commit` | needs_review；记录 actual_target_head；用户新 HEAD 和 user.txt 内容保持，既不重放发布也不 reset 用户提交。 |
| 下游真实 merge 完成，保存 synced 记录抛错 | `test_sync_git_success_before_record_publish_requires_reconciliation_not_remerge` | 新服务只读取成员实际 HEAD／状态；同步和目标均 needs_review；成员 HEAD 不变，不再 merge。 |
| 操作 JSON 非法或快照 schema 无效 | `test_recover_corrupt_operation_blocks_without_modifying_git`、`test_corrupt_goal_schema_refuses_git_mutations` | 对损坏证据不当空状态；阻塞且不修改 Git。 |

其他确定性失败证据包括 `test_failed_abort_is_reported_and_never_claims_rolled_back` 的实际 abort 故障、`test_validator_exception_and_cancellation_roll_back_managed_current_operation` 的验证异常／取消，以及 `test_checked_out_target_ref_locked_during_index_update` 的并发 ref 竞争。它们补充回滚／竞态边界，不冒充进程崩溃测试。

9.6 的“已执行但状态未发布”核心 Git 边界已有真实 Git 后的异常故障注入；**并非完整断电或真实进程杀死矩阵**。未精确注入首次 Worktree 创建后且 ready 尚未写入、read-tree 已更新索引但 ref 事务尚未 commit、最终 published 证据落盘后目标快照失败等每一持久窗口，也未模拟 fsync 失败、SIGKILL 或磁盘损坏。发现初始化中断、未知索引／目标移动时实现保守阻塞，不能据上述通过数声称每个窗口已验证。

服务层另有 `test_team_reconciliation.py::test_actual_git_publish_before_task_save_recovers_service_without_replay`（integrate／finalize 两实例）：真实 Git 已发布后注入 TaskBoard 状态保存异常，恢复时禁止任何 Git 授权变更，凭已发布证据修任务状态；最终发布后另加实际用户提交，HEAD 和用户文件保持。`test_new_goal_task_start_safely_syncs_frozen_baseline_without_dependencies` 实际核对取消旧 Goal 后复用成员，新 Goal 无依赖任务 start 安全同步新冻结基线，零模型请求且整合操作记录为空。两项随 `tests/test_team_cancel_command.py tests/test_team_reconciliation.py -q` 运行 **8 passed，13.87s**；这组服务证据补充状态对账及基线同步，不将上述未注入的核心持久窗口改记为已测。

应用受管 Git 的 hooks 禁用有实际反例测试 `test_git_merge_does_not_run_repository_hooks`、`test_managed_worktree_creation_and_final_ref_transaction_disable_git_hooks`。这仅证明受管 Git 不触发仓库 Git hooks；成员自行执行的 shell／Git hooks、应用 Hook 和远端服务具有独立生命周期，恢复对账不宣称这些外部副作用随 Git 回滚消失。远端撤销及成员外部 Hook 的崩溃后副作用未由本矩阵验证。
