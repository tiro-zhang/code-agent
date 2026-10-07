## ADDED Requirements

### Requirement: 团队规划上限及切换收尾
Lead 进入 plan SHALL 先暂停团队执行调度、取消并等待受控 execute 成员运行及整合操作收尾，再发布团队只读上限。plan SHALL 禁止创建团队／成员／Worktree、任务变更与领取、代码同步、计划执行批准及整合；允许合法状态查询和只读规划。受限规划消息 SHALL 只能触发同样受只读上限的既有成员，不能通过发消息间接启动执行操作。成员本地计划门禁与团队 plan SHALL 独立，批准计划不能突破团队 plan。裸 /do SHALL 仅解除模式限制，不自动恢复旧任务、批准计划或创建预算，后续执行须明确指派。

#### Scenario: coordinator 进入规划
- **WHEN** coordinator Lead 进入 plan 后调用 execute_command 或团队整合
- **THEN** 返回 tool_not_allowed，保留 shell 的 coordinator 分工不能突破规划上限

#### Scenario: 规划消息间接派活
- **WHEN** plan Lead 给既有成员发送要求修改代码的规划消息
- **THEN** 成员仍只读且不能执行修改，不把消息当作解除规划的指令

#### Scenario: 返回执行模式
- **WHEN** 团队有暂停的旧任务且用户输入裸 /do
- **THEN** 只切换模式，不自动唤醒旧执行，等待明确的新指派
