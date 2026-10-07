## MODIFIED Requirements

### Requirement: 统一工具契约与注册
每个工具 SHALL 提供唯一名称、用途描述、JSON Schema 参数定义和可路由的执行入口，并在登记信息中声明是否只读。内置工具与成功发现的 MCP 工具 SHALL 使用同一注册和供应商导出契约；登记信息 SHALL 只包含可序列化的工具描述和执行标识，SHALL NOT 包含 MCP 活动连接、会话或异步上下文。系统 SHALL 按名称登记和查找工具，从同一份登记信息按当前模式、激活 Skill 白名单和父范围上限筛选并导出 Anthropic Messages 与 OpenAI Chat Completions 可接受的工具列表；本地只读元信息 SHALL NOT 作为未知字段发送给供应商。重复名称 SHALL 被明确拒绝，不得覆盖原工具。`read_file`、`glob_files`、`search_code` SHALL 标为只读，其余三个核心工具 SHALL 标为非只读；未声明只读性的工具 SHALL 按非只读处理。所有 MCP 工具 SHALL 标为非只读，Server 提供的只读提示 SHALL NOT 改变该分类。系统 SHALL 另登记主进程可路由的 load_skill，在主对话始终作为系统入口导出，不受普通 Skill 白名单交集排除；子对话另受角色和全局上限限制；它 SHALL 形成串行边界，不因加载主要读取文件而进入普通只读并发组。加载仍遵守参数、包边界、适用权限及子模式约束。

系统 SHALL 另登记主进程可路由的单一 agent 系统工具，在主对话保持统一 Schema 和串行边界，角色目录不改变声明。定义式子声明启动允许工具；Fork 保留父请求声明及顺序，实际范围独立收紧。system 属性 SHALL NOT 豁免角色、规划、后台或禁止嵌套的硬上限。

#### Scenario: 枚举六个核心工具
- **WHEN** 执行模式在没有激活限制时准备向模型提供工具
- **THEN** 工具列表包含六个核心普通工具、load_skill、agent、team 及成功发现并注册的 MCP 工具的名称、描述与参数定义，两个协议中的名称和参数语义一致

#### Scenario: 重复注册
- **WHEN** 工具名称与已有登记重复
- **THEN** 登记失败并报告重名，原工具仍可按名称找到

#### Scenario: 规划模式筛选
- **WHEN** 规划模式准备向模型提供工具
- **THEN** 同一登记信息导出三个只读普通工具与实际 Skill 限制的交集，另导出 load_skill、agent 和受限 team，执行器使用相同普通范围和系统路由

#### Scenario: 新工具未声明只读
- **WHEN** 后续登记一个没有明确只读声明的工具
- **THEN** 工具按非只读处理，不能进入只读并发组或规划模式的允许列表

#### Scenario: 没有 MCP 配置时保持核心工具集
- **WHEN** 启动时没有配置 MCP Server
- **THEN** 无激活限制时执行模式提供六个核心普通工具及 load_skill、agent 和 team，原六个工具名称、参数和只读分类保持

#### Scenario: 外部只读提示不改变调度分类
- **WHEN** MCP Server 的工具元数据声明只读
- **THEN** 该工具仍按非只读登记，不进入只读并发组或规划模式工具列表

#### Scenario: 白名单为空的导出
- **WHEN** 当前有效普通范围为空
- **THEN** 两个供应商声明在主对话均仍包含 load_skill、agent 和受限 team，不再导出已被限制的普通工具

团队能力 SHALL 另提供稳定的 team 管理入口；普通主入口可创建、显式恢复或查看团队，但 SHALL NOT 获得共享任务和邮箱工具。只有真实团队身份 SHALL 声明 team_task、team_message；Lead SHALL 另声明 team_member、team_integrate。团队工具 SHALL 统一两种供应商 Schema，不随花名册变更动态生成名称。team 入口仅在其动作符合当前模式和身份时执行，团队身份下的导出另受下述身份过滤。

#### Scenario: 普通入口创建团队
- **WHEN** 普通主入口准备模型请求
- **THEN** 可见原有工具及 team 管理入口，不可见 team_task、team_message、team_member 或 team_integrate；创建成功后后续请求按 Lead 身份导出

#### Scenario: 团队成员工具
- **WHEN** 团队成员准备模型请求
- **THEN** 可见获准普通工具、受限 load_skill、team_task 和 team_message，不声明 Lead 管理、整合、普通 agent 或独立 Skill 派生能力


## ADDED Requirements

### Requirement: 团队能力声明与执行双重检查
系统 SHALL 以运行绑定的团队、成员、角色及当前模式决定团队工具范围，并在系统工具分流和每个具体动作执行前再次验证。已注册但身份不允许的工具或动作 SHALL 返回 tool_not_allowed 和 not_started=true，不视为未知工具。团队元数据、协议决定、领取及验收状态 SHALL 经可信管理入口维护，普通文件工具不能绕过身份校验改写控制状态。只读动作可以进入合法并发组，修改及身份转换动作 SHALL 保持串行调度边界。具体动作 SHALL 遵守工具权限及结果上限，不能借系统入口跳过 deny、模式或计划门禁。

#### Scenario: 普通子 Agent 伪造共享任务调用
- **WHEN** 普通 defined 或 fork 子运行返回 team_task 调用
- **THEN** 执行层返回 tool_not_allowed，不读取或修改团队任务清单

#### Scenario: 成员伪造验收
- **WHEN** 普通成员调用共享任务中的 Lead 验收动作
- **THEN** 返回未启动的身份限制，任务验收状态保持

### Requirement: coordinator 自用能力与委派能力分离
coordinator Lead SHALL 不声明或执行 write_file、edit_file、普通 agent 派生、独立 Skill 执行或非只读 MCP，保留 read_file、glob_files、search_code、受限共享 Skill 读取、execute_command 和获准团队管理／协作工具。其本人过滤 SHALL 在声明及执行层生效，不能借系统加载恢复写入。成员委派上限 SHALL 来自批准的团队能力范围及角色／Skill／权限约束，不把 Lead 本人的 coordinator 编辑禁用当作成员上限，也不越过真实策略拒绝或 plan 上限。保留 shell SHALL 继续受现有命令权限检查；产品 SHALL 说明 coordinator 是工具层分工而非全面文件系统禁写，代码编辑和冲突内容修改按分工交给成员。

#### Scenario: Lead 自己编辑
- **WHEN** coordinator Lead 伪造 edit_file 或通过独立 Skill 直接开发
- **THEN** 执行前拒绝，不因系统工具属性或后续激活扩大自用范围

#### Scenario: 队员仍能编辑
- **WHEN** coordinator Lead 派生允许编辑且已有有效许可的成员
- **THEN** 成员可以编辑自己的目录，不继承 Lead 本人的 coordinator 编辑禁用，真实权限上限继续有效
