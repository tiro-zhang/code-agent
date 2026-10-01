## Purpose

定义 MewCode 首批六个本地工具对模型和用户可见的参数、结果与失败语义，确保文件操作遵守工作目录边界，并能明确区分读取、新建、唯一匹配修改、命令执行和两类搜索。

## ADDED Requirements

### Requirement: 文件工具的工作目录边界
系统 SHALL 在启动时固定工作根目录。五个文件及搜索工具 SHALL 将相对路径相对于该目录解析；绝对路径仅在根目录内才合法。系统 SHALL 拒绝通过 `..`、绝对路径或符号链接访问根目录外的文件，返回 `path_outside_workspace`；新建文件也 SHALL 校验已存在的祖先目录。搜索 SHALL 不遍历指向目录外的链接。该约束 SHALL 明确限定于文件及搜索工具，不得宣称它同时隔离 shell 命令。

#### Scenario: 普通相对路径
- **WHEN** 模型读取 `src/main.py`
- **THEN** 系统访问启动根目录中的该文件，而不受先前 shell 命令的 `cd` 影响

#### Scenario: 目录穿越与链接越界
- **WHEN** 路径或其祖先符号链接解析到根目录外
- **THEN** 文件操作返回 `path_outside_workspace`，搜索不返回该目录外文件的内容

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
`execute_command` SHALL 接受完整非空 `command` 字符串及可选 `timeout_seconds`，超时值为 1 至 120 的整数，默认 30。命令 SHALL 在启动根目录内使用非交互 shell 执行，支持管道和重定向，标准输入不等待用户输入；每次调用 SHALL 使用独立 shell。结果 SHALL 包含退出码、标准输出、标准错误和截断信息；非零退出 SHALL 返回 `command_failed` 并保留这些数据。

#### Scenario: 管道与重定向
- **WHEN** 命令包含有效的 shell 管道或重定向
- **THEN** 按 shell 语义执行并返回对应输出及退出码

#### Scenario: 非零退出
- **WHEN** 命令以非零状态退出
- **THEN** 返回 `command_failed` 和真实退出码、标准输出及标准错误，会话继续可用

#### Scenario: 工作目录不跨调用持久化
- **WHEN** 前次命令执行了 `cd` 或设置了 shell 变量，用户下一轮再次调用命令
- **THEN** 新命令仍从启动根目录开始，不继承前次 shell 的目录和临时变量变更

### Requirement: 按模式查找文件
`glob_files` SHALL 接受相对工作根目录的文件路径模式 `pattern` 和可选 `max_results`，返回按路径排序的相对文件路径。搜索 SHALL 默认跳过隐藏文件和被忽略的文件，不跟随符号链接目录，并遵守目录与结果上限；无匹配 SHALL 作为成功的空结果返回。

#### Scenario: 查找 Python 文件
- **WHEN** 模型请求模式 `**/*.py`
- **THEN** 返回工作根目录内符合模式且未被忽略的文件路径，并标明结果是否被截断

#### Scenario: 没有文件匹配
- **WHEN** 有效模式未匹配任何文件
- **THEN** 返回成功的空列表

### Requirement: 搜索代码内容
`search_code` SHALL 接受正则表达式 `pattern`，以及可选文件模式 `glob` 和 `max_results`，在根目录内的文本文件中搜索。结果 SHALL 包含相对路径、从 1 开始的行号和匹配行文本，按路径和行号排序；工具 SHALL 默认跳过隐藏、被忽略和二进制文件，不跟随符号链接目录。无匹配 SHALL 返回成功的空列表，非法正则 SHALL 返回 `invalid_pattern`。

#### Scenario: 返回匹配位置
- **WHEN** 表达式在多个文本文件中匹配
- **THEN** 返回相应路径、行号和行文本，并遵守条数与内容大小上限

#### Scenario: 无匹配或表达式非法
- **WHEN** 搜索没有匹配或正则无效
- **THEN** 分别返回成功空列表或 `invalid_pattern`，不会把无匹配误判为命令失败

#### Scenario: 搜索依赖不可用
- **WHEN** 运行环境缺少搜索所需的可执行程序
- **THEN** 返回 `dependency_missing` 并提示安装所需程序，其余工具与对话仍可用
