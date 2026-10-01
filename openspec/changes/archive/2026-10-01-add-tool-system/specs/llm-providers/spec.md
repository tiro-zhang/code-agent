## REMOVED Requirements

### Requirement: 仅发送纯文本对话
**Reason**: 本阶段需要在请求中声明工具并回传工具调用与结果，原有纯文本限制与新增能力冲突。
**Migration**: 使用新增的“工具定义与对话消息交换”要求；既有用户文本和最终答复继续可用，工具调用与结果采用供应商规定的消息格式。

## MODIFIED Requirements

### Requirement: DeepSeek Anthropic 兼容思考流
当配置指向 DeepSeek 官方 Anthropic 兼容地址时，系统 SHALL 在 `thinking=true` 时请求其支持的思考模式，并 SHALL 将实际收到的思考片段与回答片段按顺序分别交给界面；在 `thinking=false` 时 SHALL 显式关闭该服务默认开启的思考模式。普通纯文本轮次 SHALL 只把最终回答加入语义历史；工具调用轮次 SHALL 保留协议续接需要的原始内容块，不把思考显示文本改造成用户消息。

#### Scenario: DeepSeek 返回思考和回答
- **WHEN** `thinking=true` 且 DeepSeek 兼容接口先返回思考片段、再返回回答片段
- **THEN** 系统分别输出两类片段，普通纯文本轮次仅把最终回答加入后续语义历史

#### Scenario: 关闭 DeepSeek 默认思考
- **WHEN** DeepSeek 兼容配置为 `thinking=false`
- **THEN** 系统向该服务发送关闭思考参数，终端只显示回答

#### Scenario: 思考模式下回灌工具结果
- **WHEN** DeepSeek 的思考响应包含工具调用
- **THEN** 结果回灌包含该工具轮次续接所需的原始内容块，并维持本轮思考配置

## ADDED Requirements

### Requirement: 工具定义与对话消息交换
系统 SHALL 将工具注册信息转换为当前协议的工具定义，向模型发送用户文本、已完成助手消息和对应的工具调用结果。OpenAI Chat Completions SHALL 使用助手 `tool_calls` 和带 `tool_call_id` 的 `tool` 消息；Anthropic Messages SHALL 使用助手 `tool_use` 与紧随其后的用户 `tool_result` 内容块，并为每个调用 ID 提供一个结果。工具输出 SHALL 作为工具结果传递，不得改写为系统指令。

#### Scenario: OpenAI 工具结果回灌
- **WHEN** 一个 Chat Completions 工具调用获得执行结果
- **THEN** 下一请求包含原始助手工具调用及调用 ID 对应的工具消息，正文包含结构化成功或错误结果

#### Scenario: Anthropic 工具结果回灌
- **WHEN** 一个 Messages 工具响应获得结果
- **THEN** 下一请求紧随该助手消息发送对应 `tool_result` 块；工具失败时设置 `is_error=true`，同时在正文保留错误代码和说明

### Requirement: 流式工具参数组装
系统 SHALL 按调用索引或内容块索引独立累积名称、调用 ID 和参数 JSON 碎片，保留顺序并避免不同调用之间串接。工具 SHALL 只在完整模型响应正常结束且调用可识别后才进入执行判断；完整工具调用可没有回答文本，不能因此视为空回答。缺失或重复调用 ID、缺少名称、截断响应和异常结束 SHALL 报协议错误并禁止执行。

#### Scenario: 参数被切成多个片段
- **WHEN** OpenAI 的 `delta.tool_calls` 或 Anthropic 的 `input_json_delta` 分多次发送参数
- **THEN** 系统按各自索引拼接完整参数；在正常工具结束信号前不执行任何工具

#### Scenario: 仅工具调用的响应
- **WHEN** 服务正常结束响应，返回一个完整工具调用但没有回答文本
- **THEN** 系统将其识别为有效的工具请求

#### Scenario: 流未完成
- **WHEN** 参数片段已出现但连接中断、达到生成长度上限或缺少正常结束标记
- **THEN** 系统提示响应未完成，不执行工具，不提交不完整调用

#### Scenario: 完整调用的参数无法解析
- **WHEN** 响应正常结束且调用 ID 和名称有效，但参数为非法 JSON 或不符合 Schema
- **THEN** 不执行工具；生成对应的 `invalid_arguments` 结果，并按协议回灌供最终答复使用

### Requirement: 单次工具调用的协议控制
首阶段模型请求 SHALL 允许自主选择工具，并在协议支持时限制为最多一个调用；最终答复阶段的模型请求 SHALL 保留需要的工具定义与历史，但将工具选择设置为禁止。上下文超限后的恢复重试 SHALL 沿用所在阶段的工具选择设置。应用 SHALL 自行执行单次上限，不依赖兼容服务遵守参数；服务拒绝工具协议参数时 SHALL 给出明确兼容错误，不静默退化成解析普通文本指令。

#### Scenario: 请求单个工具
- **WHEN** 以 OpenAI 或 Anthropic 协议发起首个请求
- **THEN** 分别发送 `parallel_tool_calls=false` 或 `tool_choice` 内的 `disable_parallel_tool_use=true`，并允许模型直接回答

#### Scenario: 兼容服务忽略数量限制
- **WHEN** 服务仍返回多个具有有效 ID 的调用
- **THEN** 所有调用都不执行，每个 ID 均得到 `too_many_tool_calls` 结果，然后进入禁止工具的最终答复阶段；该阶段仅可因上下文超限进行恢复重试

#### Scenario: 最终答复响应违反禁止工具要求
- **WHEN** 结果回灌后的响应仍包含工具调用
- **THEN** 不执行该调用，不再请求模型，提示协议不兼容且保留上一阶段已经记录的调用与结果

### Requirement: 工具调用期间的思考续接
Anthropic 协议中随工具调用返回的思考及其他续接必需内容块 SHALL 完整保留，包括签名、顺序与不可展示块，并原样用于结果回灌；终端的思考展示开关 SHALL NOT 导致这些必需数据被丢弃。各阶段模型请求及上下文恢复重试 SHALL 使用相同的本轮思考配置。

#### Scenario: Claude 工具调用含思考签名
- **WHEN** Claude 返回思考块、签名和工具调用
- **THEN** 回灌请求保留对应原始块及顺序，而非仅发送终端显示的思考字符串

#### Scenario: 有不可展示的思考内容块
- **WHEN** Claude 返回不可展示但续接必需的思考块
- **THEN** 系统原样保存并回传，不把其内容输出到终端
