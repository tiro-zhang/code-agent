# Agent Teams 基础合同与接入点核查

本记录对应 `add-agent-teams` 的任务 1.1。2026-10-06 独立读取现有主规格、团队增量规格、当前源码及实际测试，并核对 CodeGraph 调用链。任务复选框没有作为验收证据。

## 主规格状态

现有 [worktree-isolation 主规格](../../../openspec/specs/worktree-isolation/spec.md) 已存在，包含冻结基线、实际工作根、恢复只读、初始化清单、权限、Hook、成果保护和 TTL 清理合同。`openspec/changes/archive/2026-10-06-add-worktree-isolation/` 在本次核查前已经存在；本次只读取，不执行归档或改写其任务及规格。

普通一次性隔离任务不自动整合／同步的旧合同继续适用于普通 Agent；团队增量的同步／整合必须经团队身份、明确验收和当前授权入口，不能将团队能力隐式授给普通子 Agent。

## CodeGraph 与源码核对

实际命令：

```text
codegraph status
codegraph explore 'TeamService MemberRuntime IntegrationService Journal WorktreeManager 接入点 目标预算 缓存 权限恢复'
codegraph explore 'WorktreeManager.create SessionStore external_storage PermissionManager.authorize_noninteractive TeamService.authorize_git 接入调用链'
codegraph callers WorktreeManager.create
codegraph callers authorize_noninteractive
codegraph callees spawn_member
```

`status` 显示索引同步：307 文件、5,312 节点、21,331 边。`spawn_member` 的调用链实际包含 `require_participant`、`select_backend`、`freeze_repository`、`authorize_git`、`managed_target`、`WorktreeManager.create` 和 `register_member`；`authorize_noninteractive` 的调用者包含团队 Git 授权及 Hook 动作。

CodeGraph 的未限定符号可能匹配同名函数，覆盖测试提示也没有列出所有动态调用的测试。因此以当前源码和下列测试体补充核对，不将图中“没有 covering tests”解释为没有测试，也不将所有同名边视为精确调用。

## 当前接入点

| 基础合同与源码 | 团队接入及保持的边界 | 本次实际测试证据 |
| --- | --- | --- |
| [worktrees/paths.py](../../../src/mewcode/worktrees/paths.py)、[worktrees/manager.py](../../../src/mewcode/worktrees/manager.py) | `spawn_member` 冻结目标提交、预检实际 Git 授权，按仓库身份创建独立分支及目录；`MemberRuntime.setup` 用登记 metadata 只读 recover，再持有实际目录 Lease；不改变进程 cwd、不复制未提交源码。 | `test_worktree_runtime.py::test_isolated_child_reads_frozen_and_writes_own_directory`、`test_child_project_subdirectory_mapping_and_shell_cwd`、`test_queued_snapshot_keeps_head_before_parent_new_commit`；团队实际 `test_team_runtime_boundaries.py::test_model_write_is_denied_before_actual_task_start_then_succeeds`。 |
| [worktrees/manager.py](../../../src/mewcode/worktrees/manager.py)、[worktrees/cleanup.py](../../../src/mewcode/worktrees/cleanup.py) | 成员 pin 写入受管记录；`_protected_reason` 先保护团队及证据，再核对新增提交、修改和初始化外成果；暂停不调用一次性 exit/delete，TTL 不解除成果保护。 | `test_team_retention.py` 全部 6 实例；基础 `test_worktree_cleanup.py::test_scan_filters_live_refs_changes_age_and_foreign_directories`、`test_scan_rechecks_activity_inside_delete_lock`。 |
| [sessions/store.py](../../../src/mewcode/sessions/store.py)、[sessions/projection.py](../../../src/mewcode/sessions/projection.py)、[context/spill.py](../../../src/mewcode/context/spill.py) | `Journal` 保持真实 root，将 `storage_root` 单独绑定用户域成员目录，持有排他锁；恢复精确 ID、协议与供应商签名兼容性；复用意图／结果和合法投影，不重跑缺失结果；`team_control` 经严格结构解析。缓存私有位置不扩大文件工具根。 | `test_team_sessions.py::test_external_journal_and_cache_roundtrip`、`test_external_resume_cannot_change_root_or_protocol`；`test_session_persistence.py::test_first_incomplete_batch_truncates_later_history`；Lead 的 `test_team_lead_resume.py::test_lead_signed_provider_content_refuses_changed_model`、`test_lead_can_read_registered_private_cache_without_expanding_tool_root`。 |
| [permissions/runtime.py](../../../src/mewcode/permissions/runtime.py)、[tools/executor.py](../../../src/mewcode/tools/executor.py) | `fork(root=actual_root)` 绑定实际目录、当前父上限及非交互许可，不搬迁旧根批准；明确 deny 优先；成员角色、模式、计划／claim 门禁在权限前和实际执行前检查；内部 Git 由 `TeamService.authorize_git` 再按实际 cwd 检查。 | `test_worktree_permissions.py::test_all_approval_kinds_keep_original_root_identity`、`test_parent_live_deny_and_ask_cannot_be_weakened`；`test_team_git_permissions.py` 全部 15 实例；`test_team_review_regressions.py::test_resumed_inprocess_tools_respect_current_parent_ceiling`。 |
| [tools/registry.py](../../../src/mewcode/tools/registry.py)、[teams/capabilities.py](../../../src/mewcode/teams/capabilities.py) | 注册范围与副作用分类独立；普通入口只有 `team`，真实 Lead 才有四协作工具；成员禁止嵌套 Agent／独立 Skill；coordinator 同时要求真实 Lead 与两开关，不从 dotenv 伪造环境提权。 | `test_team_service.py::test_ordinary_entry_hidden_then_create_lead`、`test_team_capabilities.py::test_registry_scope_is_explicit_even_for_system_tools`、`test_dotenv_cannot_enable_process_coordinator_or_expand_restored_identity`；普通工具 registry／plan_mode／MCP／Skill 回归。 |
| [teams/service.py](../../../src/mewcode/teams/service.py)、[session.py](../../../src/mewcode/session.py) | Lead 原目标 parent／预算持久绑定；输入和自动接续共用已有主运行锁；恢复先绑定 journal，再 Git 对账，再监控；恢复保持 idle；预算先保存再实际请求，不由消息、失败或 pause 刷新。 | `test_team_lead_resume.py::test_goal_user_inputs_keep_same_parent_and_budget`、`test_budget_reservation_saved_before_provider_and_survives_pause`、`test_lead_snapshot_failure_blocks_request_and_preserves_unread_mail`、`test_resume_reconciles_integration_before_starting_monitor`。 |
| [hooks/runtime.py](../../../src/mewcode/hooks/runtime.py)、[teams/runtime.py](../../../src/mewcode/teams/runtime.py) | 成员拥有自己的 Hook 运行器／Provider；Hook 模式绑定实际审批、工具上限和任务 start，成员收尾先停止所属动作；MCP 借用父连接，不作为成员私有连接关闭。 | 基础 `test_worktree_runtime.py::test_background_hook_holds_lease_until_real_command_end`、`test_cancel_real_child_command_drains_then_next_task_runs`；具体双成员 MCP／Hook 证据见 [permissions.md](permissions.md)，未执行边界继续在 [spec-audit.md](spec-audit.md) 标记。 |
| [teams/integration.py](../../../src/mewcode/teams/integration.py)、[teams/tasks.py](../../../src/mewcode/teams/tasks.py) | 目标私有整合分支、不可变成果凭据、串行 flock、当前操作撤销及最终 ref／索引事务；依赖先整合再实际同步；恢复只对账，不重新 merge。 | 31 个真实 Git 整合实例、15 个权限实例、`test_team_conflict_runtime.py::test_conflict_member_uses_leased_directory_then_lead_publishes`；故障矩阵见 [integration.md](integration.md)。 |

## 本次验证范围

独立关联命令及原始 **502 passed、5 skipped，133.81s** 见 [spec-audit.md](spec-audit.md)。该次实际包含 `test_team_*.py`、`test_worktree_*.py` 和会话、注册表、计划、MCP、Skill、子 Agent 范围模块。5 个真实 tmux 实例跳过，不记通过。

本文确认基础合同和生产接入点；模型为替身的运行器测试、真实 Git／Hook／MCP 测试各自只证明所述范围。真实已授权模型的终端审批、竞争与双后端全流程由根代理另行记录，不由主规格状态或任务勾选推断。

## 接入复核后的补充结果

后续 CodeGraph status 仍显示同步（313 文件、5,437 节点、22,039 边），没有手工全量重建。后续 8 模块关联 **98 passed，27.09s** 及单独 detach 声明 **1 passed，1.54s** 见 [spec-audit.md](spec-audit.md)。

`test_team_scenario_audit.py::test_two_real_lead_startups_have_one_owner_and_no_second_scheduler` 用两个独立 Python 实例实际构造 ChatSession 并同时恢复，只有一个拿到 Lead 所有权；不会以旧 PID 或时间夺锁。其他该模块专项实际核对登记快照失败、消费 checkpoint 失败、必要缓存缺失和当前工具 grant 重建。

复核实际发现 coordinator 模型声明的旧系统工具旁路泄露 `agent`。生产 `TeamService.bind_lead` 已保存旧 `agent.system_passthrough` 并在团队身份禁用，异常／detach 恢复；回归实际核对 coordinator 规划请求声明和拒绝 shell／派活／Hook，再暂停脱离团队发普通请求，确认普通 `load_skill`／`agent`／`team` 系统入口重新出现。权限配置及团队完整参数授权的补充由权限审查代理另记 [permissions.md](permissions.md)。

## 通知预算及退出反馈的实际边界补充

`ChatSession.next_parent` 团队通知入口在父预算为零时保留 pending 通知，但不返回自动接续父 ID；实际 Member `team_message` 工具发送后，Service.status 和 TerminalProjection 仍显示保存的消息／空闲待验收。`TeamService.control(pause)` 返回真实暂停状态及待核查成员的稳定 ID／generation／backend／pane，`SessionCommandContext.team_text` 不再把停止未确认描述为已正常暂停；启动 nonce 与租约令牌不进入这份返回。

实际证据为 `test_team_scenario_boundaries.py::test_exhausted_lead_budget_preserves_real_member_notice_in_status_and_terminal` 和 `test_unconfirmed_independent_process_pause_ui_retains_identity_and_blocks_resume`，均先复现错误后保持原强断言修复。并发实际工具存档、普通子 Agent 受保护邮箱拒绝、Skill 边界及控制输入优先同模块补证；关联现有父任务／子 Agent 合同回归 **95 passed，47.76s**，完整命令见 [spec-audit.md](spec-audit.md)。普通子 Agent 私有邮箱保护已由 `PermissionManager.protected_roots` 和 `fork` 继承实现，这轮没有为已实现边界追加重复逻辑。
