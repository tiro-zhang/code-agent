# mcp-tools Specification

## Purpose

定义外部 MCP 工具如何获得稳定可调用的身份、保留参数和结果语义，并进入 MewCode 现有的权限、调度与结果回灌流程，使模型使用统一工具接口，同时明确外部能力、授权范围和非文本结果的边界。

## Requirements

### Requirement: 稳定且无覆盖的外部工具标识
每个可用 MCP 工具 SHALL 获得由原始 Server 名与工具名确定的稳定别名，位于 `mcp__` 命名空间，仅使用 ASCII 字母、数字和下划线，总长不超过 64；别名 SHALL NOT 因发现顺序、连接参数或凭据变化而改变。别名与原始 Server／工具名 SHALL 有明确映射，实际远端调用 SHALL 使用原始工具名。不同 Server 同名工具及与内置工具同名 SHALL 能并存；实际别名冲突或 Server 返回重复原名时 SHALL 拒绝相关外部项并诊断，SHALL NOT 覆盖已有工具。

#### Scenario: 多个 search 工具
- **WHEN** 两个 Server 都提供 search，另有同名内置工具
- **THEN** 三者具有不同调用身份，外部调用分别发往正确的 Server

#### Scenario: 名称含点号或很长
- **WHEN** 原始名称超出模型工具名的字符或长度约束
- **THEN** 生成满足本地约束且稳定的别名，仍按完整原名调用远端

#### Scenario: 重启后的注册顺序变化
- **WHEN** 相同 Server／工具名在下次启动以不同发现顺序返回
- **THEN** 别名保持一致，既有规则不因顺序变化指向其他工具

### Requirement: 工具定义与 Schema 忠实适配
系统 SHALL 将有效工具的描述与对象 inputSchema 纳入统一工具声明，保持参数约束；缺失 description SHALL 使用包含 Server／工具名的简短说明。系统 SHALL 支持缺省或明确 JSON Schema 2020-12，SHALL NOT 为适配模型静默删除不支持的约束。非法 Schema、不支持方言、需访问外部资源的引用或 task-only 工具 SHALL 被排除并解释原因；Schema 处理 SHALL NOT 自动读取远程引用。其他有效工具 SHALL 继续注册，两个模型协议 SHALL 使用相同名称和参数语义。

#### Scenario: 一个坏 Schema
- **WHEN** 一个 Server 同时返回一个合法工具和一个非法 inputSchema 工具
- **THEN** 合法工具可用，非法工具被排除，内置和其他 Server 不受影响

#### Scenario: 外部 Schema 引用
- **WHEN** 工具 Schema 依赖外部 URL 引用
- **THEN** 不向该 URL 发起解析请求，该工具被明确排除

### Requirement: 外部调用统一经过权限与模式检查
MCP 工具 SHALL 进入现有工具参数校验、模式限制、权限判定、调度及结果回灌流程。全部 MCP 工具 SHALL 默认非只读，SHALL NOT 因 readOnlyHint 或工具名看似查询而进入规划模式或只读并发组。Server 元数据 SHALL NOT 改变本地权限或被自动提升为系统指令。未经批准的调用 SHALL NOT 发出 tools/call；连接初始化与工具发现 SHALL 独立于逐次调用授权。

#### Scenario: 外部工具宣称只读
- **WHEN** Server 返回 readOnlyHint=true 的工具
- **THEN** 执行模式仍按非只读串行边界调度，规划模式不暴露且执行入口拒绝它

#### Scenario: 需要批准
- **WHEN** MCP 调用在 default 下无放行规则或有效批准
- **THEN** 先展示授权，批准前没有 tools/call，拒绝得到未启动的 permission_denied 且不取消其他调用

#### Scenario: 连接信息含指令
- **WHEN** Server 的 instructions、description 或工具结果要求绕过权限
- **THEN** 不改变模式或授权，相关文字不能生成可信控制决定

### Requirement: 精确的外部规则与批准范围
MCP 规则 SHALL 使用稳定别名及完整规范 JSON 参数作为匹配对象，不把远端 path/command 字段解释成本地路径或 Bash。规范参数 SHALL 递归按 Unicode 码点排序对象键、紧凑序列化并保留非 ASCII 字符、数组顺序与 JSON 值类型，拒绝非有限数值。规则合并和模式矩阵 SHALL 遵守 tool-permissions；合法外部别名的离线规则及批准记录 SHALL 可加载，但离线规则 SHALL NOT 注册或启动工具。

存续批准 SHALL 精确绑定真实 root、Server 名、mcp-configuration 定义的有效连接配置指纹、原始工具名和规范参数；任一项改变 SHALL 不匹配旧批准。持久记录 SHALL NOT 保存连接 env/header 明文。会话与永久批准 SHALL 沿用现有有效期、写入和撤销语义；永久批准保存完整调用参数这一事实 SHALL 向用户说明。

#### Scenario: 完整参数相同
- **WHEN** 同一 Server／工具在同一 root 使用仅对象键书写顺序不同的参数，且有效连接配置相同
- **THEN** 可复用同一精确会话／永久批准

#### Scenario: 改变参数或端点
- **WHEN** 调用参数值、数组顺序、原始工具名或有效 Server 连接配置发生变化
- **THEN** 不复用旧精确批准，重新依据规则和权限模式判定

#### Scenario: Server 离线但权限记录存在
- **WHEN** 启动发现失败且配置保留语法有效的 MCP 别名规则或批准记录
- **THEN** 权限配置仍可加载，内置工具可用，外部工具不会仅凭规则而被调用

### Requirement: 文本与结构化结果转换
外部结果 SHALL 转为包含 ok、data、error、truncated 的统一可序列化结果。文本 content SHALL 按顺序保留，structuredContent SHALL 保留可用 JSON 结构；资源链接 SHALL 仅作为描述和 URI 返回而不抓取，内嵌文本可作为已返回数据保留。图像、音频和二进制内容 SHALL 用类型／MIME 及未支持标记说明，SHALL NOT 把 base64 冒充可理解的文本或声称已经分析非文本内容。

#### Scenario: 同时返回文本与对象
- **WHEN** MCP 返回文本解释及 structuredContent 对象
- **THEN** 结果包含两者，在模型历史中与原工具调用 ID 配对

#### Scenario: 仅返回图像
- **WHEN** 远端工具成功但只返回图像
- **THEN** 保留远端成功状态，明确当前模型没有收到可处理的图像正文，不将 base64 直接灌入文本

#### Scenario: 结果含资源链接
- **WHEN** 工具返回资源 URI
- **THEN** 展示其可用元信息，不隐式发出 resources/read 或访问 URI

### Requirement: 外部结果有界且错误不丢失
所有远端可控结果内容 SHALL 共用 64 KiB UTF-8 内容预算，包括文本、结构化数据、资源 URI／描述、类型／MIME 元信息及远端错误正文／details；只有 MewCode 自身生成的固定结构化信封字段不计入该预算，SHALL NOT 把远端数据放进 error/details 来绕过限制。优先保留可完整容纳的 structuredContent，再按原顺序使用剩余预算保留文本及其他内容。过大结构对象 SHALL 整体省略并说明，SHALL NOT 截断 JSON 成非法结构。任何内容省略 SHALL 设置 truncated 并说明原因。`isError=true` SHALL 转为 `mcp_tool_error` 且保留预算内可用远端内容，不仅使用供应商错误标记。

#### Scenario: 超大结构对象
- **WHEN** structuredContent 超出结果内容预算
- **THEN** 结构对象被明确标为省略，仍可在预算内保留文本，结果合法且 truncated=true

#### Scenario: 工具报告业务失败
- **WHEN** MCP 返回 isError=true、文本和结构化错误数据
- **THEN** ok=false，包含 mcp_tool_error 和可用数据，模型可以根据正文调整行动

#### Scenario: 元数据与错误不能绕过上限
- **WHEN** 结果含超长资源 URI、描述或远端错误 details，混合正文总量超过 64 KiB
- **THEN** 所有远端内容共同受限，超限内容被省略或安全截短并标明，不能借结构化信封例外绕过上限

### Requirement: 外部错误分类与副作用说明
请求级 JSON-RPC／HTTP 失败 SHALL 返回 `mcp_request_error`，不可转换的响应 SHALL 返回 `mcp_protocol_error`，要求未支持能力 SHALL 返回 `mcp_unsupported_capability`，已失效 Server SHALL 返回 `mcp_unavailable`。本地期限使用 timeout，本地用户取消使用 cancelled；远端同名错误或文字 SHALL NOT 触发整轮取消。发送前失败 SHALL 可说明 not_started；已发送而结果未知 SHALL 明确可能已有副作用。已完成真实结果 SHALL 在取消清理边界保留。

#### Scenario: 远端声称取消
- **WHEN** 远端业务错误正文或代码包含 cancelled 而本地用户未取消
- **THEN** 作为普通外部错误回灌，其他调用和 Agent Loop 不因该文字被取消

#### Scenario: 请求超时
- **WHEN** tools/call 已发送但本地期限到期且没有确定结果
- **THEN** 返回 timeout 与副作用可能已发生的说明，不自动重发，不声称未启动或已回滚

#### Scenario: 请求需要非工具能力
- **WHEN** 调用返回需要 sampling、elicitation 或其他未实现续调能力的结果
- **THEN** 返回 mcp_unsupported_capability，不请求模型执行服务端采样、不采集额外输入、不隐式续调
