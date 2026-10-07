# tool-system Specification

## Purpose

定义模型可发现和调用的本地工具契约，使工具使用统一的参数描述与结果格式，并在参数错误、执行失败、超时或输出过多时给出可处理的结果，为终端会话调用工具提供稳定边界。

## Requirements

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

### Requirement: 执行前校验
系统 SHALL 在执行前按名称查找工具、检查当前模式、最新激活 Skill 和父上限共同决定的允许范围、解析完整 JSON 参数并依据对应 Schema 校验必填字段、类型、取值范围及未知字段；参数 SHALL 是对象。未注册名称 SHALL 返回 `unknown_tool`，已注册但被当前有效普通范围禁止的名称 SHALL 返回 `tool_not_allowed`，非法参数 SHALL 返回 `invalid_arguments`；这些错误 SHALL 为结构化结果，且 SHALL NOT 启动工具或产生文件改动。通过这些校验的调用 SHALL 在实际启动前完成适用的黑名单、路径边界、权限规则、权限模式及授权检查。系统 SHALL 在执行前重新检查规则与目标真实路径；已有授权 SHALL NOT 绕过新的拒绝条件。整次调用的权限拒绝 SHALL 返回 `permission_denied` 并明确 `not_started=true`；搜索候选级权限拒绝 SHALL 排除对应候选并按部分成功合同报告受限范围，而非使整次搜索失败；shell 结构检查无法完成 SHALL 返回 `permission_check_failed`，权限配置无效 SHALL 返回 `permission_config_error`，二者均 SHALL 明确 `not_started=true`；系统 SHALL NOT 在未获准时启动目标工具工作进程、发送 MCP 工具执行请求、读取受限内容或产生副作用。搜索候选的路径元数据枚举 SHALL 允许先进行，内容读取与目标搜索执行 SHALL 在相应候选获准后进行。MCP 调用 SHALL 对稳定工具别名与完整有效参数执行外部工具权限判定，并在发送请求前重新核对 Server 有效配置身份和授权范围；本地路径边界与 shell 文本检查 SHALL NOT 被描述为约束外部 Server 内部执行。

所有子运行，包括既有独立 Skill，SHALL 在系统工具分流前检查禁止嵌套及运行范围。新子 Agent 中已声明但禁止的 agent、独立 Skill 或后台非白名单工具 SHALL 返回 tool_not_allowed 和 not_started=true；既有独立 Skill 不得调用 agent，但其下一层独立加载错误和授权交互保留原合同。新子 Agent 单目标调用缺少批准 SHALL 返回 approval_required，候选级未批准保留受限搜索合同，不启动受限操作。

#### Scenario: 未知工具
- **WHEN** 一个完整调用引用未注册名称
- **THEN** 返回 `unknown_tool`，说明该工具未注册

#### Scenario: 参数非法
- **WHEN** 允许工具的参数不是合法 JSON 对象或不满足 Schema
- **THEN** 返回 `invalid_arguments`，说明字段或 JSON 问题，且不执行操作

#### Scenario: 禁止工具绕过模型列表
- **WHEN** 规划模式收到一个未出现在允许列表中的已注册写类调用
- **THEN** 执行入口再次拒绝并返回 `tool_not_allowed`，不依赖模型遵守工具列表

#### Scenario: 授权之前不得启动
- **WHEN** 主对话或既有独立 Skill 的有效工具调用需要人工授权且用户尚未决定
- **THEN** 系统保持等待授权，目标工具尚未启动，不读取受限内容或产生操作副作用

#### Scenario: 有效授权后规则变化
- **WHEN** 调用已有有效授权，但在实际执行前规则新增适用的 deny
- **THEN** 系统返回 `permission_denied` 和 `not_started=true`，不使用授权越过 deny

#### Scenario: 目标路径在等待期间变化
- **WHEN** 文件调用等待批准时目标或祖先链接发生变化
- **THEN** 系统以重新解析的真实路径复查边界、规则与授权范围，不按旧路径授权直接执行；检查失败时返回未启动的结构化错误

#### Scenario: 外部调用批准前不发送请求
- **WHEN** 一个 MCP 工具调用参数有效但仍等待人工授权
- **THEN** 系统尚未发送该工具执行请求；普通拒绝返回 permission_denied 和 not_started=true，批准后仍重新检查规则与精确授权范围

#### Scenario: 规划模式伪造外部调用
- **WHEN** 模型在规划模式中返回已注册 MCP 工具的调用，即使该工具有 allow 规则、有效授权或处于 bypass
- **THEN** 执行前返回 tool_not_allowed，不发起外部工具请求，也不提供解除模式限制的授权选项

#### Scenario: 加载后同批工具被禁止
- **WHEN** 同一响应先成功加载一个禁止编辑的共享 Skill，再请求 edit_file
- **THEN** 后续 edit_file 在实际启动前按更新后的交集拒绝，不使用模型请求发送时的旧允许集合

#### Scenario: 系统加载也有权限边界
- **WHEN** load_skill 参数有效但命中明确 deny 或请求资源越出注册包
- **THEN** 调用返回未启动的权限或边界错误，不激活、不发起子执行，也不利用系统白名单例外读取任意文件

#### Scenario: Fork 稳定声明仍不能委派
- **WHEN** Fork 模型返回继承声明中的 agent 调用
- **THEN** 执行器在系统分流前返回 tool_not_allowed，不创建新任务或发起模型请求

### Requirement: 结构化执行结果
每次调用 SHALL 返回包含 `ok`、`data`、`error` 和 `truncated` 的可序列化结果。成功时 `ok` SHALL 为 true 且 `error` SHALL 为 null；失败时 `ok` SHALL 为 false，`error` SHALL 包含稳定的 `code`、可修正的中文 `message` 和必要的 `details`，可保留已获取的部分数据。错误信息 SHALL 写入结果正文，不能仅依赖供应商的错误标记；预期工具错误和普通执行异常 SHALL NOT 导致会话退出。

#### Scenario: 执行成功
- **WHEN** 工具完成有效操作
- **THEN** 返回 `ok=true`、对应数据和 `error=null`

#### Scenario: 工具抛出异常
- **WHEN** 执行遇到文件权限问题或普通运行异常
- **THEN** 返回对应错误代码或 `execution_error`，正文说明失败原因，不向终端或模型暴露堆栈及配置中的 API 密钥

### Requirement: 超时与取消
每次普通工具执行及 Skill 激活／资源读取 SHALL 受有限时间上限约束，默认 30 秒；通过 load_skill 发起的独立模型执行 SHALL 按有限任务请求预算及可信取消合同结束，不套用普通工具固定 30 秒总期限，其中每个普通工具仍有原执行期限；命令工具可在其 Schema 范围内指定上限，MCP 工具调用 SHALL 使用 30 秒执行上限。本地工具超时 SHALL 终止该工具工作进程及同组子进程；MCP 工具超时 SHALL 通过 SDK 取消该单次请求并结束本地等待。两类超时均 SHALL 返回 `timeout`，不取消同批其他工具。任务取消 SHALL 停止全部后续调度、终止活动本地工具及同组子进程、取消活动 MCP 单次请求并结束等待、补齐每个调用结果，并在受控清理结束后恢复输入。系统 SHALL 回收本地工具进程资源，不把本地停止等待当成进程已停止；对 MCP 远端执行 SHALL NOT 因发出取消而承诺已停止。任务取消或单次调用超时 SHALL NOT 主动重启或关闭整个 MCP Server，也 SHALL NOT 自动重发工具调用。对写入、命令或远端调用已发生的影响 SHALL NOT 声称回滚，结果不确定时 SHALL 明确说明可能已有副作用。等待、期限观察与受控清理 SHALL 支持并发任务的及时观察及取消。等待授权 SHALL NOT 计入工具执行超时，执行期限 SHALL 自实际启动工具时开始。等待授权期间取消 SHALL 结束本轮任务，等待中的调用 SHALL 返回 `cancelled` 并明确 `not_started=true`；同批已启动工具 SHALL 按各自本地终止或远端取消约束处理与清理。

agent 的前台等待 SHALL 使用转换后台软期限，默认 30 秒后返回同一 task_id 的凭据，不取消重启子执行。子普通工具仍保留原硬期限。用户主动取消父任务 SHALL 取消其全部子运行并停止自动唤醒；父执行段自然结束不取消后台任务。

#### Scenario: 文件或搜索操作卡住
- **WHEN** 任一本地工具超过执行上限仍未结束
- **THEN** 该工具被终止并返回 `timeout`，其余未取消的调用仍可继续

#### Scenario: 命令及子进程超时
- **WHEN** 命令创建同组子进程并超过上限
- **THEN** 命令和这些子进程被终止，结果保留可用输出并说明超时及可能已发生的影响

#### Scenario: 用户取消执行
- **WHEN** 用户在一个或多个工具执行期间按 Ctrl+C
- **THEN** 全部活动本地工具被终止并回收，活动 MCP 请求被取消并结束等待，未完成调用得到 `cancelled`，已完成实际结果保留，会话返回提示符且不再发起本任务的模型请求；远端执行状态未知时明确说明可能仍在执行或已产生副作用

#### Scenario: 取消清理期间收到结果
- **WHEN** 工具已返回真实结果，取消发生在资源清理边界
- **THEN** 系统保留实际结果及必要的完成状态说明，不将已经发生的操作标为未启动

#### Scenario: 授权等待超过执行上限
- **WHEN** 用户等待超过调用配置的工具执行上限后批准操作
- **THEN** 调用从实际启动时获得完整执行期限，不因等待授权返回 `timeout`

#### Scenario: 等待授权时取消
- **WHEN** 用户在授权等待期间按 Ctrl+C
- **THEN** 等待中的调用返回未启动的 `cancelled`，其他活动工具被清理，本轮停止且不发起后续模型请求

#### Scenario: 外部调用超过期限
- **WHEN** MCP 工具执行请求发出后 30 秒仍未返回完整结果，期间不含授权等待
- **THEN** 系统取消该单次请求并返回 timeout，说明远端操作可能已产生副作用；不自动重发请求、不重启 Server，后续获准调用仍可按 Server 当前状态处理

#### Scenario: 取消外部调用时结果已完成
- **WHEN** MCP 工具已返回完整真实结果，用户在本地收尾边界取消任务
- **THEN** 系统保留该实际结果，不将已发生的远端操作改记为未启动或回滚，并停止任务后续调度

#### Scenario: 独立任务超过普通工具期限
- **WHEN** 独立 Skill 的多个合法模型步骤总耗时超过 30 秒但没有取消或预算耗尽
- **THEN** 不因普通工具总期限强行停止整个子对话，内部各工具仍遵守自己的期限，用户仍可取消全部执行

#### Scenario: Agent 等待软期限到期
- **WHEN** 新子 Agent 超过前台等待阈值但没有真实执行超时或取消
- **THEN** 返回同一 task_id 的后台凭据，原执行继续，其命令与请求不被重放

### Requirement: 有界结果
返回模型的单次工具结果中，文本和匹配数据的 UTF-8 内容总量 SHALL 不超过 64 KiB，结构化信封字段除外。超过限制时 SHALL 保持结果为合法结构、设置 `truncated=true` 并说明截断；文件读取和搜索 SHALL 提供能够缩小范围的参数。写入内容和编辑输入 SHALL NOT 通过截断后继续执行的方式适配限制。

#### Scenario: 命令输出过多
- **WHEN** 命令持续输出超过返回上限的数据
- **THEN** 系统有界采集输出并标明截断，同时继续监控命令直至结束、超时或取消

#### Scenario: 修改内容过大
- **WHEN** 写入内容或编辑目标超过工具公布的处理上限
- **THEN** 返回 `input_too_large`，目标文件不被截断或部分改写

### Requirement: 权限拒绝与并发调用隔离
普通权限拒绝 SHALL 作为已配对的工具结果提交给模型并继续 Agent Loop，SHALL NOT 作为用户取消或未知工具累计。多个只读调用 SHALL 保留既有并发执行能力，人工授权请求 SHALL 串行展示；一个调用命中 deny 或被用户拒绝 SHALL NOT 取消同批其他调用。每个调用的授权决定、实际启动与结果 SHALL 与其调用标识关联。

#### Scenario: 拒绝后调整方案
- **WHEN** 用户拒绝一个有效单文件或 shell 工具调用且任务未取消或达到其他停止条件
- **THEN** 模型收到 `permission_denied` 和 `not_started=true` 并能在本轮继续调整方案，系统不要求用户重新发起任务

#### Scenario: 同批部分调用拒绝
- **WHEN** 同批只读调用中一个被拒绝，另一个已获准
- **THEN** 被拒绝调用返回自己的权限错误，获准调用继续执行并返回自己的实际结果

#### Scenario: 多个调用同时需要决定
- **WHEN** 同批多个调用需要人工授权
- **THEN** 系统逐个展示授权请求并关联各自调用，不并发争用终端输入，已获准的只读调用仍能按既有并发规则执行

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
