## MODIFIED Requirements

### Requirement: 工具定义与对话消息交换
系统 SHALL 将当前模式允许的工具注册信息转换为当前协议的工具定义，向模型发送用户文本、已完成助手消息和全部对应工具结果。OpenAI Chat Completions SHALL 使用助手 `tool_calls` 和带 `tool_call_id` 的 `tool` 消息；Anthropic Messages SHALL 使用助手 `tool_use` 与紧随其后的用户 `tool_result` 内容块，为每个调用 ID 提供一个结果。多个结果 SHALL 按原始调用顺序回灌，不遗漏成功、错误或取消结果。工具输出 SHALL 作为工具结果传递，不得改写为系统指令。

#### Scenario: OpenAI 工具结果回灌
- **WHEN** 一个 Chat Completions 响应的工具调用得到执行或校验结果
- **THEN** 下一请求包含原始助手调用及每个 ID 对应的工具消息，正文包含结构化成功或错误结果

#### Scenario: Anthropic 工具结果回灌
- **WHEN** 一个 Messages 响应的全部工具调用得到结果
- **THEN** 下一请求紧随助手消息发送全部对应 `tool_result` 块；失败时设置 `is_error=true`，正文保留错误代码和说明

#### Scenario: 并发结果乱序完成
- **WHEN** 一个响应的多个工具按不同于模型列表的顺序完成
- **THEN** 下一模型请求仍按原始调用顺序发送配对结果，协议续接不依赖界面显示顺序

### Requirement: 流式工具参数组装
系统 SHALL 按调用索引或内容块索引独立累积名称、调用 ID 和参数 JSON 碎片，保留顺序并避免多个调用串接。工具 SHALL 只在完整模型响应正常结束且调用可识别后进入执行判断；完整工具调用可没有回答文本。缺失或重复调用 ID、缺少名称、截断响应和异常结束 SHALL 报协议错误并禁止执行该响应中的工具。现有参数及调用元信息上限 SHALL 继续生效。

#### Scenario: 参数被切成多个片段
- **WHEN** OpenAI 的 `delta.tool_calls` 或 Anthropic 的 `input_json_delta` 分多次发送参数
- **THEN** 系统按各自索引拼接完整参数，在整个响应正常结束前不执行工具

#### Scenario: 仅工具调用的响应
- **WHEN** 服务正常结束，返回一个或多个完整调用但没有回答文本
- **THEN** 系统识别为有效工具请求

#### Scenario: 流未完成
- **WHEN** 参数片段已出现但连接中断、达到生成长度上限或缺少正常结束标记
- **THEN** 系统提示响应未完成，不执行工具，不提交不完整调用

#### Scenario: 完整调用的参数无法解析
- **WHEN** 响应正常结束且调用 ID、名称有效，但参数为非法 JSON 或不符合 Schema
- **THEN** 该调用不执行并获得 `invalid_arguments`，配对回灌后模型可在同一任务的预算内调整参数

#### Scenario: 多个参数流交错
- **WHEN** 一个流交错返回多个调用的参数碎片
- **THEN** 每个调用保持自身的 ID、名称和参数，最终列表遵循原始索引顺序

## ADDED Requirements

### Requirement: 异步模型响应契约
两种协议 SHALL 向调用者提供可异步消费的模型片段、完整响应和用量，模型等待 SHALL NOT 阻塞活动工具的异步观察或任务取消。模式行为指令 SHALL 按协议映射为独立系统指令，与用户语义历史及工具结果分开。取消或结束时 SHALL 关闭本次活动流并回收请求资源。

#### Scenario: 异步消费
- **WHEN** 调用者异步消费模型流
- **THEN** 可即时获得文本和实际思考片段，并在正常结束后获得完整响应，无需依赖终端打印

#### Scenario: 取消网络等待
- **WHEN** 用户在等待模型片段时取消任务
- **THEN** 活动流被关闭，不再继续产生本任务的工具执行或后续请求

### Requirement: 多工具循环的协议控制
循环中的模型请求 SHALL 自主选择当前模式允许的工具，允许普通答复和一个或多个调用。OpenAI Chat Completions SHALL 启用多工具能力，Anthropic Messages SHALL 取消单工具限制。工具结果回灌后的请求 SHALL 继续允许工具，不再自动进入禁止工具的最终答复阶段。上下文恢复重试 SHALL 沿用当前模式和工具选择设置，并受同一请求预算约束；服务拒绝协议参数时 SHALL 给出明确兼容错误，不隐式更换协议或解析普通文字来执行工具。

#### Scenario: OpenAI 多工具请求
- **WHEN** 在执行或规划模式发起 Chat Completions 请求
- **THEN** 请求使用 `tool_choice=auto` 及 `parallel_tool_calls=true`，工具列表符合当前模式；本地实际执行仍遵守安全分批规则

#### Scenario: Anthropic 多工具请求
- **WHEN** 在执行或规划模式发起 Messages 请求
- **THEN** 请求使用自动工具选择，不携带 `disable_parallel_tool_use=true`，保留当前思考配置

#### Scenario: 结果后继续调用
- **WHEN** 工具结果已回灌，模型再次正常请求完整调用
- **THEN** 系统按循环规则处理这些调用，不返回旧的 `too_many_tool_calls` 或最终阶段禁止工具错误

### Requirement: 供应商用量归一化
系统 SHALL 按供应商实际流数据提供输入和输出 Token 用量，并保持每次请求只记录一次最终或已知部分统计。OpenAI SHALL 请求流式用量并处理没有 choice 的用量块；Anthropic 消息更新中的累计计数 SHALL 采用最后有效值，不将各更新相加。服务不返回用量或流中断时 SHALL 保留已知字段、将缺失字段标为未知，不因缺失用量拒绝正常响应。

#### Scenario: OpenAI 结束附近用量块
- **WHEN** 请求使用 `stream_options.include_usage=true`，服务在内容结束附近返回 `choices=[]` 的用量块
- **THEN** 系统读取其中的 `usage`，不将其误判为额外回答或因无 choice 而丢弃

#### Scenario: Anthropic 累计更新
- **WHEN** 消息更新依次报告输出计数 5 和 9
- **THEN** 本次请求的输出用量为 9，不能累计成 14

#### Scenario: 服务不报告用量
- **WHEN** 一个兼容响应正常结束，但没有完整用量字段
- **THEN** 响应按正常内容处理，缺失统计为未知

#### Scenario: 流中断后只有部分计数
- **WHEN** 已获得输入用量，但流中断导致最终输出计数缺失
- **THEN** 输入统计保留，输出为未知，本次任务按流错误停止而不是虚构完整计数

## REMOVED Requirements

### Requirement: 单次工具调用的协议控制
**Reason**: Agent Loop 需要多工具响应及工具结果后的继续调用，原有单工具和最终答复禁止工具控制已不适用。
**Migration**: 使用多工具循环的协议控制，所有执行仍经过本地模式检查、安全分批、参数校验和任务停止条件。
