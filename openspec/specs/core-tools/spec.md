# core-tools Specification

## Purpose

定义 MewCode 首批六个本地工具对模型和用户可见的参数、结果与失败语义，确保文件操作遵守工作目录边界，并能明确区分读取、新建、唯一匹配修改、命令执行和两类搜索。

## Requirements

### Requirement: 文件工具的工作目录边界
系统 SHALL 在启动时固定工作根目录。五个文件及搜索工具 SHALL 将相对路径相对于该目录解析；绝对路径仅在根目录内才合法。系统 SHALL 拒绝通过 `..`、绝对路径或符号链接访问根目录外的文件，返回 `path_outside_workspace`；新建文件也 SHALL 校验已存在的祖先目录。搜索 SHALL 不遍历指向目录外的链接。该约束 SHALL 明确限定于文件及搜索工具，不得宣称它同时隔离 shell 命令。文件权限规则 SHALL 匹配真实路径相对于固定工作根目录的规范化路径，SHALL NOT 以调用方传入的路径别名规避规则。系统 SHALL 在实际执行前重新解析目标并复查边界、权限规则和有效授权范围。

#### Scenario: 普通相对路径
- **WHEN** 模型读取 `src/main.py`
- **THEN** 系统访问启动根目录中的该文件，而不受先前 shell 命令的 `cd` 影响

#### Scenario: 目录穿越与链接越界
- **WHEN** 路径或其祖先符号链接解析到根目录外
- **THEN** 文件操作返回 `path_outside_workspace`，搜索不返回该目录外文件的内容

#### Scenario: 根目录内的路径别名
- **WHEN** 模型通过根目录内的符号链接或包含可消解路径片段的路径引用受限文件
- **THEN** 权限规则按实际目标的规范化项目相对路径判断，不能通过别名绕过 deny

### Requirement: 读取文本文件
`read_file` SHALL 接受 `path` 以及可选的 `start_line`、`max_lines`；行号从 1 开始，默认读取前 200 行。结果 SHALL 包含路径、实际行范围、原始文本和截断标记。工具 SHALL 读取 UTF-8 普通文本文件；文件不存在、不是普通文件或无法作为文本解码时 SHALL 返回明确错误，不阻塞在管道或设备文件上。

#### Scenario: 分段读取
- **WHEN** 请求一个存在文件的指定行范围
- **THEN** 返回该范围内的原始文本、实际行号及是否有剩余内容的标记，空文件或起始行超过文件末尾返回空内容

#### Scenario: 无法读取
- **WHEN** 文件不存在、为目录或特殊文件、包含不支持的二进制内容或编码
- **THEN** 返回 `file_not_found`、`not_regular_file` 或 `unsupported_encoding` 等对应错误

### Requirement: 仅创建新文件
`write_file` SHALL 接受 `path` 和完整 `content`，在根目录内按需创建父目录并以 UTF-8 创建新文件。目录边界检查 SHALL 优先进行；通过该检查后，目标路径已存在时，包括目录和符号链接，SHALL 返回 `file_exists`，不得覆盖；已有普通文件的错误 SHALL 提示使用编辑工具。检查与创建之间发生竞争也 SHALL 保持拒绝覆盖。成功结果 SHALL 包含相对路径和写入字节数。

#### Scenario: 创建新文件
- **WHEN** 目标不存在且路径合法
- **THEN** 创建文件及需要的父目录，文件内容与请求内容完全一致

#### Scenario: 目标已存在
- **WHEN** 路径通过目录边界检查，且目标文件、目录或链接已经存在，或在创建提交前由另一个进程创建
- **THEN** 返回 `file_exists`，保留已有目标的内容

### Requirement: 唯一原文匹配编辑
`edit_file` SHALL 接受 `path`、非空 `old_text` 和 `new_text`。工具 SHALL 仅对现存 UTF-8 普通文件进行区分大小写的原文匹配，不做正则解释、空白归一化或模糊匹配；匹配次数按所有起始位置计数，包括重叠匹配。仅恰好一次匹配时 SHALL 替换并提交完整新内容，保留其余内容及原有文件权限。

#### Scenario: 唯一匹配
- **WHEN** 原文恰好出现一次
- **THEN** 仅替换该处，返回路径和替换次数 1

#### Scenario: 匹配不到
- **WHEN** 原文出现零次
- **THEN** 返回 `text_not_found`、匹配数 0 和重新读取文件的建议，文件保持原状

#### Scenario: 匹配多次或重叠
- **WHEN** 原文出现两次以上，包括 `aaa` 中查找 `aa`
- **THEN** 返回 `multiple_matches`、实际匹配数和扩大原文上下文的建议，文件保持原状

#### Scenario: 空原文或编码不支持
- **WHEN** 原文为空或目标无法作为 UTF-8 普通文本处理
- **THEN** 返回明确错误，目标不被改写

### Requirement: 完整 shell 命令执行
`execute_command` SHALL 接受完整非空 `command` 字符串及可选 `timeout_seconds`，超时值为 1 至 120 的整数，默认 30。命令 SHALL 在启动根目录内使用非交互 shell 执行，支持管道和重定向，标准输入不等待用户输入；每次调用 SHALL 使用独立 shell。结果 SHALL 包含退出码、标准输出、标准错误和截断信息；非零退出 SHALL 返回 `command_failed` 并保留这些数据。权限配置中的 `Bash` SHALL 作为 `execute_command` 的规则别名，SHALL NOT 改变模型 API 工具名称或参数。命令 SHALL 在执行前接受不可解除的危险命令黑名单检查，以及完整命令串和其中显式子命令的 deny / ask 检查；任一部分命中 deny SHALL 在整条命令开始前拒绝。无法完成所支持 shell 结构的解析或结构权限分析 SHALL 返回 `permission_check_failed` 和 `not_started=true`；权限配置无效 SHALL 返回 `permission_config_error` 和 `not_started=true`；SHALL NOT 执行可识别的部分。glob allow SHALL 仅用于简单命令；包含管道、重定向、命令串联、子 shell、命令替换等复杂结构的命令 SHALL NOT 因 glob allow 获准，其放行 SHALL 依赖完整命令 exact allow、有效授权或 bypass 对非 deny 的放行语义。所有放行 SHALL 继续遵守当前权限模式、deny、黑名单及规划限制；strict SHALL 仍将 exact allow 判为 ask，default 下显式子命令的 ask SHALL 仍需有效授权满足。系统 SHALL NOT 将该检查宣称为操作系统级 shell 沙箱或脚本内部及运行时动态命令的追踪。

#### Scenario: 管道与重定向
- **WHEN** 命令包含有效的 shell 管道或重定向，且结构检查与整条调用的权限判断均通过
- **THEN** 按 shell 语义执行并返回对应输出及退出码

#### Scenario: 非零退出
- **WHEN** 命令以非零状态退出
- **THEN** 返回 `command_failed` 和真实退出码、标准输出及标准错误，会话继续可用

#### Scenario: 工作目录不跨调用持久化
- **WHEN** 前次命令执行了 `cd` 或设置了 shell 变量，用户下一轮再次调用命令
- **THEN** 新命令仍从启动根目录开始，不继承前次 shell 的目录和临时变量变更

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

### Requirement: 按模式查找文件
`glob_files` SHALL 接受相对工作根目录的文件路径模式 `pattern` 和可选 `max_results`，返回按路径排序的相对文件路径。搜索 SHALL 默认跳过隐藏文件和被忽略的文件，不跟随符号链接目录，并遵守目录与结果上限；无匹配 SHALL 作为成功的空结果返回。权限规则 SHALL 按实际候选文件的规范化项目相对路径匹配，SHALL NOT 仅按输入 pattern 判断。系统 SHALL 逐文件决定权限，先完成 ask 所需授权，再对获准候选进行目标工具操作；命中 deny 或被用户拒绝的候选 SHALL 被跳过，其余获准候选 SHALL 继续处理。结果 SHALL 提供 `permission_limited` 与 `skipped_files`，分别说明是否因权限缩小范围及因此跳过的候选文件数；结果 SHALL NOT 泄漏被拒绝候选的文件名或内容。

#### Scenario: 查找 Python 文件
- **WHEN** 模型请求模式 `**/*.py`
- **THEN** 返回工作根目录内符合模式、未被忽略且获准的文件路径，并标明结果是否被截断以及是否受权限限制

#### Scenario: 没有文件匹配
- **WHEN** 有效模式未匹配任何文件
- **THEN** 返回成功的空列表、`permission_limited=false` 和 `skipped_files=0`

#### Scenario: 部分候选受限
- **WHEN** 匹配文件中一个命中 deny，另一个经授权获准
- **THEN** 返回获准文件路径，`permission_limited=true` 且 `skipped_files=1`，不返回拒绝文件的路径

#### Scenario: 所有候选被拒绝
- **WHEN** 有匹配候选，但全部命中 deny 或被用户拒绝
- **THEN** 返回成功空列表、`permission_limited=true` 及对应 `skipped_files`，明确结果为空是因为权限限制

### Requirement: 搜索代码内容
`search_code` SHALL 接受正则表达式 `pattern`，以及可选文件模式 `glob` 和 `max_results`，在根目录内的文本文件中搜索。结果 SHALL 包含相对路径、从 1 开始的行号和匹配行文本，按路径和行号排序；工具 SHALL 默认跳过隐藏、被忽略和二进制文件，不跟随符号链接目录。无匹配 SHALL 返回成功的空列表，非法正则 SHALL 返回 `invalid_pattern`。权限规则 SHALL 按实际候选文件的规范化项目相对路径匹配，SHALL NOT 按正则 pattern 或仅按输入 glob 判断。系统 SHALL 在读取文件内容、进行文本或二进制判别及搜索匹配之前逐文件完成权限判断与 ask 所需授权，SHALL NOT 先搜索受限内容再过滤结果；候选路径元数据枚举 SHALL 允许先进行。命中 deny 或被用户拒绝的候选 SHALL 被跳过，获准候选 SHALL 继续搜索。结果 SHALL 提供 `permission_limited` 和 `skipped_files`，SHALL NOT 泄漏被拒绝文件的名称、内容或匹配信息。

#### Scenario: 返回匹配位置
- **WHEN** 表达式在多个获准文本文件中匹配
- **THEN** 返回相应路径、行号和行文本，并遵守条数与内容大小上限，明确是否因权限缩小搜索范围

#### Scenario: 无匹配或表达式非法
- **WHEN** 搜索没有匹配或正则无效
- **THEN** 分别返回成功空列表或 `invalid_pattern`，不会把无匹配误判为命令失败；有效搜索仍报告权限范围限制信息

#### Scenario: 搜索依赖不可用
- **WHEN** 运行环境缺少搜索所需的可执行程序
- **THEN** 返回 `dependency_missing` 并提示安装所需程序，其余工具与对话仍可用

#### Scenario: 内容读取前拒绝
- **WHEN** 候选文件命中 deny 或用户拒绝该文件的 ask
- **THEN** 系统不读取该文件内容或对其运行搜索，继续搜索其他获准候选，仅以跳过计数报告该限制

#### Scenario: 全部候选受限
- **WHEN** 所有候选文件均被权限规则或用户决定拒绝
- **THEN** 返回成功空列表、`permission_limited=true` 和被跳过文件数，明确不能据此判断工作区中没有匹配

### Requirement: 单文件工具授权范围
`read_file`、`write_file` 和 `edit_file` SHALL 在各自目标真实路径获准后才开始对应操作。授权 SHALL 与工具名、固定工作根目录及真实目标路径关联，本次授权 SHALL 额外绑定当前完整参数。工具 SHALL NOT 以另一文件、另一个工具或修改后的参数复用不适用的本次授权；会话与永久授权 SHALL 仅按其记录的工具及目标范围生效。权限规则或人工拒绝 SHALL 返回 `permission_denied` 和 `not_started=true`，不产生该调用的文件副作用；读取、创建和编辑各自既有参数、结果及路径越界等错误码 SHALL 保持不变。

#### Scenario: 读取授权不扩展为编辑
- **WHEN** 某文件已有 `read_file` 会话授权，但模型请求 `edit_file` 且该调用需要 ask
- **THEN** 系统要求独立的编辑授权，不将读取授权解释为修改权限

#### Scenario: 本次授权参数改变
- **WHEN** 用户批准本次写入后，调用的完整内容或目标发生改变
- **THEN** 系统不复用该本次授权，按改变后的完整调用重新判断权限并在必要时要求授权
