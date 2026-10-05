## MODIFIED Requirements

### Requirement: 明确的规则格式与工具匹配对象

每条规则 SHALL 包含 effect、rule 和 match；effect 仅接受 allow、ask、deny，match 仅接受 exact、glob，rule 使用 `工具名(模式)`。系统 SHALL 将 Bash 规范化为 execute_command 的配置别名，保留现有六个模型 API 工具名，并接受符合 mcp-tools 命名契约的稳定 MCP 工具别名。既非内置名称又非语法合法 MCP 别名的未知工具名、未知字段、非法表达式或缺少匹配方式 SHALL 被报告为配置错误。语法合法但尚未发现或 Server 离线的 MCP 别名 SHALL 允许保存在规则中，SHALL NOT 仅因当前未注册而使权限配置无效；实际调用 SHALL 仍要求名称已注册。exact SHALL 按整个匹配对象区分大小写比较，glob SHALL 匹配整个对象而不是任意子串。

文件与搜索工具的匹配对象 SHALL 是解析真实路径后的项目相对路径，统一使用 `/` 分隔；路径 glob 的 `*`、`?`、字符集合仅在一个路径片段内匹配，`**` SHALL 表示零个或多个路径片段。search_code 的内容正则 SHALL NOT 被当成权限路径。Bash 的 glob SHALL 使用字符串 glob 语义，其 allow 适用范围另受 shell 结构限制。规则 SHALL 仅作用于指定工具，不自动扩展到具有相似用途的其他工具。MCP 规则的工具名 SHALL 使用稳定工具别名，匹配对象 SHALL 是完整有效参数对象的规范 JSON 字符串；exact SHALL 比较整个规范字符串，glob SHALL 使用匹配整个字符串的字符串 glob 语义，SHALL NOT 使用文件路径 glob 语义或对单个参数另行拆分放行。MCP 规则 SHALL NOT 用原始工具名跨 Server 匹配。规范 JSON SHALL 递归按 Unicode 码点排序对象键、移除无意义空白、保留非 ASCII 字符、数组顺序及 JSON 值类型，并拒绝非有限数值；同一规范 SHALL 用于规则匹配及批准记录，SHALL NOT 使用配置凭据或连接参数替代工具调用参数。

权限配置 SHALL 接受 agent 内置系统工具，其匹配对象为完整有效参数的规范 JSON；exact 必须解析完整对象，glob 匹配整个规范字符串。批准 SHALL 绑定真实工作根、工具名及精确规范参数，agent 启动批准 SHALL NOT 授予子普通工具权限。其他工具的既有匹配对象与规则优先级保持。

#### Scenario: 命令里的字面通配符
- **WHEN** exact 规则的模式包含字面 `*`
- **THEN** 该字符按字面匹配，不因其出现而把规则推断成 glob

#### Scenario: 符号链接别名命中真实目标规则
- **WHEN** 文件工具通过项目内链接访问 src/private.txt，真实目标命中 deny
- **THEN** 系统按真实目标拒绝，不能借助链接路径名称绕过规则

#### Scenario: 路径片段与递归匹配
- **WHEN** 路径模式分别为 src/*.py 和 src/**/*.py
- **THEN** 前者不匹配 src/pkg/a.py，后者匹配 src/a.py 与 src/pkg/a.py

#### Scenario: 不同读取工具有独立规则
- **WHEN** 只有 read_file(secrets/**) 的 deny，search_code 没有对应命中规则
- **THEN** search_code 按自身规则及权限模式判定，不声称 read_file 规则禁止了所有读取通道

#### Scenario: 对象字段顺序不改变规则匹配
- **WHEN** 同一 MCP 工具的两次参数仅在对象键的输入顺序或 JSON 空白上不同
- **THEN** 两次调用使用相同规范 JSON 匹配对象，命中同一 exact 规则；数组顺序或实际参数值变化仍重新匹配

#### Scenario: Server 离线时加载规则
- **WHEN** 权限文件包含语法合法的稳定 MCP 别名规则，但本次启动未成功发现该工具
- **THEN** 权限配置仍可加载，内置工具不被阻止；模型调用未注册的该别名时返回 unknown_tool，不仅凭规则存在就执行

#### Scenario: 同名原始工具不共享规则
- **WHEN** 两个 Server 均提供名为 search 的原始工具，只有其中一个稳定别名配置 allow
- **THEN** 该规则仅匹配指定别名，另一个 Server 的工具按自身规则与模式判断

### Requirement: 拒绝可供模型调整且不伪装为取消

对于作用于整次调用的拒绝，黑名单、路径限制、规则 deny、用户拒绝或缺少可用授权通道 SHALL 生成可序列化的失败工具结果，包含稳定错误码、中文原因及适用的 not_started 标记；明确策略拒绝 SHALL 使用 permission_denied；子 Agent 缺少批准 SHALL 使用 approval_required，保留既有路径／规划模式错误码的既有语义。拒绝 SHALL NOT 设置整轮取消标记，不取消同批其他已获准工具；模型 SHALL 在剩余请求预算内收到结果并有机会调整。结果 SHALL NOT 建议模型换一种写法绕过同一禁止操作。shell 解析失败与配置失败 SHALL 同样通过普通工具错误通道返回。搜索中的候选级 deny、人工拒绝或缺少授权通道 SHALL 仅排除对应候选，按 core-tools 的部分成功合同报告 permission_limited 与 skipped_files，即使全部候选被排除也不转换为整次失败；搜索调用级参数、配置或权限检查错误仍 SHALL 返回失败。

子 Agent 前后台均 SHALL 不请求人工输入，缺少批准不挂起任务或取消同批操作；搜索候选级缺少许可仍排除候选并报告受限范围，不转换为整次失败。

#### Scenario: 被拒后选择获准策略
- **WHEN** 模型请求的工具被拒绝，随后改用一个符合权限的操作
- **THEN** 原拒绝及后续真实结果都与调用配对进入历史，任务可以正常完成

#### Scenario: 无人工交互通道
- **WHEN** 调用需要授权，但运行环境没有可用的人工决策通道且没有有效批准记录
- **THEN** 单文件、shell 或 MCP 调用返回明确拒绝；搜索排除需要该授权的候选并报告受限范围；均不默认批准、不无限等待，也不把其他预先输入的任务当作批准

#### Scenario: 外部调用拒绝后继续任务
- **WHEN** 用户拒绝一个 MCP 调用，其他调用仍获准且任务未收到本地取消信号
- **THEN** 该外部调用返回 permission_denied 和 not_started=true，不发送执行请求；模型在剩余预算内收到结果并可调整，普通拒绝不取消本轮

## ADDED Requirements

### Requirement: 子授权快照与权限模式上限
子 Agent 注册时 SHALL 复制父已有本会话批准并独立追踪权限，本次批准不复制，后续父本会话批准或撤销不修改子副本。子 SHALL NOT 自动写入新批准；规则与永久批准每次执行重新读取，明确拒绝和硬限制始终优先。角色模式 SHALL 默认为 inherit，按 strict > default > bypass 取父启动模式与角色模式中较严格者，不扩大工具范围。

#### Scenario: 副本独立
- **WHEN** 父在子注册后新增或撤销本会话批准
- **THEN** 子副本保持注册时记录，父及兄弟没有共享可变批准对象，用户可单独取消子运行

#### Scenario: 角色只能收紧
- **WHEN** 父 default 而角色 bypass，或父 bypass 而角色 strict
- **THEN** 前者使用 default，后者 strict，有效批准可满足要求但明确拒绝不能被放开

#### Scenario: 最新存储复查
- **WHEN** 最新规则新增 deny，或子依赖的唯一永久批准已撤销
- **THEN** 前者明确拒绝，后者在需批准时返回 approval_required，不使用过期存储快照
