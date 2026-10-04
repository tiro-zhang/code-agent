## MODIFIED Requirements

### Requirement: 执行前校验
系统 SHALL 在执行前按名称查找工具、检查当前模式允许范围、解析完整 JSON 参数并依据对应 Schema 校验必填字段、类型、取值范围及未知字段；参数 SHALL 是对象。未注册名称 SHALL 返回 `unknown_tool`，已注册但模式禁止的名称 SHALL 返回 `tool_not_allowed`，非法参数 SHALL 返回 `invalid_arguments`；这些错误 SHALL 为结构化结果，且 SHALL NOT 启动工具或产生文件改动。通过这些校验的调用 SHALL 在实际启动前完成适用的黑名单、路径边界、权限规则、权限模式及授权检查。系统 SHALL 在执行前重新检查规则与目标真实路径；已有授权 SHALL NOT 绕过新的拒绝条件。整次调用的权限拒绝 SHALL 返回 `permission_denied` 并明确 `not_started=true`；搜索候选级权限拒绝 SHALL 排除对应候选并按部分成功合同报告受限范围，而非使整次搜索失败；shell 结构检查无法完成 SHALL 返回 `permission_check_failed`，权限配置无效 SHALL 返回 `permission_config_error`，二者均 SHALL 明确 `not_started=true`；系统 SHALL NOT 在未获准时启动目标工具工作进程、读取受限内容或产生副作用。搜索候选的路径元数据枚举 SHALL 允许先进行，内容读取与目标搜索执行 SHALL 在相应候选获准后进行。

#### Scenario: 未知工具
- **WHEN** 一个完整调用引用未注册名称
- **THEN** 返回 `unknown_tool`，说明该工具未注册

#### Scenario: 参数非法
- **WHEN** 允许工具的参数不是合法 JSON 对象或不满足 Schema
- **THEN** 返回 `invalid_arguments`，说明字段或 JSON 问题，且不执行操作

#### Scenario: 禁止工具绕过模型列表
- **WHEN** 规划模式收到一个未出现在允许列表中的已注册写类调用
- **THEN** 执行入口再次拒绝并返回 `tool_not_allowed`，不依赖模型遵守工具列表

#### Scenario: 授权之前不得启动
- **WHEN** 有效工具调用需要人工授权且用户尚未决定
- **THEN** 系统保持等待授权，目标工具尚未启动，不读取受限内容或产生操作副作用

#### Scenario: 有效授权后规则变化
- **WHEN** 调用已有有效授权，但在实际执行前规则新增适用的 deny
- **THEN** 系统返回 `permission_denied` 和 `not_started=true`，不使用授权越过 deny

#### Scenario: 目标路径在等待期间变化
- **WHEN** 文件调用等待批准时目标或祖先链接发生变化
- **THEN** 系统以重新解析的真实路径复查边界、规则与授权范围，不按旧路径授权直接执行；检查失败时返回未启动的结构化错误

### Requirement: 超时与取消
每次工具执行 SHALL 受有限时间上限约束，默认 30 秒；命令工具可在其 Schema 范围内指定上限。超时 SHALL 终止该工具工作进程及同组子进程，返回 `timeout`，不取消同批其他工具。任务取消 SHALL 停止全部活动工具及后续调度、补齐每个调用结果，并在清理结束后恢复输入。系统 SHALL 回收进程资源，不把停止等待当成停止执行；对写入或命令已发生的影响 SHALL NOT 声称回滚，结果不确定时 SHALL 明确说明可能已有副作用。等待、期限观察与受控清理 SHALL 支持并发任务的及时观察及取消。等待授权 SHALL NOT 计入工具执行超时，执行期限 SHALL 自实际启动工具时开始。等待授权期间取消 SHALL 结束本轮任务，等待中的调用 SHALL 返回 `cancelled` 并明确 `not_started=true`；同批已启动工具 SHALL 按既有取消约束停止与清理。

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

#### Scenario: 授权等待超过执行上限
- **WHEN** 用户等待超过调用配置的工具执行上限后批准操作
- **THEN** 调用从实际启动时获得完整执行期限，不因等待授权返回 `timeout`

#### Scenario: 等待授权时取消
- **WHEN** 用户在授权等待期间按 Ctrl+C
- **THEN** 等待中的调用返回未启动的 `cancelled`，其他活动工具被清理，本轮停止且不发起后续模型请求

## ADDED Requirements

### Requirement: 权限拒绝与并发调用隔离
普通权限拒绝 SHALL 作为已配对的工具结果提交给模型并继续 Agent Loop，SHALL NOT 作为用户取消或未知工具累计。多个只读调用 SHALL 保留既有并发执行能力，人工授权请求 SHALL 串行展示；一个调用命中 deny 或被用户拒绝 SHALL NOT 取消同批其他调用。每个调用的授权决定、实际启动与结果 SHALL 与其调用标识关联。

#### Scenario: 拒绝后调整方案
- **WHEN** 用户拒绝一个有效单文件或 shell 工具调用且任务未取消或达到其他停止条件
- **THEN** 模型收到 `permission_denied` 和 `not_started=true` 并能在本轮继续调整方案，系统不要求用户重新发起任务

#### Scenario: 同批部分调用拒绝
- **WHEN** 同批只读调用中一个被拒绝，另一个已获准
- **THEN** 被拒绝调用返回自己的权限错误，获准调用继续执行并返回自己的实际结果

#### Scenario: 多个调用同时需要决定
- **WHEN** 同批多个调用需要人工授权
- **THEN** 系统逐个展示授权请求并关联各自调用，不并发争用终端输入，已获准的只读调用仍能按既有并发规则执行
