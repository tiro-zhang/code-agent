## Purpose

定义两种模型协议在纯文本多轮对话中的外部兼容行为，确保 Claude、DeepSeek Anthropic 兼容接口与符合 OpenAI Chat Completions 协议的服务都能提供实时流式回复。

## ADDED Requirements

### Requirement: Anthropic Messages 流式对话
当 `protocol=anthropic` 时，系统 SHALL 使用配置的模型、API 基础地址与凭据发起 Messages 流式请求，并 SHALL 将回答文本片段交给终端即时展示；该协议 SHALL 支持 Claude 和 DeepSeek 官方的 Anthropic 兼容地址。

#### Scenario: Claude 返回多个文本片段
- **WHEN** Claude 流式响应依次返回多个回答文本片段
- **THEN** 系统按原顺序输出这些片段，并在流成功结束后完成本轮对话

#### Scenario: DeepSeek Anthropic 兼容接口返回文本片段
- **WHEN** 配置指向 `https://api.deepseek.com/anthropic` 且服务返回多个 Messages 文本片段
- **THEN** 系统按原顺序输出这些片段，并在流成功结束后完成本轮对话

### Requirement: OpenAI 兼容流式对话
当 `protocol=openai` 时，系统 SHALL 使用配置的模型、API 基础地址与凭据发起符合 OpenAI Chat Completions 协议的流式请求，并 SHALL 支持遵循该协议的第三方服务。

#### Scenario: 第三方兼容服务返回片段
- **WHEN** 用户配置的第三方服务按 Chat Completions 流式协议返回回答片段
- **THEN** 系统按原顺序输出片段，并在流成功结束后完成本轮对话

### Requirement: Claude 扩展思考
当 Claude 配置的 `thinking=true` 时，系统 SHALL 请求所选模型支持的思考模式及可读摘要，并 SHALL 将实际收到的思考片段作为独立于最终回答的流式内容提供给界面；若模型不支持该配置，系统 SHALL 给出明确错误，而不是静默忽略。

#### Scenario: 返回可读思考
- **WHEN** 所选 Claude 模型支持思考并返回可读思考片段
- **THEN** 系统在最终回答前或其间按到达顺序提供思考片段

#### Scenario: 所选模型不支持思考配置
- **WHEN** `thinking=true` 但所选模型拒绝该思考模式
- **THEN** 系统说明思考配置与模型不兼容，且不将该轮标记为完成

### Requirement: DeepSeek Anthropic 兼容思考流
当配置指向 DeepSeek 官方 Anthropic 兼容地址时，系统 SHALL 在 `thinking=true` 时请求其支持的思考模式，并 SHALL 将实际收到的思考片段与回答片段按顺序分别交给界面；在 `thinking=false` 时 SHALL 显式关闭该服务默认开启的思考模式。

#### Scenario: DeepSeek 返回思考和回答
- **WHEN** `thinking=true` 且 DeepSeek 兼容接口先返回思考片段、再返回回答片段
- **THEN** 系统分别输出两类片段，并仅把最终回答加入后续纯文本历史

#### Scenario: 关闭 DeepSeek 默认思考
- **WHEN** DeepSeek 兼容配置为 `thinking=false`
- **THEN** 系统向该服务发送关闭思考参数，终端只显示回答

### Requirement: 关闭思考展示
当 `thinking=false` 时，系统 SHALL NOT 主动请求展示 Claude 思考内容；对于默认进行内部思考且不可关闭的模型，系统 SHALL NOT 将此配置描述为保证关闭模型内部思考。

#### Scenario: 模型默认内部思考
- **WHEN** 所选模型默认进行内部思考且配置为 `thinking=false`
- **THEN** 终端只展示最终回答，不展示思考内容

### Requirement: 仅发送纯文本对话
系统 SHALL 只向模型发送本次会话中的纯文本用户问题与助手最终回答，不请求工具调用，也不执行模型返回的操作指令。

#### Scenario: 后续提问
- **WHEN** 用户完成一轮回答后继续提问
- **THEN** 模型请求包含已完成轮次的纯文本历史，不包含终端显示过的思考摘要或工具定义
