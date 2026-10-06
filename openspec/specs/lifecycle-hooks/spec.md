# lifecycle-hooks Specification

## Purpose

为 MewCode 提供基于 Agent 生命周期的声明式自动化能力，用事件、条件和固定动作执行格式化、工具拦截及上下文补充等重复工作，同时保留既有权限、只读规划、取消收尾和工具结果配对要求。

## Requirements

### Requirement: 项目级声明与集中校验

系统 SHALL 在启动时从固定真实工作根下的 `.mewcode/hooks.yaml` 加载规则快照，SHALL NOT 随供应商配置目录改变来源。顶层 SHALL 使用 `version: 1` 和 `hooks` 列表；每条规则 SHALL 包含 `event` 和 `action`，可选 `if`、`once`、`async`，后两者 SHALL 默认为 false。系统 SHALL 集中校验全部规则，拒绝重复 YAML 键、不安全对象、未知字段、未知事件、未知动作、错误类型、无效正则和不允许的执行控制组合。不存在文件 SHALL 等同于没有 Hook；存在任一配置错误时 SHALL 报告全部可收集的诊断并停用该文件全部规则，Agent SHALL 继续启动。运行中修改文件 SHALL NOT 改变本次启动快照。

#### Scenario: 无配置的兼容启动
- **WHEN** 工作根不存在 `.mewcode/hooks.yaml`
- **THEN** 系统不执行 Hook，现有会话、权限和工具行为保持有效

#### Scenario: 同时发现多个错误
- **WHEN** 一个配置文件包含未知事件和另一个规则的非法条件组合
- **THEN** 系统报告两处可定位的错误、不启用该文件任一规则，并继续 Agent 主流程

#### Scenario: 供应商配置位于其他项目
- **WHEN** 工作根是项目 A，供应商配置来自项目 B
- **THEN** 仅从 A 的 `.mewcode/hooks.yaml` 加载本次 Hook 快照

### Requirement: 扁平条件与明确的匹配语义

省略 `if` SHALL 表示无条件；存在 `if` 时 SHALL 仅包含非空 `all` 或 `any` 列表之一，成员 SHALL 是原子条件，SHALL NOT 嵌套或混用组合。原子条件 SHALL 包含 `field`、`match`、`value`，可选布尔 `negate`；`match` SHALL 支持 exact、glob、regex。exact SHALL 比较完整值且不隐式字符串化，布尔值 SHALL NOT 等同于数值；glob 和 regex SHALL 仅匹配完整字符串，regex SHALL 使用整个字符串匹配语义。反向条件 SHALL 对有效、类型适用的匹配结果取反；字段缺失或类型不适用 SHALL 始终不匹配，包括 negate 为 true 的情形。

本地文件的 `tool.target_path` SHALL 是真实目标相对项目根的 POSIX 路径，路径 glob 的 `*`、`?` 和字符集合 SHALL 不跨路径片段，`**` SHALL 可跨零层或多层；本地文件参数 `tool.arguments.path` SHALL 保留原始值并使用路径 glob 语义。命令及其他字符串 SHALL 使用字符串 glob；MCP 参数 SHALL NOT 被自动解释为本地文件路径。现有权限 YAML 的 exact/glob 格式、对象规范化、大小写语义和 deny 优先级 SHALL 保持兼容。

#### Scenario: 全部与任一满足
- **WHEN** 两个原子条件只有一个匹配
- **THEN** all 不触发动作，any 触发动作；同时声明 all 和 any 被判为配置错误

#### Scenario: 真实目标与原始参数区分
- **WHEN** 本地文件调用通过项目内链接指向 `src/private/a.py`
- **THEN** target_path 按真实目标匹配，arguments.path 保留链接参数；MCP 的同名参数不获得本地路径身份

#### Scenario: 缺失字段不被反向匹配放大
- **WHEN** 条件反向匹配 `tool.arguments.path`，当前事件不存在该字段
- **THEN** 条件不匹配，不因字段缺失触发动作

#### Scenario: 权限回归兼容
- **WHEN** 启用 Hook 后执行既有 exact、路径 glob、shell glob 或 MCP 规范 JSON 权限规则
- **THEN** 判定保持原有语义，新增 Hook 条件语法不成为权限 YAML 的合法新格式

### Requirement: 四层生命周期和系统事件

系统 SHALL 提供以下事件，并独立于终端打印派发：`session.start`、`session.end`；`turn.start`、`turn.end`；`message.user`、`message.before_request`、`message.after_response`；`tool.before`、`tool.after`；`mode.changed`、`context.before_compact`、`context.after_compact`。会话开始 SHALL 在新建或恢复完成且会话可用后派发一次并标明来源；会话结束 SHALL 在后台资源和会话句柄关闭前派发一次。轮次 SHALL 表示一次顶层用户任务，而不是一次模型请求；普通任务及顶层 Skill 任务 SHALL 遵循同一轮次边界，结束 SHALL 携带实际终止原因。无任务的控制指令 SHALL NOT 创建轮次。

消息事件 SHALL 针对顶层用户任务和正常工作模型请求，不逐 Token 或参数片段触发；完整、正常结束的响应 SHALL 在工具调度前触发 after_response，异常或部分流 SHALL NOT 触发完整响应事件。系统 SHALL 在准备工作请求时派发 before_request，同一请求意图经过压缩和重新估算 SHALL NOT 重复派发；新的实际工作重试 SHALL 使用新的请求意图。摘要、恢复、记忆模型请求和纯预算预览 SHALL NOT 被当作普通工作消息事件。模式变更失败 SHALL NOT 发布成功变更事件；实际摘要操作 SHALL 成对发布压缩事件并标明自动、手动或恢复用途及结果。

#### Scenario: 同一任务包含多个请求
- **WHEN** 一个用户任务经过两次工具交互后正常结束
- **THEN** 轮次开始和结束各一次，工作请求及完整响应事件分别按实际交互派发

#### Scenario: 仅控制模式
- **WHEN** 用户执行裸 `/plan` 或 `/do` 并成功改变模式
- **THEN** 仅派发模式变更事件，不创建用户轮次或模型请求；重复保持同一模式不伪造变更

#### Scenario: 摘要后重新估算
- **WHEN** before_request 已运行，随后请求准备经过自动摘要和重新估算
- **THEN** 同一工作请求意图不重复运行消息 Hook，压缩事件携带独立用途

#### Scenario: 部分响应出错
- **WHEN** 模型已输出部分文本或工具参数，随后流异常结束
- **THEN** 不派发 after_response，不执行不完整工具，轮次以实际错误原因结束

### Requirement: 固定快照与关联标识

每次派发 SHALL 使用触发时的独立 JSON 可序列化快照，包含事件名、时间、会话关联、可用的任务／运行关联、当前模式及权限模式，以及该事件适用的消息、工具或系统状态字段。工具快照 SHALL 携带原始调用 ID、工具名、有效参数和可用的真实目标；after 快照 SHALL 携带实际工具结果及未启动或副作用信息。未知或非法参数 SHALL NOT 被伪造为已验证参数。动作 SHALL NOT 通过修改快照改变待执行参数、模式或实际结果。后续状态变化 SHALL NOT 改写已经排队的后台快照。

独立 Skill 子运行 SHALL 继承同一会话规则和一次性状态，并携带子运行与顶层任务关联；其工作请求、完整响应和工具事件 SHALL 可辨认，SHALL NOT 重复派发顶层用户消息、轮次或会话事件。子 Agent 占位动作 SHALL NOT 因此获得执行能力。

#### Scenario: 后台快照不跟随状态变化
- **WHEN** 工具完成事件入队后，主任务继续修改历史或切换模式
- **THEN** 后台动作收到原触发时的结果与模式，实际开始执行仍遵守当前安全状态

#### Scenario: 子运行结束
- **WHEN** 独立 Skill 子运行完成而顶层任务继续
- **THEN** 子事件具有可辨认关联，不把子完成当作顶层 turn.end 或 session.end

### Requirement: 显式工具拦截决策

tool.before SHALL 仅在工具通过既有注册、参数、当前范围和权限检查后、实际副作用前派发。该事件的所有动作 SHALL 同步执行；命令退出码为零或 HTTP 返回成功状态后，可从 JSON 对象返回 `decision: deny` 和非空 `reason` 拦截当前工具。有效 allow 或未包含拒绝决策 SHALL 继续原执行路径；非零退出、超时、网络失败或无效决策 SHALL 仅记日志并继续。首个有效 deny SHALL 停止该调用剩余前置规则，其他 allow SHALL NOT 覆盖该拒绝。

拒绝 SHALL 生成 `hook_denied` 工具结果，包含拒绝原因和 `details.not_started=true`，按原调用 ID 配对、保存并反馈给模型，SHALL NOT 发布该目标工具的 tool_started。拒绝单个工具 SHALL NOT 自动停止整个 Agent 或同一响应的其他合法工具；是否继续模型请求 SHALL 遵守原任务预算和取消状态。其他事件的动作输出 SHALL NOT 拦截或修改已完成操作。

#### Scenario: 前置动作明确拒绝
- **WHEN** 成功运行的前置命令返回 `{"decision":"deny","reason":"禁止修改受保护配置"}`
- **THEN** 目标工具未启动，模型获得对应 hook_denied 结果，可在剩余预算内调整操作

#### Scenario: 检查脚本崩溃
- **WHEN** 前置命令非零退出或成功退出但决策格式无效
- **THEN** 系统记录 Hook 失败并继续已获许可的目标工具，不把故障误判为拒绝

#### Scenario: 既有权限先拒绝
- **WHEN** 目标工具被现有 deny、规划范围或硬限制禁止
- **THEN** 保留原拒绝结果，不运行该目标的 tool.before 动作，不借 Hook 产生目标副作用

#### Scenario: 后置输出不能改变结果
- **WHEN** 已完成工具的后置动作返回 deny
- **THEN** 原工具结果保留，系统不声称已拦截或回滚该操作

### Requirement: 完整后置通知与结果配对

每个已接受并需要配对结果的工具调用 SHALL 在结果确定及受控资源清理完成后至多派发一次 tool.after，包括成功、权限拒绝、Hook 拒绝、非法参数、未知工具、超时和取消。未启动调用 SHALL 保留 not_started 信息。后置动作失败 SHALL NOT 替换原工具结果或破坏调用／结果配对；已有取消或停止状态 SHALL 阻止后置命令和 HTTP 新副作用，通知本身 SHALL 保留真实结果。

#### Scenario: 拒绝也有后置通知
- **WHEN** 调用被 Hook 拒绝
- **THEN** after 快照包含实际 hook_denied 及未启动状态，原调用恰好对应一个结果

#### Scenario: 排队调用取消
- **WHEN** 用户取消时仍有未开始的调用
- **THEN** 其 after 快照包含未启动的取消结果，不运行新的命令或 HTTP 动作

### Requirement: 命令动作与独立超时

command 动作 SHALL 包含非空 `command`，可选整数 `timeout_seconds`，默认 30 秒并限定为 1 至 120 秒。动作 SHALL 从真实工作根启动非交互 shell，并通过标准输入接收事件 JSON；系统 SHALL NOT 把事件字段插值到 shell 命令。命令 SHALL 遵守现有 shell 黑名单、权限规则、权限模式及有效授权；前台动作需要授权时 SHALL 使用现有授权流程，等待批准 SHALL 不消耗命令执行时限。超时 SHALL 结束并回收受控本地进程及同组子进程，然后记录失败；命令输出 SHALL 具有有限收集预算。

#### Scenario: 工具参数包含 shell 元字符
- **WHEN** 事件参数包含引号、换行、命令替换或重定向字符
- **THEN** 字符只作为 stdin JSON 数据传递，不改变固定 command 的 shell 含义

#### Scenario: 命令超时
- **WHEN** Hook 命令超过自身执行时限
- **THEN** 本地受控进程清理完成后记日志，Agent 继续；该时限不消耗目标工具自身的执行时限

### Requirement: HTTP 动作与有界请求

http 动作 SHALL 包含固定的绝对 HTTP 或 HTTPS `url`，可选 `method` 和字符串 `headers`，默认 POST 和空 headers；method SHALL 仅接受 GET、HEAD、POST、PUT、PATCH、DELETE、OPTIONS，url SHALL NOT 包含凭据 userinfo 或 fragment。系统 SHALL 将事件快照作为 JSON 请求体发送，SHALL NOT 使用事件字段改写端点或复制模型服务连接凭据。请求 SHALL 使用 30 秒总时限和 64 KiB 响应正文预算，SHALL NOT 自动重发或跟随重定向。非成功状态、连接错误、超时和需要决策时的无效 JSON SHALL 作为 Hook 失败记录。请求取消只表示本地等待结束，SHALL NOT 声称远端动作已回滚。

#### Scenario: 成功 HTTP 拒绝决策
- **WHEN** tool.before 的 HTTP 请求成功返回有效 deny 和原因
- **THEN** 按同一拦截协议拒绝目标工具，不将普通状态码错误当作 deny

#### Scenario: 重定向或服务错误
- **WHEN** 端点返回重定向或非成功状态
- **THEN** 不跟随或重发，记录失败并继续主流程

### Requirement: 下一工作请求的提示词注入

prompt 动作 SHALL 包含非空 `text`，SHALL 同步将其加入下一次实际工作请求的待注入上下文。请求前 Hook 产生的文本 SHALL 与此前排队的注入共同参与整体上下文预算检查及压缩后的复查，SHALL NOT 修改固定系统提示或本地许可。专用摘要、恢复和记忆模型请求 SHALL NOT 消耗该队列。预算预览及发送前取消 SHALL NOT 消耗注入；实际工作请求开始后 SHALL 消耗本次选定文本，即使该请求失败。成功完成的交互 SHALL 随既有上下文提交和压缩，失败流 SHALL NOT 伪造成功历史。需要每次工作请求持续提醒时 SHALL 通过 before_request 规则重复注入。

提示词队列 SHALL 按触发任务身份隔离，子只消费自身队列，父和兄弟不能抢占；同一父任务的新执行段 SHALL 保留该父未消费注入，不因 run_id 更新丢弃或交给其他父任务。规则与会话 once 仍共享；子结束只清理自身队列，不关闭会话引擎。

#### Scenario: 注入使请求超过预算
- **WHEN** Hook 文本使准备的工作请求超过上下文预算
- **THEN** 系统按现有有界压缩或 context_blocked 路径处理，不直接发送超出保护边界的请求

#### Scenario: 摘要不抢先消耗注入
- **WHEN** 待注入内容存在，而准备工作请求需要先进行摘要
- **THEN** 摘要不消费注入，最终工作请求仍包含原选定文本，重新估算不重复追加文本

#### Scenario: 发送前取消
- **WHEN** 请求已准备注入但实际工作请求尚未开始即被取消
- **THEN** 不消耗选定文本或请求次数，不伪造成功交互

### Requirement: 子 Agent 声明占位

subagent 动作 SHALL 校验非空 `agent` 标识和 `prompt`，触发时 SHALL 只记录该动作尚未实现。系统 SHALL NOT 发起子模型请求、调用独立 Skill 代替、执行子工具或扣除工作模型预算。

#### Scenario: 合法占位动作触发
- **WHEN** 已校验的 subagent 规则匹配事件
- **THEN** 记录未实现诊断，主流程继续，不产生子运行或额外 usage

### Requirement: 会话内最多尝试一次

once 为 true 的规则 SHALL 在本次会话内最多提交一次动作尝试。条件未匹配、模式禁止或后台动作尚需人工批准而被跳过 SHALL NOT 消耗标记；提交尝试前 SHALL 原子取得标记，执行或前台授权失败 SHALL 不返还标记。并发事件、独立 Skill 和新的 Agent 子运行 SHALL 共用该会话标记。reset SHALL 保留标记，进程重启和显式恢复 SHALL 不恢复旧标记。标记 SHALL NOT 被解释为持续拒绝策略。

#### Scenario: 并发触发同一规则
- **WHEN** 两个工具事件同时匹配 once 规则
- **THEN** 只有一个动作尝试被提交，失败也不在本会话自动重试

#### Scenario: 重置与重启
- **WHEN** 已运行一次的规则经历 reset，随后重启并恢复同一存档
- **THEN** reset 后仍不重跑，重启后的首次匹配可重新提交

### Requirement: 后台执行和声明顺序

同一事件的匹配规则 SHALL 按 YAML 声明顺序处理，同步动作完成后才处理下一条，后台动作提交后可继续；系统 SHALL NOT 提供显式优先级。async 为 true SHALL 允许 command、http 和 subagent 占位动作后台处理，SHALL NOT 用于 tool.before 或 prompt。后台命令 SHALL 只在已有非交互许可时执行，需要 ask 而无有效授权时 SHALL 跳过并记日志，SHALL NOT 发起后台授权弹窗。后台执行 SHALL 由会话统一管理并使用触发时快照，其完成顺序及与后续工具的副作用顺序 SHALL 不作保证。

#### Scenario: 非法异步组合
- **WHEN** 配置 tool.before 的 async 或 prompt 的 async
- **THEN** 集中校验报告错误，不启用该文件任一规则

#### Scenario: 后台缺少授权
- **WHEN** 后台命令需要人工批准且没有有效授权
- **THEN** 不启动命令，不弹出授权请求，记日志并继续主流程

#### Scenario: 声明顺序与完成顺序
- **WHEN** 第一条是后台 HTTP，第二条是同步命令
- **THEN** 先提交 HTTP 再执行命令，不要求 HTTP 先完成

### Requirement: 规划范围与防递归

规划模式 SHALL 跳过 command 和 http 动作并记录原因，prompt SHALL 仍可注入；Hook 声明、旧授权及 bypass SHALL NOT 绕过规划只读范围。动作实际启动前 SHALL 再次检查当前取消、关闭和模式状态。切入规划模式前 SHALL 停止新副作用提交并有界取消、回收此前本地后台命令及结束 HTTP 等待，不宣称远端影响已撤销。Hook 动作执行 SHALL NOT 再次派发工具 Hook；允许目标工具继续 SHALL NOT 构成新的权限批准。

#### Scenario: 规划中的自动动作
- **WHEN** 规划事件同时匹配命令、HTTP 和提示词规则
- **THEN** 前两者跳过并诊断，仅同步准备提示词，不增加模型权限

#### Scenario: 前台命令需要批准
- **WHEN** 执行模式的 Hook 命令命中 ask 且没有有效授权
- **THEN** 使用现有前台授权流程；拒绝仅使该 Hook 失败，原目标工具仍按自身既有许可继续

#### Scenario: Hook 命令形成递归风险
- **WHEN** 一个工具 Hook 执行 shell 命令
- **THEN** 该命令不生成新的 tool.before 或 tool.after Hook，原工具仍只获得其真实结果

### Requirement: 同步副作用与调度边界

配置存在同步 command 或 http 的工具前置或后置规则时，系统 SHALL 保守地串行处理工具批次，直到各工具及其同步 Hook 和清理完成后再启动后续调用。没有此类规则时 SHALL 保留现有连续只读工具最多四个并发及非只读串行边界。用户显式选择后台动作 SHALL 表示接受其与后续工具之间没有副作用顺序保证，后台动作 SHALL NOT 被描述为同步格式化已完成。

#### Scenario: 读取工具附带同步命令
- **WHEN** 本来可并发的读取批次配置了同步工具命令 Hook
- **THEN** 工具及同步 Hook 按批次原始顺序串行完成，不依据目标工具只读属性并发执行 Hook 副作用

#### Scenario: 没有同步副作用 Hook
- **WHEN** 只配置提示词或没有工具 Hook
- **THEN** 连续只读组保留原并发上限，写操作和 shell 仍形成串行边界

### Requirement: 失败隔离与受控收尾

规则匹配、动作执行、占位处理和诊断输出的普通失败 SHALL 被隔离，SHALL NOT 修改主任务终态、现有工具结果、权限状态或触发额外模型请求；一个普通失败 SHALL NOT 阻止后续独立规则。诊断 SHALL 包含来源、规则位置、事件和安全的失败类型，SHALL NOT 默认记录完整事件正文、命令输入或 HTTP headers。可信用户取消 SHALL 停止当前任务的新前台动作及目标工具并完成本地受控清理，SHALL NOT 被作为普通 Hook 故障吞掉；后续任务取消 SHALL NOT 自动取消此前已独立提交的后台动作。会话结束 SHALL 禁止新后台提交、有界结束活动动作，再释放会话和专用客户端。

#### Scenario: 一条规则故障
- **WHEN** 一个事件的首条规则动作抛出普通异常
- **THEN** 记录可定位诊断，后续独立规则和 Agent 主流程继续

#### Scenario: 用户取消前台检查
- **WHEN** 用户在前台 Hook 命令执行或授权等待中取消任务
- **THEN** 停止该命令与目标工具的新执行，清理后按真实取消状态补齐工具结果并结束轮次

#### Scenario: 应用退出
- **WHEN** 后台命令或 HTTP 仍运行而会话关闭
- **THEN** 不再接收新后台工作，有界清理本地资源和结束请求等待后释放句柄，远端未确认完成时不声称已回滚

### Requirement: 子运行 Hook 权限与生命周期
Hook 派发 SHALL 使用触发运行身份、权限模式、取消和当前规划限制；新的 Agent 子运行 Hook 命令 SHALL 使用子批准副本及非交互判定，不向父请求审批，不借目标工具扩大许可。既有独立 Skill 保留原权限交互合同。子关闭不关闭会话资源。Hook subagent SHALL 继续按声明占位合同记录未实现，不成为另一委派入口或递归来源。

隔离子 SHALL 共享原会话规则快照、once 状态和派发设施，但其命令动作默认 cwd 和本地 tool.target_path SHALL 绑定子实际工作根，使用子权限视图；共享 snapshot 中的父目录 SHALL NOT 被用作子命令 cwd。子关联异步动作 SHALL 保有可辨认运行归属和目录保护，退出或删除 SHALL 等其实际收尾；HTTP 等远端副作用不被描述为已经回滚。

#### Scenario: 提示词不串扰
- **WHEN** 子产生 prompt 而父同时准备请求
- **THEN** 子下一请求包含它，父不包含，同一 once 规则仍最多提交一次

#### Scenario: Hook 缺少批准
- **WHEN** 新的 Agent 子运行 Hook 命令没有适用许可
- **THEN** 记录安全诊断并按失败隔离继续，不请求主输入

#### Scenario: 子结束
- **WHEN** 一子结束而父或兄弟仍活动
- **THEN** 共享派发器和客户端仍有效，子队列不泄漏到其他运行

#### Scenario: 子 Hook 使用实际目录
- **WHEN** 隔离子工具触发写入相对产物的获准 Hook 命令
- **THEN** 产物位于子实际工作根，目标路径相对子根表达，父同名文件不被默认 cwd 操作覆盖

#### Scenario: 异步动作保护目录
- **WHEN** 子模型已最终回复而子 Hook 命令尚运行
- **THEN** 目录仍受活动保护，完成或取消收尾后才允许退出删除，父和兄弟 Hook 服务继续可用
