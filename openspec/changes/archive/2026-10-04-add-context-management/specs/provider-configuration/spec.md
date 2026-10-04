## MODIFIED Requirements

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

### Requirement: Agent 请求预算配置
配置文件 SHALL 支持可选的 `max_iterations` 十进制正整数字段，省略时使用 20。该值 SHALL 用作每次用户任务的应用层 LLM 请求上限，不表示工具调用数量；系统 SHALL NOT 将零、负数、空值或非整数解释为无限执行。原有供应商基础字段、thinking 和 --config 用法 SHALL 保持含义；旧配置 SHALL 补充新增必填 context_window 后启动。自动摘要调用 SHALL 计入当前任务请求上限，空闲手动压缩 SHALL 独立限为一次，不占下个任务预算。

#### Scenario: 旧配置继续使用
- **WHEN** 用户使用没有 `max_iterations` 的有效供应商配置
- **THEN** 系统正常启动，每次用户任务使用 20 次请求预算

#### Scenario: 自定义请求上限
- **WHEN** 有效配置包含 `max_iterations=5`
- **THEN** 每次用户任务最多发起 5 次应用层 LLM 请求，包括最终答复、自动摘要及上下文恢复请求

## ADDED Requirements

### Requirement: 显式窗口与统一输出预算配置
配置 SHALL 使用 context_window 表示服务实际可用的总上下文窗口，不按协议或模型名称猜测。可选 max_output_tokens SHALL 默认 8192，作为工作和摘要请求的实际输出上限，并在各自请求预算中预留同样的额度；摘要草稿、正式摘要以及服务计入输出额度的思考 SHALL 共用该上限。系统 SHALL 在输出预留之外分别使用自动 13000 和手动摘要 3000 的估算安全余量。文档和示例 SHALL 说明如何根据实际服务填写窗口、旧配置迁移及估算局限，不提供被宣称适用于所有兼容服务的统一窗口值。

#### Scenario: 默认输出预算
- **WHEN** 用户配置有效 context_window 但省略 max_output_tokens
- **THEN** 工作与摘要均预留 8192 输出 token，并向供应商约束相同输出上限

#### Scenario: 自定义输出额度
- **WHEN** 用户配置 max_output_tokens=4096 且与窗口及其他模型设置兼容
- **THEN** 请求和上下文检查共同使用 4096，不再在某个协议中固定使用 8192 或省略输出上限
