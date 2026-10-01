## ADDED Requirements

### Requirement: Agent 请求预算配置
配置文件 SHALL 支持可选的 `max_iterations` 十进制正整数字段，省略时使用 20。该值 SHALL 用作每次用户任务的应用层 LLM 请求上限，不表示工具调用数量；系统 SHALL NOT 将零、负数、空值或非整数解释为无限执行。原有供应商必填字段、`thinking` 和 `--config` 用法 SHALL 保持兼容。

#### Scenario: 旧配置继续使用
- **WHEN** 用户使用没有 `max_iterations` 的有效供应商配置
- **THEN** 系统正常启动，每次用户任务使用 20 次请求预算

#### Scenario: 自定义请求上限
- **WHEN** 有效配置包含 `max_iterations=5`
- **THEN** 每次用户任务最多发起 5 次应用层 LLM 请求，包括最终答复和上下文恢复请求

## MODIFIED Requirements

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
