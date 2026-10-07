# 团队受管 Git 权限验证

## 团队协议工具的可配置许可（2026-10-06）

五个团队工具现已接入与 `agent` 一致的完整参数授权合同：`team`、`team_member`、`team_task`、`team_message`、`team_integrate` 可以在真实权限 YAML 中写 allow／ask／deny 规则。exact 规则必须是完整、有限的 JSON 对象，规范化键顺序和空白；glob 对完整规范 JSON 字符串匹配，正文内 `/` 不按目录切分。会话／永久批准使用 command 范围，绑定实际根、工具及完整规范参数。

例如按动作放行通信与当前任务开工：

```yaml
version: 1
rules:
  - effect: allow
    rule: 'team_message({"action":"read"})'
    match: exact
  - effect: allow
    rule: 'team_message({"action":"send",*})'
    match: glob
  - effect: allow
    rule: 'team_task({"action":"start",*})'
    match: glob
```

没有配置不自动放行，default 后台成员仍返回 approval_required；strict 即使命中 allow 仍需批准。deny 优先，allow 不扩大实际身份、角色或规划门禁，也不批准普通工具和受管 Git。成员实际 Worktree 和父项目的当前策略共同约束许可；无需让模型直接编辑受保护的权限配置。

`tests/test_team_tool_permissions.py` 用实际 PermissionManager、TeamService、临时 Git／Worktree 与成员 Agent 工具循环验证完整链：成员从 default 模式启动、真实 plan_request、Lead 经执行器发送匹配 plan_decision、成员实际 team_task start 及正文通信。deny 反例保留 claimed 状态并返回 permission_denied／not_started。即使命中 allow，成员自接纳及规划模式开工仍被拒绝。供应商使用替身，不计真实模型 E2E。

原配置拒绝五个工具为未知工具，批准存储也误将其 command 范围当作文件 path；新增测试初轮 **17 failed、15 passed**。修复后原 32 个实例通过。后续门禁补充中曾误把成员创建任务当越权，并给 accept 使用不存在的 evidence 参数；已依照实际 Schema 改为 Lead 专用 accept 动作，不修改生产门禁。

最终与既有配置、规则、批准、权限运行时、Agent、MCP、规划唤醒和权限生命周期联合验证：

```text
.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_team_tool_permissions.py tests/test_permission_config.py tests/test_permission_rules.py tests/test_permission_grants.py tests/test_permission_runtime.py tests/test_agent_permissions.py tests/test_mcp_permissions.py tests/test_team_plan_wakeup.py tests/test_team_permission_lifecycle.py --basetemp=/private/tmp/team-tools-regression2
161 passed in 11.00s
```

其中团队协议新增 37 个实例。`git diff --check` 通过；完整自动回归和真实 tmux 由同轮整体验收另行记录。

本轮新增 `tests/test_team_git_permissions.py` 使用真实临时 Git 仓库及实际 TeamService／PermissionManager。旧实现的授权构造参数缺失和操作预检缺失先由失败测试复现；实现后执行：

```text
UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run pytest tests/test_team_git_permissions.py -q
14 passed in 13.62s
```

| 实际边界 | 真实函数证据 |
| --- | --- |
| Service 的当前 deny／ask 在首次受管目录、refs、操作记录创建前拒绝，原任务保持 | `test_service_git_permission_refuses_before_worktree_ref_or_operation`（2 参数实例） |
| 代码已独立验证并接纳后，当前权限仍检查，冻结凭据／任务／refs 精确不变 | `test_service_accepted_code_merge_denial_preserves_task_and_receipt`（2 参数实例） |
| 底层初始化拒绝不创建存储和分支；integrate／sync／finalize 开始前预检拒绝不改变目标 JSON 或操作／凭据记录 | `test_initialize_callback_denial_preserves_absent_storage_and_refs`、`test_operation_preflight_denial_keeps_exact_goal_and_input_records`（3 参数实例） |
| merge 执行前再次拒绝，原 ToolError 原样传播，恢复原快照及移除未执行的 prepared 记录，不宣称已回滚 Git | `test_policy_recheck_before_merge_propagates_original_error_without_operation` |
| 验证期间撤回策略后，真正已启动操作的验证失败／取消只清理本次获授权的目录／旧提交，不等新批准 | `test_started_operation_cleanup_does_not_request_new_permission`（2 参数实例） |
| 显式 rollback 重新授权；拒绝保留冲突及此前记录 | `test_explicit_rollback_requires_new_authorization_and_keeps_conflict` |
| 最终发布重新检查实际 update-ref／read-tree cwd 和参数，策略收紧不推进用户分支 | `test_final_publication_rechecks_actual_update_ref_and_read_tree_policy` |
| ref 事务提交前撤回授权，仅核对完全属于本次且尚未提交的索引变化后恢复；此前整合和成员成果保留 | `test_commit_policy_denial_restores_only_owned_index_transaction` |

`IntegrationService(..., authorize=None)` 的可选回调合同为 `async authorize(root: Path, args: tuple[str, ...]) -> None`。回调参数包含实际受管 Git 的 hooks 配置参数；生产 TeamService 必须传递实际权限检查，默认 None 仅保持底层独立 Git 服务及既有测试合同。成员 Worktree 的创建预检由 TeamService／成员运行器另行接入。

首次操作预检先于 Git 副作用及操作发布；真正执行前再检查当前策略。内部清理旁路仅绑定本次已启动操作的实际目录和 pre_head，对应 abort／reset；不能用于显式回滚或新操作。目标 ref 已提交或副作用不确定时保留证据进入待核查，不能由清理覆盖未知后续修改。

这些是确定性自动化证据，不代表真实模型、真实人工授权或 tmux 团队端到端通过。

## 最后关联回归

```text
UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run pytest tests/test_team_git_permissions.py tests/test_team_integration.py tests/test_team_conflict_runtime.py -q
46 passed in 52.23s
```

包含 14 个权限专项实例、31 个原始真实 Git 边界实例及 1 个冲突成员运行器接入实例。默认 `authorize=None` 的既有整合合同保留；生产 Service 已接实际权限回调。`git diff --check` 通过，81 场景映射仍保留全部行，新增权限函数引用已核查。

## 未知后续编辑的补充反例

将 `test_commit_policy_denial_restores_only_owned_index_transaction` 增为两个参数实例：一例仅撤销本次未提交 read-tree，另一例在索引更新后模拟用户编辑，再撤回 ref 提交授权。后一例保留用户正文和全部成员／整合提交，进入 needs_review，不强行恢复目录。补充测试后权限模块复验 **15 passed，12.50s**；实现没有继续改动，前述 46 个关联实例的通过记录仍为实际那次执行结果，不能改写成未执行的 47 passed。

## 独立关联回归（2026-10-06）

本模块及团队／Worktree 基础合同已随 **502 passed、5 skipped，133.81s** 复验，完整命令见 [spec-audit.md](spec-audit.md)。跳过是本执行环境无法建立私有 tmux socket 的 5 个实例，不计通过。该结果不改写上面的原始执行，也不等同于真实模型验收。

## 计划与兄弟资源的精确补充（2026-10-06）

```text
UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run pytest tests/test_team_permission_lifecycle.py -q
3 passed in 4.37s
```

`test_matching_plan_approval_and_actual_start_do_not_grant_command_permission` 的 deny／ask 两例先经实际模型工具循环发送 plan_request，真实 Lead 匹配 plan_decision，再经实际 team_task start 确认代码任务同步。门闩暂停下一请求，收紧当前真实策略后释放。执行器分别返回 permission_denied／approval_required 且 not_started；成员／父根均无命令产物，计划仍已批准，未生成普通工具 session grant。测试没有依靠手工设置 approved 或 running。

`test_stopping_parallel_member_preserves_sibling_actual_mcp_and_hook` 运行两个真实 inprocess 成员、一个真实共享 stdio MCP 子进程和成员各自真实后台 Hook 命令。两成员实际开工、同时流式且 Hook 都活跃时停止 A：A Provider 关闭、Hook PID 已回收、未完成探针产物；B 的 Hook PID、MCP PID、上下文根和取消事件保持。放行 B 后实际 Hook 产物完成，实际模型工具循环通过 MCP 收到正确 echo 结果；日志显示只有一个 MCP 启动和一次调用，A 未调用 MCP。最后测试所有者关闭并确认共享服务线程结束。Provider 仍使用替身，不计真实模型 E2E。

新增测试初次失败分别来自探针配置父目录未创建、代码任务正确拒绝未追踪探针文件；修复测试设施并使用实际非代码资源任务后通过。本轮没有修改实现或更改既有测试断言。
