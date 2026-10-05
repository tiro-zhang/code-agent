## Purpose

定义 MewCode 的命名子 Agent 角色文件、来源覆盖、模型解析与发现行为，使主 Agent 能通过稳定工具选择具有固定职责和工具限制的角色，并能追踪本次运行实际使用的定义来源与快照。

## ADDED Requirements

### Requirement: 有界 Markdown 角色定义
角色 SHALL 使用 UTF-8 Markdown、YAML frontmatter 和非空正文，入口不超过 64 KiB；正文 SHALL 作为定义式子 Agent 全生命周期的固定系统指令。frontmatter SHALL 接受 name、description、allowed-tools、disallowed-tools、model、max-iterations、permission-mode，拒绝重复键、未知字段、不安全 YAML 及非法类型。name 和单行非空 description SHALL 必填，name SHALL 匹配 `[a-z][a-z0-9_-]{0,63}`。工具列表 SHALL 使用精确名称，省略白名单表示不增加限制，空列表表示不允许工具；黑名单优先。model SHALL 接受 inherit、haiku、sonnet、opus；permission-mode SHALL 接受 inherit、strict、default、bypass；两者缺省 inherit。max-iterations SHALL 为正整数，缺省 20，布尔值 SHALL NOT 当作整数。

#### Scenario: 非法入口
- **WHEN** 角色文件含重复键、非法类型、空正文或超过文件预算
- **THEN** 诊断来源并跳过该项，不部分加载或默认开放工具

#### Scenario: 空白名单与黑名单
- **WHEN** 一个角色白名单为空，另一个角色同时白名单和黑名单包含 read_file
- **THEN** 前者没有可执行工具，后者不能执行 read_file，系统入口不豁免角色限制

### Requirement: 四来源的完整同名覆盖
系统 SHALL 从项目 `.mewcode/agents/`、用户 `~/.mewcode/agents/`、包内及配置 agent_plugin_dirs 提供的插件角色目录发现直接 Markdown 文件，优先级依次降低。缺失目录 SHALL 视为空；不可读、符号链接、非普通文件和非法单项 SHALL 诊断并跳过，被跳过项不参与覆盖。同层有效同名项 SHALL 使候选目录失败，不按遍历顺序选取；高层有效项 SHALL 整体替换低层定义，不拼接正文或字段。插件目录配置 SHALL 为 JSON 路径列表，缺省为空，发现 SHALL NOT 执行插件代码。

#### Scenario: 四层同名
- **WHEN** 四层均提供合法 general
- **THEN** 只选择项目角色，其完整配置、正文和来源一致

#### Scenario: 项目坏项与同层冲突
- **WHEN** 项目角色非法而用户同名合法，或同一层有两个有效同名项
- **THEN** 前一情况诊断后使用用户定义，后一情况拒绝目录候选并报告冲突来源

### Requirement: 有效目录校验和冻结
系统 SHALL 在工具发现完成后校验有效角色引用的全部工具名称；未知名称 SHALL 明确阻止启动或候选更新发布，不能静默扩大范围。已注册但受模式或全局禁止的名称 SHALL NOT 被当作未知工具。角色目录 SHALL 在非空用户输入边界完整更新；更新失败保留上一完整目录。子任务注册时 SHALL 冻结角色、来源、父范围和配置，中途修改文件 SHALL NOT 改变已接受任务。

#### Scenario: 未调用角色拼错工具
- **WHEN** 有效角色引用 read_flie，用户尚未调用它
- **THEN** 工具发现后的目录校验明确失败并报告角色与来源

#### Scenario: 排队期间修改定义
- **WHEN** 子任务已接受并排队，用户随后修改角色正文或白名单
- **THEN** 该子任务仍使用注册时快照，后续成功更新目录的新任务使用新定义

### Requirement: 按名称说明发现及内置样板
主对话 SHALL 获得有效角色名称与说明，SHALL NOT 自动获得全部角色正文或通过角色数量改变工具 Schema。系统 SHALL 提供可覆盖的内置 explore 和 general，前者仅允许三个核心读工具，后者不附加白名单，两者默认继承模型与权限模式。源码包及 wheel SHALL 均可发现内置角色。角色名称 SHALL 与 Skill 名称分属独立选择空间，不自动生成同名斜杠控制命令。

#### Scenario: 主请求能力目录
- **WHEN** 未使用角色正文含独有标记
- **THEN** 主请求只发现角色名和说明，不含正文标记；agent 工具 Schema 与角色增删前一致

#### Scenario: 安装产物发现角色
- **WHEN** 在无项目或用户角色的临时目录从 wheel 启动
- **THEN** 可发现 explore 和 general，项目覆盖仍按四层规则生效

### Requirement: 显式模型别名解析
agent_models 配置 SHALL 是 haiku、sonnet、opus 到同一模型服务实际 model、context_window 及可选 max_output_tokens 的 JSON 映射，拒绝未知键、重复键和非法预算。inherit SHALL 使用父服务配置；指定别名无可用配置时 SHALL 在子模型请求前返回 agent_model_unavailable，不静默回退或猜测实际型号。窗口预算 SHALL 满足现有上下文余量约束。Fork SHALL 使用父实际模型和请求配置，不接受角色或模型覆盖。

#### Scenario: 兼容协议没有别名配置
- **WHEN** 角色指定 haiku，当前服务未配置该别名
- **THEN** 委派返回明确未启动错误，不因协议为 anthropic 而自动选择 Claude 型号

#### Scenario: 配置独立模型
- **WHEN** sonnet 映射和预算合法且角色选择它
- **THEN** 子请求采用该模型及预算，父模型配置不被修改
