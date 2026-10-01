# tool-system Specification

## Purpose

定义模型可发现和调用的本地工具契约，使工具使用统一的参数描述与结果格式，并在参数错误、执行失败、超时或输出过多时给出可处理的结果，为终端会话调用工具提供稳定边界。

## Requirements

### Requirement: 统一工具契约与注册
每个工具 SHALL 提供唯一名称、用途描述、JSON Schema 参数定义和执行入口。系统 SHALL 按名称登记和查找工具，并从同一份登记信息导出 Anthropic Messages 与 OpenAI Chat Completions 可接受的工具列表；重复名称 SHALL 被明确拒绝，不得覆盖原工具。

#### Scenario: 枚举六个核心工具
- **WHEN** 会话准备向模型提供工具
- **THEN** 工具列表包含六个已注册工具的名称、描述与参数定义，两个协议中的名称和参数语义一致

#### Scenario: 重复注册
- **WHEN** 工具名称与已有登记重复
- **THEN** 登记失败并报告重名，原工具仍可按名称找到

### Requirement: 执行前校验
系统 SHALL 在执行前解析完整 JSON 参数并依据对应 Schema 校验必填字段、类型、取值范围和未知字段；参数 SHALL 是对象。未知工具或非法参数 SHALL 返回结构化错误，且 SHALL NOT 执行工具或产生文件改动。

#### Scenario: 未知工具
- **WHEN** 一个完整调用引用未注册名称
- **THEN** 返回 `unknown_tool` 错误，说明该工具未注册

#### Scenario: 参数非法
- **WHEN** 参数不是合法 JSON 对象，或不满足工具 Schema
- **THEN** 返回 `invalid_arguments` 错误，说明出错字段或 JSON 问题，且不执行操作

### Requirement: 结构化执行结果
每次调用 SHALL 返回包含 `ok`、`data`、`error` 和 `truncated` 的可序列化结果。成功时 `ok` SHALL 为 true 且 `error` SHALL 为 null；失败时 `ok` SHALL 为 false，`error` SHALL 包含稳定的 `code`、可修正的中文 `message` 和必要的 `details`，可保留已获取的部分数据。错误信息 SHALL 写入结果正文，不能仅依赖供应商的错误标记；预期工具错误和普通执行异常 SHALL NOT 导致会话退出。

#### Scenario: 执行成功
- **WHEN** 工具完成有效操作
- **THEN** 返回 `ok=true`、对应数据和 `error=null`

#### Scenario: 工具抛出异常
- **WHEN** 执行遇到文件权限问题或普通运行异常
- **THEN** 返回对应错误代码或 `execution_error`，正文说明失败原因，不向终端或模型暴露堆栈及配置中的 API 密钥

### Requirement: 超时与取消
每次工具执行 SHALL 受有限时间上限约束，默认 30 秒；命令工具可在其 Schema 范围内指定上限。超时 SHALL 终止工具工作进程及同一进程组中的子进程，返回 `timeout`；用户取消 SHALL 返回 `cancelled` 并恢复输入。系统 SHALL 回收进程资源，不把停止等待当成停止执行；对写入或命令已发生的影响 SHALL NOT 声称已经回滚，结果不确定时 SHALL 明确说明可能已有副作用。

#### Scenario: 文件或搜索操作卡住
- **WHEN** 任一工具超过执行上限仍未结束
- **THEN** 工具被终止并返回 `timeout`，会话仍可继续

#### Scenario: 命令及子进程超时
- **WHEN** 命令创建同一进程组内的子进程并超过上限
- **THEN** 命令和这些子进程被终止，结果保留可用输出并说明执行超时及可能已经发生的影响

#### Scenario: 用户取消执行
- **WHEN** 用户在工具执行期间按 Ctrl+C
- **THEN** 工具执行被取消，调用得到 `cancelled` 结果，会话返回提示符且不再发起本轮的模型答复请求

### Requirement: 有界结果
返回模型的单次工具结果中，文本和匹配数据的 UTF-8 内容总量 SHALL 不超过 64 KiB，结构化信封字段除外。超过限制时 SHALL 保持结果为合法结构、设置 `truncated=true` 并说明截断；文件读取和搜索 SHALL 提供能够缩小范围的参数。写入内容和编辑输入 SHALL NOT 通过截断后继续执行的方式适配限制。

#### Scenario: 命令输出过多
- **WHEN** 命令持续输出超过返回上限的数据
- **THEN** 系统有界采集输出并标明截断，同时继续监控命令直至结束、超时或取消

#### Scenario: 修改内容过大
- **WHEN** 写入内容或编辑目标超过工具公布的处理上限
- **THEN** 返回 `input_too_large`，目标文件不被截断或部分改写
