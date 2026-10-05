# 规格场景与本次证据映射

按变更每个 Scenario 列出实现入口和本次测试来源。所有测试属于本次 1263 项完整回归；真实终端范围及未验证服务见 README.md，不用严格语法校验代替功能验证。

## agent-definitions

实现：src/mewcode/ 下的 agents/definitions.py、config.py。

本次验证：[test_agent_definitions.py](../../../tests/test_agent_definitions.py)、[test_agent_config.py](../../../tests/test_agent_config.py)、[test_agent_lifecycle.py](../../../tests/test_agent_lifecycle.py)、[test_agent_service.py](../../../tests/test_agent_service.py)。

| 需求 | 场景 | 核对 |
| --- | --- | --- |
| 有界 Markdown 角色定义 | 非法入口 | 实现与对应测试合同一致；真实范围见报告 |
| 有界 Markdown 角色定义 | 空白名单与黑名单 | 实现与对应测试合同一致；真实范围见报告 |
| 四来源的完整同名覆盖 | 四层同名 | 实现与对应测试合同一致；真实范围见报告 |
| 四来源的完整同名覆盖 | 项目坏项与同层冲突 | 实现与对应测试合同一致；真实范围见报告 |
| 有效目录校验和冻结 | 未调用角色拼错工具 | 实现与对应测试合同一致；真实范围见报告 |
| 有效目录校验和冻结 | 排队期间修改定义 | 实现与对应测试合同一致；真实范围见报告 |
| 按名称说明发现及内置样板 | 主请求能力目录 | 实现与对应测试合同一致；真实范围见报告 |
| 按名称说明发现及内置样板 | 安装产物发现角色 | 实现与对应测试合同一致；真实范围见报告 |
| 显式模型别名解析 | 兼容协议没有别名配置 | 实现与对应测试合同一致；真实范围见报告 |
| 显式模型别名解析 | 配置独立模型 | 实现与对应测试合同一致；真实范围见报告 |

## agent-loop

实现：src/mewcode/ 下的 agent.py、session.py、skills/budget.py 与 invocation.py。

本次验证：[test_agent_loop.py](../../../tests/test_agent_loop.py)、[test_context_agent.py](../../../tests/test_context_agent.py)、[test_context_manual.py](../../../tests/test_context_manual.py)、[test_skills_isolated.py](../../../tests/test_skills_isolated.py)、[test_agent_lifecycle.py](../../../tests/test_agent_lifecycle.py)、[test_parent_tasks.py](../../../tests/test_parent_tasks.py)、[test_session_persistence.py](../../../tests/test_session_persistence.py)、[test_memory_integration.py](../../../tests/test_memory_integration.py)。

| 需求 | 场景 | 核对 |
| --- | --- | --- |
| 统一请求预算 | 单次响应含多个工具 | 实现与对应测试合同一致；真实范围见报告 |
| 统一请求预算 | 最后一次请求返回工具 | 实现与对应测试合同一致；真实范围见报告 |
| 统一请求预算 | 最后一次请求直接答复 | 实现与对应测试合同一致；真实范围见报告 |
| 统一请求预算 | 上下文恢复消耗预算 | 实现与对应测试合同一致；真实范围见报告 |
| 统一请求预算 | 新任务重新计数 | 实现与对应测试合同一致；真实范围见报告 |
| 统一请求预算 | 自动摘要也消耗预算 | 实现与对应测试合同一致；真实范围见报告 |
| 统一请求预算 | 摘要用完剩余预算 | 实现与对应测试合同一致；真实范围见报告 |
| 统一请求预算 | 手动摘要独立计数 | 实现与对应测试合同一致；真实范围见报告 |
| 统一请求预算 | 恢复或记忆维护不消耗工作额度 | 实现与对应测试合同一致；真实范围见报告 |
| 统一请求预算 | 子运行消耗剩余次数 | 实现与对应测试合同一致；真实范围见报告 |
| 统一请求预算 | 没有剩余请求时加载独立能力 | 实现与对应测试合同一致；真实范围见报告 |
| 明确终止结果 | 模型结束 | 实现与对应测试合同一致；真实范围见报告 |
| 明确终止结果 | 用户取消 | 实现与对应测试合同一致；真实范围见报告 |
| 明确终止结果 | 请求或流式协议失败 | 实现与对应测试合同一致；真实范围见报告 |
| 明确终止结果 | 上下文不能继续 | 实现与对应测试合同一致；真实范围见报告 |
| 明确终止结果 | 持久化确定性失败 | 实现与对应测试合同一致；真实范围见报告 |
| 自然结束挂钩与独立维护事件 | 替换终端消费者 | 实现与对应测试合同一致；真实范围见报告 |
| 自然结束挂钩与独立维护事件 | 下一任务取消 | 实现与对应测试合同一致；真实范围见报告 |

## background-tasks

实现：src/mewcode/ 下的 tasks/ 与 session.py、app.py。

本次验证：[test_background_tasks.py](../../../tests/test_background_tasks.py)、[test_parent_tasks.py](../../../tests/test_parent_tasks.py)、[test_agent_service.py](../../../tests/test_agent_service.py)、[test_agent_lifecycle.py](../../../tests/test_agent_lifecycle.py)、[test_agent_terminal.py](../../../tests/test_agent_terminal.py)、[test_plain_task_drafts.py](../../../tests/test_plain_task_drafts.py)。

| 需求 | 场景 | 核对 |
| --- | --- | --- |
| 会话内有界任务登记 | 容量耗尽 | 实现与对应测试合同一致；真实范围见报告 |
| 同一执行的三种后台入口 | 等待超过期限 | 实现与对应测试合同一致；真实范围见报告 |
| 同一执行的三种后台入口 | 显式或手动后台 | 实现与对应测试合同一致；真实范围见报告 |
| 原子回流通道选择 | 完成与切换同时发生 | 实现与对应测试合同一致；真实范围见报告 |
| 结果有界保存及可观察通知 | 摘要超限 | 实现与对应测试合同一致；真实范围见报告 |
| 请求边界收件箱与一次投递 | 模型正在流式回答 | 实现与对应测试合同一致；真实范围见报告 |
| 请求边界收件箱与一次投递 | 准备后取消或重复通知 | 实现与对应测试合同一致；真实范围见报告 |
| 空闲自动唤醒和输入优先 | 空闲结果成批到达 | 实现与对应测试合同一致；真实范围见报告 |
| 空闲自动唤醒和输入优先 | 新输入与通知竞争 | 实现与对应测试合同一致；真实范围见报告 |
| 自动接续保留父预算 | 剩余一次请求 | 实现与对应测试合同一致；真实范围见报告 |
| 自动接续保留父预算 | 父已用完预算 | 实现与对应测试合同一致；真实范围见报告 |
| 父主动取消的完整传播 | 取消父与子完成竞争 | 实现与对应测试合同一致；真实范围见报告 |
| 父主动取消的完整传播 | 两个父的后台任务 | 实现与对应测试合同一致；真实范围见报告 |
| 重置退出与共享资源收尾 | reset 后迟到结果 | 实现与对应测试合同一致；真实范围见报告 |
| 重置退出与共享资源收尾 | 应用退出有活动任务 | 实现与对应测试合同一致；真实范围见报告 |
| 不恢复后台执行 | 有后台凭据的存档恢复 | 实现与对应测试合同一致；真实范围见报告 |

## interactive-chat

实现：src/mewcode/ 下的 app.py、terminal/、permissions/terminal.py。

本次验证：[test_agent_terminal.py](../../../tests/test_agent_terminal.py)、[test_plain_task_drafts.py](../../../tests/test_plain_task_drafts.py)、[test_terminal_input.py](../../../tests/test_terminal_input.py)、[test_terminal_controller.py](../../../tests/test_terminal_controller.py)、[test_terminal_projection.py](../../../tests/test_terminal_projection.py)、[test_permission_review.py](../../../tests/test_permission_review.py)、[test_permission_app.py](../../../tests/test_permission_app.py)。

| 需求 | 场景 | 核对 |
| --- | --- | --- |
| 自主任务交互 | 多步操作 | 实现与对应测试合同一致；真实范围见报告 |
| 自主任务交互 | 上限停止 | 实现与对应测试合同一致；真实范围见报告 |
| 自主任务交互 | 拒绝后自主继续 | 实现与对应测试合同一致；真实范围见报告 |
| 自主任务交互 | 多项授权串行输入 | 实现与对应测试合同一致；真实范围见报告 |
| 后台状态和手动转换 | 手动切换 | 实现与对应测试合同一致；真实范围见报告 |
| 后台状态和手动转换 | 未提交草稿 | 实现与对应测试合同一致；真实范围见报告 |

## lifecycle-hooks

实现：src/mewcode/ 下的 hooks/ 与 session.py。

本次验证：[test_hook_scopes.py](../../../tests/test_hook_scopes.py)、[test_hook_prompt.py](../../../tests/test_hook_prompt.py)、[test_hook_runtime.py](../../../tests/test_hook_runtime.py)、[test_hook_actions.py](../../../tests/test_hook_actions.py)、[test_hook_lifecycle.py](../../../tests/test_hook_lifecycle.py)、[test_hook_skills.py](../../../tests/test_hook_skills.py)、[test_subagent_runtime.py](../../../tests/test_subagent_runtime.py)。

| 需求 | 场景 | 核对 |
| --- | --- | --- |
| 下一工作请求的提示词注入 | 注入使请求超过预算 | 实现与对应测试合同一致；真实范围见报告 |
| 下一工作请求的提示词注入 | 摘要不抢先消耗注入 | 实现与对应测试合同一致；真实范围见报告 |
| 下一工作请求的提示词注入 | 发送前取消 | 实现与对应测试合同一致；真实范围见报告 |
| 会话内最多尝试一次 | 并发触发同一规则 | 实现与对应测试合同一致；真实范围见报告 |
| 会话内最多尝试一次 | 重置与重启 | 实现与对应测试合同一致；真实范围见报告 |
| 子运行 Hook 权限与生命周期 | 提示词不串扰 | 实现与对应测试合同一致；真实范围见报告 |
| 子运行 Hook 权限与生命周期 | Hook 缺少批准 | 实现与对应测试合同一致；真实范围见报告 |
| 子运行 Hook 权限与生命周期 | 子结束 | 实现与对应测试合同一致；真实范围见报告 |

## plan-mode

实现：src/mewcode/ 下的 session.py、agents/runtime.py、tools/executor.py。

本次验证：[test_plan_mode.py](../../../tests/test_plan_mode.py)、[test_parent_tasks.py](../../../tests/test_parent_tasks.py)、[test_skills_isolated.py](../../../tests/test_skills_isolated.py)、[test_mcp_permissions.py](../../../tests/test_mcp_permissions.py)、[test_subagent_tool_guard.py](../../../tests/test_subagent_tool_guard.py)。

| 需求 | 场景 | 核对 |
| --- | --- | --- |
| 规划工具的双重限制 | 模型可见工具 | 实现与对应测试合同一致；真实范围见报告 |
| 规划工具的双重限制 | 模型仍要求写入 | 实现与对应测试合同一致；真实范围见报告 |
| 规划工具的双重限制 | 放行不能越过只读限制 | 实现与对应测试合同一致；真实范围见报告 |
| 规划工具的双重限制 | 只读调用需要授权 | 实现与对应测试合同一致；真实范围见报告 |
| 规划工具的双重限制 | 外部工具不能通过权限进入规划模式 | 实现与对应测试合同一致；真实范围见报告 |
| 规划工具的双重限制 | 规划加载独立测试技能 | 实现与对应测试合同一致；真实范围见报告 |
| 规划工具的双重限制 | 规划中的新子 Agent 无审批 | 实现与对应测试合同一致；真实范围见报告 |
| 模式切换清理后台委派 | 后台执行期间规划 | 实现与对应测试合同一致；真实范围见报告 |
| 模式切换清理后台委派 | 返回执行 | 实现与对应测试合同一致；真实范围见报告 |

## prompt-caching

实现：src/mewcode/ 下的 providers/anthropic.py、openai.py、agents/request.py。

本次验证：[test_agent_cache.py](../../../tests/test_agent_cache.py)、[test_cache_usage.py](../../../tests/test_cache_usage.py)、[test_tool_providers.py](../../../tests/test_tool_providers.py)、[test_agent_request_snapshot.py](../../../tests/test_agent_request_snapshot.py)。

| 需求 | 场景 | 核对 |
| --- | --- | --- |
| 按服务能力保持可缓存前缀 | DeepSeek 同模式连续请求 | 实现与对应测试合同一致；真实范围见报告 |
| 按服务能力保持可缓存前缀 | 官方 Claude 固定内容 | 实现与对应测试合同一致；真实范围见报告 |
| 按服务能力保持可缓存前缀 | 第三方兼容服务 | 实现与对应测试合同一致；真实范围见报告 |
| Fork 缓存真实验收 | 真实复用 | 实现与对应测试合同一致；真实范围见报告 |
| Fork 缓存真实验收 | 字段缺失 | 实现与对应测试合同一致；真实范围见报告 |

## slash-commands

实现：src/mewcode/ 下的 commands/ 与 session.py。

本次验证：[test_agent_commands.py](../../../tests/test_agent_commands.py)、[test_terminal_commands.py](../../../tests/test_terminal_commands.py)、[test_skills_commands.py](../../../tests/test_skills_commands.py)、[test_skills_session.py](../../../tests/test_skills_session.py)、[test_parent_tasks.py](../../../tests/test_parent_tasks.py)。

| 需求 | 场景 | 核对 |
| --- | --- | --- |
| 本地 Skill 管理与对话重置 | 停用恢复工具范围 | 实现与对应测试合同一致；真实范围见报告 |
| 本地 Skill 管理与对话重置 | 清屏和重置区别 | 实现与对应测试合同一致；真实范围见报告 |
| 本地 Skill 管理与对话重置 | 重置非法参数 | 实现与对应测试合同一致；真实范围见报告 |
| 本地角色与任务管理 | 本地查看 | 实现与对应测试合同一致；真实范围见报告 |
| 本地角色与任务管理 | 取消某父 | 实现与对应测试合同一致；真实范围见报告 |
| 本地角色与任务管理 | 非法参数与命令冲突 | 实现与对应测试合同一致；真实范围见报告 |

## subagent-runtime

实现：src/mewcode/ 下的 agents/runtime.py、service.py、tool.py、tasks/manager.py。

本次验证：[test_subagent_runtime.py](../../../tests/test_subagent_runtime.py)、[test_subagent_effects.py](../../../tests/test_subagent_effects.py)、[test_agent_tool.py](../../../tests/test_agent_tool.py)、[test_agent_service.py](../../../tests/test_agent_service.py)、[test_provider_sharing.py](../../../tests/test_provider_sharing.py)。

| 需求 | 场景 | 核对 |
| --- | --- | --- |
| 统一类型分流入口 | 类型参数非法 | 实现与对应测试合同一致；真实范围见报告 |
| 统一类型分流入口 | 同一入口两种运行 | 实现与对应测试合同一致；真实范围见报告 |
| 定义式空历史和固定角色 | 父历史和激活不泄漏 | 实现与对应测试合同一致；真实范围见报告 |
| Fork 使用合法原始请求快照 | 委派发生在多工具响应 | 实现与对应测试合同一致；真实范围见报告 |
| Fork 使用合法原始请求快照 | 父继续压缩历史 | 实现与对应测试合同一致；真实范围见报告 |
| 独立状态和共享资源所有权 | 两个子运行同时活动 | 实现与对应测试合同一致；真实范围见报告 |
| 多层工具过滤与禁止嵌套 | Fork 请求嵌套 Agent | 实现与对应测试合同一致；真实范围见报告 |
| 多层工具过滤与禁止嵌套 | 系统入口旁路 | 实现与对应测试合同一致；真实范围见报告 |
| 后台允许获准的副作用工具 | 有批准的后台编辑 | 实现与对应测试合同一致；真实范围见报告 |
| 后台允许获准的副作用工具 | MCP 未纳入后台 | 实现与对应测试合同一致；真实范围见报告 |
| 有限非交互跑到底 | 需要新批准 | 实现与对应测试合同一致；真实范围见报告 |
| 有限非交互跑到底 | 子用完请求额度 | 实现与对应测试合同一致；真实范围见报告 |
| 有来源最终结果及真实完成语义 | 最终答复报告测试失败 | 实现与对应测试合同一致；真实范围见报告 |
| 有来源最终结果及真实完成语义 | 副作用后取消 | 实现与对应测试合同一致；真实范围见报告 |

## tool-permissions

实现：src/mewcode/ 下的 permissions/ 与 tools/executor.py。

本次验证：[test_agent_permissions.py](../../../tests/test_agent_permissions.py)、[test_subagent_permissions.py](../../../tests/test_subagent_permissions.py)、[test_permission_rules.py](../../../tests/test_permission_rules.py)、[test_permission_config.py](../../../tests/test_permission_config.py)、[test_permission_runtime.py](../../../tests/test_permission_runtime.py)、[test_permission_search.py](../../../tests/test_permission_search.py)、[test_mcp_permissions.py](../../../tests/test_mcp_permissions.py)、[test_permission_loop.py](../../../tests/test_permission_loop.py)。

| 需求 | 场景 | 核对 |
| --- | --- | --- |
| 明确的规则格式与工具匹配对象 | 命令里的字面通配符 | 实现与对应测试合同一致；真实范围见报告 |
| 明确的规则格式与工具匹配对象 | 符号链接别名命中真实目标规则 | 实现与对应测试合同一致；真实范围见报告 |
| 明确的规则格式与工具匹配对象 | 路径片段与递归匹配 | 实现与对应测试合同一致；真实范围见报告 |
| 明确的规则格式与工具匹配对象 | 不同读取工具有独立规则 | 实现与对应测试合同一致；真实范围见报告 |
| 明确的规则格式与工具匹配对象 | 对象字段顺序不改变规则匹配 | 实现与对应测试合同一致；真实范围见报告 |
| 明确的规则格式与工具匹配对象 | Server 离线时加载规则 | 实现与对应测试合同一致；真实范围见报告 |
| 明确的规则格式与工具匹配对象 | 同名原始工具不共享规则 | 实现与对应测试合同一致；真实范围见报告 |
| 拒绝可供模型调整且不伪装为取消 | 被拒后选择获准策略 | 实现与对应测试合同一致；真实范围见报告 |
| 拒绝可供模型调整且不伪装为取消 | 无人工交互通道 | 实现与对应测试合同一致；真实范围见报告 |
| 拒绝可供模型调整且不伪装为取消 | 外部调用拒绝后继续任务 | 实现与对应测试合同一致；真实范围见报告 |
| 子授权快照与权限模式上限 | 副本独立 | 实现与对应测试合同一致；真实范围见报告 |
| 子授权快照与权限模式上限 | 角色只能收紧 | 实现与对应测试合同一致；真实范围见报告 |
| 子授权快照与权限模式上限 | 最新存储复查 | 实现与对应测试合同一致；真实范围见报告 |

## tool-system

实现：src/mewcode/ 下的 tools/、agents/tool.py 与 skills/runner.py。

本次验证：[test_tool_registry.py](../../../tests/test_tool_registry.py)、[test_tool_providers.py](../../../tests/test_tool_providers.py)、[test_tool_execution.py](../../../tests/test_tool_execution.py)、[test_tool_scheduler.py](../../../tests/test_tool_scheduler.py)、[test_permission_execution.py](../../../tests/test_permission_execution.py)、[test_mcp_integration.py](../../../tests/test_mcp_integration.py)、[test_skills_tools.py](../../../tests/test_skills_tools.py)、[test_skills_isolated.py](../../../tests/test_skills_isolated.py)、[test_subagent_tool_guard.py](../../../tests/test_subagent_tool_guard.py)、[test_subagent_runtime.py](../../../tests/test_subagent_runtime.py)、[test_background_tasks.py](../../../tests/test_background_tasks.py)。

| 需求 | 场景 | 核对 |
| --- | --- | --- |
| 统一工具契约与注册 | 枚举六个核心工具 | 实现与对应测试合同一致；真实范围见报告 |
| 统一工具契约与注册 | 重复注册 | 实现与对应测试合同一致；真实范围见报告 |
| 统一工具契约与注册 | 规划模式筛选 | 实现与对应测试合同一致；真实范围见报告 |
| 统一工具契约与注册 | 新工具未声明只读 | 实现与对应测试合同一致；真实范围见报告 |
| 统一工具契约与注册 | 没有 MCP 配置时保持核心工具集 | 实现与对应测试合同一致；真实范围见报告 |
| 统一工具契约与注册 | 外部只读提示不改变调度分类 | 实现与对应测试合同一致；真实范围见报告 |
| 统一工具契约与注册 | 白名单为空的导出 | 实现与对应测试合同一致；真实范围见报告 |
| 执行前校验 | 未知工具 | 实现与对应测试合同一致；真实范围见报告 |
| 执行前校验 | 参数非法 | 实现与对应测试合同一致；真实范围见报告 |
| 执行前校验 | 禁止工具绕过模型列表 | 实现与对应测试合同一致；真实范围见报告 |
| 执行前校验 | 授权之前不得启动 | 实现与对应测试合同一致；真实范围见报告 |
| 执行前校验 | 有效授权后规则变化 | 实现与对应测试合同一致；真实范围见报告 |
| 执行前校验 | 目标路径在等待期间变化 | 实现与对应测试合同一致；真实范围见报告 |
| 执行前校验 | 外部调用批准前不发送请求 | 实现与对应测试合同一致；真实范围见报告 |
| 执行前校验 | 规划模式伪造外部调用 | 实现与对应测试合同一致；真实范围见报告 |
| 执行前校验 | 加载后同批工具被禁止 | 实现与对应测试合同一致；真实范围见报告 |
| 执行前校验 | 系统加载也有权限边界 | 实现与对应测试合同一致；真实范围见报告 |
| 执行前校验 | Fork 稳定声明仍不能委派 | 实现与对应测试合同一致；真实范围见报告 |
| 超时与取消 | 文件或搜索操作卡住 | 实现与对应测试合同一致；真实范围见报告 |
| 超时与取消 | 命令及子进程超时 | 实现与对应测试合同一致；真实范围见报告 |
| 超时与取消 | 用户取消执行 | 实现与对应测试合同一致；真实范围见报告 |
| 超时与取消 | 取消清理期间收到结果 | 实现与对应测试合同一致；真实范围见报告 |
| 超时与取消 | 授权等待超过执行上限 | 实现与对应测试合同一致；真实范围见报告 |
| 超时与取消 | 等待授权时取消 | 实现与对应测试合同一致；真实范围见报告 |
| 超时与取消 | 外部调用超过期限 | 实现与对应测试合同一致；真实范围见报告 |
| 超时与取消 | 取消外部调用时结果已完成 | 实现与对应测试合同一致；真实范围见报告 |
| 超时与取消 | 独立任务超过普通工具期限 | 实现与对应测试合同一致；真实范围见报告 |
| 超时与取消 | Agent 等待软期限到期 | 实现与对应测试合同一致；真实范围见报告 |

共 133 个场景。Fork 真实缓存另见 cache-evidence.json；容量、竞争及异常注入以自动化为依据，官方 Claude 和真实外部 MCP 未冒充实机通过。
