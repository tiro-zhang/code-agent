## Purpose

定义 MewCode 如何在一次应用运行中连接多个外部 MCP Server、兼容协议版本、完整发现工具并复用连接，以及失败、超时、取消和退出时的资源与结果边界，使一个 Server 的问题不会破坏其他可用能力。

## ADDED Requirements

### Requirement: 双传输与自动协议兼容
系统 SHALL 支持 stdio 和 Streamable HTTP，并自动确定 Server 支持的新旧协议：至少覆盖 `2026-07-28` 的能力发现流程和 `2025-11-25` 的 initialize/initialized 流程。用户 SHALL 无需为这两代 Server 手工选择协议版本；系统 SHALL 仅使用双方兼容的协议行为，不强制新版执行旧握手。JSON-RPC 2.0 请求 SHALL 按 ID 关联响应，通知 SHALL NOT 被误配为请求响应；连接异常 SHALL NOT 被当作成功协商。

#### Scenario: 新旧 Server 同时存在
- **WHEN** 配置分别指向两代协议的有效 Server
- **THEN** 两者都可完成协议确认、工具发现和调用，各自使用相符的协议流程

#### Scenario: 响应乱序与通知穿插
- **WHEN** 同一连接的不同请求响应乱序到达，期间收到通知
- **THEN** 每个请求只获得匹配其 ID 的响应，通知不完成错误的等待请求

### Requirement: 启动时完整发现
系统 SHALL 在首次聊天提示符前尝试初始化有效 Server，连接准备最多 4 个并行。每个 Server 的连接、协议确认和全部工具分页 SHALL 共用 15 秒启动期限。系统 SHALL 读取 tools/list 的全部分页，以确定性 Server 名／原始工具名顺序提交注册；重复 cursor、协议错误或期限届满 SHALL 使该 Server 本次发现失败，不发布半份清单。空列表或 Server 未提供工具能力 SHALL 允许连接成功且注册零个工具。单个工具定义无法使用 SHALL 按 mcp-tools 排除该项，不阻止其他有效工具。

#### Scenario: 工具分页
- **WHEN** 工具列表分三页返回
- **THEN** 首次模型请求之前已注册全部有效工具，注册顺序不依赖分页或网络完成顺序

#### Scenario: 发现中断
- **WHEN** Server 返回部分页面后卡住或重复同一 cursor
- **THEN** 该 Server 发现失败且没有半注册工具，其他有效 Server 与内置工具仍可使用

#### Scenario: 服务仅支持非工具能力
- **WHEN** Server 的能力不包含 tools
- **THEN** 不发送无意义的工具发现请求，不请求 resources/prompts，显示零工具而非影响其他 Server

### Requirement: 运行期间连接复用
每个 Server SHALL 在一次 MewCode 运行中复用其成功建立的连接，后续 tools/call SHALL NOT 再启动子进程或重复初始化。普通任务结束、历史裁剪、规划／执行切换 SHALL NOT 关闭连接。系统 SHALL 仅在启动时发现工具；工具变化通知 SHALL NOT 触发本期动态注册或刷新。

#### Scenario: 跨任务调用
- **WHEN** 两轮独立对话调用同一 stdio Server
- **THEN** 只启动一次 Server 进程，并复用连接执行两次调用

#### Scenario: 模式与历史变化
- **WHEN** 会话经过 `/plan`、有效 `/do` 或历史裁剪后再次调用 MCP
- **THEN** 继续使用原连接和启动工具快照

### Requirement: 单 Server 故障隔离
初始化失败、协议不兼容、进程退出或连接失效 SHALL 只影响所属 Server。已确认失效的连接 SHALL 标记不可用并完成受影响等待者；已注册工具 SHALL 保留名称，后续调用快速返回 `mcp_unavailable`，SHALL NOT 重启进程或重新建立失效会话。请求级工具错误、普通 HTTP 错误及单次超时 SHALL NOT 在连接仍可用时自动使整个 Server 失效。

#### Scenario: 一个 Server 无法启动
- **WHEN** Server A 的 command 不存在，Server B 正常
- **THEN** A 给出安全诊断，B 的工具和内置工具正常注册，应用进入提示符

#### Scenario: 运行中连接退出
- **WHEN** 已注册工具的 stdio Server 进程退出
- **THEN** 其待完成调用返回错误，后续调用快速失败且不重新启动；其他 Server 的调用继续可用

#### Scenario: 普通工具失败
- **WHEN** Server 返回一次工具业务错误，之后仍可正常响应
- **THEN** 本次返回失败结果，后续模型新发起的调用可继续使用连接

### Requirement: 响应恢复与工具不自动重发
系统 SHALL 允许协议传输层以 GET 和响应事件标识恢复已发请求的 SSE 响应流，恢复 SHALL 受原请求期限和取消控制。系统 SHALL NOT 因工具失败、超时、HeaderMismatch 或输入续调要求自动再次发出原 tools/call，SHALL NOT 开展周期性健康检查、应用层自动重连或 Server 重启。协议协商回退和工具列表分页 SHALL 不作为工具重试；Agent 根据失败结果产生的新调用 SHALL 是新的显式调用。

#### Scenario: 恢复剩余响应
- **WHEN** 支持恢复的 HTTP 响应流中断但原请求未超时或取消
- **THEN** 可以用 GET 恢复接收，不重复提交原 tools/call POST

#### Scenario: 要求重新提交工具
- **WHEN** Server 返回 HeaderMismatch、输入续调要求或一次请求错误
- **THEN** 当前调用返回明确失败／不支持结果，适配层不隐式再次调用工具

#### Scenario: 恢复超过期限
- **WHEN** 传输层仍在恢复但本地调用期限已到
- **THEN** 停止本地等待并返回 timeout，不以每次恢复重置总期限

#### Scenario: 旧协议响应恢复期间取消
- **WHEN** 旧协议 HTTP 工具调用在 GET 恢复响应时被本地取消或达到原期限
- **THEN** 结束该请求的响应读取，不再发送该请求的恢复 GET；共享连接及其他请求仍可使用，不把调用者结束等待冒充后台恢复已停止

### Requirement: 启动与请求取消
启动期间 Ctrl+C SHALL 停止初始化和待启动项，回收已创建资源后退出。工具请求的默认执行上限 SHALL 为 30 秒，授权等待不计入；远端参数中的 timeout_seconds SHALL NOT 修改该本地上限。单调用超时或任务取消 SHALL 清理其本地等待，并使用适用的远端取消机制，但 SHALL NOT 因此主动关闭仍可用的共享连接。请求已发出而结果未知时 SHALL 说明可能已有副作用，不声称远端已停止或回滚。

#### Scenario: 初始化中取消
- **WHEN** 一个 Server 还在连接、另一个已有进程时用户按 Ctrl+C
- **THEN** 启动停止，已创建资源被清理，应用不继续进入聊天或遗留本次创建的 Server

#### Scenario: 取消单次远端调用
- **WHEN** 用户取消一个已发送的 MCP 请求，连接本身仍可用
- **THEN** 当前任务补齐取消结果且不重发，下一任务仍可使用原连接

### Requirement: 独立且有界的退出清理
应用退出 SHALL 停止接受新调用，清理请求并分别关闭每个 Server；每个 Server 从开始关闭到正常／强制清理结束 SHALL 共用 5 秒总预算，其中前 3 秒用于正常退出，剩余时间用于取消 owner、关闭本地资源及必要的进程终止／回收。超出总预算仍无法回收 SHALL 报告清理失败，SHALL NOT 无限等待或宣称已完成回收；一个 Server 失败 SHALL NOT 阻止其他 Server 和模型客户端收尾。stdio SHALL 回收本次创建的子进程和可管理后代；HTTP SHALL 关闭本地流与连接资源并按适用协议尝试结束会话，SHALL NOT 承诺终止远端服务。关闭 SHALL 可重复调用。stdio stderr SHALL 被持续、有界处理，不与协议 stdout 混淆，也不原样显示连接秘密。

#### Scenario: 一项关闭卡住
- **WHEN** Server A 不响应关闭，Server B 能正常关闭
- **THEN** B 与模型客户端完成收尾，A 正常关闭 3 秒仍未完成则使用剩余预算强制清理，总计 5 秒后如仍未回收则报告失败，应用不会无限等 A

#### Scenario: EOF 或退出指令
- **WHEN** 用户通过 EOF 或 `/exit` 结束应用
- **THEN** 缓存连接关闭，本次创建的 stdio 进程被回收，重复关闭不重复启动或报未处理异常

#### Scenario: stderr 输出持续增长
- **WHEN** stdio Server 大量输出日志到 stderr
- **THEN** 协议收发不因未读 stderr 阻塞，内存保持有界，日志不被解析为工具响应或原样泄露凭据
