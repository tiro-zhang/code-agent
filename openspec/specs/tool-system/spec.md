# tool-system Specification

## Purpose

定义模型可发现和调用的本地工具契约，使工具使用统一的参数描述与结果格式，并在参数错误、执行失败、超时或输出过多时给出可处理的结果，为终端会话调用工具提供稳定边界。

## Requirements

### Requirement: 统一工具契约与注册
每个工具 SHALL 提供唯一名称、用途描述、JSON Schema 参数定义和执行入口，并在登记信息中声明是否只读。系统 SHALL 按名称登记和查找工具，从同一份登记信息按当前模式筛选并导出 Anthropic Messages 与 OpenAI Chat Completions 可接受的工具列表；本地只读元信息 SHALL NOT 作为未知字段发送给供应商。重复名称 SHALL 被明确拒绝，不得覆盖原工具。`read_file`、`glob_files`、`search_code` SHALL 标为只读，其余三个核心工具 SHALL 标为非只读；未声明只读性的工具 SHALL 按非只读处理。

#### Scenario: 枚举六个核心工具
- **WHEN** 执行模式准备向模型提供工具
- **THEN** 工具列表包含六个已注册工具的名称、描述与参数定义，两个协议中的名称和参数语义一致

#### Scenario: 重复注册
- **WHEN** 工具名称与已有登记重复
- **THEN** 登记失败并报告重名，原工具仍可按名称找到

#### Scenario: 规划模式筛选
- **WHEN** 规划模式准备向模型提供工具
- **THEN** 同一登记信息仅导出三个只读工具，执行器使用相同的只读分类

#### Scenario: 新工具未声明只读
- **WHEN** 后续登记一个没有明确只读声明的工具
- **THEN** 工具按非只读处理，不能进入只读并发组或规划模式的允许列表

### Requirement: 执行前校验
系统 SHALL 在执行前按名称查找工具、检查当前模式允许范围、解析完整 JSON 参数并依据对应 Schema 校验必填字段、类型、取值范围及未知字段；参数 SHALL 是对象。未注册名称 SHALL 返回 `unknown_tool`，已注册但模式禁止的名称 SHALL 返回 `tool_not_allowed`，非法参数 SHALL 返回 `invalid_arguments`；这些错误 SHALL 为结构化结果，且 SHALL NOT 启动工具或产生文件改动。

#### Scenario: 未知工具
- **WHEN** 一个完整调用引用未注册名称
- **THEN** 返回 `unknown_tool`，说明该工具未注册

#### Scenario: 参数非法
- **WHEN** 允许工具的参数不是合法 JSON 对象或不满足 Schema
- **THEN** 返回 `invalid_arguments`，说明字段或 JSON 问题，且不执行操作

#### Scenario: 禁止工具绕过模型列表
- **WHEN** 规划模式收到一个未出现在允许列表中的已注册写类调用
- **THEN** 执行入口再次拒绝并返回 `tool_not_allowed`，不依赖模型遵守工具列表

### Requirement: 结构化执行结果
每次调用 SHALL 返回包含 `ok`、`data`、`error` 和 `truncated` 的可序列化结果。成功时 `ok` SHALL 为 true 且 `error` SHALL 为 null；失败时 `ok` SHALL 为 false，`error` SHALL 包含稳定的 `code`、可修正的中文 `message` 和必要的 `details`，可保留已获取的部分数据。错误信息 SHALL 写入结果正文，不能仅依赖供应商的错误标记；预期工具错误和普通执行异常 SHALL NOT 导致会话退出。

#### Scenario: 执行成功
- **WHEN** 工具完成有效操作
- **THEN** 返回 `ok=true`、对应数据和 `error=null`

#### Scenario: 工具抛出异常
- **WHEN** 执行遇到文件权限问题或普通运行异常
- **THEN** 返回对应错误代码或 `execution_error`，正文说明失败原因，不向终端或模型暴露堆栈及配置中的 API 密钥

### Requirement: 超时与取消
每次工具执行 SHALL 受有限时间上限约束，默认 30 秒；命令工具可在其 Schema 范围内指定上限。超时 SHALL 终止该工具工作进程及同组子进程，返回 `timeout`，不取消同批其他工具。任务取消 SHALL 停止全部活动工具及后续调度、补齐每个调用结果，并在清理结束后恢复输入。系统 SHALL 回收进程资源，不把停止等待当成停止执行；对写入或命令已发生的影响 SHALL NOT 声称回滚，结果不确定时 SHALL 明确说明可能已有副作用。等待、期限观察与受控清理 SHALL 支持并发任务的及时观察及取消。

#### Scenario: 文件或搜索操作卡住
- **WHEN** 任一工具超过执行上限仍未结束
- **THEN** 该工具被终止并返回 `timeout`，其余未取消的调用仍可继续

#### Scenario: 命令及子进程超时
- **WHEN** 命令创建同组子进程并超过上限
- **THEN** 命令和这些子进程被终止，结果保留可用输出并说明超时及可能已发生的影响

#### Scenario: 用户取消执行
- **WHEN** 用户在一个或多个工具执行期间按 Ctrl+C
- **THEN** 全部活动工具被终止并回收，未完成调用得到 `cancelled`，已完成实际结果保留，会话返回提示符且不再发起本任务的模型请求

#### Scenario: 取消清理期间收到结果
- **WHEN** 工具已返回真实结果，取消发生在资源清理边界
- **THEN** 系统保留实际结果及必要的完成状态说明，不将已经发生的操作标为未启动

### Requirement: 有界结果
返回模型的单次工具结果中，文本和匹配数据的 UTF-8 内容总量 SHALL 不超过 64 KiB，结构化信封字段除外。超过限制时 SHALL 保持结果为合法结构、设置 `truncated=true` 并说明截断；文件读取和搜索 SHALL 提供能够缩小范围的参数。写入内容和编辑输入 SHALL NOT 通过截断后继续执行的方式适配限制。

#### Scenario: 命令输出过多
- **WHEN** 命令持续输出超过返回上限的数据
- **THEN** 系统有界采集输出并标明截断，同时继续监控命令直至结束、超时或取消

#### Scenario: 修改内容过大
- **WHEN** 写入内容或编辑目标超过工具公布的处理上限
- **THEN** 返回 `input_too_large`，目标文件不被截断或部分改写
