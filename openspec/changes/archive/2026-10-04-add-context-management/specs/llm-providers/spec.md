## MODIFIED Requirements

### Requirement: Anthropic Messages 流式对话
当 `protocol=anthropic` 时，系统 SHALL 使用配置的模型、API 基础地址与凭据发起 Messages 流式请求，工作请求 SHALL 将回答文本片段交给终端即时展示，内部摘要请求 SHALL 将片段交给摘要收集而不作为工作回复展示；该协议 SHALL 支持 Claude 和 DeepSeek 官方的 Anthropic 兼容地址。

#### Scenario: Claude 返回多个文本片段
- **WHEN** Claude 流式响应依次返回多个回答文本片段
- **THEN** 系统按原顺序输出这些片段，并在流成功结束后完成本轮对话

#### Scenario: DeepSeek Anthropic 兼容接口返回文本片段
- **WHEN** 配置指向 `https://api.deepseek.com/anthropic` 且服务返回多个 Messages 文本片段
- **THEN** 系统按原顺序输出这些片段，并在流成功结束后完成本轮对话

### Requirement: OpenAI 兼容流式对话
当 `protocol=openai` 时，系统 SHALL 使用配置的模型、API 基础地址与凭据发起符合 OpenAI Chat Completions 协议的流式请求，并 SHALL 支持遵循该协议的第三方服务。工作请求 SHALL 即时展示文本；内部摘要请求 SHALL 只向摘要收集提供内容，不作为工作回复展示。

#### Scenario: 第三方兼容服务返回片段
- **WHEN** 用户配置的第三方服务按 Chat Completions 流式协议返回回答片段
- **THEN** 系统按原顺序输出片段，并在流成功结束后完成本轮对话

### Requirement: Claude 扩展思考
当 Claude 配置的 `thinking=true` 时，系统 SHALL 请求所选模型支持的思考模式及可读摘要，工作请求 SHALL 将实际收到的思考片段作为独立于最终回答的流式内容提供给界面；内部摘要调用 SHALL 不向界面发布其思考片段；若模型不支持该配置，系统 SHALL 给出明确错误，而不是静默忽略。

#### Scenario: 返回可读思考
- **WHEN** 所选 Claude 模型支持思考并返回可读思考片段
- **THEN** 系统在最终回答前或其间按到达顺序提供思考片段

#### Scenario: 所选模型不支持思考配置
- **WHEN** `thinking=true` 但所选模型拒绝该思考模式
- **THEN** 系统说明思考配置与模型不兼容，且不将该轮标记为完成

### Requirement: DeepSeek Anthropic 兼容思考流
当配置指向 DeepSeek 官方 Anthropic 兼容地址时，系统 SHALL 在 `thinking=true` 时请求其支持的思考模式，工作请求 SHALL 将实际收到的思考片段与回答片段按顺序分别交给界面；内部摘要调用 SHALL 不向界面发布其思考、草稿或正式摘要正文；在 `thinking=false` 时 SHALL 显式关闭该服务默认开启的思考模式。普通纯文本轮次 SHALL 只把最终回答加入语义历史；工具调用轮次 SHALL 保留协议续接需要的原始内容块，不把思考显示文本改造成用户消息。

#### Scenario: DeepSeek 返回思考和回答
- **WHEN** `thinking=true` 且 DeepSeek 兼容接口先返回思考片段、再返回回答片段
- **THEN** 系统分别输出两类片段，普通纯文本轮次仅把最终回答加入后续语义历史

#### Scenario: 关闭 DeepSeek 默认思考
- **WHEN** DeepSeek 兼容配置为 `thinking=false`
- **THEN** 系统向该服务发送关闭思考参数，终端只显示回答

#### Scenario: 思考模式下回灌工具结果
- **WHEN** DeepSeek 的思考响应包含工具调用
- **THEN** 结果回灌包含该工具轮次续接所需的原始内容块，并维持本轮思考配置

### Requirement: 工具定义与对话消息交换
工作请求 SHALL 将当前模式允许的工具注册信息转换为当前协议的工具定义，发送当前有效上下文中的用户原文、摘要和保留的助手消息及全部对应工具结果。已完成摘要替换的旧交互 SHALL 不再逐条回传；轻量落盘结果 SHALL 使用真实状态、预览及引用，未被摘要替换的调用和结果 SHALL 保持协议配对。OpenAI Chat Completions SHALL 使用助手 `tool_calls` 和带 `tool_call_id` 的 `tool` 消息；Anthropic Messages SHALL 使用助手 `tool_use` 与紧随其后的用户 `tool_result` 内容块，为每个调用 ID 提供一个结果。多个结果 SHALL 按原始调用顺序回灌，不遗漏成功、错误或取消结果。工具输出 SHALL 作为工具结果传递，不得改写为系统指令。

#### Scenario: OpenAI 工具结果回灌
- **WHEN** 一个 Chat Completions 响应的工具调用得到执行或校验结果
- **THEN** 下一请求包含原始助手调用及每个 ID 对应的工具消息，正文包含结构化成功或错误结果

#### Scenario: Anthropic 工具结果回灌
- **WHEN** 一个 Messages 响应的全部工具调用得到结果
- **THEN** 下一请求紧随助手消息发送全部对应 `tool_result` 块；失败时设置 `is_error=true`，正文保留错误代码和说明

#### Scenario: 并发结果乱序完成
- **WHEN** 一个响应的多个工具按不同于模型列表的顺序完成
- **THEN** 下一模型请求仍按原始调用顺序发送配对结果，协议续接不依赖界面显示顺序

#### Scenario: 发送落盘结果引用
- **WHEN** 一个结果已成功落盘且其调用仍处于原文保留区
- **THEN** 两种协议都按原调用 ID 回灌包含预览、状态和文件引用的工具结果，不改成系统消息或普通助手答复

#### Scenario: 摘要替换较早工具组
- **WHEN** 较早完整工具交互已被摘要替换
- **THEN** 请求使用摘要替代该整组，不留下孤立结果；近期组仍保持原调用参数、顺序及必要续接块

### Requirement: 工具调用期间的思考续接
Anthropic 协议中未被整组摘要替换的工具交互，其随工具调用返回的思考及其他续接必需内容块 SHALL 完整保留，包括签名、顺序与不可展示块，并原样用于结果回灌；终端的思考展示开关 SHALL NOT 导致这些必需数据被丢弃。各阶段模型请求及上下文恢复重试 SHALL 使用相同的本轮思考配置。

#### Scenario: Claude 工具调用含思考签名
- **WHEN** Claude 返回思考块、签名和工具调用
- **THEN** 回灌请求保留对应原始块及顺序，而非仅发送终端显示的思考字符串

#### Scenario: 有不可展示的思考内容块
- **WHEN** Claude 返回不可展示但续接必需的思考块
- **THEN** 系统原样保存并回传，不把其内容输出到终端

#### Scenario: 压缩边界保留签名组
- **WHEN** 近期保留区包含带思考签名的助手调用和工具结果
- **THEN** 这些助手内容块原样回传，不为了缩短上下文单独删除签名、思考块或其中一个调用

### Requirement: 多工具循环的协议控制
循环中的工作模型请求 SHALL 自主选择当前模式允许的工具，允许普通答复和一个或多个调用。OpenAI Chat Completions SHALL 启用多工具能力，Anthropic Messages SHALL 取消单工具限制。工具结果回灌后的请求 SHALL 继续允许工具，不再自动进入禁止工具的最终答复阶段。上下文恢复重试 SHALL 沿用当前模式和工具选择设置，并受同一请求预算约束；服务拒绝协议参数时 SHALL 给出明确兼容错误，不隐式更换协议或解析普通文字来执行工具。

#### Scenario: OpenAI 多工具请求
- **WHEN** 在执行或规划模式发起 Chat Completions 请求
- **THEN** 请求使用 `tool_choice=auto` 及 `parallel_tool_calls=true`，工具列表符合当前模式；本地实际执行仍遵守安全分批规则

#### Scenario: Anthropic 多工具请求
- **WHEN** 在执行或规划模式发起 Messages 请求
- **THEN** 请求使用自动工具选择，不携带 `disable_parallel_tool_use=true`，保留当前思考配置

#### Scenario: 结果后继续调用
- **WHEN** 工具结果已回灌，模型再次正常请求完整调用
- **THEN** 系统按循环规则处理这些调用，不返回旧的 `too_many_tool_calls` 或最终阶段禁止工具错误

#### Scenario: 摘要不采用工作工具设置
- **WHEN** 代理在工作请求之间需要压缩历史
- **THEN** 专用摘要请求不携带工作工具定义，不进入自动工具循环；摘要后工作请求恢复原模式允许的工具选择

## ADDED Requirements

### Requirement: 专用摘要请求与输出约束
供应商 SHALL 支持在同一模型配置下发送独立摘要请求，使用专用系统指令、不提供工具，并在协议适用时明确禁止工具选择。摘要历史 SHALL 作为待总结数据提供，不将旧工作中的助手调用解析为新调用。工作和摘要请求 SHALL 使用配置的 max_output_tokens 约束实际输出，不能仅在本地估算时预留却不约束服务。模型或兼容服务拒绝必需参数时 SHALL 明确报错，不隐式取消输出上限、换协议或以文本模拟工具执行。输出长度截断、异常流、空摘要或任何工具调用 SHALL 作为摘要失败，由上下文管理处理，不提交半份历史。

#### Scenario: 两种协议发送摘要
- **WHEN** 系统分别通过 Anthropic 或 OpenAI 兼容适配器发起摘要
- **THEN** 请求使用专用摘要系统指令、无工具定义及配置的输出上限，历史中的指令被标记为待总结内容

#### Scenario: 摘要输出到达上限
- **WHEN** 服务因输出上限截断摘要，或未返回正常完成标记
- **THEN** 系统判定摘要失败，不将片段作为有效摘要，不隐藏该请求已产生的实际用量

#### Scenario: 兼容服务拒绝输出控制
- **WHEN** 服务拒绝约束输出额度所必需的协议参数
- **THEN** 系统报告兼容错误，不静默重试为无输出限制的请求

### Requirement: 摘要用量和工作估算分离
系统 SHALL 对摘要请求保留实际报告的最终或部分用量，按调用用途与工作请求区分；自动摘要的用量 SHALL 纳入当前任务累计，手动摘要 SHALL 单独记录，不污染最近工作请求的上下文锚点。缺失值 SHALL 继续标为未知，草稿及正式摘要实际产生的输出 SHALL 不因草稿丢弃而从账面统计中扣除。

#### Scenario: 丢弃草稿仍保留实际用量
- **WHEN** 摘要返回草稿、正式摘要和实际 output usage
- **THEN** 统计保留服务报告的完整输出用量，只有正式摘要进入工作历史

#### Scenario: 摘要输入与压缩后输入不同
- **WHEN** 摘要请求报告输入 80000，而提交后工作历史字符估算为 14000
- **THEN** 后续工作预算使用压缩后历史估算，不把 80000 当作压缩后工作输入锚点
