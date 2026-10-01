# MewCode

MewCode 是一个 Python 终端 AI 编程助手，支持 Claude、DeepSeek 的 Anthropic 兼容接口，以及符合 OpenAI Chat Completions 协议的服务。模型可以选择文件、命令与搜索工具，执行结果会回传给模型并生成最终答复；同一次运行中保留对话和工具操作历史。

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

`thinking=true` 时，MewCode 会请求 Claude 返回可读的思考摘要，或请求 DeepSeek 的 Anthropic 兼容接口返回思考内容，并将收到的片段标成“思考”实时显示。Claude API 不提供原始思维链；自适应思考也可能在简单问题中不返回思考片段。DeepSeek 兼容地址使用 `https://api.deepseek.com/anthropic`，并按其协议设置思考开关。对于 Claude，`thinking=false` 不展示思考，但不能保证关闭某些模型默认且不可关闭的内部思考；对于 DeepSeek，该值会发送关闭思考的参数。

对话历史超出模型上下文限制时，MewCode 会自动丢弃最早的完整对话轮次并重试请求，同时在终端提示已丢弃的轮数；仅当前一轮本身超限、或超限发生在已输出流式片段之后无法安全重试时，才显示错误并结束本轮。兼容服务需要支持所选协议的原生工具定义、流式调用和工具结果；拒绝工具参数时会明确提示协议不兼容。

## 六个核心工具

每轮最多执行一个工具。正常流程中，普通聊天只请求一次模型；工具轮次先获取调用，执行后再请求一次模型解释结果，并禁止继续调用工具。上下文超限时允许裁剪较早的完整对话轮次并额外请求模型，重试保持当前阶段的工具设置，不重复执行工具。工具失败后不会自动修正并再次执行，继续操作需要下一轮提问。服务若一次返回多个调用，所有调用都会被拒绝并获得错误结果。当前没有自动 Agent Loop。

上下文恢复目前保留整轮裁剪方案；被裁剪的较早信息会从本次会话历史中移除，终端会显示提示。裁剪策略、重试预算与摘要压缩留待后续优化。

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

终端显示 `工具>` 开始摘要和成功、失败、超时、取消、截断状态，摘要不会完整打印写入正文。首个响应中断时不执行工具；工具一旦获得结果就保存调用和结果，随后模型答复失败或中断也保留该记录。工具执行中取消后直接回到提示符。退出程序后这些历史不恢复。

### 试用

从准备好的工作目录启动，配置路径可以是绝对路径。逐轮发送，例如：

1. “调用 write_file，创建 hello.txt，内容为 你好 MewCode。”
2. “读取 hello.txt。”
3. “将 hello.txt 中唯一的 MewCode 替换为工具系统。”
4. “执行 `printf hello | tr a-z A-Z`，告诉我输出。”
5. “列出所有 txt 文件。”
6. “搜索包含 工具系统 的行。”
