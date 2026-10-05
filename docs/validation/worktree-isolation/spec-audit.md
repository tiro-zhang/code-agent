# 本次增量规格核对

逐项核对 10 份增量规格的所有场景。以下文件是行为证据入口；同一集成用例可覆盖多个场景，不把场景数解释为独立测试数。既有合同使用本次完整回归结果，新目录合同使用 Worktree 专项测试。真实服务覆盖范围见上级验收报告。

## agent-definitions

| 需求 | 场景 | 本次自动化证据 |
| --- | --- | --- |
| 有界 Markdown 角色定义 | 非法入口 | [test_agent_definitions.py](../../../tests/test_agent_definitions.py)、[test_agent_lifecycle.py](../../../tests/test_agent_lifecycle.py) |
| 有界 Markdown 角色定义 | 空白名单与黑名单 | [test_agent_definitions.py](../../../tests/test_agent_definitions.py)、[test_agent_lifecycle.py](../../../tests/test_agent_lifecycle.py) |
| 有界 Markdown 角色定义 | 合法隔离声明 | [test_agent_definitions.py](../../../tests/test_agent_definitions.py)、[test_agent_lifecycle.py](../../../tests/test_agent_lifecycle.py) |
| 有界 Markdown 角色定义 | 非法隔离值和缺省兼容 | [test_agent_definitions.py](../../../tests/test_agent_definitions.py)、[test_agent_lifecycle.py](../../../tests/test_agent_lifecycle.py) |
| 有效目录校验和冻结 | 未调用角色拼错工具 | [test_agent_definitions.py](../../../tests/test_agent_definitions.py)、[test_agent_lifecycle.py](../../../tests/test_agent_lifecycle.py) |
| 有效目录校验和冻结 | 排队期间修改定义 | [test_agent_definitions.py](../../../tests/test_agent_definitions.py)、[test_agent_lifecycle.py](../../../tests/test_agent_lifecycle.py) |
| 有效目录校验和冻结 | 排队期间改变隔离声明 | [test_agent_definitions.py](../../../tests/test_agent_definitions.py)、[test_agent_lifecycle.py](../../../tests/test_agent_lifecycle.py) |

## context-management

| 需求 | 场景 | 本次自动化证据 |
| --- | --- | --- |
| 会话缓存生命周期与权限边界 | 规划期间需要落盘 | [test_context_spill.py](../../../tests/test_context_spill.py)、[test_context_summary.py](../../../tests/test_context_summary.py)、[test_worktree_evidence.py](../../../tests/test_worktree_evidence.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 会话缓存生命周期与权限边界 | 缓存路径被拒绝 | [test_context_spill.py](../../../tests/test_context_spill.py)、[test_context_summary.py](../../../tests/test_context_summary.py)、[test_worktree_evidence.py](../../../tests/test_worktree_evidence.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 会话缓存生命周期与权限边界 | 正常退出或清理失败 | [test_context_spill.py](../../../tests/test_context_spill.py)、[test_context_summary.py](../../../tests/test_context_summary.py)、[test_worktree_evidence.py](../../../tests/test_worktree_evidence.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 会话缓存生命周期与权限边界 | 大量历史缓存引用 | [test_context_spill.py](../../../tests/test_context_spill.py)、[test_context_summary.py](../../../tests/test_context_summary.py)、[test_worktree_evidence.py](../../../tests/test_worktree_evidence.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 会话缓存生命周期与权限边界 | 缓存引用在摘要前失效 | [test_context_spill.py](../../../tests/test_context_spill.py)、[test_context_summary.py](../../../tests/test_context_summary.py)、[test_worktree_evidence.py](../../../tests/test_worktree_evidence.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 会话缓存生命周期与权限边界 | 恢复后重新读取结果 | [test_context_spill.py](../../../tests/test_context_spill.py)、[test_context_summary.py](../../../tests/test_context_summary.py)、[test_worktree_evidence.py](../../../tests/test_worktree_evidence.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 会话缓存生命周期与权限边界 | 删除工作树后父证据可读取 | [test_context_spill.py](../../../tests/test_context_spill.py)、[test_context_summary.py](../../../tests/test_context_summary.py)、[test_worktree_evidence.py](../../../tests/test_worktree_evidence.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 会话缓存生命周期与权限边界 | 证据归档部分失败 | [test_context_spill.py](../../../tests/test_context_spill.py)、[test_context_summary.py](../../../tests/test_context_summary.py)、[test_worktree_evidence.py](../../../tests/test_worktree_evidence.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 目录相关状态的绝对路径身份 | 相同相对路径位于不同根 | [test_context_spill.py](../../../tests/test_context_spill.py)、[test_context_summary.py](../../../tests/test_context_summary.py)、[test_worktree_evidence.py](../../../tests/test_worktree_evidence.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 目录相关状态的绝对路径身份 | 同目录文件已改变 | [test_context_spill.py](../../../tests/test_context_spill.py)、[test_context_summary.py](../../../tests/test_context_summary.py)、[test_worktree_evidence.py](../../../tests/test_worktree_evidence.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |

## core-tools

| 需求 | 场景 | 本次自动化证据 |
| --- | --- | --- |
| 文件工具的工作目录边界 | 普通相对路径 | [test_file_tools.py](../../../tests/test_file_tools.py)、[test_permission_shell.py](../../../tests/test_permission_shell.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 文件工具的工作目录边界 | 目录穿越与链接越界 | [test_file_tools.py](../../../tests/test_file_tools.py)、[test_permission_shell.py](../../../tests/test_permission_shell.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 文件工具的工作目录边界 | 根目录内的路径别名 | [test_file_tools.py](../../../tests/test_file_tools.py)、[test_permission_shell.py](../../../tests/test_permission_shell.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 文件工具的工作目录边界 | 隔离子访问父绝对路径 | [test_file_tools.py](../../../tests/test_file_tools.py)、[test_permission_shell.py](../../../tests/test_permission_shell.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 文件工具的工作目录边界 | 根内同名文件互不覆盖 | [test_file_tools.py](../../../tests/test_file_tools.py)、[test_permission_shell.py](../../../tests/test_permission_shell.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 完整 shell 命令执行 | 管道与重定向 | [test_file_tools.py](../../../tests/test_file_tools.py)、[test_permission_shell.py](../../../tests/test_permission_shell.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 完整 shell 命令执行 | 非零退出 | [test_file_tools.py](../../../tests/test_file_tools.py)、[test_permission_shell.py](../../../tests/test_permission_shell.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 完整 shell 命令执行 | 工作目录不跨调用持久化 | [test_file_tools.py](../../../tests/test_file_tools.py)、[test_permission_shell.py](../../../tests/test_permission_shell.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 完整 shell 命令执行 | 显式子命令命中拒绝 | [test_file_tools.py](../../../tests/test_file_tools.py)、[test_permission_shell.py](../../../tests/test_permission_shell.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 完整 shell 命令执行 | 简单命令的 glob allow 不扩展到组合命令 | [test_file_tools.py](../../../tests/test_file_tools.py)、[test_permission_shell.py](../../../tests/test_permission_shell.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 完整 shell 命令执行 | 子命令 ask 高于完整串 allow | [test_file_tools.py](../../../tests/test_file_tools.py)、[test_permission_shell.py](../../../tests/test_permission_shell.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 完整 shell 命令执行 | 解析无法完成 | [test_file_tools.py](../../../tests/test_file_tools.py)、[test_permission_shell.py](../../../tests/test_permission_shell.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 完整 shell 命令执行 | 黑名单不可批准 | [test_file_tools.py](../../../tests/test_file_tools.py)、[test_permission_shell.py](../../../tests/test_permission_shell.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 完整 shell 命令执行 | 隔离命令使用子 cwd | [test_file_tools.py](../../../tests/test_file_tools.py)、[test_permission_shell.py](../../../tests/test_permission_shell.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |

## lifecycle-hooks

| 需求 | 场景 | 本次自动化证据 |
| --- | --- | --- |
| 子运行 Hook 权限与生命周期 | 提示词不串扰 | [test_worktree_hooks.py](../../../tests/test_worktree_hooks.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 子运行 Hook 权限与生命周期 | Hook 缺少批准 | [test_worktree_hooks.py](../../../tests/test_worktree_hooks.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 子运行 Hook 权限与生命周期 | 子结束 | [test_worktree_hooks.py](../../../tests/test_worktree_hooks.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 子运行 Hook 权限与生命周期 | 子 Hook 使用实际目录 | [test_worktree_hooks.py](../../../tests/test_worktree_hooks.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 子运行 Hook 权限与生命周期 | 异步动作保护目录 | [test_worktree_hooks.py](../../../tests/test_worktree_hooks.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |

## project-instructions

| 需求 | 场景 | 本次自动化证据 |
| --- | --- | --- |
| 三层指令发现与优先级 | 三层存在冲突 | [test_instructions.py](../../../tests/test_instructions.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 三层指令发现与优先级 | 配置文件位于项目外 | [test_instructions.py](../../../tests/test_instructions.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 三层指令发现与优先级 | 不同工作副本的同名指令 | [test_instructions.py](../../../tests/test_instructions.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 三层指令发现与优先级 | 子指令引用逃逸 | [test_instructions.py](../../../tests/test_instructions.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |

## runtime-context

| 需求 | 场景 | 本次自动化证据 |
| --- | --- | --- |
| 环境快照的消息注入 | 从不同目录启动 | [test_prompt_context.py](../../../tests/test_prompt_context.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 环境快照的消息注入 | 同一会话连续请求 | [test_prompt_context.py](../../../tests/test_prompt_context.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 环境快照的消息注入 | 恢复后环境变化 | [test_prompt_context.py](../../../tests/test_prompt_context.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 环境快照的消息注入 | 隔离子首个工作请求 | [test_prompt_context.py](../../../tests/test_prompt_context.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 环境快照的消息注入 | 压缩或精简后目录保持正确 | [test_prompt_context.py](../../../tests/test_prompt_context.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |

## session-persistence

| 需求 | 场景 | 本次自动化证据 |
| --- | --- | --- |
| 独立运行审计和主投影隔离 | 子操作完成但摘要未回流 | [test_session_persistence.py](../../../tests/test_session_persistence.py)、[test_worktree_evidence.py](../../../tests/test_worktree_evidence.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 独立运行审计和主投影隔离 | 子意图无法保存 | [test_session_persistence.py](../../../tests/test_session_persistence.py)、[test_worktree_evidence.py](../../../tests/test_worktree_evidence.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 独立运行审计和主投影隔离 | 恢复含保留工作树的会话 | [test_session_persistence.py](../../../tests/test_session_persistence.py)、[test_worktree_evidence.py](../../../tests/test_worktree_evidence.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 独立运行审计和主投影隔离 | 工作树已删除但归档仍有效 | [test_session_persistence.py](../../../tests/test_session_persistence.py)、[test_worktree_evidence.py](../../../tests/test_worktree_evidence.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |

## subagent-runtime

| 需求 | 场景 | 本次自动化证据 |
| --- | --- | --- |
| 定义式空历史和固定角色 | 父历史和激活不泄漏 | [test_subagent_runtime.py](../../../tests/test_subagent_runtime.py)、[test_subagent_effects.py](../../../tests/test_subagent_effects.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 定义式空历史和固定角色 | 子目录规则与包资源 | [test_subagent_runtime.py](../../../tests/test_subagent_runtime.py)、[test_subagent_effects.py](../../../tests/test_subagent_effects.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 独立状态和共享资源所有权 | 两个子运行同时活动 | [test_subagent_runtime.py](../../../tests/test_subagent_runtime.py)、[test_subagent_effects.py](../../../tests/test_subagent_effects.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 独立状态和共享资源所有权 | 隔离子与父同时操作同名路径 | [test_subagent_runtime.py](../../../tests/test_subagent_runtime.py)、[test_subagent_effects.py](../../../tests/test_subagent_effects.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 独立状态和共享资源所有权 | 共享 MCP 的目录语义 | [test_subagent_runtime.py](../../../tests/test_subagent_runtime.py)、[test_subagent_effects.py](../../../tests/test_subagent_effects.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 有来源最终结果及真实完成语义 | 最终答复报告测试失败 | [test_subagent_runtime.py](../../../tests/test_subagent_runtime.py)、[test_subagent_effects.py](../../../tests/test_subagent_effects.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 有来源最终结果及真实完成语义 | 副作用后取消 | [test_subagent_runtime.py](../../../tests/test_subagent_runtime.py)、[test_subagent_effects.py](../../../tests/test_subagent_effects.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 有来源最终结果及真实完成语义 | 模型完成而目录保留 | [test_subagent_runtime.py](../../../tests/test_subagent_runtime.py)、[test_subagent_effects.py](../../../tests/test_subagent_effects.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 隔离任务的启动与冻结基线 | 初始化失败未发送模型 | [test_subagent_runtime.py](../../../tests/test_subagent_runtime.py)、[test_subagent_effects.py](../../../tests/test_subagent_effects.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 隔离任务的启动与冻结基线 | 同角色两次委派 | [test_subagent_runtime.py](../../../tests/test_subagent_runtime.py)、[test_subagent_effects.py](../../../tests/test_subagent_effects.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |

## tool-permissions

| 需求 | 场景 | 本次自动化证据 |
| --- | --- | --- |
| 三来源规则无覆盖顺序合并 | 三来源互相冲突 | [test_permission_rules.py](../../../tests/test_permission_rules.py)、[test_subagent_permissions.py](../../../tests/test_subagent_permissions.py)、[test_worktree_permissions.py](../../../tests/test_worktree_permissions.py) |
| 三来源规则无覆盖顺序合并 | 精确放行不能盖过通配询问 | [test_permission_rules.py](../../../tests/test_permission_rules.py)、[test_subagent_permissions.py](../../../tests/test_subagent_permissions.py)、[test_worktree_permissions.py](../../../tests/test_worktree_permissions.py) |
| 三来源规则无覆盖顺序合并 | 项目根独立于供应商配置 | [test_permission_rules.py](../../../tests/test_permission_rules.py)、[test_subagent_permissions.py](../../../tests/test_subagent_permissions.py)、[test_worktree_permissions.py](../../../tests/test_worktree_permissions.py) |
| 三来源规则无覆盖顺序合并 | 父新增拒绝仍限制子目录 | [test_permission_rules.py](../../../tests/test_permission_rules.py)、[test_subagent_permissions.py](../../../tests/test_subagent_permissions.py)、[test_worktree_permissions.py](../../../tests/test_worktree_permissions.py) |
| 三来源规则无覆盖顺序合并 | 子放行不能覆盖父询问 | [test_permission_rules.py](../../../tests/test_permission_rules.py)、[test_subagent_permissions.py](../../../tests/test_subagent_permissions.py)、[test_worktree_permissions.py](../../../tests/test_worktree_permissions.py) |
| 子授权快照与权限模式上限 | 副本独立 | [test_permission_rules.py](../../../tests/test_permission_rules.py)、[test_subagent_permissions.py](../../../tests/test_subagent_permissions.py)、[test_worktree_permissions.py](../../../tests/test_worktree_permissions.py) |
| 子授权快照与权限模式上限 | 角色只能收紧 | [test_permission_rules.py](../../../tests/test_permission_rules.py)、[test_subagent_permissions.py](../../../tests/test_subagent_permissions.py)、[test_worktree_permissions.py](../../../tests/test_worktree_permissions.py) |
| 子授权快照与权限模式上限 | 最新存储复查 | [test_permission_rules.py](../../../tests/test_permission_rules.py)、[test_subagent_permissions.py](../../../tests/test_subagent_permissions.py)、[test_worktree_permissions.py](../../../tests/test_worktree_permissions.py) |
| 子授权快照与权限模式上限 | 父批准不迁移到同名子文件 | [test_permission_rules.py](../../../tests/test_permission_rules.py)、[test_subagent_permissions.py](../../../tests/test_subagent_permissions.py)、[test_worktree_permissions.py](../../../tests/test_worktree_permissions.py) |
| 子授权快照与权限模式上限 | 命令和 MCP 也不跨根重绑 | [test_permission_rules.py](../../../tests/test_permission_rules.py)、[test_subagent_permissions.py](../../../tests/test_subagent_permissions.py)、[test_worktree_permissions.py](../../../tests/test_worktree_permissions.py) |

## worktree-isolation

| 需求 | 场景 | 本次自动化证据 |
| --- | --- | --- |
| 受管工作树与明确的隔离边界 | 两个子工作树使用同一版本库 | [test_worktree_manager.py](../../../tests/test_worktree_manager.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 受管工作树与明确的隔离边界 | 任意项目缺少忽略配置 | [test_worktree_manager.py](../../../tests/test_worktree_manager.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 受管工作树与明确的隔离边界 | 管理路径已被追踪 | [test_worktree_manager.py](../../../tests/test_worktree_manager.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 有界安全嵌套名称 | 合法嵌套名称 | [test_worktree_paths.py](../../../tests/test_worktree_paths.py)、[test_worktree_manager.py](../../../tests/test_worktree_manager.py) |
| 有界安全嵌套名称 | 路径穿越和超限 | [test_worktree_paths.py](../../../tests/test_worktree_paths.py)、[test_worktree_manager.py](../../../tests/test_worktree_manager.py) |
| 有界安全嵌套名称 | 祖先目录被替换为链接 | [test_worktree_paths.py](../../../tests/test_worktree_paths.py)、[test_worktree_manager.py](../../../tests/test_worktree_manager.py) |
| 有界安全嵌套名称 | 工作树相互嵌套 | [test_worktree_paths.py](../../../tests/test_worktree_paths.py)、[test_worktree_manager.py](../../../tests/test_worktree_manager.py) |
| 接受任务时冻结提交基线 | 父目录有未提交修改 | [test_worktree_runtime.py](../../../tests/test_worktree_runtime.py)、[test_worktree_paths.py](../../../tests/test_worktree_paths.py) |
| 接受任务时冻结提交基线 | 排队后父提交新版本 | [test_worktree_runtime.py](../../../tests/test_worktree_runtime.py)、[test_worktree_paths.py](../../../tests/test_worktree_paths.py) |
| 接受任务时冻结提交基线 | 排队取消 | [test_worktree_runtime.py](../../../tests/test_worktree_runtime.py)、[test_worktree_paths.py](../../../tests/test_worktree_paths.py) |
| 接受任务时冻结提交基线 | 项目子目录在基线中缺失 | [test_worktree_runtime.py](../../../tests/test_worktree_runtime.py)、[test_worktree_paths.py](../../../tests/test_worktree_paths.py) |
| 创建与纯读取快速恢复 | 完整目录快速恢复 | [test_worktree_manager.py](../../../tests/test_worktree_manager.py) |
| 创建与纯读取快速恢复 | 普通目录冒充工作树 | [test_worktree_manager.py](../../../tests/test_worktree_manager.py) |
| 创建与纯读取快速恢复 | 初始化中断 | [test_worktree_manager.py](../../../tests/test_worktree_manager.py) |
| 进入租约与并发归属 | 两个运行进入同一目录 | [test_worktree_manager.py](../../../tests/test_worktree_manager.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 进入租约与并发归属 | 子退出而父继续 | [test_worktree_manager.py](../../../tests/test_worktree_manager.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py) |
| 声明式配置与有界初始化 | 缺省配置复制 | [test_worktree_config.py](../../../tests/test_worktree_config.py)、[test_worktree_initialization.py](../../../tests/test_worktree_initialization.py)、[test_worktree_init.py](../../../tests/test_worktree_init.py) |
| 声明式配置与有界初始化 | 可选文件缺失 | [test_worktree_config.py](../../../tests/test_worktree_config.py)、[test_worktree_initialization.py](../../../tests/test_worktree_initialization.py)、[test_worktree_init.py](../../../tests/test_worktree_init.py) |
| 声明式配置与有界初始化 | 规则越界或复制超限 | [test_worktree_config.py](../../../tests/test_worktree_config.py)、[test_worktree_initialization.py](../../../tests/test_worktree_initialization.py)、[test_worktree_init.py](../../../tests/test_worktree_init.py) |
| 声明式配置与有界初始化 | 未提交普通源码不属于补齐清单 | [test_worktree_config.py](../../../tests/test_worktree_config.py)、[test_worktree_initialization.py](../../../tests/test_worktree_initialization.py)、[test_worktree_init.py](../../../tests/test_worktree_init.py) |
| 声明式配置与有界初始化 | 配置冻结与严格字段 | [test_worktree_config.py](../../../tests/test_worktree_config.py)、[test_worktree_initialization.py](../../../tests/test_worktree_initialization.py)、[test_worktree_init.py](../../../tests/test_worktree_init.py) |
| 子工作树 Git hooks 配置 | 配置子目录 hooks | [test_worktree_initialization.py](../../../tests/test_worktree_initialization.py)、[test_worktree_manager.py](../../../tests/test_worktree_manager.py) |
| 子工作树 Git hooks 配置 | Git 配置不兼容 | [test_worktree_initialization.py](../../../tests/test_worktree_initialization.py)、[test_worktree_manager.py](../../../tests/test_worktree_manager.py) |
| 共享依赖与初始化基线 | 依赖目录软链 | [test_worktree_initialization.py](../../../tests/test_worktree_initialization.py)、[test_worktree_manager.py](../../../tests/test_worktree_manager.py)、[test_file_tools.py](../../../tests/test_file_tools.py) |
| 共享依赖与初始化基线 | 初始化配置后来修改 | [test_worktree_initialization.py](../../../tests/test_worktree_initialization.py)、[test_worktree_manager.py](../../../tests/test_worktree_manager.py)、[test_file_tools.py](../../../tests/test_file_tools.py) |
| 规划和取消不能绕过生命周期保护 | 规划期间需要新建工作树 | [test_worktree_manager.py](../../../tests/test_worktree_manager.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py)、[test_worktree_cleanup.py](../../../tests/test_worktree_cleanup.py) |
| 规划和取消不能绕过生命周期保护 | 创建窗口取消 | [test_worktree_manager.py](../../../tests/test_worktree_manager.py)、[test_worktree_runtime.py](../../../tests/test_worktree_runtime.py)、[test_worktree_cleanup.py](../../../tests/test_worktree_cleanup.py) |
| 退出前收尾与证据保护 | 模型结束但异步 Hook 仍活动 | [test_worktree_runtime.py](../../../tests/test_worktree_runtime.py)、[test_worktree_hooks.py](../../../tests/test_worktree_hooks.py)、[test_worktree_evidence.py](../../../tests/test_worktree_evidence.py) |
| 退出前收尾与证据保护 | 父证据归档失败 | [test_worktree_runtime.py](../../../tests/test_worktree_runtime.py)、[test_worktree_hooks.py](../../../tests/test_worktree_hooks.py)、[test_worktree_evidence.py](../../../tests/test_worktree_evidence.py) |
| 文件和提交的默认删除保护 | 没有成果的子运行 | [test_worktree_manager.py](../../../tests/test_worktree_manager.py) |
| 文件和提交的默认删除保护 | 干净文件树但有新提交 | [test_worktree_manager.py](../../../tests/test_worktree_manager.py) |
| 文件和提交的默认删除保护 | 新增被忽略的成果文件 | [test_worktree_manager.py](../../../tests/test_worktree_manager.py) |
| 文件和提交的默认删除保护 | 检查失败或部分删除失败 | [test_worktree_manager.py](../../../tests/test_worktree_manager.py) |
| 三层过滤的会话过期清理 | 过期但有成果 | [test_worktree_cleanup.py](../../../tests/test_worktree_cleanup.py) |
| 三层过滤的会话过期清理 | 过期但仍活动 | [test_worktree_cleanup.py](../../../tests/test_worktree_cleanup.py) |
| 三层过滤的会话过期清理 | 扫描后再次进入 | [test_worktree_cleanup.py](../../../tests/test_worktree_cleanup.py) |
| 三层过滤的会话过期清理 | 会话关闭 | [test_worktree_cleanup.py](../../../tests/test_worktree_cleanup.py) |

核对场景数：106。

补充边界：评审发现的产物规则过宽、枚举预算、规划只读恢复、退出状态写入失败、取消展示与真实收尾、长正文缓存索引、主历史提交前崩溃均有本次回归。清理竞争以真实锁和可控时钟自动化验证；没有通过模型等待 30 天。
