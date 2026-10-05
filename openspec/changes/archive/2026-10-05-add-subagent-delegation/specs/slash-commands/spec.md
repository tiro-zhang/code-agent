## MODIFIED Requirements

### Requirement: 本地 Skill 管理与对话重置
`/skills` 或 `/skills list` SHALL 本地列出有效名称、说明、模式、来源及激活状态；`/skills active` SHALL 显示当前激活项与有效普通工具范围。`/skills deactivate <name>` 和 `/skills deactivate --all` SHALL 仅在空闲期通过会话能力停用激活，不清除对话历史、不执行模型。未知名称、非法子命令或额外参数 SHALL 在操作前本地报错。`/reset` SHALL 为无参数的本地状态操作，按 session-persistence 合同原子清空当前模型历史和激活状态并重置上下文估算及摘要失败状态；保持会话身份、模式、权限、存档和长期记忆，不把重置描述为撤销实际操作或删除存档。`/clear` SHALL 继续只清屏，且保留全部 Skill 激活。

reset SHALL 先使旧任务代次不可接续，取消并有界收尾全部 Agent 子，再清空语义历史；迟到结果不写入新历史。会话权限和 Hook once 保留，终态任务记录在当前进程可本地查看；/clear 不取消或改变后台任务。

#### Scenario: 停用恢复工具范围
- **WHEN** 用户空闲时停用一个导致工具交集收窄的 Skill
- **THEN** 成功保存状态后下一请求使用其余激活项的交集，历史和其他激活保持，不调用模型

#### Scenario: 清屏和重置区别
- **WHEN** 对话已有激活和历史，用户先 /clear 后 /reset
- **THEN** 清屏后历史及激活仍在；重置持久提交后当前模型历史和激活为空，下一请求使用完整当前环境提醒

#### Scenario: 重置非法参数
- **WHEN** 用户输入 /reset extra
- **THEN** 本地显示正确用法，不清历史或激活，不调用模型

## ADDED Requirements

### Requirement: 本地角色与任务管理
系统 SHALL 注册 /agents [list|show <name>] 和 /tasks [list|show <id>|cancel <id>|cancel-parent <parent_id>]，通过现有注册表提供帮助、补全、保留名称及参数校验。它们 SHALL 本地查看有效角色来源和任务状态、完整结果、用量，或受控取消，不请求模型。取消子只影响该子，取消父影响其全部子并阻止唤醒；未知名称、身份及额外参数在操作前报错。

#### Scenario: 本地查看
- **WHEN** 查询有效角色或后台任务
- **THEN** 显示实际来源或状态、结果及用量，模型请求数不增加

#### Scenario: 取消某父
- **WHEN** 执行 /tasks cancel-parent 对应身份
- **THEN** 该父子任务受控结束并不再唤醒，其他父任务继续

#### Scenario: 非法参数与命令冲突
- **WHEN** 参数非法或 Skill 与新控制命令同名
- **THEN** 前者报用法且不操作任务，后者按既有目录合同报告冲突
