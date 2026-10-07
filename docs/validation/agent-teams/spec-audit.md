# Agent Teams 规格场景验收映射

本记录逐项覆盖 11 份增量规格的 81 个 Scenario。函数名称是实际仓库测试，不将测试计划当已验证结果。**自动化通过仅指该行注明的确定性范围；81 行不等于完整端到端通过。**“部分”列出已测边界及剩余缺口。模型替身、故障注入和真实本地 Git／进程测试不能代替真实已授权模型验收。

## 独立复核与本次关联回归（2026-10-06）

先核查当前源码，再执行以下命令；未将任务复选框或模型正文作为依据：

```text
UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run pytest tests/test_team_*.py tests/test_worktree_*.py tests/test_session_persistence.py tests/test_tool_registry.py tests/test_plan_mode.py tests/test_mcp_tools.py tests/test_skills_tools.py tests/test_subagent_tool_guard.py -q
502 passed, 5 skipped in 133.81s
```

5 个跳过实例是 1 个真实 tmux 后端测试及 `test_real_tmux_full_worker_activation_tool_history_and_owned_mcp_close` 的 4 种停止方式；当前执行环境的探测不能创建私有 tmux socket。它们不计通过。上述运行已包含原先 7 项失败的 Lead 专项，当前全部通过；下面历史快照仍原样保留，不用新结果改写旧执行。新增成员边界测试实际经过运行器、工具执行器和存档，但 Provider 使用替身。

补充两项精确证据：`UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run pytest tests/test_team_permission_lifecycle.py -q`，**3 passed，4.37s**。实际匹配计划批准和开工后分别执行当前 deny／ask 检查；另以双成员真实后台 Hook 与共享 stdio MCP 核查停止 A 不破坏 B。没有为这次测试修改实现。502 项是之前那次关联命令的原始结果，不虚增为未执行的 505 项。

精确场景补充及修复后回归：

```text
UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run pytest tests/test_team_scenario_audit.py tests/test_team_permission_lifecycle.py tests/test_team_plan_wakeup.py tests/test_team_hook_guards.py tests/test_team_tool_permissions.py tests/test_plan_mode.py tests/test_team_capabilities.py tests/test_worktree_permissions.py -q
98 passed in 27.09s
```

跨恢复、真实双 Lead 进程、登记发布失败、成员消费历史故障、成员缓存缺失、同时唤醒、独立代码验收失败、正文伪批准和旧工具 grant 均有实际链路断言。coordinator 专项最初实际复现模型声明仍泄露 `agent`（1 failed，1.41s）；根代理修复团队身份的系统声明旁路后，上述关联回归通过。随后仅补“detach 后普通模型声明恢复”的断言，单独执行同一 coordinator 测试 **1 passed，1.54s**；该追加断言不是前一次 98 项已经执行的内容。没有改弱失败断言来关闭映射。

CodeGraph 索引当前同步；已用调用链与源码核对基础 Worktree、会话、权限接入，见 [integration-points.md](integration-points.md)。Git 故障注入矩阵和 9.6 限定见 [integration.md](integration.md)。本代理没有独立执行真实模型 E2E，根代理本次真实验收需另附具体记录；本表自动化状态不代替该记录。

剩余精确边界补充及修复后回归：

```text
UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run pytest tests/test_team_scenario_boundaries.py tests/test_team_scenario_audit.py tests/test_team_commands.py tests/test_team_lead_resume.py tests/test_team_crash_recovery.py tests/test_team_retention.py tests/test_parent_tasks.py tests/test_subagent_runtime.py -q
95 passed in 47.76s
```

`test_team_scenario_boundaries.py` 的 8 项使用实际成员工具执行、Journal、权限、Service 和 TerminalProjection；模型为替身。两个命令进程同时存活证明工具执行重叠；停止未确认测试使用真实独立 PID，但 tmux 命令地址是故障注入 fixture，不能记作真实 tmux 全流程。首次实际复现零预算通知仍可返回自动接续父 ID，以及停止未确认仍无条件显示“已暂停”且遗漏成员标识；保留原断言，分别修最小唤醒门禁和真实暂停结果展示后上述回归通过。普通子 Agent 的邮箱写入已有当前权限 `protected_roots` 拒绝，只补精确证据，没有重复添加实现。

取消入口与新 Goal 基线同步补充实际执行：`UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run pytest tests/test_team_cancel_command.py tests/test_team_reconciliation.py -q`，**8 passed，13.87s**。这是独立限定回归，不是全项目回归，也未虚加进前一次 95 项。

最后核对 CodeGraph status 同步：317 文件、5,518 节点、22,503 边。11 份规格全部 81 个 Scenario 名称和顺序逐一匹配，所有函数名以实际测试 AST 核查，全部文档链接目标存在；`git diff --check` 通过。

存储恢复最终终态补充（存储代理报告且源码／断言已逐项核对）：`UV_CACHE_DIR=/tmp/mewcode-team-uv-cache uv run pytest tests/test_team_crash_recovery.py tests/test_team_store.py -q`，**34 passed、0 skipped，20.54s**，其中 crash_recovery 共 17 个实例。Member 明确 needs_review 的新增断言、私有恢复标记篡改／伪造拒绝、真实 Member／Journal 双租约、跨新 Goal 保留审核和重复审计均在这次执行，不归入之前的 95 项。

表内现在为 81 项限定范围自动化证据。这个数字表示每行所述确定性证据已存在并执行，不表示所有组合或真实模型端到端已验收；Git 9.6 未注入窗口和根代理最新全量验收仍须分别记录。

## 实施期间执行快照（2026-10-06）


命令：`UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run pytest`，参数为 `tests/test_team_{backends,capabilities,commands,control,integration,mailbox,runtime,security,service,sessions,store,tasks,worker}.py`（执行时逐个展开），以及 `tests/test_tool_registry.py tests/test_tool_session.py tests/test_mcp_tools.py tests/test_skills_tools.py tests/test_session_persistence.py tests/test_worktree_cleanup.py tests/test_subagent_tool_guard.py -q`。

结果：**253 passed、4 skipped、1 failed，70.32s**。唯一失败为下表空白名单导出旧断言；当前按增量规格导出 `team`，原测试只期待 `load_skill`／`agent`。4 个真实 tmux 测试在本次 sandbox 中未执行；不能记为通过。根代理随后更新该旧断言，新复验见下段。此前 Git 子系统单独执行 31 passed 的原始证据保留在 [integration.md](integration.md)。

新增／修复后复验：`UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run pytest tests/test_team_retention.py tests/test_team_commands.py tests/test_team_conflict_runtime.py tests/test_skills_tools.py tests/test_tool_registry.py tests/test_plan_mode.py tests/test_prompt_context.py -q`，**51 passed，24.68s**。其中真实 Git 冲突运行器／长期保留使用临时真实仓库，终端审批使用 prompt_toolkit 管道输入，模型仍为替身。三份 `.env.*.example` 用真实配置加载器核查团队默认值合法，未输出密钥。

实施中的 Lead 新专项另一次快照为 `tests/test_team_retention.py tests/test_team_commands.py tests/test_team_lead_resume.py tests/test_team_conflict_runtime.py tests/test_skills_tools.py -q`：**29 passed、7 failed，20.32s**。7 项失败均在新增 Lead 预算／恢复／缓存专项，当时尚处于根代理实施中；根代理最终回归须追加结果，不能在此视为通过。

最后展示修正复验：`UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run pytest tests/test_team_commands.py tests/test_team_retention.py -q`，**26 passed，8.53s**。新增三种前置状态参数化测试 `test_team_status_does_not_report_satisfied_dependency_as_blocked` 先复现 integrated 代码前置误报阻塞，再按任务板合同修正展示；展示测试不替代实际依赖同步测试。`git diff --check` 通过，81 场景行及所有引用函数／文档链接目标已核查。

该阶段未执行真实模型团队双后端全流程。最新自动化结果及独立复核范围见本文开头；根代理另行记录本次真实模型验收。尚缺专项测试或端到端证据的条目继续保留。

## agent-teams

规格：[spec.md](../../../openspec/changes/archive/2026-10-06-add-agent-teams/specs/agent-teams/spec.md)

| Scenario | 真实测试函数 | 本次范围／状态 |
| --- | --- | --- |
| 同仓库接续目标 | [`test_team_scenario_audit.py::test_restored_team_new_goal_preserves_member_history_tasks_and_result`](../../../tests/test_team_scenario_audit.py)<br>[`test_team_reconciliation.py::test_new_goal_task_start_safely_syncs_frozen_baseline_without_dependencies`](../../../tests/test_team_reconciliation.py) | 自动化通过；完成旧目标并实际 finalize，关闭／恢复团队后明确新目标；成员 ID／目录／branch／session_ref、旧完整任务及成果保留，新目标／任务／run_id 区分，实际模型替身接续原历史且旧请求不重放；原成员新 Goal 无依赖任务 start 实际安全同步新冻结基线，旧目录／ID 保留，无模型或整合重放 |
| 同名或错误仓库 | [`test_team_store.py::test_create_pause_resume_and_ownership`](../../../tests/test_team_store.py)<br>[`test_team_store.py::test_resume_allows_same_repository_worktree_and_rejects_other_repo`](../../../tests/test_team_store.py) | 自动化通过；同名／异仓库拒绝 |
| 保存失败 | [`test_team_scenario_audit.py::test_registration_publish_failure_never_starts_unregistered_member`](../../../tests/test_team_scenario_audit.py)<br>[`test_team_store.py::test_atomic_replace_failure_preserves_prior_snapshot`](../../../tests/test_team_store.py) | 自动化通过；实际创建成员 Worktree 后注入登记 team.json 发布失败，原花名册／快照精确不变、零 runner／handle／模型；已发生 Worktree 和受管证据如实保留，未声称无副作用 |
| 恶意路径 | [`test_team_store.py::test_special_and_symlink_records_refused`](../../../tests/test_team_store.py)<br>[`test_team_store.py::test_member_directory_symlink_refuses_resume`](../../../tests/test_team_store.py) | 自动化通过；链接／特殊文件拒绝 |
| 并发恢复 | [`test_team_scenario_audit.py::test_two_real_lead_startups_have_one_owner_and_no_second_scheduler`](../../../tests/test_team_scenario_audit.py)<br>[`test_team_store.py::test_real_process_lock_ownership_and_exit_release`](../../../tests/test_team_store.py) | 自动化通过；两个真实独立 Python 进程同时走 ChatSession 团队恢复，只有一个 Owner，另一明确失败；两者无成员运行器，获权者零自动父接续，实际退出后团队暂停 |
| 正常退出后收到消息 | [`test_team_mailbox.py::test_commit_cancel_does_not_ack_and_paused_send_does_not_wake`](../../../tests/test_team_mailbox.py) | 自动化通过；暂停发送只存档 |
| 显式恢复 | [`test_team_runtime.py::test_explicit_resume_reuses_member_and_disk_context`](../../../tests/test_team_runtime.py)<br>[`test_team_commands.py::test_app_explicit_team_resume_idle_and_passes_absolute_config_before_start`](../../../tests/test_team_commands.py)<br>[`test_team_lead_resume.py::test_resume_restores_history_consumed_ids_budget_without_request`](../../../tests/test_team_lead_resume.py)<br>[`test_team_lead_resume.py::test_resume_reconciles_integration_before_starting_monitor`](../../../tests/test_team_lead_resume.py) | 自动化通过；真实 Service 恢复原历史／消费 ID／预算且零请求，整合先对账再启动监控；模型替身，不重放 |
| 异常退出 | [`test_team_crash_recovery.py::test_explicit_lead_resume_isolates_incomplete_member_task_without_replay`](../../../tests/test_team_crash_recovery.py)<br>[`test_team_crash_recovery.py::test_live_or_unknown_generation_cannot_be_taken_over_after_lead_disconnect`](../../../tests/test_team_crash_recovery.py)<br>[`test_team_crash_recovery.py::test_modified_private_recovery_record_does_not_authorize_resume`](../../../tests/test_team_crash_recovery.py)<br>[`test_team_crash_recovery.py::test_forged_review_marker_without_actual_journal_intent_cannot_classify_unknown_exit`](../../../tests/test_team_crash_recovery.py)<br>[`test_team_reviewed_reassignment.py::test_failed_reassignment_preserves_actual_review_and_claim`](../../../tests/test_team_reviewed_reassignment.py)<br>[`test_team_reviewed_reassignment.py::test_member_bind_failure_keeps_review_after_actual_new_claim_publication`](../../../tests/test_team_reviewed_reassignment.py) | 自动化通过；真实成员租约／Journal 进程意图 fsync 后 SIGKILL，有／无实际文件写入均保留证据、截断未配对调用、报告未知副作用、不重放；受影响 Task blocked、已确认停止但上下文未确认的 Member 明确 needs_review，正常暂停保留审核。私有 recovery 标记须匹配真实意图和成员／Journal 双锁，不凭标记、旧 PID 或时间接管；仍存活所有者／未知运行代次拒恢复并保持原证据，不伪造正常 idle；重新指派的终态／目标／保存／并发版本拒绝均不清审核，成员绑定保存失败保留已发布新 Task claim 和 Member needs_review，不强行跨文件回滚 |
| 取消后接新目标 | [`test_team_lead_resume.py::test_cancelled_goal_allows_new_goal_keeps_member_tasks_and_old_mail_audit`](../../../tests/test_team_lead_resume.py)<br>[`test_team_lead_resume.py::test_cancelled_goal_restores_same_identity_and_remaining_budget`](../../../tests/test_team_lead_resume.py)<br>[`test_team_cancel_command.py::test_cancel_parent_command_stops_bound_team_and_preserves_task_and_workspace`](../../../tests/test_team_cancel_command.py)<br>[`test_team_cancel_command.py::test_cancel_unrelated_parent_does_not_stop_team_goal`](../../../tests/test_team_cancel_command.py) | 自动化通过；实际运行器消费旧领取邮件仅存审计且零请求；新目标复用成员、原目录和历史，旧任务及旧预算保留，新 claim 不覆盖旧领取；真实本地 cancel-parent 入口停止绑定 Goal 的成员并保留 claim／目录实际产物，取消其他普通父任务不影响团队目标 |

## interactive-chat

规格：[spec.md](../../../openspec/changes/archive/2026-10-06-add-agent-teams/specs/interactive-chat/spec.md)

| Scenario | 真实测试函数 | 本次范围／状态 |
| --- | --- | --- |
| 查看暂停团队 | [`test_team_commands.py::test_team_commands_create_inspect_pause_resume_use_real_service_without_model_or_member`](../../../tests/test_team_commands.py) | 自动化通过；真实服务查看保持无模型／无成员 |
| 后端探测回退 | [`test_team_scenario_boundaries.py::test_service_auto_fallback_notice_reaches_terminal_before_member_start`](../../../tests/test_team_scenario_boundaries.py) | 自动化通过；Service 实际 auto 派生前经不可用 tmux 探测 fixture 回退，回调到 TerminalProjection／Renderer，明确显示原因、同进程独立协程及不提供文件系统沙箱；无模型请求，任务 phase／统计不变；真实 tmux 探测环境另待 E2E |
| 成员空闲但任务未验收 | [`test_team_commands.py::test_team_status_shows_repository_plan_task_mailbox_and_integration_without_waking`](../../../tests/test_team_commands.py)<br>[`test_team_tasks.py::test_result_acceptance_and_late_claim_audit`](../../../tests/test_team_tasks.py) | 自动化通过；真实 Service 状态展示仓库／Lead／cwd／计划版本与匹配决定记录／任务阻塞／邮箱保存和未确认唤醒／整合状态；真实模型全流程未执行 |
| 通知与用户输入竞争 | [`test_team_scenario_boundaries.py::test_submitted_team_control_wins_real_mail_wake_and_keeps_notice`](../../../tests/test_team_scenario_boundaries.py)<br>[`test_team_commands.py::test_team_notice_is_deferred_beside_real_approval_and_never_answers_it`](../../../tests/test_team_commands.py) | 自动化通过；真实 prompt_toolkit 已提交 /team status Future 与真实登记成员邮箱唤醒竞争，实际 _idle_input 优先返回控制命令，通知仍 pending／未消费且状态可见，零新增 Lead 请求；真实批准输入保留待人工回车，不自动提交 |
| Lead 预算耗尽 | [`test_team_scenario_boundaries.py::test_exhausted_lead_budget_preserves_real_member_notice_in_status_and_terminal`](../../../tests/test_team_scenario_boundaries.py) | 自动化通过；唯一 Lead 请求后预算恰为零，实际 Member 工具发送消息已保存、status 展示正文及待决邮箱，Terminal 展示成员 idle；监控轮询后 next_parent 仍空，同一 goal_parent／used=1，Lead 请求不增加；原先零预算仍返回可唤醒父 ID 已红测后最小修复 |
| 成员停止超时 | [`test_team_scenario_boundaries.py::test_unconfirmed_independent_process_pause_ui_retains_identity_and_blocks_resume`](../../../tests/test_team_scenario_boundaries.py)<br>[`test_team_backends.py::test_unconfirmed_stop_retains_pane_and_returns_needs_review`](../../../tests/test_team_backends.py) | 自动化通过；真实独立 sleep PID 保持活动，TmuxBackend 命令地址使用故障注入 fixture，停止无法确认后真实 /team pause 存 needs_review、保留 handle／存档，UI 报告 member／generation／backend／pane，不泄漏 nonce、不声称已正常暂停；实际 resume 拒绝且无模型／重复运行器。真实 tmux 超时全过程另待 E2E |

## plan-mode

规格：[spec.md](../../../openspec/changes/archive/2026-10-06-add-agent-teams/specs/plan-mode/spec.md)

| Scenario | 真实测试函数 | 本次范围／状态 |
| --- | --- | --- |
| coordinator 进入规划 | [`test_team_scenario_audit.py::test_actual_coordinator_plan_filters_model_tools_and_blocks_shell_tasks_and_hooks`](../../../tests/test_team_scenario_audit.py)<br>[`test_team_integration.py::test_plan_switch_during_validation_prevents_code_publication`](../../../tests/test_team_integration.py)<br>[`test_team_hook_guards.py::test_member_hooks_cannot_write_before_task_start`](../../../tests/test_team_hook_guards.py) | 自动化通过；实际 coordinator 两开关／Lead 下，模型声明排除 shell、editor、agent，伪造 shell／任务创建／spawn 在执行前拒绝，实际 Hook 无产物，成员已停止；Git 验证期间切规划拒发布；声明旁路缺陷已实际复现后修复 |
| 规划消息间接派活 | [`test_team_plan_wakeup.py::test_plan_message_wakes_existing_member_with_real_readonly_tool_guard`](../../../tests/test_team_plan_wakeup.py)<br>[`test_team_hook_guards.py::test_member_hooks_cannot_write_before_task_start`](../../../tests/test_team_hook_guards.py) | 自动化通过；规划邮件唤醒已登记成员，实际模型伪造 write／shell／创建任务全部未启动，ToolResult 配对且零产物；待审批或只批准未 start 的真实 Hook 仍不写入 |
| 返回执行模式 | [`test_team_lead_resume.py::test_plan_stops_member_and_bare_execute_does_not_restart_it`](../../../tests/test_team_lead_resume.py)<br>[`test_team_lead_resume.py::test_bare_do_and_resume_do_not_wake_goal_or_workers`](../../../tests/test_team_lead_resume.py) | 自动化通过；实际 set_mode 规划会停止并保存成员，裸切执行不重启成员、不请求模型、不创建接续父运行 |

## provider-configuration

规格：[spec.md](../../../openspec/changes/archive/2026-10-06-add-agent-teams/specs/provider-configuration/spec.md)

| Scenario | 真实测试函数 | 本次范围／状态 |
| --- | --- | --- |
| 后端值非法 | [`test_team_capabilities.py::test_config_rejects_team_values`](../../../tests/test_team_capabilities.py) | 自动化通过；参数化非法后端／容量／布尔值 |
| 旧配置 | [`test_team_capabilities.py::test_legacy_config_has_team_defaults`](../../../tests/test_team_capabilities.py) | 自动化通过；旧配置使用 auto、4、32、false |
| 只开一把锁 | [`test_team_capabilities.py::test_two_locks`](../../../tests/test_team_capabilities.py) | 自动化通过；参数化两开关真值表及真实环境来源 |
| 文件伪装主动启用 | [`test_team_capabilities.py::test_dotenv_cannot_enable_process_coordinator_or_expand_restored_identity`](../../../tests/test_team_capabilities.py)<br>[`test_team_capabilities.py::test_two_locks`](../../../tests/test_team_capabilities.py) | 自动化通过；真实配置加载器读取同名 dotenv 字段不能伪造进程环境，两开关与真实 Lead 身份共同决定能力 |
| 成员继承启动环境 | [`test_team_capabilities.py::test_identity_and_coordinator_are_independent_of_delegation`](../../../tests/test_team_capabilities.py)<br>[`test_team_security.py::test_coordinator_delegates_legal_write_while_own_editor_is_blocked`](../../../tests/test_team_security.py) | 自动化通过；Lead 与成员能力分离 |

## session-persistence

规格：[spec.md](../../../openspec/changes/archive/2026-10-06-add-agent-teams/specs/session-persistence/spec.md)

| Scenario | 真实测试函数 | 本次范围／状态 |
| --- | --- | --- |
| 同时存档 | [`test_team_scenario_boundaries.py::test_simultaneous_real_member_commands_have_separate_journal_roots_and_call_ids`](../../../tests/test_team_scenario_boundaries.py) | 自动化通过；两个实际 Member 模型工具循环先 start，各自命令进程同时存活等待释放；两套真实 Journal 的 header 根、interaction_started 意图、唯一 tool_result 调用 ID／stdout 分别匹配本成员，无兄弟调用 ID，不共享存档路径 |
| 缺少工具结果 | [`test_team_crash_recovery.py::test_explicit_lead_resume_isolates_incomplete_member_task_without_replay`](../../../tests/test_team_crash_recovery.py)<br>[`test_team_crash_recovery.py::test_later_text_cannot_continue_incomplete_claim_until_explicit_reassignment`](../../../tests/test_team_crash_recovery.py)<br>[`test_team_crash_recovery.py::test_lazy_member_setup_blocks_newly_discovered_incomplete_interaction`](../../../tests/test_team_crash_recovery.py)<br>[`test_team_crash_recovery.py::test_repeated_recovery_audits_incomplete_successful_task_without_downgrade_or_duplicate`](../../../tests/test_team_crash_recovery.py)<br>[`test_team_crash_recovery.py::test_new_goal_keeps_member_context_review_until_explicit_new_claim`](../../../tests/test_team_crash_recovery.py) | 自动化通过；真实意图落盘后的 SIGKILL 有／无副作用窗口，合法 checkpoint 不抹未确认交互事实；Lead 恢复和普通正文／旧 assignment 零模型，懒启动新发现缺结果同样 Task blocked／Member needs_review；显式 reassign 真实双锁核查、新 claim 后仅一次正常请求，不重放旧调用。新 Goal 不自动清审核，已有成果／旧预算保留，重复 close／resume 及成功终态迟到审计不降级、不重复 |
| 文本描述已经获批 | [`test_team_scenario_audit.py::test_history_text_cannot_approve_restored_member_plan`](../../../tests/test_team_scenario_audit.py) | 自动化通过；实际保存“Lead 已批准”的正文后暂停／恢复，正文进入模型请求但没有结构化批准，控制仍 awaiting_plan；实际伪造 write 返回 tool_not_allowed／not_started，零产物，原 claim 保持 |
| 消费保存失败 | [`test_team_scenario_audit.py::test_member_receive_checkpoint_failure_keeps_unread_and_stops_model`](../../../tests/test_team_scenario_audit.py)<br>[`test_team_lead_resume.py::test_lead_snapshot_failure_blocks_request_and_preserves_unread_mail`](../../../tests/test_team_lead_resume.py) | 自动化通过；实际成员 receive 的历史 checkpoint 保存失败，邮件未读且未入 consumed_ids／工作历史，零模型，storage_blocked 与收尾警告可见；实际 Lead 故障也未消费／未请求 |
| 长时间暂停 | [`test_team_scenario_boundaries.py::test_long_paused_member_beyond_ttl_keeps_history_and_actual_snapshot_reminder`](../../../tests/test_team_scenario_boundaries.py)<br>[`test_team_retention.py::test_project_ttl_does_not_scan_external_member_journal_and_cache`](../../../tests/test_team_retention.py)<br>[`test_team_retention.py::test_real_idle_and_paused_member_worktree_survives_expired_scan`](../../../tests/test_team_retention.py) | 自动化通过；实际成员完成并暂停，真实 Journal 全时间戳调整至 61 天前（未伪造当前时间），显式恢复保持零自动请求；明确用户激活后同一 session_ref／目录／分支恢复，首实际模型替身请求包含旧结论、“距今 61 天”和完整“涉及当前文件时请重新读取，历史内容仅为当时快照”提醒；TTL 清理与外部缓存保留另有对照 |
| 缓存缺失 | [`test_team_scenario_audit.py::test_missing_required_member_cache_blocks_wake_without_new_identity_or_tree`](../../../tests/test_team_scenario_audit.py)<br>[`test_team_lead_resume.py::test_missing_lead_cache_refuses_resume`](../../../tests/test_team_lead_resume.py)<br>[`test_team_lead_resume.py::test_lead_signed_provider_content_refuses_changed_model`](../../../tests/test_team_lead_resume.py) | 自动化通过；实际成员必要缓存被删除后显式恢复／唤醒，消息已存但通知明确失败，零成员请求、零新身份／工作树／refs；原 session_ref 和历史保留，缺失 cache 未用空文件替换；Lead 缺失及签名不兼容亦拒绝 |

## team-integration

规格：[spec.md](../../../openspec/changes/archive/2026-10-06-add-agent-teams/specs/team-integration/spec.md)

| Scenario | 真实测试函数 | 本次范围／状态 |
| --- | --- | --- |
| 分支在验收后移动 | [`test_team_integration.py::test_registered_input_remains_immutable_after_member_branch_reset`](../../../tests/test_team_integration.py)<br>[`test_team_integration.py::test_immutable_input_and_dependency_sync_leave_user_branch_unchanged`](../../../tests/test_team_integration.py) | 自动化通过；真实 Git 输入冻结 |
| 前置代码整合成功 | [`test_team_integration.py::test_immutable_input_and_dependency_sync_leave_user_branch_unchanged`](../../../tests/test_team_integration.py)<br>[`test_team_tasks.py::test_code_dependency_requires_integrated_commit_and_actual_sync`](../../../tests/test_team_tasks.py) | 自动化通过；真实 Git 安全同步加任务门禁；Member start 全流程待模型验收 |
| 下游目录有未知修改 | [`test_team_integration.py::test_input_wrong_repository_unknown_branch_unaccepted_and_dirty_sync`](../../../tests/test_team_integration.py) | 自动化通过；真实脏目录保护 |
| 第二次整合冲突 | [`test_team_integration.py::test_second_conflict_rollback_preserves_first_and_all_member_refs`](../../../tests/test_team_integration.py)<br>[`test_team_conflict_runtime.py::test_conflict_member_uses_leased_directory_then_lead_publishes`](../../../tests/test_team_conflict_runtime.py) | 自动化通过；仅撤销第二操作，另真实运行器交接租约及实际工具根，成员解决冲突后 Lead 验证发布 |
| 回滚失败 | [`test_team_integration.py::test_failed_abort_is_reported_and_never_claims_rolled_back`](../../../tests/test_team_integration.py)<br>[`test_team_integration.py::test_rollback_refuses_unknown_edits_after_conflict_and_blocks_further_work`](../../../tests/test_team_integration.py) | 自动化通过；撤销失败／未知编辑待核查 |
| 目标分支发生外部更新 | [`test_team_integration.py::test_final_external_update_during_candidate_validation_is_preserved`](../../../tests/test_team_integration.py)<br>[`test_team_integration.py::test_final_dirty_target_and_external_movement_do_not_overwrite`](../../../tests/test_team_integration.py) | 自动化通过；真实外部目标提交保护 |
| 必需任务失败 | [`test_team_integration.py::test_final_candidate_gates_and_checked_out_branch_index_update`](../../../tests/test_team_integration.py) | 自动化通过；必需任务／候选验证／真实索引门禁 |
| Git 已成功但记录提交前退出 | [`test_team_integration.py::test_recovery_reconciles_git_success_without_replaying_and_preserves_user_update`](../../../tests/test_team_integration.py)<br>[`test_team_integration.py::test_recover_published_operation_after_goal_state_save_failure_without_remerge`](../../../tests/test_team_integration.py)<br>[`test_team_integration.py::test_final_publish_crash_recovers_without_touching_later_user_commit`](../../../tests/test_team_integration.py)<br>[`test_team_integration.py::test_sync_git_success_before_record_publish_requires_reconciliation_not_remerge`](../../../tests/test_team_integration.py) | 自动化通过；4 处真实 Git 后的持久边界故障注入，恢复只对账／修复完整发布快照，不重放、不覆盖后续用户提交；非真实 SIGKILL／断电，详见 [integration.md](integration.md) |

## team-member-runtime

规格：[spec.md](../../../openspec/changes/archive/2026-10-06-add-agent-teams/specs/team-member-runtime/spec.md)

| Scenario | 真实测试函数 | 本次范围／状态 |
| --- | --- | --- |
| 第二次指派 | [`test_team_scenario_audit.py::test_restored_team_new_goal_preserves_member_history_tasks_and_result`](../../../tests/test_team_scenario_audit.py)<br>[`test_team_review_regressions.py::test_claim_switches_persistent_budget_without_replacing_active_reference`](../../../tests/test_team_review_regressions.py) | 自动化通过；已自然结束的同一成员跨应用接新目标任务，身份／历史保持而 run_id 改变，旧 provider 请求恰一次、新请求恰一次，不重建同名成员；持久 claim 预算引用切换另有专项 |
| 自动选择协程 | [`test_team_backends.py::test_auto_reports_reason_before_using_inprocess`](../../../tests/test_team_backends.py) | 自动化通过；探测替身验证启动前公开选择 |
| 窗格启动失败 | [`test_team_backends.py::test_start_failure_does_not_run_alternate_backend`](../../../tests/test_team_backends.py)<br>[`test_team_backends.py::test_start_failure_keeps_residual_when_pane_gone_but_process_alive`](../../../tests/test_team_backends.py) | 自动化通过；后端故障注入不降级，未知残留保留 |
| 成员空闲时清理扫描 | [`test_team_retention.py::test_real_idle_and_paused_member_worktree_survives_expired_scan`](../../../tests/test_team_retention.py)<br>[`test_team_retention.py::test_releasing_team_pin_preserves_actual_result_or_evidence`](../../../tests/test_team_retention.py) | 自动化通过；真实成员 idle／paused 超 TTL，解除 pin 仍保护提交／脏内容／证据，clean 对照真实删除 |
| 目录正在使用 | [`test_team_store.py::test_resume_refuses_actual_member_lease_even_if_state_idle`](../../../tests/test_team_store.py)<br>[`test_team_integration.py::test_physical_conflict_lease_blocks_lead_and_other_member_until_stopped`](../../../tests/test_team_integration.py) | 自动化通过；真实 flock 阻止并发目录接管 |
| 旧批准到达 | [`test_team_control.py::test_plan_decision_requires_matching_lead_and_version`](../../../tests/test_team_control.py)<br>[`test_team_security.py::test_old_plan_decision_cannot_approve_after_reassignment`](../../../tests/test_team_security.py)<br>[`test_team_runtime_boundaries.py::test_plan_wait_releases_slot_and_rejection_keeps_revision_read_only`](../../../tests/test_team_runtime_boundaries.py) | 自动化通过；实际模型工具循环提交计划、驳回只读修订、新版本等待拒绝旧批准；仅匹配新批准后才能 start 和编辑，Provider 为替身 |
| 计划获准而工具未获准 | [`test_team_permission_lifecycle.py::test_matching_plan_approval_and_actual_start_do_not_grant_command_permission`](../../../tests/test_team_permission_lifecycle.py) | 自动化通过；实际成员工具循环提交计划、真实 Lead 匹配批准、实际 start 后收紧当前策略，真实命令 deny／ask 均 not_started，零文件／零普通工具 grant；真实人工审批另待 E2E |
| 同时唤醒 | [`test_team_scenario_audit.py::test_simultaneous_member_mail_lazily_launches_one_real_receiver`](../../../tests/test_team_scenario_audit.py) | 自动化通过；两个实际成员来源并发发给 dormant idle 成员，实际启动入口仅一次／provider 构造一次，唯一接收运行在首请求各注入一次两封邮件；两个通知成功，持久 consumed_ids 完整，不丢信 |
| 上下文保存失败 | [`test_team_runtime_boundaries.py::test_save_failure_after_model_answer_does_not_publish_idle`](../../../tests/test_team_runtime_boundaries.py) | 自动化通过；实际模型回答后分别注入历史 checkpoint／控制记录失败，成员运行终止且保留警告，不发布成功 idle 邮件（2 参数实例） |
| 预算耗尽后收到聊天 | [`test_team_runtime_boundaries.py::test_member_budget_is_not_refreshed_by_mail_or_disk_restore`](../../../tests/test_team_runtime_boundaries.py)<br>[`test_team_terminal_audit.py::test_actual_member_tool_loop_exhaustion_keeps_lead_success_and_audits`](../../../tests/test_team_terminal_audit.py) | 自动化通过；普通邮件和磁盘恢复不能刷新额度或发新请求；真实成员工具循环耗尽保持 Lead 已 accepted／integrated／completed 成果且留迟到审计 |
| 停止一个协程成员 | [`test_team_permission_lifecycle.py::test_stopping_parallel_member_preserves_sibling_actual_mcp_and_hook`](../../../tests/test_team_permission_lifecycle.py)<br>[`test_team_runtime_boundaries.py::test_two_members_stream_in_parallel_and_stopping_one_preserves_sibling`](../../../tests/test_team_runtime_boundaries.py) | 自动化通过；双成员真实并行 Provider、独立后台 Hook 及共享 stdio MCP；停止 A 回收其 Hook PID，B Hook 继续产生产物、同一 MCP PID 真实调用成功，B 上下文／取消与 Provider 保持有效 |

## team-messaging

规格：[spec.md](../../../openspec/changes/archive/2026-10-06-add-agent-teams/specs/team-messaging/spec.md)

| Scenario | 真实测试函数 | 本次范围／状态 |
| --- | --- | --- |
| 伪造 Lead | [`test_team_security.py::test_member_cannot_forge_lead_or_sender_in_system_entry`](../../../tests/test_team_security.py)<br>[`test_team_mailbox.py::test_protocol_permissions_and_stale_plan`](../../../tests/test_team_mailbox.py) | 自动化通过；真实工具身份门禁与协议门禁 |
| 普通消息省略可选字段 | [`test_team_mailbox.py::test_send_dedupe_notify_and_read_checkpoint`](../../../tests/test_team_mailbox.py) | 自动化通过；默认字段及持久消费检查点 |
| 活动锁很旧 | [`test_team_store.py::test_real_process_lock_ownership_and_exit_release`](../../../tests/test_team_store.py) | 自动化通过；真实进程持旧时间戳锁不能被抢占 |
| 通知失败 | [`test_team_mailbox.py::test_broadcast_partial_failure_and_resume_idempotence`](../../../tests/test_team_mailbox.py)<br>[`test_team_mailbox.py::test_mailbox_persistence_failure_never_notifies`](../../../tests/test_team_mailbox.py) | 自动化通过；通知与存档结果分离 |
| 存档成功但已读更新前崩溃 | [`test_team_mailbox.py::test_saved_history_repairs_failed_ack_without_double_context`](../../../tests/test_team_mailbox.py) | 自动化通过；失败 ack 通过已消费 ID 修复，避免二次注入 |
| 正在流式响应 | [`test_team_runtime_boundaries.py::test_streaming_mail_is_injected_once_only_at_next_model_request`](../../../tests/test_team_runtime_boundaries.py) | 自动化通过；实际流式门闩间新增邮件不改变当前请求／历史，完成真实工具结果后只在下一模型请求注入一次 |
| 正文包含命令 | [`test_team_backends.py::test_notify_uses_opaque_wait_for_and_checks_pane_owner`](../../../tests/test_team_backends.py)<br>[`test_team_backends.py::test_safe_command_does_not_interpret_shell_body`](../../../tests/test_team_backends.py) | 自动化通过；不透明通知／无正文 shell 插值 |
| 广播部分失败 | [`test_team_mailbox.py::test_broadcast_partial_failure_and_resume_idempotence`](../../../tests/test_team_mailbox.py)<br>[`test_team_mailbox.py::test_broadcast_retry_does_not_notify_already_successful_targets`](../../../tests/test_team_mailbox.py) | 自动化通过；逐成员结果与幂等重试 |
| 重复审批或停止 | [`test_team_mailbox.py::test_repeated_decision_after_state_transition_and_broadcast_snapshot`](../../../tests/test_team_mailbox.py)<br>[`test_team_mailbox.py::test_shutdown_ack_requires_actual_stopped_source_and_current_generation`](../../../tests/test_team_mailbox.py) | 自动化通过；重复决定与停止确认验证真实来源／代次 |

## team-tasks

规格：[spec.md](../../../openspec/changes/archive/2026-10-06-add-agent-teams/specs/team-tasks/spec.md)

| Scenario | 真实测试函数 | 本次范围／状态 |
| --- | --- | --- |
| 两个成员更新 | [`test_team_tasks.py::test_revision_dependency_and_atomic_claim`](../../../tests/test_team_tasks.py) | 自动化通过；revision 冲突保留赢家 |
| 删除前置任务 | [`test_team_tasks.py::test_revision_dependency_and_atomic_claim`](../../../tests/test_team_tasks.py) | 自动化通过；删除前置任务受依赖检查 |
| 并发建立环 | [`test_team_tasks.py::test_concurrent_dag_edits_never_publish_cycle`](../../../tests/test_team_tasks.py) | 自动化通过；并发 DAG 修改检查和发布在同锁内 |
| 两个成员抢任务 | [`test_team_tasks.py::test_real_process_double_claim_has_one_winner`](../../../tests/test_team_tasks.py) | 自动化通过；真实双进程原子 claim |
| 仅模型结束 | [`test_team_tasks.py::test_result_acceptance_and_late_claim_audit`](../../../tests/test_team_tasks.py)<br>[`test_team_tasks.py::test_code_dependency_requires_integrated_commit_and_actual_sync`](../../../tests/test_team_tasks.py)<br>[`test_team_terminal_audit.py::test_actual_member_tool_loop_exhaustion_keeps_lead_success_and_audits`](../../../tests/test_team_terminal_audit.py) | 自动化通过；submitted／accepted／integrated 分离；实际后续工具循环预算耗尽不能降级 Lead 成功终态，迟到原因保留审计 |
| 提交测试失败 | [`test_team_scenario_audit.py::test_real_code_accept_validation_failure_preserves_submission_and_branch`](../../../tests/test_team_scenario_audit.py) | 自动化通过；真实成员 Git 提交及 submitted 后，Lead 独立 validation_command 在成员实际 root 产生产物并 exit 1；Task blocked 未接纳，原 result／branch／commit 保留，无 input／op／goal 发布凭据、refs 精确不变 |
| 旧负责人迟到提交 | [`test_team_tasks.py::test_result_acceptance_and_late_claim_audit`](../../../tests/test_team_tasks.py)<br>[`test_team_security.py::test_old_assignment_cannot_restore_reassigned_claim_on_consume`](../../../tests/test_team_security.py) | 自动化通过；旧 claim 只存审计，不能改变任务 |

## tool-permissions

规格：[spec.md](../../../openspec/changes/archive/2026-10-06-add-agent-teams/specs/tool-permissions/spec.md)

| Scenario | 真实测试函数 | 本次范围／状态 |
| --- | --- | --- |
| 已批计划包含拒绝命令 | [`test_team_permission_lifecycle.py::test_matching_plan_approval_and_actual_start_do_not_grant_command_permission`](../../../tests/test_team_permission_lifecycle.py) | 自动化通过；实际匹配版本批准并开工后，当前真实 deny 返回 permission_denied，当前 ask 返回 approval_required；计划仍批准，命令无副作用，计划没有生成工具 grant |
| 恢复会话旧批准 | [`test_team_scenario_audit.py::test_old_member_tool_grant_is_not_restored_from_history`](../../../tests/test_team_scenario_audit.py)<br>[`test_team_worker.py::test_worker_current_strict_policy_overrides_historical_bypass`](../../../tests/test_team_worker.py) | 自动化通过；旧成员实际 session grant 曾允许写入；保存描述已批的历史后关闭／恢复，当前 default 成员无旧 grant，实际同路径 write 返回 approval_required／not_started，原正文保持；独立 worker 亦重建当前 strict |
| 整合权限不足 | [`test_team_git_permissions.py::test_service_git_permission_refuses_before_worktree_ref_or_operation`](../../../tests/test_team_git_permissions.py)<br>[`test_team_git_permissions.py::test_service_accepted_code_merge_denial_preserves_task_and_receipt`](../../../tests/test_team_git_permissions.py) | 自动化通过；真实 Service 当前 deny／ask 不创建首次受管 Worktree／ref／操作，已接纳代码仍需当前授权，任务和冻结凭据精确不变；详见 [permissions.md](permissions.md)。真实模型 E2E 未执行。 |
| 非成员伪造团队路径 | [`test_team_scenario_boundaries.py::test_actual_ordinary_child_cannot_forge_collaboration_or_write_private_mailbox`](../../../tests/test_team_scenario_boundaries.py)<br>[`test_team_worker.py::test_worker_rejects_copied_startup_and_links`](../../../tests/test_team_worker.py) | 自动化通过；实际普通 defined 子 Agent 伪造协作邮箱路径，team_message 在执行前 tool_not_allowed；对真实用户域私有邮箱 write_file 在当前 protected_roots 权限策略中 permission_denied／not_started，邮箱字节与合法消息保留。复制 worker 启动凭据／链接另有拒绝证据；本场景按原规格保护协作身份与普通文件工具，不扩大为 shell 文件系统沙箱 |

## tool-system

规格：[spec.md](../../../openspec/changes/archive/2026-10-06-add-agent-teams/specs/tool-system/spec.md)

| Scenario | 真实测试函数 | 本次范围／状态 |
| --- | --- | --- |
| 枚举六个核心工具 | [`test_tool_registry.py::test_read_only_filter_and_execution_guard`](../../../tests/test_tool_registry.py)<br>[`test_plan_mode.py::test_do_switches_only_and_next_explicit_task_uses_all_tools`](../../../tests/test_plan_mode.py)<br>[`test_team_service.py::test_ordinary_entry_hidden_then_create_lead`](../../../tests/test_team_service.py) | 自动化通过；六核心、普通执行九工具及 Lead 四协作工具导出 |
| 重复注册 | [`test_tool_registry.py::test_registry_rejects_duplicates_and_invalid_inputs`](../../../tests/test_tool_registry.py) | 自动化通过；重复及非法工具注册拒绝 |
| 规划模式筛选 | [`test_plan_mode.py::test_plan_filters_api_and_execution_for_all_side_effect_tools`](../../../tests/test_plan_mode.py)<br>[`test_team_scenario_audit.py::test_actual_coordinator_plan_filters_model_tools_and_blocks_shell_tasks_and_hooks`](../../../tests/test_team_scenario_audit.py)<br>[`test_team_plan_wakeup.py::test_plan_message_wakes_existing_member_with_real_readonly_tool_guard`](../../../tests/test_team_plan_wakeup.py) | 自动化通过；实际普通／coordinator／成员模型声明依只读及身份过滤，伪造 editor／shell／间接派活真实执行器拒绝；普通系统入口合同保留、detach 恢复原声明，不将独立 Skill 或 Agent 上限当写入许可 |
| 新工具未声明只读 | [`test_tool_registry.py::test_unclassified_tool_defaults_to_non_read_only`](../../../tests/test_tool_registry.py) | 自动化通过；未分类默认可写 |
| 没有 MCP 配置时保持核心工具集 | [`test_plan_mode.py::test_do_switches_only_and_next_explicit_task_uses_all_tools`](../../../tests/test_plan_mode.py) | 自动化通过；普通执行请求九个工具，不含未配置 MCP |
| 外部只读提示不改变调度分类 | [`test_mcp_tools.py::test_descriptors_serializable_conservative_and_deterministic`](../../../tests/test_mcp_tools.py) | 自动化通过；远端只读提示不改变本地分类 |
| 白名单为空的导出 | [`test_skills_tools.py::test_system_route_updates_main_state_before_same_batch_edit`](../../../tests/test_skills_tools.py) | 自动化通过；根代理更新旧断言后本次复验通过，空白名单仍有 load_skill／agent／team |
| 普通入口创建团队 | [`test_team_service.py::test_ordinary_entry_hidden_then_create_lead`](../../../tests/test_team_service.py) | 自动化通过；Lead 验证前隐藏四协作工具，create 后才导出 |
| 团队成员工具 | [`test_team_scenario_boundaries.py::test_member_actual_shared_skill_resource_and_nested_derivation_boundaries`](../../../tests/test_team_scenario_boundaries.py)<br>[`test_team_security.py::test_role_tool_exclusion_is_not_readded_by_team_spawn`](../../../tests/test_team_security.py) | 自动化通过；真实 Member 工具循环成功 load shared Skill 及包内资源，下一请求获得 SOP；有效参数的 isolated Skill／agent／team_member 派生均 tool_not_allowed／not_started，成员名册和 runner 数不增加、无普通子任务；角色上限另有实际拒绝测试 |
| 普通子 Agent 伪造共享任务调用 | [`test_subagent_tool_guard.py::test_system_tool_is_blocked_before_permission_or_handler`](../../../tests/test_subagent_tool_guard.py)<br>[`test_team_capabilities.py::test_registry_scope_is_explicit_even_for_system_tools`](../../../tests/test_team_capabilities.py) | 自动化通过；system 工具也受真实注册范围限制 |
| 成员伪造验收 | [`test_team_capabilities.py::test_member_cannot_accept_or_mutate_in_plan`](../../../tests/test_team_capabilities.py)<br>[`test_team_security.py::test_member_cannot_forge_lead_or_sender_in_system_entry`](../../../tests/test_team_security.py) | 自动化通过；成员接纳和身份伪造门禁 |
| Lead 自己编辑 | [`test_team_security.py::test_coordinator_delegates_legal_write_while_own_editor_is_blocked`](../../../tests/test_team_security.py) | 自动化通过；Lead editor 排除且可委派合法修改 |
| 队员仍能编辑 | [`test_team_security.py::test_coordinator_delegates_legal_write_while_own_editor_is_blocked`](../../../tests/test_team_security.py)<br>[`test_team_security.py::test_real_deny_still_blocks_started_member_even_under_bypass`](../../../tests/test_team_security.py) | 自动化通过；成员编辑能力保留但真实权限不放宽 |


## 后续验证边界

真实 tmux 测试函数 `test_real_tmux_workers_have_independent_cwd_notification_and_clean_exit` 和参数化 `test_real_tmux_full_worker_activation_tool_history_and_owned_mcp_close` 仅在真实 tmux 可用且允许创建本地 socket 时执行；最新关联回归中前者 1 例、后者 4 例均跳过。后一测试仍使用模型替身与临时 MCP 服务，不能记为真实模型 E2E。

状态展示、长期保留、流式请求边界、模型结束后的保存失败及预算耗尽已新增实际链路测试。并发实际工具存档、成员 Skill 派生边界、跨 TTL 提醒、零 Lead 预算通知、控制命令竞争、停止未确认的 UI 与真实崩溃缺工具结果已补精确证据。异常退出的成员终态已用最终新增断言核查：已确认停止而上下文仍未知时明确 needs_review，真实运行／Journal 所有者未确认退出或运行代次仍未知时拒绝重复接管；私有标记不能替代双租约及真实日志事实。真实模型、真实 tmux 超时和 Git 每个 fsync／ref 崩溃窗口不由替身或故障注入代替；9.6 尚非穷尽验证，见 integration.md。
