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
配置 SHALL 使用 `name`、`protocol`、`model`、`base_url`、`api_key` 五个必填字段，并支持可选的 `thinking` 布尔字段；`thinking` SHALL 接受 `true` 或 `false`，省略时视为 `false`。`protocol` SHALL 接受 `anthropic` 或 `openai`，`base_url` SHALL 表示相应协议的 API 基础地址。

#### Scenario: 省略可选字段
- **WHEN** 配置文件包含五个有效必填字段而没有 `thinking`
- **THEN** 系统按 `thinking=false` 加载配置

#### Scenario: OpenAI 兼容地址
- **WHEN** `protocol=openai` 且 `base_url` 指向符合 Chat Completions 协议的第三方 API 基础地址
- **THEN** 系统将该地址用于 OpenAI 兼容请求

#### Scenario: DeepSeek Anthropic 兼容地址
- **WHEN** `protocol=anthropic` 且 `base_url=https://api.deepseek.com/anthropic`
- **THEN** 系统将该地址用于 Anthropic SDK 的 Messages 请求

### Requirement: 启动前校验
系统 SHALL 在进入交互会话前检查配置文件是否存在、必填字段是否非空、`protocol` 与 `thinking` 是否有效，以及显式配置的 `max_iterations` 是否为十进制正整数，并在校验失败时指出可修正的字段。

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

### Requirement: 凭据不泄露
系统 SHALL NOT 在正常输出或配置及请求错误提示中显示 `api_key` 的值。

#### Scenario: 配置验证失败
- **WHEN** 包含 `api_key` 的配置文件有其他字段无效
- **THEN** 系统的错误输出不包含 API 密钥明文

### Requirement: Agent 请求预算配置
配置文件 SHALL 支持可选的 `max_iterations` 十进制正整数字段，省略时使用 20。该值 SHALL 用作每次用户任务的应用层 LLM 请求上限，不表示工具调用数量；系统 SHALL NOT 将零、负数、空值或非整数解释为无限执行。原有供应商必填字段、`thinking` 和 `--config` 用法 SHALL 保持兼容。

#### Scenario: 旧配置继续使用
- **WHEN** 用户使用没有 `max_iterations` 的有效供应商配置
- **THEN** 系统正常启动，每次用户任务使用 20 次请求预算

#### Scenario: 自定义请求上限
- **WHEN** 有效配置包含 `max_iterations=5`
- **THEN** 每次用户任务最多发起 5 次应用层 LLM 请求，包括最终答复和上下文恢复请求
