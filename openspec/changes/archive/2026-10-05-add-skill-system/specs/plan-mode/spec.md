## MODIFIED Requirements

### Requirement: 规划工具的双重限制
规划模式 SHALL 仅向模型提供 `read_file`、`glob_files`、`search_code` 三个只读普通工具与当前激活 Skill 白名单及父范围上限的交集，并另提供系统 `load_skill` 入口；实际普通调用启动前 SHALL 再次检查当前有效范围。加载可以更新激活或运行同样受只读限制的独立对话，不改变模式、不自动执行辅助脚本。已注册但被规划模式禁止的调用 SHALL 返回 `tool_not_allowed`，SHALL NOT 启动工作进程、发送 MCP 工具执行请求或产生副作用；未注册名称 SHALL 仍返回 `unknown_tool`。`execute_command` SHALL NOT 因命令看起来只读而获准执行。全部 MCP 工具 SHALL 在规划模式禁止，Server 提供的只读提示 SHALL NOT 改变该限制。bypass、allow 规则及已有人工授权 SHALL NOT 绕过上述规划限制。规划模式允许的只读工具 SHALL 仍遵守路径边界、权限规则与当前权限模式，必要时 SHALL 在读取内容或进行目标搜索之前获得授权。

#### Scenario: 模型可见工具
- **WHEN** 规划模式发起任一次模型请求，包括恢复重试
- **THEN** 普通工具列表只包含三个只读工具与实际 Skill 限制的交集，另有 load_skill 系统入口

#### Scenario: 模型仍要求写入
- **WHEN** 规划响应包含 `write_file`、`edit_file` 或 `execute_command`
- **THEN** 每个禁止调用获得 `tool_not_allowed` 结果，工作区不因这些调用被改变；其已注册性质不触发未知工具累计

#### Scenario: 放行不能越过只读限制
- **WHEN** 规划模式为 bypass，或写入及命令调用具有 allow 规则或有效授权
- **THEN** 写入、编辑与命令仍返回 `tool_not_allowed`，不显示解除规划限制的授权选项

#### Scenario: 只读调用需要授权
- **WHEN** 规划模式中的合法读取或搜索调用在当前权限模式下需要 ask，且没有有效授权
- **THEN** 系统先等待授权，不因工具只读而直接执行；普通拒绝以工具结果交给规划循环继续处理

#### Scenario: 外部工具不能通过权限进入规划模式
- **WHEN** 规划模式收到一个已注册 MCP 工具调用，且它具有只读提示、allow 规则、已有授权或 bypass 权限模式
- **THEN** 普通工具列表仍受三个内置只读工具和 Skill 交集限制，执行入口返回 tool_not_allowed，不发送外部请求，不把已注册调用计为未知工具

#### Scenario: 规划加载独立测试技能
- **WHEN** 规划模式加载白名单含 execute_command 的独立 test
- **THEN** 加载入口可运行只读子对话，但子工具范围没有 execute_command，不通过权限批准或独立模式放开测试命令
