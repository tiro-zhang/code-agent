## MODIFIED Requirements

### Requirement: 文件工具的工作目录边界
系统 SHALL 为每个运行固定并解析实际工作根：主会话使用启动目录，未隔离子沿用原合同，隔离子使用进入时绑定的工作副本项目目录。后续调用 SHALL 显式使用所属运行的根，不修改进程 cwd 或共享父根。五个文件及搜索工具 SHALL 将相对路径相对于该目录解析；绝对路径仅在根目录内才合法。系统 SHALL 拒绝通过 `..`、绝对路径或符号链接访问根目录外的文件，返回 `path_outside_workspace`；新建文件也 SHALL 校验已存在的祖先目录。搜索 SHALL 不遍历指向目录外的链接。该约束 SHALL 明确限定于文件及搜索工具，不得宣称它同时隔离 shell 命令。文件权限规则 SHALL 匹配真实路径相对于固定工作根目录的规范化路径，SHALL NOT 以调用方传入的路径别名规避规则。系统 SHALL 在实际执行前重新解析目标并复查边界、权限规则和有效授权范围。

#### Scenario: 普通相对路径
- **WHEN** 模型读取 `src/main.py`
- **THEN** 系统访问当前运行固定工作根目录中的该文件，而不受先前 shell 命令的 `cd` 影响

#### Scenario: 目录穿越与链接越界
- **WHEN** 路径或其祖先符号链接解析到根目录外
- **THEN** 文件操作返回 `path_outside_workspace`，搜索不返回该目录外文件的内容

#### Scenario: 根目录内的路径别名
- **WHEN** 模型通过根目录内的符号链接或包含可消解路径片段的路径引用受限文件
- **THEN** 权限规则按实际目标的规范化项目相对路径判断，不能通过别名绕过 deny

#### Scenario: 隔离子访问父绝对路径
- **WHEN** 隔离子请求读写父目录的绝对文件路径，真实目标不在子实际工作根内
- **THEN** 返回 path_outside_workspace，父已有批准不能解除该目录边界

#### Scenario: 根内同名文件互不覆盖
- **WHEN** 父与隔离子分别获准编辑同名相对文件
- **THEN** 各自产物写入所属工作根，父根及兄弟根保持独立

### Requirement: 完整 shell 命令执行
`execute_command` SHALL 接受完整非空 `command` 字符串及可选 `timeout_seconds`，超时值为 1 至 120 的整数，默认 30。命令 SHALL 在当前运行固定工作根目录内使用非交互 shell 执行，支持管道和重定向，标准输入不等待用户输入；每次调用 SHALL 使用独立 shell。结果 SHALL 包含退出码、标准输出、标准错误和截断信息；非零退出 SHALL 返回 `command_failed` 并保留这些数据。权限配置中的 `Bash` SHALL 作为 `execute_command` 的规则别名，SHALL NOT 改变模型 API 工具名称或参数。命令 SHALL 在执行前接受不可解除的危险命令黑名单检查，以及完整命令串和其中显式子命令的 deny / ask 检查；任一部分命中 deny SHALL 在整条命令开始前拒绝。无法完成所支持 shell 结构的解析或结构权限分析 SHALL 返回 `permission_check_failed` 和 `not_started=true`；权限配置无效 SHALL 返回 `permission_config_error` 和 `not_started=true`；SHALL NOT 执行可识别的部分。glob allow SHALL 仅用于简单命令；包含管道、重定向、命令串联、子 shell、命令替换等复杂结构的命令 SHALL NOT 因 glob allow 获准，其放行 SHALL 依赖完整命令 exact allow、有效授权或 bypass 对非 deny 的放行语义。所有放行 SHALL 继续遵守当前权限模式、deny、黑名单及规划限制；strict SHALL 仍将 exact allow 判为 ask，default 下显式子命令的 ask SHALL 仍需有效授权满足。系统 SHALL NOT 将该检查宣称为操作系统级 shell 沙箱或脚本内部及运行时动态命令的追踪。

#### Scenario: 管道与重定向
- **WHEN** 命令包含有效的 shell 管道或重定向，且结构检查与整条调用的权限判断均通过
- **THEN** 按 shell 语义执行并返回对应输出及退出码

#### Scenario: 非零退出
- **WHEN** 命令以非零状态退出
- **THEN** 返回 `command_failed` 和真实退出码、标准输出及标准错误，会话继续可用

#### Scenario: 工作目录不跨调用持久化
- **WHEN** 前次命令执行了 `cd` 或设置了 shell 变量，用户下一轮再次调用命令
- **THEN** 新命令仍从当前运行固定工作根目录开始，不继承前次 shell 的目录和临时变量变更

#### Scenario: 显式子命令命中拒绝
- **WHEN** 组合命令的完整串未命中 deny，但其中任一显式子命令命中 deny
- **THEN** 整条调用返回 `permission_denied` 和 `not_started=true`，前置命令及其他组成部分均不执行

#### Scenario: 简单命令的 glob allow 不扩展到组合命令
- **WHEN** default 模式下存在 glob allow `Bash(git *)`，调用为 `git status && printf done`，且没有完整命令 exact allow 或有效授权
- **THEN** 系统要求对完整命令授权，不通过该 glob allow 直接执行组合命令

#### Scenario: 子命令 ask 高于完整串 allow
- **WHEN** default 模式下完整命令具有 exact allow，但其中显式子命令命中 ask，且没有有效授权
- **THEN** 系统仍要求人工授权，不以完整串 allow 越过 ask

#### Scenario: 解析无法完成
- **WHEN** shell 结构不能被权限检查完整解析
- **THEN** 返回 `permission_check_failed` 和 `not_started=true`，命令及其可识别部分均不执行

#### Scenario: 黑名单不可批准
- **WHEN** 命令命中硬黑名单，即使当前为 bypass 模式或已有有效授权
- **THEN** 系统在执行前拒绝整条调用，用户授权不能解除该拒绝

#### Scenario: 隔离命令使用子 cwd
- **WHEN** 隔离子获准执行输出 cwd 和写入相对产物的命令
- **THEN** 命令从子实际工作根开始，产物落在子目录，父和兄弟的默认 cwd 不变，仍不承诺目录沙箱
