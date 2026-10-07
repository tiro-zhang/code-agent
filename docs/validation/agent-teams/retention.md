# 团队长期引用与状态展示验证

本轮执行：

```text
UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run pytest tests/test_team_commands.py tests/test_team_retention.py -q
26 passed in 8.53s
```

`test_team_retention.py` 的 6 个测试实例实际创建 Git 仓库、团队、成员 Worktree、用户域 journal/cache，并通过 ScriptedProvider 令成员自然结束后空闲。

| 实际验证 | 函数证据 |
| --- | --- |
| 成员 idle 有真实租约，paused 释放租约后清除 evidence 保护仍由 team_pin 保留；记录时间设为 61 天前 | `test_real_idle_and_paused_member_worktree_survives_expired_scan` |
| 解除 pin 后分别保护实际提交、未追踪成果、evidence_protected；无这些保护的 clean 对照确实删除目录及分支 | `test_releasing_team_pin_preserves_actual_result_or_evidence`（4 参数实例） |
| 项目与成员工作根的 TTL 清理只删除普通项目存档对照；外部用户域 journal/cache 字节保持，暂停后可重新读取历史和证据 | `test_project_ttl_does_not_scan_external_member_journal_and_cache` |

状态展示新增真实 Service 测试 `test_team_status_shows_repository_plan_task_mailbox_and_integration_without_waking`：显示仓库、Lead、cwd/backend、计划版本和匹配结构化决定说明、任务依赖阻塞、消息已保存／唤醒未确认及未初始化整合状态；查看零模型／零成员进程。消息中的配置密钥及终端控制字符经过展示脱敏。另用三种合法前置状态展示输入验证 integrated／completed 代码和 completed 非代码不误报阻塞。

该阶段记录只证明确定性运行器与持久边界，当时未覆盖跨 TTL 的文件跨度提醒，也不代表真实模型双成员资源隔离或真实 tmux 团队端到端通过。最新补充见下段；81 场景映射及未执行项见 [spec-audit.md](spec-audit.md)。

## 独立关联回归（2026-10-06）

本模块及团队／Worktree 基础合同已随 **502 passed、5 skipped，133.81s** 复验，完整命令见 [spec-audit.md](spec-audit.md)。跳过是本执行环境无法建立私有 tmux socket 的 5 个实例，不计通过。该结果不改写上面的原始执行，也不等同于真实模型验收。

## 长暂停实际恢复请求补充

`test_team_scenario_boundaries.py::test_long_paused_member_beyond_ttl_keeps_history_and_actual_snapshot_reminder` 实际完成成员请求并暂停，将该成员真实 Journal 的记录时间设置为 61 天前，再显式恢复。没有替换当前时间，也没有手工插入提醒；恢复本身零成员／零模型请求。明确用户激活后，原 session_ref、Worktree 根和分支保持，实际模型替身的第一请求同时含旧成员结论、“距今 61 天”和完整“涉及当前文件时请重新读取，历史内容仅为当时快照”提醒。该测试补上跨度提醒的精确自动化证据，不能证明模型实际遵守提醒重读。

关联回归为 **95 passed，47.76s**，完整命令见 [spec-audit.md](spec-audit.md) 的剩余边界补充。上述旧 26 项及 502 项执行快照保持原值，不将新增测试虚加进历史命令。
