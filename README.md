# MewCode

MewCode 是一个 Python 终端 AI 编程助手，支持 Claude、DeepSeek 的 Anthropic 兼容接口，以及符合 OpenAI Chat Completions 协议的服务。一次提问可以自主完成搜索、读取、修改、运行检查等多个步骤：模型选择工具，结果回传后继续行动，直到模型结束或触发停止条件。同一次运行中保留对话和工具操作历史。

## 安装与启动

需要 Python 3.11 或更新版本，运行环境为 macOS 或 Linux（POSIX，提供 `/bin/sh`）。两个搜索工具依赖 PATH 中的 `rg`（ripgrep），macOS 可用 `brew install ripgrep`，Debian/Ubuntu 可用 `sudo apt install ripgrep`。未安装时搜索返回 `dependency_missing`，其他工具仍可使用。项目可用 `uv` 安装：

```bash
uv sync
uv run mewcode --config .env
```

也可以用常规 Python 环境安装：

```bash
python -m pip install -e .
mewcode --config .env
```

`--config` 为必填参数。输入 `/exit` 或在提示符处按 Ctrl+D 结束会话；生成过程中按 Ctrl+C 会取消本轮并返回提示符。会话历史只保存在本次运行的内存里。

## 配置

每个 `.env` 文件只放一组配置。可复制 [.env.anthropic.example](.env.anthropic.example)、[.env.deepseek.example](.env.deepseek.example) 或 [.env.openai.example](.env.openai.example) 到 `.env.claude`、`.env.deepseek`、`.env.openai` 等本地文件，填入自己的 API 密钥，再用 `--config` 选择文件。真实 `.env` 文件已被 Git 忽略；不要把密钥填入会被 Git 跟踪的 `.example` 模板。

| 字段 | 含义 |
| --- | --- |
| `name` | 启动时显示的配置名 |
| `protocol` | `anthropic` 或 `openai` |
| `model` | 服务端模型标识 |
| `base_url` | API 基础地址，不能填完整的 `/messages` 或 `/chat/completions` 请求地址；OpenAI 兼容服务通常需要其 `/v1` 前缀 |
| `api_key` | 认证密钥 |
| `thinking` | 可选，`true` 或 `false`，省略视为 `false`；适用于 Anthropic 协议的 Claude 和 DeepSeek 兼容地址 |
| `max_iterations` | 可选，十进制正整数，缺省 20；每个用户任务的应用层模型请求上限，包含失败请求、最终答复和上下文恢复重试 |

`thinking=true` 时，MewCode 会请求 Claude 返回可读的思考摘要，或请求 DeepSeek 的 Anthropic 兼容接口返回思考内容，并将收到的片段标成“思考”实时显示。Claude API 不提供原始思维链；自适应思考也可能在简单问题中不返回思考片段。DeepSeek 兼容地址使用 `https://api.deepseek.com/anthropic`，并按其协议设置思考开关。对于 Claude，`thinking=false` 不展示思考，但不能保证关闭某些模型默认且不可关闭的内部思考；对于 DeepSeek，该值会发送关闭思考的参数。

对话历史超出模型上下文限制时，MewCode 会自动丢弃最早的完整对话轮次并重试请求，同时在终端提示已丢弃的轮数；仅当前一轮本身超限、或超限发生在已输出流式片段之后无法安全重试时，才显示错误并结束本轮。兼容服务需要支持所选协议的原生工具定义、流式调用和工具结果；拒绝工具参数时会明确提示协议不兼容。

## 六个核心工具

模型可以连续调用工具，也可以一次返回多个调用。连续的 `read_file`、`glob_files`、`search_code` 最多 4 个并发；写文件、改文件和所有 shell 命令逐个执行，并等待前面的批次完成，后面的读取不会越过修改。工具错误回传后，模型可以在同一任务中调整参数；调度器不自动重试工具。

每个任务有独立请求预算，多工具响应只消耗一次模型请求。最后一次请求如果返回工具，整个批次完成并保存结果后停止，不额外请求总结。普通非空答复以 `model_done` 结束；请求上限、用户取消、连续 3 轮全部未知工具或流失败分别报告 `max_iterations`、`cancelled`、`unknown_tool_limit`、`stream_error`。模型结束并不替代实际验收。

上下文恢复保留整轮裁剪方案，恢复请求也消耗同一预算；当前任务的多个阶段和成对结果整体保留。较早历史被移除时终端会提示，摘要压缩留待后续优化。SDK 隐式重试关闭，除现有上下文恢复外不自动重试模型请求。

## 规划与执行

- `/plan`：进入持续的只读规划模式，提示输入任务；指令本身不请求模型。
- `/plan <任务>`：立即开始只读探索与规划。模型仅能看到三个读类工具，执行入口也会拒绝写入、编辑和 shell。
- 后续普通消息继续修订计划并保持只读。每次正常结束的最终答复作为最新有效计划，包含目标、步骤和验证方式；新修订开始时旧计划立即失效，修订失败或取消不会恢复旧版本。
- `/do`：直接执行最新有效计划，切回全部工具并使用新预算。执行开始即消费计划，重复 `/do` 不会重放；没有有效计划时只提示，不请求模型。

计划和历史仅保存在当前进程内。活动任务中 Ctrl+C 会关闭模型流、终止活动工具及受控子进程，补齐取消结果后返回提示符；已经完成的真实操作仍保留，未启动调用会标明未启动，不声称回滚。空闲时 Ctrl+C 退出。

终端分别显示规划／执行模式、权限模式、请求次数/上限及执行阶段，并用调用 ID 区分接收、等待授权、实际开始和结果。Token 用量使用服务实际统计；缺失字段显示“未知”，累计更新不重复相加。任一请求缺少统计时，累计值只表示已知部分并标为不完整，不能当作完整用量或账单。

## 权限与人工授权

默认使用 `default` 权限模式：没有适用 allow 规则或已有批准的操作先询问，包括只读文件和搜索。这改变了旧版默认直接执行工具的行为。六个工具名称与模型参数保持不变，权限判定在父进程完成，未批准的目标操作不启动，等待授权不消耗工具执行超时。普通权限拒绝作为工具结果交给模型，模型可以继续调整本轮任务；Ctrl+C 则取消整轮。

```bash
uv run mewcode --config .env --permission-mode strict
```

`--permission-mode` 支持 `strict`、`default`、`bypass`，省略时为 `default`。规则合并后再应用模式：

| 规则结果 | strict | default | bypass |
| --- | --- | --- | --- |
| deny | 拒绝 | 拒绝 | 拒绝 |
| ask | 需授权 | 需授权 | 放行 |
| allow | 需授权 | 放行 | 放行 |
| 未命中 | 需授权 | 需授权 | 放行 |

“需授权”先检查已有会话／永久批准，未命中才询问。任何模式与批准都不能解除内置危险命令黑名单、文件／搜索路径越界限制或明确 deny。权限模式独立于规划／执行：`/plan` 始终禁止修改和 shell，即使使用 bypass；`/do` 保留权限状态，只代表开始执行最新计划，后续调用仍独立判断权限。

### YAML 规则与来源

启动时的真实工作根目录固定了项目范围，不跟随 `.env` 位置或 shell 的 `cd`。读取以下三个文件，将所有规则作为集合合并：

| 来源 | 路径 | 用途 |
| --- | --- | --- |
| 用户 | `~/.mewcode/permissions.yaml` | 跨项目手工规则 |
| 项目 | `<root>/.mewcode/permissions.yaml` | 可提交、共享的项目规则 |
| 本地 | `<root>/.mewcode/permissions.local.yaml` | 本机项目规则和永久批准，Git 忽略 |

任何来源的匹配结果都遵守 **deny > ask > allow**，不按来源或列表顺序覆盖；精确 allow 不能压过 glob ask／deny。缺失或空文件等于没有规则，`version` 缺省为 1，`rules` 缺省为空列表。重复键、未知字段、非法工具或规则、不安全 YAML 对象、无效批准均会报错：启动时停止启动，运行中拒绝相关调用。每次调用和审批结束后重新加载；等待时新增 deny 也能阻止原操作。

以下可保存为项目级 `.mewcode/permissions.yaml`：

```yaml
version: 1
rules:
  - effect: allow
    rule: "read_file(src/**)"
    match: glob
  - effect: allow
    rule: "glob_files(**)"
    match: glob
  - effect: ask
    rule: "edit_file(src/**)"
    match: glob
  - effect: deny
    rule: "read_file(secrets/**)"
    match: glob
  - effect: deny
    rule: "search_code(secrets/**)"
    match: glob
  - effect: allow
    rule: "Bash(git *)"
    match: glob
  - effect: ask
    rule: "Bash(git push*)"
    match: glob
  - effect: allow
    rule: "Bash(python -m pytest)"
    match: exact
```

规则格式是 `工具名(模式)`，`Bash` 是 `execute_command` 的规则别名。`exact` 匹配完整字面字符串；`glob` 匹配完整范围，文件路径的 `*` 不跨目录、`**` 可跨零层或多层，大小写敏感。文件规则使用 resolve 后的真实路径相对 root 的 POSIX 表达，链接别名不能绕开规则。工具间不继承权限，例如 `read_file` 的规则不能授权 `search_code` 或 `edit_file`；`search_code` 的权限模式匹配文件路径，内容正则 `pattern` 不用作权限路径。

搜索先枚举项目内可见候选路径，再逐文件授权，随后仅把获准的显式文件列表交给搜索。deny 或被拒绝的文件会跳过，文件内容不会先读再过滤。结果中的 `permission_limited` 与 `skipped_files` 说明搜索范围受限；它独立于输出 `truncated`。所有候选被排除仍返回受限的成功空结果，不能据此断言完整项目没有匹配；没有候选则是普通空结果。受拒文件名和内容不会进入模型结果。

### 审批范围与管理

终端每次只展示一个授权请求，关联请求 ID 和工具调用。审批可查看完整参数、真实目标、写入内容或当前提供的编辑原文／新文差异；预览不会额外读取未授权文件。长详情可输入 `next`、`back` 翻页，或 `all` 查看全部，然后选择：

| 输入 | 决定 | 有效范围 |
| --- | --- | --- |
| 回车或 `1` | 拒绝 | 当前请求，模型可以继续本轮 |
| `2` | 本次 | 当前完整调用参数与展示的目标，一次使用 |
| `3` | 会话 | 本次运行、工作根目录、具体工具与精确对象 |
| `4` | 永久 | 同样的精确范围，保存至项目本地批准，重启可恢复 |

会话／永久 shell 批准只记住完整精确 command，不从 `git status` 扩展到 `git *`。文件批准绑定具体工具与真实文件路径，允许未来同一工具在同一路径使用不同内容；搜索批准记住展示的每个真实候选文件，允许以后不同搜索表达式，新候选仍需重新判定。读取批准不会变成编辑批准，链接后来指向其他目标也不能复用原批准。新增 ask 不自动撤销已有有效批准；新 deny 始终优先。

永久批准是本地 YAML 的 `approvals`，由可信终端管理入口原子保存并保留原有 rules；用户／项目级文件不接受此字段。自动保存的记录形如：

```yaml
version: 1
approvals:
  - id: "approval-id"
    root: "/absolute/project"
    tool: read_file
    scope:
      kind: path
      value: "/absolute/project/src/main.py"
```

永久保存失败只按本次批准执行，并明确提示未记住。空闲提示符可输入 `/permissions` 查看当前模式、项目及授权记录，`/permissions mode strict|default|bypass` 切换模式，`/permissions revoke session` 或 `/permissions revoke permanent` 清除本项目对应批准。控制命令不请求模型、不改变历史或计划，不删除规则；撤销失败不会报告成功。

非法审批选项保持未批准并重新询问。Ctrl+C 清理当前任务后返回提示符并丢弃未提交的授权输入；授权中遇到 EOF 则取消、清理后退出。非交互输入没有自动批准通道：需 ask 的单文件／shell 操作返回未启动的 `permission_denied`，搜索跳过需授权候选。程序调用可显式注入可信审批回调。

### shell 检查的边界

内置 regex 黑名单覆盖已知的根目录／主目录递归删除、磁盘格式化或擦除、原始设备写入、fork bomb 形式，不能通过 YAML 放开。黑名单先检查原文，再对 AST 中可见的静态命令复查；可能保守误拦引用文本，不保证发现编码或间接执行的危险行为。

shell glob allow 只适用于单个简单静态命令，无重定向、连接、子 shell 或展开。因此 `Bash(git *)` 可放行 `git status`，不能据此放行 `git status && printf done`；复杂操作需完整 exact allow 或独立批准，且仍服从子命令 ask／deny。支持的管道、连接、重定向、子 shell、命令替换会检查可见子命令，任一 deny 拒绝整条，前缀也不会提前执行。不能完整分析的语法（例如 heredoc、控制结构、复杂参数或算术展开）在执行前返回 `permission_check_failed`。

这些检查不构成操作系统文件系统沙箱。获准 shell 仍具有当前用户权限，可以访问项目外、网络，或间接执行脚本；解析器不执行展开来求值，也不审查脚本内部。专用文件写入／编辑会保护三份权限 YAML 及链接别名，但任意已获准 shell 仍可能间接改写配置。文件路径 resolve 检查也不承诺对抗恶意并发替换目录祖先或硬链接。

## 系统提示与缓存

固定系统提示按身份、系统约束、任务模式、动作执行、工具使用、语气风格、文本输出七个模块拼装，模块之间空一行。环境与当前模式放在消息中，保持固定提示和同模式工具定义稳定，供服务复用公共前缀。

每次模型请求都会附加 `<mewcode-context>` 提醒。本地将它与真实用户消息区分，线上使用普通消息内容；标签不提供原生系统权限，也不会在终端生成额外的 `你>` 输入。环境快照包含实际工作根目录、操作系统、Python 版本和会话启动日期。自定义指令、已激活 Skill、长期记忆保留按顺序显式传入文本的位置，默认不加载文件或记忆。

提醒周期按实际模型请求计数，包含失败与上下文恢复请求：第 1、6、11…次完整，其余精简；普通新任务延续周期，显式 `/plan` 和有效 `/do` 重置周期。裁剪后强制完整补充，未成功提交的完整提醒会继续补发。已提交的提醒原样保留，随所属真实任务一起裁剪。

DeepSeek 使用服务端自动缓存；官方 Claude 在固定系统内容末尾设置显式缓存标记；OpenAI 和其他兼容地址保持基础协议。规划仍只提供三个只读工具，执行提供六个，模式切换可能需要重新建立缓存。

终端分别显示总输入、输出、缓存命中、未命中、实际写入及命中率。DeepSeek Anthropic 兼容实测的基础 `input_tokens` 为未命中输入，总输入为它与 `cache_read_input_tokens` 之和；`cache_creation_input_tokens=0` 不足以说明自动缓存实际写入量，因此写入显示未知。原生 Claude 的总输入还包含缓存创建量。累计命中率使用累计命中除以累计总输入，任一请求缺少必要统计则显示未知；已知部分会标注“部分”。

全局提示和工具描述共同引导优先使用专用工具、先读后改、失败后重新核查。它们是模型行为指引，现有规划工具白名单仍由程序执行。真实对比中观察到了缓存命中，也观察到模型在匹配失败后跳过重读的情况；小样本不能证明整体质量或耗时必然改善。详见[本章验收记录](docs/validation/structured-system-prompt/README.md)。

## 工具参数

| 工具 | 参数 | 行为 |
| --- | --- | --- |
| `read_file` | `path`；`start_line=1`；`max_lines=200`（1–1000） | 读取 UTF-8 文本的指定行，返回内容和行号；空文件成功返回空内容 |
| `write_file` | `path`、`content` | 创建文件及缺少的父目录；已有文件、目录和链接一律拒绝覆盖 |
| `edit_file` | `path`、`old_text`、`new_text` | 非空原文恰好匹配一次才替换；零次或多次（含重叠）都保持文件不变；新文为空表示删除 |
| `execute_command` | `command`；`timeout_seconds=30`（1–120） | 将完整命令字符串交给 `/bin/sh -c`，支持管道、重定向；分别返回 stdout、stderr 和退出码 |
| `glob_files` | `pattern`；`max_results=100`（1–1000） | 按路径 glob 列出文件；支持 `*`、`?`、`[]` 与跨目录 `**`；无斜杠的模式匹配各层文件名 |
| `search_code` | 正则 `pattern`；可选路径 `glob`；`max_results=100`（1–1000） | 用 rg 正则搜索，返回相对路径、行号和匹配行 |

所有参数都先通过 JSON Schema 校验，不接受额外字段。工具统一返回 `ok`、`data`、`error`（含 `code`、`message`、`details`）和 `truncated`。

### 工作目录与限制

- 启动 MewCode 时的当前目录固定为工作根目录，与配置文件所在位置无关。三个文件工具和两个搜索工具限制在这个目录内，解析真实路径后拒绝越界和指向外部的链接。文件工具只操作普通 UTF-8 文本，跳过或拒绝不支持的编码与特殊文件。
- 两个搜索工具跳过隐藏、忽略文件及符号链接，内容搜索跳过二进制内容；无匹配是成功的空结果。相对路径及匹配行保持有序，路径 glob 不覆盖 rg 的默认忽略规则。
- shell 以工作根目录启动，具有当前操作系统用户的权限，**不是目录沙箱**，可以访问目录外或网络。每次命令使用独立 shell，`cd`、变量和环境变化不会延续到下一次调用；stdin 关闭，不支持交互程序。
- 所有工具默认 30 秒超时，只有命令允许 1–120 秒覆盖。超时或 Ctrl+C 会终止工作进程及同组子进程；主动脱离进程组的后台程序不在这一清理保证内。
- 结果内容上限 64 KiB；命令 stdout、stderr 各最多 32 KiB。文件创建内容、编辑前后文件各不超过 1 MiB，工具参数流不超过 2 MiB。超出的输出会标明截断，输入超限则报错。
- 创建文件以原子方式拒绝覆盖，编辑以原子替换提交并检查并发变化；不能保证对抗另一个恶意进程同时替换目录祖先。超时、取消和外部命令可能已产生副作用，继续前应先核查状态。

终端显示 `工具>` 操作摘要和成功、失败、超时、取消、截断状态，摘要不会完整打印写入正文。只有完整响应才执行工具；全批调用与按原始顺序排列的结果在下一次模型请求前成对保存。后续模型答复失败或中断仍保留已提交记录。退出后这些历史不恢复。

### 试用

从准备好的工作目录启动，配置路径可以是绝对路径。例如发送：“搜索 add 函数，读取实现，修复减法错误，然后执行 Python 断言检查。”

先规划再执行的例子：输入 `/plan 检查 add 函数并提出修复计划`，再输入普通消息修订验证步骤，最后输入 `/do` 执行最新计划。
