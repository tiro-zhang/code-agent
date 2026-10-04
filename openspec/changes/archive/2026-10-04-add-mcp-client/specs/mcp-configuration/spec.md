## Purpose

定义用户如何通过用户级和项目级配置声明外部 MCP Server，以及配置覆盖、变量展开和错误处理的可观察行为，使启动时的连接集合确定、可解释，并保持现有供应商和权限配置的独立语义。

## ADDED Requirements

### Requirement: 两层独立 MCP 配置
系统 SHALL 从 `~/.mewcode/mcp.yaml` 和 `<root>/.mewcode/mcp.yaml` 读取 MCP 配置；root SHALL 是启动时解析后的工作根目录，与供应商配置文件位置无关。顶层 SHALL 仅接受 `version` 和 `mcpServers`，version 缺省为 1 且只支持 1，mcpServers 缺省为空 map，以非空字符串 Server 名为键。不存在或空文件 SHALL 等同空配置。系统 SHALL 保持供应商 `--config .env` 和权限配置的原有读取及合并行为。

#### Scenario: 没有 MCP 文件
- **WHEN** 两层配置均不存在
- **THEN** MewCode 正常使用内置工具且不启动任何 MCP 连接

#### Scenario: 配置位置独立
- **WHEN** 用户在项目 A 启动而供应商配置位于项目 B
- **THEN** MCP 项目配置来自 A，权限配置和供应商配置仍按各自合同读取

### Requirement: 按 Server 名整项覆盖
系统 SHALL 先按 Server 名合并两层配置，项目级同名条目 SHALL 完整替换用户级条目；args、env、headers SHALL NOT 深合并。不同名条目 SHALL 全部保留。系统 SHALL 仅对最终生效的 Server 条目执行字段校验和环境展开；项目级坏条目 SHALL NOT 回退到同名用户级配置。项目级空 map SHALL NOT 清除用户级条目。

#### Scenario: HTTP 地址与凭据一起替换
- **WHEN** 用户级 docs 含 URL A 和 headers，项目级 docs 仅声明 transport=http 和 URL B
- **THEN** docs 使用 B 和缺省空 headers，不继承 A 的认证信息

#### Scenario: 改变传输
- **WHEN** 项目级将用户级同名 stdio Server 替换为完整 HTTP 配置
- **THEN** 最终条目没有旧 command、args 或 env，不因旧字段而被拒绝

#### Scenario: 被覆盖条目含缺失环境变量
- **WHEN** 用户级同名条目引用缺失变量，但有效的项目级条目不引用它
- **THEN** 被覆盖条目的变量不会使最终 Server 失败

#### Scenario: 项目级条目无效
- **WHEN** 项目级同名 Server 缺少必需字段
- **THEN** 该 Server 不启动，系统报告错误，不启动同名用户级配置

### Requirement: 两种传输的配置合同
每个条目 SHALL 显式声明 transport，仅接受 `stdio` 或 `http`。stdio SHALL 要求非空 command，args 为字符串数组且缺省 `[]`，env 为字符串到字符串的 map 且缺省 `{}`；HTTP SHALL 要求有效 http(s) url，headers 为字符串到字符串的 map 且缺省 `{}`。两类 SHALL 拒绝混用字段、未知字段及非法值；HTTP URL SHALL 拒绝 userinfo。stdio SHALL 直接以 command 和参数数组启动，在 root 工作目录运行，显式 env 覆盖其基础环境，SHALL NOT 将 command/args 拼为 shell 脚本。

#### Scenario: stdio 参数保留字面含义
- **WHEN** args 中一个参数包含空格、分号或美元符号
- **THEN** 它作为一个字面参数传给子进程，不由 shell 拆分或展开

#### Scenario: HTTP 配置非法
- **WHEN** HTTP 条目使用非 HTTP(S) URL、URL userinfo、非字符串 headers 或 stdio 字段
- **THEN** 仅该 Server 被禁用，报告相应字段问题

### Requirement: 最终环境值单遍展开
系统 SHALL 仅在最终生效条目的 env 和 headers 值中，将 `${VAR}` 按启动时进程环境快照单遍替换；VAR SHALL 满足 `[A-Za-z_][A-Za-z0-9_]*`。系统 SHALL NOT 递归展开替换所得文本，不执行 shell 表达式，不从供应商 `.env` 读取插值变量，不展开 command、args 或 url。未定义变量和不支持的 `${...}` 表达式 SHALL 使该 Server 配置无效；已设置的空值 SHALL 作为空字符串使用。

#### Scenario: 认证头拼接
- **WHEN** 进程环境 DOCS_TOKEN 为一个值且 header 为 `Bearer ${DOCS_TOKEN}`
- **THEN** 发出的 header 包含拼接后的值，显示诊断时不输出该值

#### Scenario: 变量不存在
- **WHEN** 某 Server 的 env 或 headers 引用未设置变量
- **THEN** 不启动该 Server，提示 Server 名、字段和变量名，其他有效 Server 继续启动

#### Scenario: 不递归或读取供应商凭据
- **WHEN** 环境变量值本身含 `${OTHER}`，或变量只存在于供应商 `.env`
- **THEN** 前者保留字面 `${OTHER}`，后者视为进程环境中未定义

### Requirement: 严格解析与错误隔离
配置 SHALL 作为数据安全解析，拒绝任意层重复键、不安全对象标签、无效 YAML、非法顶层结构、未知顶层字段及不支持版本。任一来源出现文档级错误时，系统 SHALL 暂停本次运行的全部 MCP 初始化并显示受控诊断，但 SHALL 保持普通聊天与内置工具可用；SHALL NOT 忽略坏层后从另一层推导可能错误的有效集合。文档结构有效但合并后单条 Server 校验失败时 SHALL 仅排除该条目。

#### Scenario: 重复 Server 名
- **WHEN** 同一文件内重复声明同名键
- **THEN** 系统报告配置错误，不按 YAML 最后值静默覆盖，不启动本次 MCP 集合

#### Scenario: 一个坏 Server 与一个好 Server
- **WHEN** map 文档有效，但其中一个 HTTP 条目缺少 url，另一个 stdio 条目有效
- **THEN** stdio 条目正常初始化，HTTP 条目仅产生自己的诊断

### Requirement: 有效连接配置身份
系统 SHALL 为最终有效连接配置生成确定性指纹，输入 SHALL 填充所有缺省值并使用规范 JSON 序列化后计算 SHA-256；规范 JSON SHALL 递归按 Unicode 码点排序对象键、使用紧凑分隔、保留非 ASCII 字符、数组顺序与 JSON 值类型。身份 SHALL 包含 Server 名、transport；stdio 还 SHALL 包含真实 cwd、使用最终环境 PATH 解析并固定的实际可执行文件绝对路径、args 及基础环境与显式 env 合成后的完整有效子进程环境；HTTP 还 SHALL 包含实际请求使用的 url 和最终 headers。条目未能解析到可启动程序时 SHALL 报告该 Server 初始化失败。批准身份 SHALL 使用该指纹，SHALL NOT 只依赖 Server 名或未解析的 command。指纹 SHALL 在本次运行中固定，SHALL NOT 被描述为远端身份认证或程序内容完整性校验。

#### Scenario: PATH 选择了不同程序
- **WHEN** 两次启动的 command 相同，但最终 PATH 解析到不同可执行文件
- **THEN** 有效配置指纹不同，先前的精确批准不能用于新程序

#### Scenario: 默认值和键顺序等价
- **WHEN** 两份条目仅 map 顺序不同，或一份显式提供与缺省值相同的空 args/env/headers，且最终运行环境相同
- **THEN** 有效配置指纹一致

#### Scenario: 继承环境或连接凭据变化
- **WHEN** stdio 最终有效环境、HTTP headers 或实际端点变化
- **THEN** 指纹改变，永久批准不复用；记录仅保存指纹，不保存连接秘密明文

### Requirement: 配置快照与凭据保护
有效配置与展开环境 SHALL 在启动时形成固定快照，本次运行中修改文件 SHALL 不触发重载、重连或工具刷新。连接配置中的 env、headers、URL 查询凭据和原始异常 SHALL NOT 被直接写入模型上下文或终端诊断；诊断 SHALL 使用 Server 名、来源、阶段和可操作的安全原因。配置文件声明启动 stdio 程序的行为 SHALL 在使用文档中明确说明。

#### Scenario: 运行中修改配置
- **WHEN** 用户在会话期间改变 URL 或 command
- **THEN** 本次运行继续使用原快照，重启后才使用新配置

#### Scenario: 连接异常含认证值
- **WHEN** 底层错误包含 header、env 值或带凭据的 URL
- **THEN** 终端和模型仅看到受控错误，不看到原配置秘密
