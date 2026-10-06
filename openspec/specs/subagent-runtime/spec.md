# subagent-runtime Specification

## Purpose

定义统一 Agent 委派工具及定义式、Fork 式子执行的外部契约，使子任务有独立上下文、权限和用量归属，在有限非交互执行后返回有来源的结果，同时维护共享基础设施的资源生命周期。

## Requirements

### Requirement: 统一类型分流入口
系统 SHALL 提供唯一 agent 工具，接受必填 type=defined|fork 和非空 prompt，defined 必须指定已发现 role，background 缺省 false；fork SHALL 禁止 role 和模型覆盖，background 省略或 true 均后台，显式 false SHALL 返回 invalid_arguments。未知字段、错误组合和未知角色 SHALL 在启动前返回结构化错误及 not_started=true。角色增删 SHALL NOT 增加工具或修改描述、Schema、排序。入口 SHALL 形成串行调度边界。

#### Scenario: 类型参数非法
- **WHEN** defined 缺少 role、fork 带 role 或显式要求前台
- **THEN** 参数校验拒绝，不创建执行任务或发起模型请求

#### Scenario: 同一入口两种运行
- **WHEN** 主 Agent 分别调用 defined 和 fork
- **THEN** 两者使用同一工具名与结果契约，按类型采用对应初始化及后台规则

### Requirement: 定义式空历史和固定角色
定义式 SHALL 从新对话开始，包含固定基础提示、完整角色正文、当前项目规则与模式约束和显式子任务目标；SHALL NOT 隐式导入父历史、思考、Skill 激活或记忆抽取内容。角色正文 SHALL 在该子运行后续工作请求中持续有效，不因父角色热更新或历史压缩被替换。

隔离 defined 运行 SHALL 在进入自己的工作副本后加载该实际工作根的项目指令和可发现 Skill，保持父工具范围上限；角色正文仍采用接受时快照。它 SHALL NOT 直接借用父项目资源的目录快照，SHALL NOT 导入父项目记忆抽取内容或新增对子任务的自动记忆提取；读取适用记忆时 SHALL 使用自身项目目录及既有用户域合同。未隔离 defined 的初始化保持原行为。

#### Scenario: 父历史和激活不泄漏
- **WHEN** 父已有独有历史标记和激活 SOP，调用 defined 并未在 prompt 中提供它们
- **THEN** 子请求不包含这些标记，父历史和激活保持原状

#### Scenario: 子目录规则与包资源
- **WHEN** 父子目录有不同项目指令和同名 Skill 包资源
- **THEN** 隔离子采用自身实际工作根的指令和资源，角色正文仍被冻结，父正文和激活不会隐式导入

### Requirement: Fork 使用合法原始请求快照
Fork SHALL 使用触发委派的父模型请求输入快照，深复制系统、工具及顺序、消息块、适用签名、模型和思考配置，追加子任务及明确子身份。SHALL NOT 把该历史改写为 JSON 背景摘录，不导入尚未配对的当前 assistant 工具批次，不跟随父后续消息、压缩或激活更新。首次请求不可容纳时 SHALL 返回上下文限制失败，不为宣称缓存复用而偷偷删改前缀；后续子压缩遵守自身预算。

#### Scenario: 委派发生在多工具响应
- **WHEN** 父当前 assistant 响应同时含 agent 和其他尚未完成调用
- **THEN** Fork 复制该响应之前已经发送的完整输入，新请求无孤立调用或结果，子目标单独追加

#### Scenario: 父继续压缩历史
- **WHEN** Fork 注册后父压缩或修改主历史
- **THEN** Fork 冻结快照及消息内对象不变，子修改也不影响父

### Requirement: 独立状态和共享资源所有权
每个子运行 SHALL 独立持有消息、提示控制状态、权限追踪与适用本会话批准副本、上下文估算与摘要、结果缓存命名空间、文件读状态、取消和逐请求用量。Fork 的继承内容 SHALL 是副本，定义式控制状态为空。LLM 传输客户端、MCP 连接及 Hook 基础设施 SHALL 由会话共享；未隔离 defined、Fork 和既有独立 Skill 的共享工作根合同保持。声明隔离的 defined SHALL 独立拥有工作副本、实际工作根和目录租约，在使用操作系统文件系统时不宣称独立版本库或强沙箱。子结束 SHALL 只释放自身资源，不关闭共享服务或使兄弟缓存失效。

系统 SHALL 不通过进程级 chdir、改写父上下文 root 或全局清空缓存切换目录。共享 MCP SHALL 保留 Server 自身目录语义，不被虚假描述为已切换到子 cwd；子关闭 SHALL 不关闭这些共享连接。运行租约 SHALL 持续到本运行受控操作和关联 Hook 实际收尾；必要证据归档或旧引用释放前 SHALL 保留独立的删除保护。归档失败 SHALL 持久记录保留原因，关闭时可释放已收尾运行的句柄，但不能解除证据保护后删除目录。

#### Scenario: 两个子运行同时活动
- **WHEN** 一子压缩历史、消费 Hook 提示或结束
- **THEN** 父和另一子消息、注入、用量及客户端仍有效，缓存路径和记录可按身份区分

#### Scenario: 隔离子与父同时操作同名路径
- **WHEN** 隔离子 A、隔离子 B 和父同时读取或编辑 src/app.py
- **THEN** 每次内置工具操作使用所属运行的实际工作根，文件和目录相关状态不会因同名相对路径串扰

#### Scenario: 共享 MCP 的目录语义
- **WHEN** 隔离子获准调用共享 stdio MCP
- **THEN** 服务保留会话连接的启动目录及真实配置身份，不被重新连接或声称采用子 cwd，批准按子真实运行身份检查

### Requirement: 多层工具过滤与禁止嵌套
实际工具范围 SHALL 受父注册时范围上限、当前规划上限、角色白黑名单、子 Skill 限制、全局禁止和后台白名单共同限制。所有子运行，包括既有独立 Skill，SHALL 禁止调用 agent 或通过 load_skill 启动独立执行；系统工具属性 SHALL NOT 绕过这些限制。定义式 SHALL 声明其启动允许工具；Fork SHALL 保留父声明且执行前检查实际范围。新的 Agent 子运行中禁止的已注册调用 SHALL 返回 tool_not_allowed，不启动子请求或副作用，不计为未知工具；既有独立 Skill 的下一层独立加载保留其原错误语义。

#### Scenario: Fork 请求嵌套 Agent
- **WHEN** Fork 从继承声明中返回 agent 调用
- **THEN** 执行层拒绝，未启动下一层子 Agent，模型可在剩余预算内调整

#### Scenario: 系统入口旁路
- **WHEN** 子调用 load_skill 启动独立 Skill，或角色禁止 load_skill 仍伪造加载
- **THEN** 调用在读取受限资源或启动独立模型前被拒绝；合法获准共享激活及包资源读取仍可用

### Requirement: 后台允许获准的副作用工具
后台默认白名单 SHALL 包含 read_file、write_file、edit_file、execute_command、glob_files、search_code 和受限 load_skill；后台普通操作 SHALL 继续检查角色、父上限、规划及非交互权限。可选 agent_background_tools SHALL 是精确已注册名称的 JSON 列表，省略使用默认值，空列表无可执行工具；后台 MCP SHALL 仅在显式纳入后可执行。切后台时已启动操作 SHALL 按原授权及工具期限收尾，后续新调用按后台范围检查。

#### Scenario: 有批准的后台编辑
- **WHEN** 后台角色及父范围允许编辑且持有对应有效批准
- **THEN** 编辑可以执行，实际产物与结果归属该子任务

#### Scenario: MCP 未纳入后台
- **WHEN** Fork 保留父 MCP 声明但后台白名单没有该名称
- **THEN** 返回 tool_not_allowed，不发送 MCP 执行请求

### Requirement: 有限非交互跑到底
子运行 SHALL 自主执行模型与工具循环，无人工审批或交互输入；完整非空无工具响应 SHALL 表示自然结束。每个新 Agent 子运行 SHALL 使用自身 max-iterations 请求预算，工作、失败请求及子自动摘要均计数，普通工具数量不增加次数。预算、上下文、流错误或可信取消 SHALL 按真实停止原因结束，不额外请求总结，不改变父或兄弟预算。权限不足 SHALL 通过普通工具错误回灌，不伪装取消。

#### Scenario: 需要新批准
- **WHEN** 子请求需要批准且没有适用记录
- **THEN** 单目标调用返回 approval_required 和 not_started=true，不等待输入；模型可在预算内调整或说明受限

#### Scenario: 子用完请求额度
- **WHEN** 子已耗尽自身模型请求预算
- **THEN** 结束并报告 max_iterations，不给子新预算，不扣减父接续剩余额度

### Requirement: 有来源最终结果及真实完成语义
结果 SHALL 包含 task_id、parent_task_id、运行类型、适用角色及定义来源、reason、最终文本、实际工具证据、逐运行用量及副作用状态。正常最终回答 SHALL 直接作为摘要，不额外调用总结模型。模型自然结束 SHALL NOT 被解释为测试通过或业务目标已达成；失败、取消、权限受限和不确定副作用 SHALL 如实保留。父语义历史 SHALL 只获得最终结果或接收凭据及一次异步结果，不追加子消息、思考和逐步工具过程。

隔离结果及本地任务详情 SHALL 额外提供工作树路径、子实际工作根、分支、基线、创建／恢复、初始化、证据归档和保留／删除状态及原因。模型结束与清理状态 SHALL 分开，清理受阻不改写模型真实结束原因，尚未创建的任务不伪造已有路径或成果。父仍只接收有来源的最终结果或既有接收凭据及异步结果，不自动 merge、push 或重跑。

#### Scenario: 最终答复报告测试失败
- **WHEN** 子正常结束但报告真实测试错误
- **THEN** 子执行状态为 completed、reason 为 model_done，结果保留测试失败证据，父不把 completed 描述为测试通过

#### Scenario: 副作用后取消
- **WHEN** 子命令已产生文件，随后用户取消父任务
- **THEN** 结果保留真实文件证据及取消状态，不声称回滚或任务成功

#### Scenario: 模型完成而目录保留
- **WHEN** 子以 model_done 结束但存在新提交或归档失败
- **THEN** 模型终态保持，结果明确报告保留路径、分支及原因，不声称成果已合并或已清理

### Requirement: 隔离任务的启动与冻结基线
声明隔离的 defined SHALL 接受时冻结完整 HEAD，排队不创建工作副本，实际启动时才创建、初始化和进入；首个模型请求 SHALL 在上述阶段成功后开始。Git、路径、初始化或租约错误 SHALL 返回有来源的阶段诊断，并说明子模型是否启动及实际残留，不回退到共享工作根、不消耗未发生的模型请求预算。已有目录的创建快速恢复 SHALL 遵守纯文件系统读取合同。

#### Scenario: 初始化失败未发送模型
- **WHEN** 子目录已创建但必需环境文件初始化失败
- **THEN** 子模型请求数为零，返回初始化阶段、实际残留及保留原因，不能把内部已发生创建描述为所有操作未开始

#### Scenario: 同角色两次委派
- **WHEN** 两个任务选择同一个声明隔离的角色
- **THEN** 默认使用不同任务身份产生的目录和分支，仍受现有任务容量和工具范围约束，不新增编排行为
