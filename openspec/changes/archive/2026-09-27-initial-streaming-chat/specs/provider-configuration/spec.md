## Purpose

定义启动时选择与校验模型供应商配置的行为，使用户可以通过不同的 `.env` 文件切换后端，同时避免无效配置进入交互会话。

## ADDED Requirements

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
系统 SHALL 在进入交互会话前检查配置文件是否存在、必填字段是否非空、`protocol` 与 `thinking` 是否有效，并在校验失败时指出可修正的字段。

#### Scenario: 缺失 API 凭据
- **WHEN** 指定文件缺少 `api_key` 或其值为空
- **THEN** 系统提示 `api_key` 配置错误，并且不进入交互会话

#### Scenario: 不支持的协议
- **WHEN** `protocol` 既不是 `anthropic` 也不是 `openai`
- **THEN** 系统提示有效的协议值，并且不进入交互会话

#### Scenario: OpenAI 配置启用思考
- **WHEN** `protocol=openai` 且 `thinking=true`
- **THEN** 系统提示该字段在本阶段仅适用于 Anthropic 协议，并且不进入交互会话

### Requirement: 凭据不泄露
系统 SHALL NOT 在正常输出或配置及请求错误提示中显示 `api_key` 的值。

#### Scenario: 配置验证失败
- **WHEN** 包含 `api_key` 的配置文件有其他字段无效
- **THEN** 系统的错误输出不包含 API 密钥明文
