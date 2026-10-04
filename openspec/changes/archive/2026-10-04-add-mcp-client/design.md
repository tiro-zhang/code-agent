## Context

动机见 [proposal.md](proposal.md)。现有 `Tool.execute` 是同步入口，`ToolExecutor` 每次以 spawn 启动工作进程，并传入整个注册表；活跃的异步 MCP 会话不能附着在这些待序列化对象上。应用已经使用一个事件循环，但 `ChatSession` 没有异步启动／关闭阶段，`app._run` 最终仅关闭 Provider。

权限基线为已归档的 `2026-10-03-add-permission-system` 及已同步的主规格。运行时对扩展工具有完整参数授权兜底，但规则、批准存储和展示仍围绕六种内置工具；`read_only` 同时控制规划可用范围与连续只读组并发。现有工具结果经两个 Provider 作为 JSON 文本回灌，单次数据内容有 64 KiB 上限。

本设计新增外部依赖并横跨启动、执行、权限和关闭，因此符合 design 产物的创建条件，不跳过设计。

## Goals / Non-Goals

**Goals:**

- 把协议兼容留在 SDK 边界内，把连接生命周期、工具定义和执行授权分离；连接不能被每轮任务关闭或传入本地工作进程。
- 使单个 Server 的连接、发现、调用及关闭失败都有明确归属；工具失败继续使用既有结果通道。
- 保留内置工具隔离、权限检查、调用／结果配对及终端输入规则，为远端取消提供诚实的语义。

**Non-Goals:**

- 功能范围见 proposal 的 Non-Goals；这里不构建通用插件框架，也不重写 Agent 或 Provider 的工具循环。
- 不把 MCP 连接启动当成一次模型工具调用；配置声明授权启动本地 Server，逐次工具调用仍独立经过权限检查。
- 不信任外部工具的文件路径参数具有本地文件工具的沙箱含义，不分析远端程序的内部行为。

## Decisions

### 1. 已确认决定与建议默认值

探索中已经确认：官方 SDK；自动兼容新旧协议；两种传输；同名 Server 项目级整项替换；外部工具沿用三档权限且默认非只读、串行、规划禁用；允许 SDK 恢复响应流，但 MewCode 不重启 Server、不重建失效会话、不自动重发工具。

本提案选择以下**建议默认值，供文档整体审阅**：配置文件名与字段、严格校验及错误范围、命名算法、启动／调用／关闭期限、只在启动发现、结果内容支持范围、规则与授权身份粒度、诊断方式。这些默认值在 specs 中成为可评审的拟议合同，不表示用户在探索中已逐项确认，更不表示实现已获批准。

### 2. 官方 SDK 的采用边界

采用官方 Python SDK 中支持 `Client(mode="auto")` 的发布版本，由其探测协议，并在需要时使用旧版 initialize/initialized；不在 MewCode 硬编码每个 Server 必须握手。stdio 和 HTTP 共享同一套工具管理接口。协议能力仅声明已实现的内容，不安装 sampling、elicitation、roots 等回调，也不接受服务端 instructions 自动注入系统提示。

工具调用通过 SDK 低层 session 的单次 `call_tool` 路径，避免高层 `Client.call_tool` 自动处理 `HEADER_MISMATCH` 重发或多轮输入续调。需要未实现能力、服务端要求 task-only 执行、结构不支持或协议出错时返回明确结果；由 Agent 看到结果后决定后续行动，适配器不隐式重发。协议协商所需探测／回退、工具列表分页和 SDK 用 GET 恢复响应流不属于工具重试。

已核查的上游行为：

- [自动协商](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/client/_probe.py)：在同一连接中 discover，必要时回退旧握手；网络错误不应被当成版本回退成功。
- [SDK 迁移说明](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/migration.md)：新版接口和 HTTP 配置方式，以及保留的响应流恢复行为。
- [HTTP transport](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/client/streamable_http.py)：恢复 SSE 时使用 Last-Event-ID GET；公开入口未提供关闭恢复的选项。
- [高层调用](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/client/client.py) 与 [低层 session](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/client/session.py)：高层额外续调行为应绕开。
- [2026-07-28 变更](https://blog.modelcontextprotocol.io/posts/2026-07-28/) 与 [2025-11-25 生命周期](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)：两代协议采用不同初始化方式。

这些源码链接跟随 main，不能代替发布版本验证。实现首项任务应选定可安装版本、记录版本／源码标签并锁定依赖，用真实 SDK 合同测试证明以上假设；若不满足，不静默退回仅支持旧协议或自研协议栈，需修订设计。HTTP 定制 headers 与 timeout 放在该 SDK 版本要求的 HTTP client 上；不沿用旧版本已移除的参数。

2026-10-04 已验证并锁定官方 `mcp==2.3.0`。真实测试发现旧协议 HTTP 调用取消后 SDK 仍继续 GET 响应恢复，详见 [合同证据](evidence/sdk-contract.md)。用户已确认补充 HTTP 单请求生命周期适配、保留原合同：MewCode 经 SDK 支持的自定义 HTTP client 和响应流扩展点，为每次工具请求关联独立的取消状态与固定 deadline；在超时／取消时结束对应响应读取，并阻止该请求继续发出恢复 GET。不关闭共享连接，不改变协议帧、不实现请求 ID 配对，不修改 SDK 私有状态；旧协议的取消通知仍由 SDK 发出。真实 SDK 测试同时验证新旧协议停止恢复、后续调用成功、其他请求及 Server 不受影响。

替代方案：自研请求配对和传输已在探索中排除；直接使用高层工具自动续调会扩大已确认范围；固定旧协议不能满足自动兼容的决定。

### 3. 配置快照、覆盖与变量展开

新增独立 MCP 配置加载器，沿用项目已有 YAML 依赖和严格安全加载约定，但不复用权限规则的合并语义。

| 来源 | 位置 |
|---|---|
| 用户级 | `~/.mewcode/mcp.yaml` |
| 项目级 | `<root>/.mewcode/mcp.yaml` |

root 固定为启动时解析后的工作根，与供应商 `.env` 所在位置无关。顶层允许 `version`（缺省 1）和 `mcpServers`（缺省空 map）；不存在或空文件等同空配置。不增加本地第三层、disabled 标记或热加载。

```yaml
version: 1
mcpServers:
  local-search:
    transport: stdio
    command: python
    args: ["/absolute/path/to/server.py"]
    env:
      SEARCH_TOKEN: "${SEARCH_TOKEN}"
  docs:
    transport: http
    url: https://example.com/mcp
    headers:
      Authorization: "Bearer ${DOCS_TOKEN}"
```

先解析文档结构，再按原始 Server 名进行浅合并；项目级同名值完整取代用户级值，包括 transport、args、env、headers。随后只校验和展开最终生效的条目，覆盖掉的条目不需要满足其字段／环境变量要求。项目级同名坏条目不回退到用户级。空 `mcpServers` 不清除用户级 Server。

stdio 要求非空 command，args 缺省 `[]` 且每项为字符串，env 缺省 `{}` 且键值为字符串；HTTP 要求有效 http(s) URL，headers 缺省 `{}` 且键值为字符串。transport 必填，只接受 stdio/http，两类字段不能混用；不接受 URL userinfo，URL query 仍可存在但不进入诊断。重复 YAML 键、不安全标签、未知顶层字段或不支持 version 使整个 MCP 配置不可用，显示原因但保留普通聊天与内置工具；最终单个 Server 的坏字段仅禁用该 Server。

仅对最终条目的 env、headers 值做单遍 `${VAR}` 替换，VAR 是合法环境变量名，取启动时进程环境快照；不读取供应商 `.env` 作为插值来源，不做 shell 展开、默认值语法或递归展开。缺失变量禁用该条目并只显示变量名和 Server 名；已设置的空值按空字符串处理。command、args、url 不展开。stdio 按参数数组启动而非 shell，cwd 使用 root，子进程环境由 SDK 基础环境与显式 env 合成。

替代方案：字段级深合并会遗留旧 headers 或跨 transport 字段；同时扩展现有供应商 `.env` 会混淆凭据与配置生命周期；坏项目条目回退可能启动用户未期望的另一个 Server。

### 4. 生命周期所有者与故障隔离

`app._run` 在现有供应商／权限初始化成功后、首次提示符前启动 `MCPManager`。初始化权限失败时不拉起 MCP 子进程。Manager 持有一个 Server 名到连接记录的映射，生命周期覆盖本次应用运行。

每个 Server 使用独立长寿命 owner task，在同一 task 内进入并退出 SDK 的 transport/client 上下文；连接初始化与退出不拆到不同 task。应用现有 `protected()` 会用 ensure_future，故不直接将跨 task 退出的 SDK context 交给它；manager.close 只通知并等待 owner 收尾。各 owner 自行捕获错误，不让一个 SDK task group 的异常取消其他 owner 或应用主循环。

实施采用父进程内每 Server 一个独立守护线程事件循环，owner 始终在该循环内管理 SDK 上下文，正常关闭后线程退出。这样第三方 owner 完全忽略取消时，5 秒后仍能报告清理失败并完成应用收尾，不被主 `asyncio.run` 的隐式等待拖住。stdio 内部启动器固定配置环境、记录本次 PID／进程组，并在同一 PID exec 目标程序；SDK 仍负责协议与管道。关闭前缓存进程组身份，主进程正常退出也检查可管理后代，不把 owner 返回等同于整个进程组已清空。

```text
Application event loop
  |
  +--> MCPManager
  |      +--> Owner A --> SDK client A --> stdio process
  |      +--> Owner B --> SDK client B --> HTTP endpoint
  |
  +--> Registry descriptors --> Executor
                                 |
                                 +--> Local worker
                                 +--> MCPManager call
```

状态为 `starting -> ready -> failed -> closed`，启动失败可直接进入 failed；ready 无工具也合法。每个 Server 的连接、协商和完整工具发现共用 15 秒期限；连接准备最多 4 个并行，完成后按 Server 名和原始工具名排序注册，使声明顺序不取决于网络速度。启动期间 Ctrl+C 有独立取消入口，取消全部待启动／正在启动项，回收已创建资源并退出。

每次调用默认 30 秒，从授权结束、准备发送请求时起计算；远端参数中恰好名为 timeout_seconds 的字段不改变本地期限。期限包住 SDK 流恢复，避免其多次恢复延长调用等待。单请求超时、业务错误或仍可继续使用连接的 HTTP 错误不自动标记整个 Server 失效；进程退出、连接接收循环终止、旧协议会话失效等确认无法再使用的连接进入 failed，当前等待者获得错误。

HTTP 单请求生命周期覆盖实际网络发送与响应流读取，不能仅以 `wait_for(session.call_tool(...))` 的调用者返回作为停止恢复的证据。取消后的恢复 GET 计数必须保持不变；SDK 的连接级 GET 监听与其他调用不属于该次取消范围。已收到真实结果时保留结果，随后关闭该调用的生命周期；退出时对全部活动调用应用相同清理。

旧协议礼貌取消通知使用独立 50 毫秒 I/O 期限，不继承已经结束的原请求，也不无限占用 SDK 的共享发送通道。它是尽力发送的通知，不改变原工具调用期限或副作用说明。

发现后失效的工具保留注册标识，以 `mcp_unavailable` 快速失败，避免既有历史被改成 unknown_tool；failed 状态后不尝试新网络调用。启动未成功完成发现的 Server 不注册工具。工具列表只在启动发现，不处理动态刷新通知。

退出时先停止接受新调用并取消活动请求，再通知每个 owner 关闭；每个 Server 共用 5 秒总关闭预算，前 3 秒正常退出，剩余时间取消 owner、强制关闭本地资源并终止／回收本次创建的 stdio 进程及可管理后代。各项独立关闭，不让一个卡住阻止其他资源及 Provider 收尾；总预算到期仍无法回收必须报告失败，不能继续无界等待或宣称资源已回收。HTTP 关闭连接池／流，并依协商协议让 SDK 尝试结束旧会话，不承诺能停止外部服务。EOF、/exit、启动取消和异常退出使用同一清理职责，重复关闭幂等。

替代方案：每次工具调用重新建立连接破坏缓存；一个共享异常任务组可能联动取消；将活跃 Client 存在工具对象中会破坏整个 registry 的 spawn 序列化。

### 5. 工具发现、标识与执行分流

连接成功后消费 `tools/list` 的全部分页，遇到重复 cursor、发现超时或列表协议错误则该 Server 发现失败，不发布半份清单。单个工具的非法名称、Schema 或 task-only 要求只排除该工具并汇总原因。支持缺省或明确 JSON Schema 2020-12 的对象 Schema，保留原有约束，不为 Provider 静默删除字段；外部 `$ref` 不触发网络读取，无法支持的引用／方言使该工具不可用。

发现阶段检查文档内引用的目标和作用于同一实例的引用环；支持通过 properties/items 递进到子实例的合法递归 Schema。统一参数校验同时将校验器异常归一化为 ToolError，使调度始终生成配对结果。

注册项保存可序列化的工具描述、原始名称、Server 名及执行种类；外部适配器不保存 Client、流、锁或 Future。`ToolExecutor` 在统一 prepare／模式／权限检查后分流：本地同步执行沿用工作进程，远端由父进程异步委托 manager。明确引入异步工具执行契约，不能让原同步 `Tool.execute` 返回一个未等待的 coroutine。注册表的声明、名称查找和参数校验保持共用；Agent 和调度器不按 MCP 名称做特判。

模型名称采用稳定别名，形式为 `mcp__<server-slug>__<tool-slug>__<digest16>`：slug 将非 ASCII 字母／数字／下划线替换为下划线，分别取最多 12 和 20 字符，空值使用 `server`／`tool`；digest 是原始 `[server_name, tool_name]` 以不转义中文、紧凑逗号／冒号分隔序列化的 UTF-8 JSON 的 SHA-256 前 16 位十六进制。别名总长不超过 64，保留原始名字用于实际 `tools/call`。始终带摘要，避免短名与截断规则变化形成两种身份；别名只由名字决定，不由发现顺序或连接凭据决定。

发现阶段检查别名冲突，任何实际冲突拒绝相冲突的外部工具，不靠注册顺序覆盖；同一个 Server 返回重复原始工具名同样拒绝这些重复项。不同 Server 的同名工具及与内置工具同名均可共存。

每个 MCP 工具统一设 `read_only=False`，保留但不执行 readOnlyHint 等 annotations 的权限含义。执行模式导出全部注册工具；规划模式仅导出三个内置只读工具，并在执行入口再次拒绝 MCP 调用。提示改成“当前提供的工具”，不把数量固定为六个。由于全部 MCP 工具为串行边界，本期不承诺 Agent 同批并发 MCP 调用；SDK 并发请求关联仍由合同测试验证。

### 6. 外部工具权限与批准身份

继续使用现有三来源规则、deny > ask > allow、三档模式及授权 UI。MCP 的规则工具名使用完整稳定别名，匹配对象为完整参数的规范 JSON（递归按 Unicode 码点排序对象键、紧凑分隔、不转义非 ASCII 字符、保留数组顺序和 JSON 值类型、拒绝非有限数值）；exact 比较全串，glob 使用整串 glob，不能把 path、command 等远端字段解释为本地文件或 Bash 操作。合法 MCP 别名的规则允许在 Server 离线时加载，注册状态只决定是否可调用；其他未知工具仍报配置错误。

本次授权绑定原调用、完整参数与当前 Server 身份；会话／永久授权绑定 `root + server_name + server_config_fingerprint + original_tool_name + canonical_arguments`。指纹输入集中定义在 mcp-configuration：填充缺省值后按上述规范 JSON 序列化，再计算 SHA-256；包含 Server 名、transport，stdio 包含真实 cwd、按最终 PATH 解析并固定的实际可执行文件绝对路径、args、SDK 基础环境与显式 env 合成的完整有效环境，HTTP 包含实际 url 与最终 headers。实际启动必须使用该快照；无法解析可执行文件时该 Server 初始化失败。只持久化摘要和必要的非秘密身份，不保存 env/header 明文。地址、实际程序路径、参数或有效环境／凭据变化会使旧批准不匹配；工具变化或项目根变化同理。指纹不是程序内容完整性校验或远端身份认证。权限规则本身是用户显式配置，按稳定别名继续生效，不伪装成随指纹失效的批准记录。

扩展 approvals 的 MCP 范围种类而不复用本地 command/path；本地旧记录格式继续有效。MCP 记录保留别名、Server 名、原始工具名、指纹和完整规范参数，严格验证身份字段一致性；Server 离线不使已存记录损坏整个权限配置。永久批准仍仅落 `permissions.local.yaml`，沿用原子写入、并发检测、保存失败降级为本次批准和撤销入口。

授权 UI 展示 Server 名、工具原名、有效端点或启动程序的安全描述、完整调用参数及精确记忆范围；不宣称远端参数是已验证的真实文件路径。连接凭据脱敏不改动实际用于授权绑定的参数；明确提示永久批准会把调用参数保存在本地授权文件。连接启动不经过每次工具审批，不新增项目信任弹窗；项目配置可启动本地程序的事实在 README 中说明。

实际执行保留 PATH 定位后的绝对启动入口，包括 venv 的符号链接入口；真实目标路径另纳入指纹，不用它替换启动入口。HTTP 安全描述只显示协议与主机／端口，省略可能含凭据的路径、查询和片段；stdio 显示入口路径，不显示 args/env。

替代方案：整台 Server 或整个工具的永久批准会扩大用户一次批准的范围；只用 Server 名会把旧服务批准套给同名新地址；把所有远端路径送进本地路径检查无法约束远端行为。

### 7. 结果、错误与取消

适配到现有 `ToolResult`。文本 content 按顺序保留，`structuredContent` 保留 JSON 结构；resource link 仅保留描述与 URI，不抓取，内嵌文本可作为已有结果文本保留。图像、音频和二进制 resource 不把 base64 填入模型文本，改为类型／MIME 与明确的未支持内容标记。纯非文本成功结果仍保留远端成功状态，但清楚告知没有可供当前模型使用的正文，不假装分析过图像。

所有远端可控结果内容共用现有 64 KiB 内容预算，包括文本、结构化数据、资源 URI／描述、类型／MIME 元信息和远端错误正文／details；只有本地生成的固定信封字段例外，不能把远端数据移入 error/details 绕过预算。优先保留可以完整容纳的 structuredContent，然后以剩余预算按原顺序保留文本及其他内容；结构化对象过大时整体省略并记录原因，不截断 JSON 字节生成坏对象。任何省略均设置 truncated 并提供原因；不得把同一 structuredContent 的文本副本误当独立完整结果保证。

| 情况 | 结果 |
|---|---|
| MCP `isError=true` | `mcp_tool_error`，保留远端文本／结构化数据 |
| JSON-RPC 错误、请求级 HTTP 错误 | `mcp_request_error`，保留必要的安全错误信息 |
| 无法转换的响应 | `mcp_protocol_error` |
| 未实现的输入续调／能力要求 | `mcp_unsupported_capability`，不自动后续调用 |
| Server 已不可用 | `mcp_unavailable` |
| 本地调用期限届满 | `timeout` |
| 本地用户任务取消 | `cancelled` |

只有本地取消入口产生触发整轮取消的 cancelled；远端错误码或正文包含 cancelled 不映射成用户取消。发送前失败可说明 not_started；请求已经发出而结果不确定时说明 `side_effects_may_have_occurred=true`，不将超时等同未执行，不承诺回滚。

单请求取消清理该请求等待者并使用 SDK 适用的取消路径，不关闭共享 Server 连接；远端是否停止无法保证。若已获得真实结果，收尾阶段取消保留它。应用退出另行关闭所有 Server。SDK 原始异常和子进程 stderr 不直接进入终端或模型；诊断只显示 Server、阶段和安全原因，清除配置中已知 env/header 值、URL query 等凭据。stdio stderr 必须持续排空且有界处理，不阻塞协议 stdout，不把日志行当 JSON-RPC。

### 8. 验证与验收

验证聚焦跨边界合同：实际 SDK 接两代协议、两种传输、分页和请求 ID 关联；内置 spawn 与 MCP 连接共存；权限矩阵和批准身份；一次工具调用不被 SDK 隐式重发；流恢复不突破 deadline；单 Server 失败／关闭卡住不影响其他；启动及调用取消、重复 Ctrl+C 和 EOF 清理。

使用可控制的本地 stdio/HTTP Server fixture，记录 initialize/discover/list/call、进程生命周期和执行计数。对 SDK 真实协议交互做集成测试，不只 mock 自己的 adapter。实施阶段在 tmux 中启动真实 MewCode、发送自然语言请求并观察真实模型选用 MCP 工具，覆盖两传输、两轮连接复用、拒绝后继续、规划禁用、一个 Server 挂掉后调用另一个及退出回收；追加独立 MCP checklist 并逐项记录。远端服务与凭据不足时报告未验收项，不以单元测试或模拟模型冒充端到端通过。

## Risks / Trade-offs

- [SDK main 与发布版接口不同] → 首项锁定可安装版本，以合同测试验证探测、低层调用、取消及流恢复，不根据文档示例盲装。
- [共享连接因单请求取消或 task group 异常被一起关闭] → 每 Server owner 隔离，单请求取消与整个连接关闭分别测试。
- [本地工具 spawn 序列化注册表失败] → 注册项不持活跃会话，加入 MCP 存在时调用内置工具的回归。
- [远端超时后仍产生副作用] → 不重发、明确不确定状态，让模型依据结果核查。
- [默认非只读使查询类 MCP 也串行且规划不可用] → 遵守已确认边界，未来显式设计信任策略后再扩展。
- [工具 schema／非文本内容超出当前能力] → 明确排除或标记，保留可处理内容，不静默改写约束。
- [同名 Server 更换后复用旧批准] → 精确批准绑定有效配置指纹，规则与批准仍分别解释。
- [配置或 SDK 日志泄露凭据] → 不输出原始配置、异常、stderr；仅生成受控诊断并测试已知秘密不外泄。

## Migration Plan

1. 保持供应商 CLI 和内置工具行为；无 MCP 文件时没有额外连接，旧本地授权记录继续可读。
2. 先验证依赖和协议合同，再实现配置、连接 owner、适配、执行与权限，最后接启动和终端。任务见 tasks.md。
3. 独立新增 MCP 文档与 checklist，回归已有功能并执行真实 tmux 验收，完成后再考虑归档和同步规格。
4. 回退到不认识 MCP 批准范围的旧版本前，通过支持 MCP 的版本撤销本项目永久批准或备份并移除 MCP 批准条目；不能把旧版严格解析失败描述为兼容。保留配置文件不代表旧版会加载外部工具。
