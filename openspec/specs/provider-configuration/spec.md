# provider-configuration Specification

## Purpose

定义启动时选择与校验模型供应商配置的行为，使用户可以通过不同的 `.env` 文件切换后端，同时避免无效配置进入交互会话。

## Requirements

### Requirement: 按文件选择单组配置
系统 SHALL 要求通过启动参数 `--config` 指定一个 `.env` 文件，并 SHALL 从该文件读取单组供应商配置；启动后 SHALL 显示该配置的 `name`，以便用户辨别当前后端。

#### Scenario: 选择 Claude 配置
- **WHEN** 用户用 `--config` 指向有效的 Claude 配置文件
- **THEN** 系统使用该文件中的供应商信息启动会话

#### Scenario: 切换配置
- **WHEN** 用户在下次启动时用 `--config` 指向另一份有效配置文件
- **THEN** 系统使用新文件中的配置，而不沿用上次运行的配置

#### Scenario: 未指定配置文件
- **WHEN** 用户启动 MewCode 时没有提供 `--config`
- **THEN** 系统说明必须指定配置文件，且不进入交互会话

### Requirement: 六字段配置格式
配置 SHALL 保留 `name`、`protocol`、`model`、`base_url`、`api_key` 和 `thinking` 六个基础字段的名称及含义，其中前五个必填；此外 SHALL 要求 `context_window`，并支持可选 `max_output_tokens`。`thinking` 为可选布尔字段；`thinking` SHALL 接受 `true` 或 `false`，省略时视为 `false`。`protocol` SHALL 接受 `anthropic` 或 `openai`，`base_url` SHALL 表示相应协议的 API 基础地址。

#### Scenario: 省略可选字段
- **WHEN** 配置文件包含有效的供应商必填字段和 context_window，而没有 thinking
- **THEN** 系统按 `thinking=false` 加载配置

#### Scenario: OpenAI 兼容地址
- **WHEN** `protocol=openai` 且 `base_url` 指向符合 Chat Completions 协议的第三方 API 基础地址
- **THEN** 系统将该地址用于 OpenAI 兼容请求

#### Scenario: DeepSeek Anthropic 兼容地址
- **WHEN** `protocol=anthropic` 且 `base_url=https://api.deepseek.com/anthropic`
- **THEN** 系统将该地址用于 Anthropic SDK 的 Messages 请求

### Requirement: 启动前校验
系统 SHALL 在进入交互会话前检查配置文件是否存在、必填字段是否非空、`protocol` 与 `thinking` 是否有效，以及 context_window 是否存在、context_window、max_output_tokens 和 max_iterations 的显式值是否为十进制正整数，并检查 context_window 大于 max_output_tokens 加 13000，并在校验失败时指出可修正的字段。

#### Scenario: 缺失 API 凭据
- **WHEN** 指定文件缺少 `api_key` 或其值为空
- **THEN** 系统提示 `api_key` 配置错误，并且不进入交互会话

#### Scenario: 不支持的协议
- **WHEN** `protocol` 既不是 `anthropic` 也不是 `openai`
- **THEN** 系统提示有效的协议值，并且不进入交互会话

#### Scenario: OpenAI 配置启用思考
- **WHEN** `protocol=openai` 且 `thinking=true`
- **THEN** 系统提示该字段在本阶段仅适用于 Anthropic 协议，并且不进入交互会话

#### Scenario: 非法请求上限
- **WHEN** 显式的 `max_iterations` 为零、负数、空字符串或非整数
- **THEN** 系统提示该字段必须是正整数，且不发起模型请求或进入交互会话

#### Scenario: 旧配置缺少窗口
- **WHEN** 供应商配置有效但没有 context_window
- **THEN** 启动失败并说明需要按所选服务实际窗口补充该字段，不请求模型，不猜测默认窗口

#### Scenario: 安全余量无法容纳
- **WHEN** context_window 小于或等于 max_output_tokens 加 13000
- **THEN** 启动提示窗口与输出额度不匹配，不进入会话

#### Scenario: 非法窗口或输出上限
- **WHEN** context_window 或显式 max_output_tokens 为零、负数、空值或非整数
- **THEN** 提示具体字段需要十进制正整数，不请求模型，不输出 API 密钥

### Requirement: 凭据不泄露
系统 SHALL NOT 在正常输出或配置及请求错误提示中显示 `api_key` 的值。

#### Scenario: 配置验证失败
- **WHEN** 包含 `api_key` 的配置文件有其他字段无效
- **THEN** 系统的错误输出不包含 API 密钥明文

### Requirement: Agent 请求预算配置
配置文件 SHALL 支持可选的 `max_iterations` 十进制正整数字段，省略时使用 20。该值 SHALL 用作每次用户任务的应用层 LLM 请求上限，不表示工具调用数量；系统 SHALL NOT 将零、负数、空值或非整数解释为无限执行。原有供应商基础字段、thinking 和 --config 用法 SHALL 保持含义；旧配置 SHALL 补充新增必填 context_window 后启动。自动摘要调用 SHALL 计入当前任务请求上限，空闲手动压缩 SHALL 独立限为一次，不占下个任务预算。

#### Scenario: 旧配置继续使用
- **WHEN** 用户使用没有 `max_iterations` 的有效供应商配置
- **THEN** 系统正常启动，每次用户任务使用 20 次请求预算

#### Scenario: 自定义请求上限
- **WHEN** 有效配置包含 `max_iterations=5`
- **THEN** 每次用户任务最多发起 5 次应用层 LLM 请求，包括最终答复、自动摘要及上下文恢复请求

### Requirement: 显式窗口与统一输出预算配置
配置 SHALL 使用 context_window 表示服务实际可用的总上下文窗口，不按协议或模型名称猜测。可选 max_output_tokens SHALL 默认 8192，作为工作和摘要请求的实际输出上限，并在各自请求预算中预留同样的额度；摘要草稿、正式摘要以及服务计入输出额度的思考 SHALL 共用该上限。系统 SHALL 在输出预留之外分别使用自动 13000 和手动摘要 3000 的估算安全余量。文档和示例 SHALL 说明如何根据实际服务填写窗口、旧配置迁移及估算局限，不提供被宣称适用于所有兼容服务的统一窗口值。

#### Scenario: 默认输出预算
- **WHEN** 用户配置有效 context_window 但省略 max_output_tokens
- **THEN** 工作与摘要均预留 8192 输出 token，并向供应商约束相同输出上限

#### Scenario: 自定义输出额度
- **WHEN** 用户配置 max_output_tokens=4096 且与窗口及其他模型设置兼容
- **THEN** 请求和上下文检查共同使用 4096，不再在某个协议中固定使用 8192 或省略输出上限

### Requirement: 独立 Skill 的模型预算配置
主配置 SHALL 支持可选 skill_models JSON 对象，以当前服务下模型 ID 为键、显式 context_window 和可选 max_output_tokens 为值。映射及每项 SHALL 拒绝重复键、未知字段、非对象值、非法模型标识、非正整数和布尔预算；输出额度省略 SHALL 沿用主配置，每个窗口 SHALL 满足 context_window > max_output_tokens + 13000。省略映射 SHALL 不改变旧配置启动行为。独立 Skill 省略 model 或指定主模型 SHALL 使用主模型预算；指定其他模型 SHALL 使用映射中的对应预算并复用主 protocol、base_url、api_key 和 thinking，SHALL NOT 从 Skill 接受凭据、任意配置路径或跨服务地址。缺少对应预算 SHALL 在子请求前返回 skill_model_unavailable，不猜窗口或静默换回主模型；服务拒绝选定模型或参数 SHALL 如实返回运行失败。共享 Skill 指定 model SHALL 按 Skill 文件错误处理，不改变主模型及续接历史。

#### Scenario: 旧配置没有模型映射
- **WHEN** 主配置合法且省略 skill_models
- **THEN** 旧启动行为保持，未指定独立模型的 Skill 沿用主模型配置

#### Scenario: 独立模型有显式预算
- **WHEN** Skill 指定已配置的其他模型 ID
- **THEN** 子模型请求使用该 ID、其窗口及输出上限和主服务连接，主模型、主历史及主估算锚点保持

#### Scenario: 模型预算缺失
- **WHEN** 独立 Skill 指定其他模型，但主映射没有该项
- **THEN** 返回 skill_model_unavailable，不发送子请求、不使用主模型代替

#### Scenario: 非法备用窗口
- **WHEN** 映射项为布尔窗口、含重复键或窗口不大于输出加余量
- **THEN** 启动配置校验失败，不创建模型客户端或开始 Skill 执行

### Requirement: 团队运行配置与严格校验
指定供应商配置 SHALL 支持 `team_backend=auto|tmux|inprocess`，省略使用 auto；支持正整数 `team_max_running`、非负整数 `team_max_queued`，省略分别为 4 和 32。非法、空值或布尔预算 SHALL 在启动前指出字段并拒绝，不解释为无限容量。成员显式后端 SHALL 覆盖团队默认选择，但不能绕过可用性检查或失败报告；实际选择 SHALL 可查看。模型、窗口与输出预算 SHALL 沿用现有已配置角色模型合同，不接受成员提供任意服务凭据。

#### Scenario: 后端值非法
- **WHEN** 配置包含不支持的 team_backend 或非法容量
- **THEN** 启动前明确报错，不创建团队、窗格或模型请求

#### Scenario: 旧配置
- **WHEN** 合法配置没有团队字段
- **THEN** 普通启动保持，新创建团队默认使用 auto 和公开容量上限

### Requirement: coordinator 双开关与身份限制
配置 SHALL 支持 `team_coordinator_enabled=true|false`，省略为 false；用户 SHALL 通过真实启动进程环境变量 `MEWCODE_COORDINATOR=1` 主动启用。只有配置为 true、环境变量恰为 1 且当前运行身份为 Lead 时 SHALL 生效。指定 .env 文件中同名环境变量、历史正文、团队存档和模型工具参数 SHALL NOT 代替真实进程环境。恢复 SHALL 按本次两个来源重算，不恢复历史开关。成员进程继承环境变量 SHALL 不使成员变为 coordinator；当前开关及缺失条件 SHALL 可查看。

#### Scenario: 只开一把锁
- **WHEN** 只有配置允许或只有环境变量为 1
- **THEN** coordinator 不生效，状态指出缺失条件

#### Scenario: 文件伪装主动启用
- **WHEN** 配置文件写有 MEWCODE_COORDINATOR=1 但进程环境没有该变量
- **THEN** coordinator 不生效，不把配置文件值当作用户本次主动启用

#### Scenario: 成员继承启动环境
- **WHEN** Lead 为 coordinator 且成员进程继承相同环境变量
- **THEN** 成员仍按成员能力执行，文件编辑不因该变量被误剥夺
