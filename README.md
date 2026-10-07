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

`--config` 为必填参数。输入 `/exit` 或在提示符处按 Ctrl+D 结束会话；生成过程中按 Ctrl+C 会取消本轮并返回提示符。会话保留私有存档；普通会话用 `--resume ID|latest` 显式恢复，团队用 `--team <name>`，两者互斥。恢复不重放工具。

## 终端输入与状态

支持增强交互的终端提供可编辑的聊天草稿，输入区附近持续显示 `[PLAN]`／`[DEFAULT]` 与权限模式。输入 `/help` 可查看当前终端的按键说明和完整斜杠命令。

| 操作 | 用法 |
| --- | --- |
| 发送 | Enter 发送完整草稿；只含空白的草稿不请求模型 |
| 换行 | 先按 Esc 再按 Enter；能发送等效按键的终端也可用 Alt+Enter |
| 粘贴 | 支持括号粘贴的终端将整块代码、报错栈及内部和末尾换行插入草稿，不自动发送；检查后按 Enter 一次发送 |
| 重用输入 | 光标在首行时按上方向键、在末行时按下方向键浏览本次运行已提交的普通消息；多行草稿内部优先移动光标。选中后可修改，仍需按 Enter 发送 |
| 命令补全 | 单行命令位置输入 `/` 自动显示名称、别名及描述，继续输入筛选；Tab 仍可显式补全命令和参数。菜单中第一次 Enter 确认候选，第二次 Enter 才提交；Esc 关闭菜单 |

普通问题和提示词命令在提交后回显一次原始输入，形成清楚的对话轮次；不会回显展开后的 Skill SOP。普通消息保留换行、缩进和首尾空白。输入历史仅在当前进程内保存，退出后清除，仅保存已提交普通消息的原始输入（包括 `//` 转义）；不收录命令、自动展开的提示词、授权答案或模型回复。

斜杠命令只在**单行草稿**中生效，名称和别名不区分大小写，可忽略命令外侧空白；参数保留大小写、引号和内部空白。未知命令或非法参数会显示中文错误和用法，不发送给模型，也不改变模式或权限。若单行消息需要以 `/` 开头，用 `//` 转义，例如 `//tmp/example` 发送为 `/tmp/example`。多行草稿中的 `/exit`、`/plan` 等文字均作为普通正文发送。

| 命令 | 类型 | 用途 |
| --- | --- | --- |
| `/help` | 本地 | 查看命令、用法和按键 |
| `/compact` | 状态 | 独立压缩较早历史，最多一次模型摘要请求；用量与近期工作记录分开 |
| `/clear` | 状态 | 仅清屏，保留上下文、输入历史、会话、记忆、模式、权限和统计；纯文本输出提示不可用 |
| `/reset` | 状态 | 清空模型历史、激活 Skill 和上下文估算状态，保留会话 ID、权限、模式、记忆及存档证据 |
| `/skills [list\|active]` | 本地 | 查看有效 Skill、来源、模式、激活状态及普通工具范围 |
| `/skills deactivate <name\|--all>` | 状态 | 停用指定或全部激活 Skill，不清历史、不请求模型 |
| `/agents [list\|show <name>]` | 本地 | 查看有效 Agent 角色、来源、工具限制及固定提示 |
| `/tasks [list\|show <id>]` | 本地 | 查看当前进程子任务及完整结果、证据和逐请求用量 |
| `/tasks cancel <id>` / `/tasks cancel-parent <parent_id>` | 状态 | 取消单子或该父全部子任务；已产生文件和远端操作不承诺回滚 |
| `/team list`、`/team status [name]` | 本地 | 查看保存状态，不请求模型、不创建窗格或任务 |
| `/team create <name>`、`/team resume <name>` | 状态 | 创建或恢复并绑定 Lead，保持空闲等待明确目标 |
| `/team pause`、`/team stop <member_id>` | 状态 | 暂停团队或停止指定成员，保留存档和成果 |
| `/plan [任务]` | 状态 | 进入或重入只读规划；可选任务提交一次 |
| `/do` | 状态 | 仅切回执行模式，随后需输入明确任务 |
| `/session [list]` | 本地 | 查看当前身份或项目存档；恢复用启动参数 `--resume ID\|latest` |
| `/memory` | 本地 | 查看维护状态和项目／用户记忆路径 |
| `/memory list [project\|user]` | 本地 | 默认列出两域合法笔记 |
| `/memory show <project\|user> <id>` | 本地 | 查看完整笔记，包括来源和正文 |
| `/permission` | 状态 | 查看权限；兼容别名 `/permissions` |
| `/permission mode strict\|default\|bypass` | 状态 | 切换权限模式 |
| `/permission revoke session\|permanent` | 状态 | 撤销当前项目对应授权 |
| `/status` | 本地 | 查看当前模式、上下文估算及工作／摘要／记忆／恢复的实际用量 |
| `/review [目标]` | 提示词 | 加载有效 review Skill；内置版本共享对话，默认审查当前 Git 未提交改动 |
| `/commit [目标]`、`/test [目标]` | 提示词 | 内置提交与独立测试 Skill；按定义和当前有效授权执行 |
| `/<Skill 名> [参数]` | 提示词 | 自动发现的能力短命令，无需先激活 |
| `/exit` | 状态 | 受控退出并保留存档 |

三种类型描述执行方式，与权限等级无关。`/review` 沿用当前模式和权限，不自动修复；规划模式不能执行 Git shell 命令，模型应说明审查范围限制。普通消息与展开后的审查任务走同一对话流程，历史和存档保存实际任务。帮助和 Tab 候选由同一注册表生成，隐藏命令及别名不展示；非法定义或名称／别名冲突会在供应商、存档和 MCP 初始化之前终止启动。

`/memory` 仅查看，即使自动维护关闭也可查询已有笔记；不创建目录或锁、不修复权限、不重建索引、不排队维护。`/session` 不支持运行中创建、切换或恢复会话。用户可通过 Markdown Skill 添加能力短命令；控制命令及别名仍为保留名称。

运行时状态区只显示当前模式、真实阶段、耗时和请求次数；等待授权不表示工具已开始。普通成功调用按连续批次汇总，例如“已读取 3 个文件、完成 1 次搜索”；同一文件重复读取同时保留调用次数，无法确认目标时只计调用。失败、拒绝、超时、取消、截断、搜索受限和副作用未知单独显示。任务结束只显示一次停止原因和累计输入／输出摘要，空闲状态不重复旧统计。标题、列表、代码围栏和 diff 有轻量样式，保留原文以便复制。

按 **F2** 查看当前／最近任务的调用与 API 思考，**Tab** 切换，**PgUp/PgDn** 翻页，**Esc/F2** 返回；空闲和运行期间均可打开。浏览保留草稿和光标，Enter 不提交；出现授权时自动进入授权审阅。详情只使用已有内存快照，不请求模型、重放工具或读取引用文件。正文缓存总计 2 MiB，其中思考最多 256 KiB、单条工具结果最多 64 KiB；超限明确标记截断或移出，保留状态和关联。新任务替换上一任务，`/reset` 清除，`/clear` 保留，不新增思考落盘。

`/status` 提供同一份有界详情及完整逐请求、累计 Token／缓存统计和调用 ID；plain 通过它查询详情。缺失统计显示未知，已知部分标为不完整。正常后台记忆进度和用量不打断正文，可用 `/memory`、`/status` 查询；失败、冲突和退出未完成仍提示。手动 `/compact` 保留独立结果及用量。`/help`、`/status` 均是本地查询，不请求模型。

输入或输出不是交互终端，或终端不支持增强布局时，会自动使用纯文本兼容模式（plain）：逐行读取问题并流式输出，不支持多行草稿编辑。例如管道中依次提供两个问题和 `/exit`，会依次处理并退出；管道里的数字不会成为授权答案，需要人工批准但没有可信授权通道的操作会拒绝，搜索会跳过需授权候选。

增强终端空闲时，Ctrl+C 先清空非空草稿（包括纯空白）和菜单；草稿为空才退出。任务、授权或手动摘要期间 Ctrl+C 立即显示“正在停止”，等待实际清理完成后再恢复输入；重复取消不会提前宣布完成。EOF（终端通常为 Ctrl+D）和外部 SIGINT 沿用既有语义，plain 的 Ctrl+C 仍退出空闲会话。授权中 EOF 取消本轮未发送的调用并收尾退出。聊天草稿、运行中提前输入和上次授权残留不会自动成为下一次授权答案或下一轮任务；取消或退出后恢复终端的光标、回显和键盘操作。取消不撤销已经发生的操作。

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
| `max_iterations` | 可选，十进制正整数，缺省 20；主对话、独立 Skill、自动摘要、失败与恢复请求共同消耗 |
| `context_window` | 必填，服务的总上下文窗口（token）；按所选服务实际能力填写，不从模型名推断 |
| `max_output_tokens` | 可选，正整数，默认 8192；工作和摘要请求均以此限制输出 |
| `team_backend` | 可选 `auto`（默认）、`tmux`、`inprocess`；实际选择可查看 |
| `team_max_running` | 可选正整数，默认 4 |
| `team_max_queued` | 可选非负整数，默认 32；0 禁止排队 |
| `team_coordinator_enabled` | 可选严格 `true`／`false`，默认 false；还需真实进程环境开关和 Lead 身份 |
| `skill_models` | 可选 JSON 模型预算映射；独立 Skill 选择其他模型时必须显式配置其窗口 |

`thinking=true` 时，MewCode 会请求 Claude 返回可读的思考摘要，或请求 DeepSeek 的 Anthropic 兼容接口返回思考内容，实际收到的工作思考默认收起，阶段显示“思考中”，可按 F2 切到思考页（plain 使用 `/status`）查看保留的片段。Claude API 不提供原始思维链；自适应思考也可能在简单问题中不返回思考片段。DeepSeek 兼容地址使用 `https://api.deepseek.com/anthropic`，并按其协议设置思考开关。对于 Claude，`thinking=false` 不展示思考，但不能保证关闭某些模型默认且不可关闭的内部思考；对于 DeepSeek，该值会发送关闭思考的参数。

旧配置需要补充 `context_window`，缺失时启动会显示字段迁移错误。必须满足 `context_window > max_output_tokens + 13000`。模板中的 128000 仅是示例，使用前须按实际服务窗口调整；真实密钥配置不会被应用自动改写。兼容服务必须接受输出上限及原生工具协议，拒绝参数时会明确报错，不会静默取消限制重试。

## 长会话上下文

每次工作请求前先进行轻量处理：单个工具结果估算超过 8000 token 时存盘；同一助手批次的结果合计超过 20000 时，优先落盘最大的结果，同值按原调用顺序。对话留下最多约 1000 token 的预览、状态和文件路径。工具采集的 64 KiB 上限保持不变，已经截掉的输出不能通过缓存恢复。

输入估算优先锚定上一次有效工作请求的真实总输入（含缓存命中），只对新增回传内容按字符估算；没有 usage 或历史改变时按完整请求估算。估算不是账单，也不是精确 tokenizer。

当输入估算加输出额度、13000 安全余量达到窗口时，系统用同一模型摘要较早历史。当前任务和近期用户原文不改写；近期区从尾部保留约 10000 token 且至少五条非提醒消息，完整工具调用与结果不拆分。较早用户消息可进入摘要，关键约束须逐字引用并校验来源。摘要固定六部分，草稿和内部思考丢弃，不显示为工作答复；压缩后会提醒模型重新读取文件细节，编辑前核对当前代码。

`/compact` 可主动发起一次摘要，摘要请求的额外余量缩为 3000；输出预算仍单独预留。提交后的工作请求仍须满足 13000 自动余量。手动操作不消耗下一任务的请求次数，不改变计划或权限；Ctrl+C 可取消并保留历史。自动摘要计入本轮 `max_iterations`，实际摘要用量会单独标记；`/status` 显示估算、窗口、落盘数、连续失败与熔断状态，手动用量不覆盖最近工作记录。

连续三次摘要失败熔断自动摘要，普通新消息不会重置；成功的 `/compact` 可恢复。无可压缩历史、保护区过大或服务恢复后仍超限时，以 `context_blocked` 停止，保留已提交记录并返回提示符。服务超限恢复不直接删除旧轮次、不重放工具，也不会在已经展示部分答复后透明重试。

缓存位于项目内 `.mewcode/context/<session-id>/`，目录／文件仅当前用户可访问，使用多行 JSONL，可用 `read_file` 的 `start_line` 和 `max_lines` 分页读取。缓存目录自带忽略规则，不参与任意工作项目的 Git 和普通搜索；超过 15 项历史引用时通过可分页结果索引保留，避免路径撑满摘要。明确路径读取仍遵守现有权限和 deny。正常退出释放句柄并保留存档关联的缓存；超过 30 天未活动的存档在启动时连同登记缓存清理，活动文件和归属不明文件跳过。旧版未关联存档的随机缓存不自动接管或删除。

## Skill：复用操作指令

入口按项目 `.mewcode/skills/`、用户 `~/.mewcode/skills/`、内置三层从高到低覆盖。支持直接子文件 `review.md`，或目录入口 `review/SKILL.md`。名称取自 frontmatter；参考文档不会递归注册。坏文件局部跳过并提示，合法低层项仍可生效；同层有效重名或与控制命令／别名冲突拒绝整体候选。

```markdown
---
name: inspect
description: 按指定目标读取代码并报告证据
allowed-tools: [read_file, glob_files, search_code]
mode: shared
---

目标：{{args}}
先读取项目约定和目标代码，再报告问题、位置及未验证范围。
目标为空时请用户说明范围。只报告证据，不自动修改文件。
```

`name`、非空单行 `description`、`mode` 必填；名称为小写字母开头的字母、数字、`_`、`-`，最长 64 字符。`mode` 为 `shared` 或 `isolated`。独立模式可另设 `history: 0`（默认）、正整数（最近 N 个已提交用户轮次）或 `all`（当前合法历史投影），以及可选 `model`。共享模式不得带 `history` 或 `model`。拒绝重复 YAML 键、未知字段和非法类型；入口为不超过 64 KiB 的 UTF-8 普通文件。

正文支持单遍字面替换 `{{args}}` 和 `{{skill_dir}}`，分别为参数原文和入口的绝对目录；参数中的占位符不会二次解释，不展开环境变量或执行表达式。没有参数时替换为空，默认目标写在 SOP 内。展开超限返回 `skill_content_too_large`，不部分激活或截断指令。

启动只向模型提供名字和一句说明。模型通过系统工具 `load_skill(name, args?, history?, resource?)` 按需加载完整 SOP；用户也可用 `/inspect 目标` 直接加载，不额外请求模型生成提示词。共享 Skill 的结果留在主历史，全文在**每次工作请求**的应用上下文开头重建，精简提醒或摘要后也保留。多项可同时激活，再次加载同名项更新参数。Skill 置顶不改变系统规则、手写项目约定、模式或权限。

`allowed-tools` 是精确工具名列表，省略表示不增加限制，`[]` 表示没有普通工具。所有激活项与当前模式、独立对话的父范围取交集；规划普通工具仍只有三个读类工具。有效定义中的未知工具名在 MCP 发现完成后、接受输入或恢复摘要前导致启动失败。`load_skill` 始终可见，但仍须通过参数、权限规则和人工授权；例如 `load_skill(*)` 的 glob deny 会拒绝加载。普通 allow、bypass 或批准均不能扩大 Skill 交集。

独立 Skill 使用新的对话、SOP 激活和窗口估算，复用当前项目、权限与 MCP；文件副作用真实存在。导入的历史是带角色和来源的只读背景，不复制供应商签名为新对话续接。当前目标始终提供，即使 history=0。子对话继承父普通工具上限，可以继续加载共享 Skill，但不能再启动独立层。最终回答本身作为摘要回流，不额外请求总结模型；失败、取消和实际证据也回流。主子共用 `max_iterations` 和可信取消，子请求用量只计一次；独立任务不套普通工具 30 秒总期限，内部工具仍遵守原期限。

指定其他模型需在当前 `.env` 配置，例如：

```dotenv
skill_models='{"small-model":{"context_window":64000,"max_output_tokens":4096}}'
```

模型 ID 必须是当前服务支持的标识；复用主 `protocol`、`base_url`、`api_key`、`thinking`，仅覆盖模型及预算。窗口必填，输出额度省略时继承主配置，仍须满足窗口大于输出 + 13000。JSON 拒绝重复键、未知字段、布尔预算和非法结构。缺少映射返回 `skill_model_unavailable`，不猜窗口或静默回退；Skill 文件不接受凭据或服务地址。

目录包可携带 `templates/`、`examples/`、`scripts/`、`references/`。`load_skill(name="inspect", resource="references/guide.md")` 只读取该有效包内的有界 UTF-8 文本，不激活、不执行，也不能同时传 args/history。用户包可位于项目外，但普通文件工具的工作根不扩大；拒绝绝对路径、父路径逃逸、链接、特殊文件、二进制和超限资源。单文件 Skill 不开放邻近文件。辅助脚本必须经实际允许且获准的普通工具执行。

下一次非空输入提交时检查热更新，一次发布目录、帮助、补全和激活内容；当前任务使用固定入口快照。合法同名更新按原参数重渲染，删除自动停用并提示；整体候选错误保留上一完整快照。资源文件按实际读取时内容返回。`/clear`、失败、取消、摘要和模式切换均保留激活；用户可用 `/skills deactivate` 停用，或 `/reset` 同时清空历史和激活。

恢复只采用结构化激活记录，并按当前目录重新加载；旧正文不能复活状态。reset 检查点覆盖旧工作投影，但不删除审计和缓存。子运行意图、结果和停止状态写入同一排他日志，子消息不直接进入主投影；缺失主回流时报告证据与可能副作用，不自动重跑。新版本可读取旧存档；不承诺旧版本正确接续含新记录的会话。

内置 `commit`、`review` 为共享模式，`test` 为独立模式、history=2；三者普通白名单均为读取、定位、搜索和命令。它们可按同名覆盖规则替换。此阶段不提供市场分发、安装器或版本管理。

## 六个核心普通工具与系统加载入口

模型可以连续调用工具，也可以一次返回多个调用。连续的 `read_file`、`glob_files`、`search_code` 最多 4 个并发；写文件、改文件和所有 shell 命令逐个执行，并等待前面的批次完成，后面的读取不会越过修改。工具错误回传后，模型可以在同一任务中调整参数；调度器不自动重试工具。

每个任务有独立请求预算，多工具响应只消耗一次模型请求。最后一次请求如果返回工具，整个批次完成并保存结果后停止，不额外请求总结。普通非空答复以 `model_done` 结束；请求上限、用户取消、连续 3 轮全部未知工具或流失败分别报告 `max_iterations`、`cancelled`、`unknown_tool_limit`、`stream_error`；无法安全容纳上下文时报告 `context_blocked`。模型结束并不替代实际验收。

自动摘要和上下文恢复消耗同一任务请求预算；较早完整交互可以摘要，当前任务原文与近期成对结果保留。SDK 隐式重试关闭，摘要失败最多连续尝试三次，并受剩余请求次数约束。

## 规划与执行

- `/plan`：进入持续的只读规划模式，提示输入任务；指令本身不请求模型。
- `/plan <任务>`：立即开始只读探索与规划。普通工具为三个读类工具与 Skill 范围交集，另有 `load_skill`；执行入口仍拒绝写入、编辑和 shell。
- 后续普通消息继续修订计划并保持只读。最终答复包含完整目标、步骤和验证方式，并作为普通历史保留。
- `/do`：只切换到执行模式，不请求模型、不自动执行计划，也不要求存在计划。随后输入如“按刚才的计划修复并运行测试”才启动任务。重复 `/do` 不重置当前请求周期。

这是 `/do` 的行为变更：原来会直接消费计划，现在仅切换模式。历史和模式保存到会话存档；恢复规划会话后同样可以 `/do`，再提交明确任务。会话授权不会随存档恢复。

活动任务中 Ctrl+C 会关闭模型流、终止活动工具及受控子进程，补齐取消结果后返回提示符；已经完成的真实操作仍保留，未启动调用会标明未启动，不声称回滚。空闲时 Ctrl+C 退出。

终端分别显示规划／执行模式、权限模式、请求次数/上限及执行阶段，并用调用 ID 区分接收、等待授权、实际开始和结果。Token 用量使用服务实际统计；缺失字段显示“未知”，累计更新不重复相加。任一请求缺少统计时，累计值只表示已知部分并标为不完整，不能当作完整用量或账单。

## 权限与人工授权

默认使用 `default` 权限模式：没有适用 allow 规则或已有批准的操作先询问，包括只读文件和搜索。这改变了旧版默认直接执行工具的行为。六个内置工具名称与模型参数保持不变；MCP 工具也经过相同的权限判定。权限判定在父进程完成，未批准的目标操作不启动，等待授权不消耗工具执行超时。普通权限拒绝作为工具结果交给模型，模型可以继续调整本轮任务；Ctrl+C 则取消整轮。

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

“需授权”先检查已有会话／永久批准，未命中才询问。任何模式与批准都不能解除内置危险命令黑名单、文件／搜索路径越界限制或明确 deny。权限模式独立于规划／执行：`/plan` 始终禁止修改和 shell，即使使用 bypass；`/do` 保留权限状态，只切换任务模式，后续调用仍独立判断权限。

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

`agent` 与五个团队工具 `team`、`team_member`、`team_task`、`team_message`、`team_integrate` 使用完整参数 JSON。`exact` 的 JSON 对象会规范化键顺序和空白，正文、任务／领取标识等任何参数变化都须重新匹配；`glob` 匹配键排序、无多余空白的完整 JSON 字符串，`*` 可跨正文中的 `/`。例如为后台成员配置读消息、发送计划／正文和开工许可：

```yaml
version: 1
rules:
  - effect: allow
    rule: 'team_message({"action":"read"})'
    match: exact
  - effect: allow
    rule: 'team_message({"action":"send",*})'
    match: glob
  - effect: allow
    rule: 'team_task({"action":"start",*})'
    match: glob
```

按动作放行仍覆盖该动作的所有参数；需要更窄范围时，使用授权详情里的完整实际参数写 `exact` 规则。会话／永久批准也绑定工具名、实际项目根和完整规范参数，不能跨参数复用。没有规则仍为 ask，成员没有交互批准通道时返回 `approval_required`；明确 deny 优先。上述许可不能扩大角色、规划模式、任务领取或计划门禁，也不批准实际文件、命令、MCP 和受管 Git 操作。成员工作根和父项目的当前策略都须允许，成员专用项目规则应在实际 Worktree 根配置。

搜索先枚举项目内可见候选路径，再逐文件授权，随后仅把获准的显式文件列表交给搜索。deny 或被拒绝的文件会跳过，文件内容不会先读再过滤。结果中的 `permission_limited` 与 `skipped_files` 说明搜索范围受限；它独立于输出 `truncated`。所有候选被排除仍返回受限的成功空结果，不能据此断言完整项目没有匹配；没有候选则是普通空结果。受拒文件名和内容不会进入模型结果。

### 审批范围与管理

终端每次只展示一个授权请求，关联请求 ID 和工具调用。首屏显示操作摘要、原因、工作根目录、适用的目标数量及批准范围；审批可查看完整参数、真实目标、写入内容或当前提供的编辑原文／新文差异，预览不会额外读取未授权文件。

| 详情输入 | 内容 |
| --- | --- |
| `targets` | 全部真实文件目标、完整精确 shell 命令，或 MCP 外部工具身份与范围 |
| `arguments` | 本次调用的完整有效参数 |
| `content` | 完整写入内容或当前调用提供的编辑差异 |
| `summary` | 返回操作摘要与决定入口 |
| `next` / `back` | 向后／向前翻页 |
| `all` | 查看全部详情 |
| `results`（仅增强终端） | 查看本次授权等待期间其他工具的全部结果，支持分页 |

长内容按终端大小分页，缩小窗口后仍可完整浏览。查看详情、翻页或返回摘要都不表示批准。另一工具在等待期间完成时，其状态与当前授权请求分别显示；异常摘要优先保留，后续成功不会覆盖异常，`results` 可在决定前查看全部旁路结果。plain 模式在当前决定后输出这些结果。审阅后明确选择：

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

永久保存失败只按本次批准执行，并明确提示未记住。空闲提示符可输入 `/permissions` 查看当前模式、项目及授权记录，`/permissions mode strict|default|bypass` 切换模式，`/permissions revoke session` 或 `/permissions revoke permanent` 清除本项目对应批准。控制命令不请求模型、不改变对话历史或任务模式，不删除规则；撤销失败不会报告成功。

非法审批选项保持未批准并重新询问。授权阶段不使用聊天历史或命令补全；增强终端中的括号粘贴不会提交批准，也不能用一块粘贴连续批准多个请求，须在当前请求内重新明确选择。Ctrl+C 清理当前任务后返回提示符并丢弃未提交的授权输入；授权中遇到 EOF 则取消、清理后退出。非交互输入没有自动批准通道：需 ask 的单文件／shell 操作返回未启动的 `permission_denied`，搜索跳过需授权候选。程序调用可显式注入可信审批回调。

### shell 检查的边界

内置 regex 黑名单覆盖已知的根目录／主目录递归删除、磁盘格式化或擦除、原始设备写入、fork bomb 形式，不能通过 YAML 放开。黑名单先检查原文，再对 AST 中可见的静态命令复查；可能保守误拦引用文本，不保证发现编码或间接执行的危险行为。

shell glob allow 只适用于单个简单静态命令，无重定向、连接、子 shell 或展开。因此 `Bash(git *)` 可放行 `git status`，不能据此放行 `git status && printf done`；复杂操作需完整 exact allow 或独立批准，且仍服从子命令 ask／deny。支持的管道、连接、重定向、子 shell、命令替换会检查可见子命令，任一 deny 拒绝整条，前缀也不会提前执行。不能完整分析的语法（例如 heredoc、控制结构、复杂参数或算术展开）在执行前返回 `permission_check_failed`。

这些检查不构成操作系统文件系统沙箱。获准 shell 仍具有当前用户权限，可以访问项目外、网络，或间接执行脚本；解析器不执行展开来求值，也不审查脚本内部。专用文件写入／编辑会保护三份权限 YAML 及链接别名，但任意已获准 shell 仍可能间接改写配置。文件路径 resolve 检查也不承诺对抗恶意并发替换目录祖先或硬链接。

## 系统提示与缓存

固定系统提示按身份、系统约束、任务模式、动作执行、工具使用、语气风格、文本输出七个模块拼装，模块之间空一行。环境与当前模式放在消息中，保持固定提示和同模式工具定义稳定，供服务复用公共前缀。

每次工作请求都会附加 `<mewcode-context>` 提醒。本地将它与真实用户消息区分，线上使用普通消息内容；标签不提供原生系统权限，也不会在终端生成额外的 `你>` 输入。当前激活 Skill 全文及名称说明索引置于提醒开头，每轮重建；完整轮次再补充实际工作根、系统、Python 版本、启动日期、手写指令和长期记忆，随后表达当前模式及工具范围。未激活 SOP 不注入。

提醒周期仅按实际工作模型请求计数，包含失败与恢复工作请求，专用摘要不推进周期：第 1、6、11…次完整，其余精简；普通新任务延续周期，显式 `/plan` 和实际 plan→execute 的 `/do` 重置周期，执行模式下重复 `/do` 保持周期。摘要后强制完整补充，未成功提交的完整提醒会继续补发。已提交的提醒原样保留，随关联的完整交互一起摘要。

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

所有参数都先通过各工具的 JSON Schema 校验；内置工具不接受额外字段。工具统一返回 `ok`、`data`、`error`（含 `code`、`message`、`details`）和 `truncated`。

### 工作目录与限制

- 启动 MewCode 时的当前目录固定为工作根目录，与配置文件所在位置无关。三个文件工具和两个搜索工具限制在这个目录内，解析真实路径后拒绝越界和指向外部的链接。文件工具只操作普通 UTF-8 文本，跳过或拒绝不支持的编码与特殊文件。
- 两个搜索工具跳过隐藏、忽略文件及符号链接，内容搜索跳过二进制内容；无匹配是成功的空结果。相对路径及匹配行保持有序，路径 glob 不覆盖 rg 的默认忽略规则。
- shell 以工作根目录启动，具有当前操作系统用户的权限，**不是目录沙箱**，可以访问目录外或网络。每次命令使用独立 shell，`cd`、变量和环境变化不会延续到下一次调用；stdin 关闭，不支持交互程序。
- 所有工具默认 30 秒超时，只有命令允许 1–120 秒覆盖。本地工具超时或 Ctrl+C 会终止工作进程及同组子进程；主动脱离进程组的后台程序不在这一清理保证内。
- 结果内容上限 64 KiB；命令 stdout、stderr 各最多 32 KiB。文件创建内容、编辑前后文件各不超过 1 MiB，工具参数流不超过 2 MiB。超出的输出会标明截断，输入超限则报错。
- 创建文件以原子方式拒绝覆盖，编辑以原子替换提交并检查并发变化；不能保证对抗另一个恶意进程同时替换目录祖先。超时、取消和外部命令可能已产生副作用，继续前应先核查状态。

终端显示 `工具>` 操作摘要和成功、失败、超时、取消、截断状态，摘要不会完整打印写入正文。只有完整响应才执行工具；全批调用与按原始顺序排列的结果在下一次模型请求前成对保存。后续模型答复失败或中断仍保留已提交记录。退出后可使用 `--resume` 显式恢复。

### 试用

从准备好的工作目录启动，配置路径可以是绝对路径。例如发送：“搜索 add 函数，读取实现，修复减法错误，然后执行 Python 断言检查。”

先规划再执行的例子：输入 `/plan 检查 add 函数并提出修复计划`，再输入普通消息修订验证步骤，然后输入 `/do` 切换模式，最后输入“按刚才的计划修复并运行测试”。


## 项目指令、会话存档和自动记忆

普通启动创建新会话，并在首次请求前加载当前指令与记忆。工作根目录始终是启动目录的真实路径。显式恢复和列表命令为：

```bash
uv run mewcode --config .env --list-sessions
uv run mewcode --config .env --resume latest
uv run mewcode --config .env --resume 20261004-123456-abcd
```

`--resume` 与 `--list-sessions` 互斥，`--config` 仍必填。列表只扫描当前项目的 JSONL，不启动模型或 MCP；标题、累计消息数、最后活动和状态由存档推导。恢复不执行旧工具，也不自动继续旧任务。另一进程正在使用的会话不可恢复。

手写指令依次加载 `MEWCODE.md`、`.mewcode/MEWCODE.md`、`~/.mewcode/MEWCODE.md`，项目根的优先级最高。独立行 `@include docs/规范.md` 可以引用相对文件，路径可包含空格，代码围栏中的例子不展开。项目引用必须位于真实项目根内，用户引用必须位于用户 `.mewcode` 内；深度最多 5 层，环路及重复引用跳过，三层合计最多读取 256,000 字节。缺失入口不自动生成模板，引用错误会提示并继续其他文件。指令和笔记不能扩大工具权限或绕过 deny。

存档位于 `.mewcode/sessions/<YYYYMMDD-HHMMSS-xxxx>.jsonl`。日志追加保存完整消息、工具调用与实际结果，以及上下文检查点，不另存 meta 文件。恢复跳过坏行，工具批次缺结果时退回前一个完整交互；未确认操作可能已有副作用，需要核查实际状态。协议或必要供应商续接块不兼容时拒绝恢复；缓存缺失时受控停止。超过 24 小时的跨度会注入时间提醒；当前模型窗口不足时最多尝试一次独立恢复摘要。模式、摘要失败计数会恢复，会话授权、终端输入历史、待执行计划和旧用量估算锚点不会恢复。

每次自然结束的工作任务会异步请求一次记忆更新，分为用户偏好、纠正反馈、项目知识和参考资料。用户级只保存用户明确表达的通用偏好，其他内容保存在当前项目。单条笔记带 frontmatter，项目笔记位于 `.mewcode/memory/`，用户笔记位于 `~/.mewcode/memory/`。`MEMORY.md` 是可重建的事实摘要索引；单份索引和合并注入均不超过 200 行、25 KB，超出的完整笔记仍保留。语义去重由模型判断，不能保证所有重复都立即消失。

记忆维护与工作请求并行，工具列表为空，独立显示真实用量，不占工作轮次或覆盖最近任务统计。每个工作请求会检查最新索引；更新冲突保留现有笔记并提示。退出先等待后台任务最多 2 秒，再取消并关闭流、释放存档和关闭客户端。取消、失败、手动摘要及恢复操作不触发新的记忆提取；崩溃前未完成的后台更新可能丢失，不会重放。

存档、缓存和自动笔记使用私有权限、自带 Git 忽略规则，普通搜索排除这些内部目录。启动清理严格超过 30 天未活动的会话，仅删除登记且归属明确的缓存，活动或损坏存档跳过。手写指令可纳入版本控制。本阶段未实现向量数据库、RAG 或团队记忆同步。

## 外部 MCP 工具

MewCode 使用官方 Python SDK `mcp==2.3.0`，在首次提示符之前自动连接配置的 Server 并发现全部工具。支持本地 stdio、远程 Streamable HTTP，自动兼容 `2025-11-25` 初始化握手和 `2026-07-28` 能力发现，无需手动选择版本。当前不接入 MCP resources、prompts、sampling、elicitation 或 task-only 工具。

配置依次读取 `~/.mewcode/mcp.yaml` 和启动工作目录下的 `.mewcode/mcp.yaml`。后者按 Server 名**整项覆盖**前者，env、headers 不逐键继承。缺省或空文件表示没有配置；YAML 重复键、未知顶层字段或错误版本使本轮全部 MCP 配置不可用，单个 Server 字段错误只跳过该项。内置工具与聊天仍可使用。

```yaml
version: 1
mcpServers:
  local_demo:
    transport: stdio
    command: /absolute/path/to/python
    args: [/absolute/path/to/server.py]
    env:
      SERVICE_TOKEN: ${SERVICE_TOKEN}
  remote_demo:
    transport: http
    url: https://mcp.example.com/mcp
    headers:
      Authorization: Bearer ${SERVICE_TOKEN}
```

只展开 env、headers 的值中的 `${VAR}`，来源是 MewCode 启动时的进程环境，不是 `--config` 指定的模型 `.env`。缺少变量或使用 `${VAR:-default}` 等表达式会跳过该 Server；空值允许、替换结果不递归展开。command、args、url 保持字面值。HTTP URL 不允许 userinfo。配置只在启动读取，修改后需重启。

stdio 直接执行 command 和 args，不经过 shell，cwd 固定为真实工作根目录。MewCode 固定绝对启动入口和 SDK 基础环境与显式 env 合成的环境，保留 venv 等符号链接入口的启动语义，真实目标另纳入指纹；内部启动器在同一进程中 exec 目标程序，并记录进程组供退出清理。声明本地 Server 就意味着启动时会执行该程序，工具的逐次审批发生在连接和发现之后。stderr 丢弃，不与协议 stdout 混用。

每个 Server 的准备并行上限为 4，连接、协商及全部工具分页共用 15 秒期限。失败只影响所属 Server；已就绪连接跨对话、历史裁剪及模式切换复用。只在启动发现工具，不做健康检查、自动重连、进程重启或工具自动重发。已注册 Server 失效时，工具名称保留，调用返回 `mcp_unavailable`。

外部工具别名为 `mcp__<Server片段>__<工具片段>__<16位摘要>`，最多 64 个 ASCII 字符；摘要来自原始 Server／工具名，不随连接地址变化。实际 RPC 始终使用原始工具名。外部工具默认非只读，按串行边界调度，即使声明 readOnlyHint 也不能进入 `/plan`；`/do` 恢复完整工具集，但不会自动批准调用。

MCP 使用同一 strict/default/bypass 矩阵和 deny > ask > allow 优先级。规则的匹配对象是**完整规范 JSON 参数**：对象键递归排序、紧凑序列化、保留 Unicode、数组顺序与值类型。exact 规则中的 JSON 会规范化；glob 按整个规范字符串匹配，不把远端 path/command 当作本地文件路径或 Bash。规则中的工具名必须使用启动时显示的完整稳定别名；离线合法规则仍能加载，但不能据此注册工具。

人工授权显示 Server、原名、别名、安全连接描述、完整参数和精确范围，长参数可以翻页或 all 查看。HTTP 连接只显示协议及主机／端口；stdio 显示启动入口，省略 args/env。会话／永久批准绑定真实项目根、Server 名、有效连接配置指纹、原始工具名和相同参数；改变端点、实际程序路径、args、有效环境或 headers 会使旧批准失效。指纹不是程序内容完整性校验或远端身份认证。永久批准会保存完整调用参数，但不保存连接 env/header 明文；使用既有 `permissions.local.yaml` 和 `/permissions revoke` 管理。

MCP 单次调用上限为 30 秒，审批等待不计入，远端参数中的 timeout_seconds 不改变该上限。HTTP 可以使用 GET 恢复原响应流，但恢复受原期限和取消控制；新旧协议取消后均停止该请求的恢复，不关闭共享连接。不自动重发 tools/call，也不自动处理 HeaderMismatch 或输入续调要求。取消只表示本地等待结束，远端可能仍在执行或已有副作用。

工具结果保留文本及 structuredContent，资源链接仅返回元信息，不抓取 URI。图像、音频、二进制内容标为未支持，不将 base64 当正文。全部远端结果内容共享 64 KiB UTF-8 预算，优先保留能完整容纳的结构化数据，其余按顺序截短或省略并说明。失败区分 `mcp_tool_error`、`mcp_request_error`、`mcp_protocol_error`、`mcp_unsupported_capability`、`mcp_unavailable` 和本地 timeout/cancelled。

退出、EOF 或启动取消时分别清理每个 Server：总预算 5 秒，前 3 秒正常关闭，余下时间强制清理。无法确认回收时会明确报告失败；单个清理任务不能无限阻塞其他 Server 或模型客户端退出。HTTP 仅关闭客户端资源并按协议尝试结束会话，不停止远端服务或撤销已发生的操作。

授权首页先展示实际读取目标、完整命令、写入内容或编辑 diff，操作范围与内容来自同一请求快照。决定栏仅一处：回车／1 拒绝、2 本次、3 会话、4 永久。用 `targets`、`arguments`、`content`、`scope`、`summary`、`results` 查看不同部分，PgUp/PgDn、↑/↓ 或 next/back 翻页；浏览不会批准。


## 生命周期 Hook

在启动项目的 `.mewcode/hooks.yaml` 声明固定动作。配置在启动时加载一次；修改后需重启。缺少文件表示关闭此功能，任一配置错误使整份文件停用并记录诊断，Agent 继续运行。

```yaml
version: 1
hooks:
  - event: message.before_request
    once: true
    action:
      type: prompt
      text: 修改前先读取文件，完成后运行相关检查。
  - event: tool.before
    if:
      all:
        - field: tool.name
          match: exact
          value: write_file
        - field: tool.target_path
          match: glob
          value: protected/**
    action:
      type: command
      command: python3 .mewcode/check_write.py
      timeout_seconds: 5
  - event: tool.after
    if:
      all:
        - field: tool.name
          match: exact
          value: edit_file
        - field: tool.result.ok
          match: exact
          value: true
    action:
      type: command
      command: python3 .mewcode/format_changed.py
  - event: turn.end
    async: true
    action:
      type: http
      url: https://hooks.example.com/events
      method: POST
      headers:
        X-Project: demo
```

`event` 和 `action` 必填；省略 `if` 无条件触发。条件只接受非空的一层 `all`（全部满足）或 `any`（任一满足），不能嵌套或混用。原子条件使用 `field`、`match`、`value`，可加 `negate: true`：exact 按完整 JSON 值及类型比较，glob 区分大小写，regex 匹配整个字符串。三个本地文件工具的 `tool.arguments.path` 和真实目标 `tool.target_path` 使用权限规则的路径片段 glob（`*` 不跨目录、`**` 可跨目录）；其他字符串 glob 不按路径分段。不存在或类型不适用的字段即使反向也不匹配。条件不能访问列表下标。

| 层级 | 事件 | 事件专有字段 |
| --- | --- | --- |
| 会话 | `session.start` / `session.end` | `source`（new/resume）/ `reason` |
| 轮次 | `turn.start` / `turn.end` | `message.text` / `reason` |
| 消息 | `message.user` / `message.before_request` | `message.text`、`message.role` |
| 消息 | `message.after_response` | 上述字段及 `message.tool_calls`；只观察完整工作响应 |
| 工具 | `tool.before` / `tool.after` | `tool.call_id`、`tool.name`、有效 `tool.arguments`、可用的 `tool.target_path`；after 增加实际 `tool.result` |
| 系统 | `mode.changed` | `mode_change.from`、`mode_change.to` |
| 系统 | `context.before_compact` / `context.after_compact` | `context.purpose`（auto/manual/restore）、`estimated_tokens`、`threshold`；after 增加 `result`、`reason`（success/failed/cancelled/noop） |

所有事件包含 `event`、UTC `time`、本次运行的 `session_id`、`mode`、`permission_mode`。可用时还含存档 `archive_session_id`、顶层 `turn_id`、`run_id`、`parent_run_id`、工作意图 `request_id` 和 `budget.used/max_iterations`。字段缺失表示当前事件没有该信息。非法工具参数不作为已验证参数提供；真实目标是经过边界检查的项目相对路径，区别于调用中的原始路径。结果沿用工具格式，失败详情位于 `tool.result.error.details`。快照独立且不可变，动作不能修改工具参数。

| 动作 type | 字段与执行约束 |
| --- | --- |
| `command` | 必填非空 `command`；`timeout_seconds` 默认 30，整数 1–120。固定 `/bin/sh -c`、真实项目 cwd，事件 JSON 从 stdin 传入，不做 shell 插值。stdout/stderr 各 32 KiB；超时及取消回收本地进程组 |
| `prompt` | 必填非空 `text`，同步加入下一次实际工作请求的上下文，参与预算和压缩后复查；不改固定系统提示或权限 |
| `http` | 必填固定绝对 HTTP(S) `url`，不允许 userinfo 或 fragment；可选 `method`（GET/HEAD/POST/PUT/PATCH/DELETE/OPTIONS，默认 POST）、字符串 `headers`。JSON 请求体为事件快照；30 秒总期限，响应 64 KiB，不重发或跟随重定向，不复制模型凭据 |
| `subagent` | 必填非空 `agent` 和 `prompt`；本阶段只记录未实现诊断，不启动 Skill 或模型 |

只有 `tool.before` 能拒绝实际工具。它在注册、参数、范围及权限检查通过后运行；command 成功退出或 HTTP 2xx 后，整个响应必须为 JSON 对象，例如 `{"decision":"deny","reason":"受保护文件，请选择替代目标"}`。也可返回 `{"decision":"allow"}` 或 `{}` 放行。非零退出、超时、无效／截断 JSON 仅记失败日志并放行，不构成拒绝。拒绝生成原调用 ID 的 `hook_denied` 工具结果，`error.details.not_started=true`，原因反馈给模型以调整操作。后置动作不修改已完成结果。Hook 动作不会递归触发工具 Hook。

例如 `.mewcode/check_write.py` 可读取快照并返回决策：

```python
import json
import sys

event = json.load(sys.stdin)
path = event.get("tool", {}).get("target_path", "")
if path.startswith("protected/"):
    print(json.dumps({"decision": "deny", "reason": "受保护目录，请选择其他目标"}))
else:
    print(json.dumps({"decision": "allow"}))
```

规则按声明顺序派发，拒绝短路本次前置检查。`once: true` 在本次会话首次实际提交动作时原子领取，失败也算；条件不匹配、规划跳过、后台等待授权或队列溢出不消耗。`/reset` 保留标记，重启或恢复进程重新开始，不持久化。`async: true` 使用独立取消归属的后台队列，最多 4 个运行、32 个待执行；溢出跳过并记日志。`tool.before` 和 prompt 禁止异步。同步 command/http 工具 Hook 会保守串行整个工具批次，确保格式化完成后才开始下一次读取。

前台命令复用唯一授权界面，展示 Hook 规则位置和事件，审批等待不计入执行超时；批准后恢复所属启动、控制指令或任务阶段。控制指令等待期间关闭输入，直到动作和取消清理结束才恢复空闲，不重置最近任务。后台命令只用已有有效许可，不弹授权。命令保留明确 deny、shell 黑名单和现有授权范围。人工选择会话／永久批准仍绑定完整精确命令，相同命令可以复用；Hook 放行不顺带批准目标工具。规划模式跳过 command/http，prompt 仍可注入；切入规划先清理后台副作用。取消期间持续显示“正在停止”直到收尾完成，关闭先停止后台再释放客户端和会话句柄。HTTP 取消只结束本地等待，不承诺远端回滚。

注入在工作请求开始后消费，失败请求也算；发送前取消、预览、摘要、恢复和记忆维护不消费。独立 Skill 与新子 Agent 共享 Hook 派发和会话 once，提示队列按父任务及子身份隔离；同父多次接续保留自己的队列，并携带父子关联，不重复会话和顶层用户任务边界。

Hook 自身失败通过 `mewcode.hooks` 日志记录安全类型、来源及事件，不记录完整 stdin、headers 或原始输出，也不中断 Agent。`hook_denied` 沿原工具异常通道即时展示；Hook 信息使用独立终端路由，不计入普通成功工具聚合、模型 usage、最近任务详情或记忆维护。后台通知不改变草稿、光标和当前授权。当前不提供自动成功通知或 Hook 专用看板；纯文本终端使用相同安全输出。Hook 的 subagent 动作仍是占位，once 持久化及显式优先级留待后续章节；模型委派使用下述 agent 工具。

## 子 Agent 委派

主模型通过稳定的 `agent` 工具委派任务：`type=defined` 从空历史按指定 `role` 启动；`type=fork` 复制触发委派的父模型已发送请求，追加明确子目标，强制后台运行。Fork 保留原系统、工具顺序、消息与签名，便于服务复用提示缓存；实际节省以服务返回的缓存计数为准，未知计数显示未知。

角色是带 YAML frontmatter 的 Markdown 文件，例如 `.mewcode/agents/reviewer.md`：

```markdown
---
name: reviewer
description: 读取相关代码并报告问题和证据
allowed-tools: [read_file, glob_files, search_code]
disallowed-tools: [load_skill]
model: inherit
max-iterations: 20
permission-mode: inherit
---
你负责审查收到的子任务。读取实际代码，引用路径和具体证据，区分已确认问题与推断；最后简要报告结论和未验证事项。
```

`name`、单行 `description` 和非空正文必填。正文是该子运行始终保留的系统提示。`allowed-tools` 省略时不再限制父范围，空列表不允许任何工具；`disallowed-tools` 优先于白名单，也限制系统入口。模型可选 `inherit`、`haiku`、`sonnet`、`opus`；最大轮次缺省 20，必须是正整数。权限模式可选 `inherit`、`strict`、`default`、`bypass`，子只能相对于父收紧。文件限制 64 KiB，只加载目录直属普通 `.md` 文件，禁止链接、重复 YAML 键和未知字段。

有效定义按项目 `.mewcode/agents/` > 用户 `~/.mewcode/agents/` > 内置 > 插件目录的优先级整份覆盖。同层有效重名或有效定义引用未知工具会拒绝候选目录；坏文件跳过并提示，不遮蔽合法低层定义。非空输入边界刷新目录，已接受或排队任务保留注册时快照。内置 `explore` 只读探索，`general` 受父有效范围限制。主请求只包含名称说明目录，不展开未选择角色正文。

配置文件可增加三个 JSON 配置（均可省略）：

```dotenv
agent_models='{"haiku":{"model":"your-small-model","context_window":64000,"max_output_tokens":4096}}'
agent_plugin_dirs='["/absolute/path/to/plugin/agents"]'
agent_background_tools='["read_file","write_file","edit_file","execute_command","glob_files","search_code","load_skill"]'
```

模型别名必须显式配置实际模型及窗口；窗口大于输出额度加 13000，不猜测别名或回退。模型仍使用同一服务及传输客户端。插件目录为本地来源。后台工具名单必须使用精确已注册名：省略时使用上面的七项，空列表禁止所有工具；MCP 只有显式加入名单才可后台执行。实际范围还受父注册上限、当前 plan、角色限制及子 Skill 激活限制约束。所有子运行（包括独立 Skill）禁止再调用 `agent`；新子也禁止启动独立 Skill。

定义式参数示例：`{"type":"defined","role":"explore","prompt":"读取 README.md 并概括启动步骤","background":true}`。Fork 参数示例：`{"type":"fork","prompt":"分析当前问题的另一种解释"}`；Fork 不接受 `role` 或模型覆盖，显式 `background=false` 会被拒绝。

定义式默认前台等待，超过 30 秒自动转后台；增强终端等待前台子任务时 Ctrl+B 可手动转换。转换保留同一任务和已启动操作，不重启或重放。其他阶段及纯文本终端不能用 Ctrl+B 切换。后台返回带 `task_id` 和 `parent_task_id` 的接收凭据；终态在下一工作请求边界合并，主 Agent 空闲时自动接续原父剩余预算。已提交输入和控制命令优先，未提交草稿在接续结束后恢复；其他父的结果保留到其接续。父预算耗尽、取消或不可恢复失败后，只保留本地结果，不获得新预算。

子运行无交互审批。事先准备对应路径、命令及 MCP 的项目规则、永久批准或会话批准；同工作根注册时复制会话批准，不复制“本次”批准；隔离子目录使用独立根身份，不重绑父文件、命令或 MCP 批准。批准委派本身不授予子文件或命令权限。缺许可的单目标返回 `approval_required`，搜索会过滤无许可候选并报告跳过数量，模型可据此继续或报告限制。明确拒绝、真实路径校验和命令黑名单仍生效。

消息、权限追踪、结果缓存、取消和请求用量归子运行所有；LLM 传输、Hook 引擎和 MCP 连接共享；工作目录缺省共享，声明 Worktree 的角色使用独立工作副本。子正文、思考和逐步工具输出不混入主回复；最终正文和证据有来源，通知正文默认限制 64 KiB，完整结果用 `/tasks show` 查看，引用缓存保留到会话关闭。`completed` 只表示模型自然结束，结果中报告测试失败仍是测试失败。

父自然结束执行段保留后台任务；主动取消父会禁止接续并清理其活动和排队子任务。`/plan` 在发布只读模式前清理 execute 子任务；`/do` 仅切模式。`/reset` 清理旧代任务后清空对话，保留权限、Hook once 及本进程终态记录；`/clear` 仅清屏。退出先清理子运行和 Hook，再关闭共享服务。任务状态仅保存在当前进程，恢复含凭据的存档不会重建队列或重放执行。Worktree 隔离适用于显式声明的定义式角色；未声明角色和 Fork 沿用共享工作目录。团队编排与跨会话后台恢复不在本功能范围内。

`/agents [list|show <name>]` 查看角色；`/tasks [list|show <id>|cancel <id>|cancel-parent <parent_id>]` 查看或取消任务。`show` 支持父身份，分别显示主累计、各子逐请求帐本和父子总量，不把子累计重复相加；缺失统计保持未知。主累计包含原合同下共用预算的独立 Skill。父最终结束另有状态行，执行段结束不表示所有后台子已结束。所有查看操作在本地完成，不请求模型。


## 子 Agent Worktree 隔离

在项目、用户或插件角色的 frontmatter 添加 `isolation: worktree`，例如：

```markdown
---
name: implementer
description: 在独立工作副本修改并验证任务
isolation: worktree
allowed-tools: [read_file, edit_file, write_file, execute_command, glob_files, search_code]
permission-mode: inherit
---
读取实际内容，按任务修改并验证。报告成果路径、测试结果和未完成事项。
```

只接受 `worktree`；省略沿用原行为，Fork 不自动隔离。接受任务时冻结父 HEAD、角色和初始化配置，实际启动才创建 `.mewcode/worktrees/<任务身份>`、独立分支和私有 `.mewcode/worktree-state/` 记录。子目录**从冻结的 HEAD 创建，不包含父目录未提交修改**；排队期间父的新提交也不会改变基线。仓库必须有提交；第一版拒绝裸仓库、子模块及包含子模块的布局。项目位于仓库子目录时，子实际工作根保留相同相对位置。

内部目录名允许 ASCII 字母、数字、下划线、连字符和 `/` 嵌套，总长最多 128、单段 64、最多 4 层；拒绝绝对路径、空段、`.`／`..` 和链接逃逸。完整已有目录的恢复仅读取文件系统，不执行 Git 或重复初始化；未知、损坏和半初始化目录不会被自动接管。

可在原实际项目根 `.mewcode/worktrees.yaml` 配置环境初始化。缺失时采用下列默认值；提供字段会替换相应清单，空列表可禁用复制或软链：

```yaml
version: 1
copy-files:
  - {path: .env, required: false}
link-directories:
  - {path: .venv, required: false}
  - {path: node_modules, required: false}
copy-ignored-files: []
# 可选：仅枚举 Git 已忽略的普通文件，例如
# copy-ignored-files:
#   - {pattern: local-config/*.json, required: true}
# hooks-path: .githooks   # 相对新工作树顶层的专属 Git hooks 目录
cleanup:
  ttl-days: 30
  interval-seconds: 1800
```

`copy-files`／`link-directories` 使用项目相对 `path`，忽略文件清单使用 `pattern`；`required` 缺省 false。禁止未知或重复键、非法类型、路径穿越和任意全目录复制。普通文件复制每项最多 1 MiB、合计 16 MiB、枚举最多 512 项；不覆盖冻结提交已有文件，不复制普通未提交源码，也不默认搬迁会话、缓存、项目记忆或本地批准。仓库外的启动配置不会自动复制。软链保持共享依赖目录的真实边界。可选 `regenerable-paths` 是有界相对规则列表，缺省仅分类 `.mewcode/context/**`、`.pytest_cache/**` 和 `**/__pycache__/*.pyc`；自定义规则须限定目录和文件名，例如 `outputs/*.log`；拒绝 `**/*`、`outputs/*` 等可能吞掉任意成果的规则。初始化摘要或链接变化及证据引用仍优先保护。周期必须是正整数，布尔值无效。专属 hooks 使用 `extensions.worktreeConfig` 与 `git config --worktree`，不能安全迁移已有 Git 配置时明确失败。

文件、搜索和命令工具均显式使用子 cwd，提示持续包含目录、分支和冻结提交。项目指令、Skill 和项目记忆按子根加载，用户域共享；不新增对子任务的记忆自动提取。子权限按实际根判定，父当前规则作为上限，子规则只能收紧；父 `ask`／`deny` 不被子 `allow` 覆盖，父热更新拒绝继续生效。缺少子根适用许可时非交互返回 `approval_required`。批准委派本身不授予子普通工具权限。

此机制隔离 Git 工作副本。shell 可以访问目录外；依赖软链、Git 公共版本库、LLM 传输及 MCP 服务保持既有共享合同，MCP stdio 启动 cwd 不会伪装成子目录。模型工具不能编辑受管状态或权限配置。功能不提供自动合并、跨目录同步或新的并行编排接口；上层可根据成果用 Git 决定整合方式。

子工具和异步 Hook 实际收尾后才释放运行租约；结果缓存先归档到父拥有的私有缓存，登记来源和映射后再评估删除。任务结果及 `/tasks show` 分别报告模型结束原因与 Worktree 的目录、分支、基线、初始化、归档、保留／删除状态。归档失败会持久保护原目录和证据，关闭句柄不会消除保护；恢复存档只展示证据，不重跑或接管子任务。

无文件成果、无提交成果且证据已安全归档时可清理。暂存／未暂存修改、未追踪或未知忽略文件、初始化配置内容／权限位／链接改变均保留。只要 HEAD 不等于冻结基线，就保留提交，包括已推送提交。目录和分支分别删除并报告部分失败。会话启动及每 1800 秒有界扫描准确受管记录，依次检查归属、严格超过 30 天且无真实活动租约／证据保护、无成果；规划期间暂停，关闭时停止并等待本地收尾。人工目录、旧格式或不明状态保持原样，不执行全局 prune 或强制删除。


## 持久 Agent Teams

团队适用于同一 Git 仓库的长期协作，与上文一次性 `agent` 子任务分别管理。需要已有 Git 提交；团队登记仓库身份及稳定成员，每个新目标冻结当时目标分支和提交，旧目标、任务、消息与成果保留。同名创建或错误仓库恢复会拒绝，不能用路径创建团队。

```text
/team create demo
/team status demo
/team pause
/team resume demo
/team stop <member_id>
```

重启可使用 `uv run mewcode --config .env --team demo`，与 `--resume` 互斥。普通启动不会恢复团队。创建／恢复本身不请求模型、不创建成员窗格，保持空闲；明确目标或指派后已有成员按稳定身份和磁盘上下文懒启动。查看不唤醒成员。正常退出暂停团队，无法确认进程停止或存档时报告 `needs_review`，不能重复接管。`/reset` 停止旧目标调度并保留团队存档；取消、预算耗尽或新聊天不会自动开启新目标、补充旧预算。

`auto` 在成员启动前优先探测 tmux，公开实际选择；探测不可用才显示原因并选择 `inprocess`。显式 `tmux` 不可用会失败。选定后启动或运行失败不降级，避免重复执行。tmux 使用独立进程／窗格，inprocess 使用同一应用内异步运行器；二者都有独立工作根、会话及工具上下文，都是软隔离，**不是系统沙箱**。shell 可以访问工作根之外，共享依赖链接、Git 公共目录及外部服务仍有真实边界；权限和路径校验继续生效。

没有 MCP 时，普通主入口导出九个工具：六个核心普通工具，加 `load_skill`、`agent`、`team`。验证为 Lead 后才增加 `team_member`、`team_task`、`team_message`、`team_integrate`。成员可使用受限任务／消息工具，不能创建嵌套团队、调用 `agent`、接纳自己的成果或发布整合。角色范围、规划模式、成员计划批准和实际文件／命令／MCP 授权是独立门槛：计划获批不授予工具权限，明确拒绝优先。成员没有人工授权通道，缺少实际工作根适用许可会阻塞；恢复重新加载当前权限，不从历史正文恢复授权。

coordinator 仅在配置 `team_coordinator_enabled=true`、真实启动进程环境 `MEWCODE_COORDINATOR=1` 且当前身份为 Lead 时生效：

```bash
MEWCODE_COORDINATOR=1 uv run mewcode --config .env --team demo
```

把同名变量写进 `--config` 文件不能代替进程环境；`/team status` 显示两个条件。coordinator Lead 不直接写入／编辑文件，也不能借独立 Skill 绕过；shell 仍可用于检查和编排，并继续经过权限检查，代码修改与冲突解决交成员。成员继承环境变量不变成 coordinator。`/plan` 同时限制 Lead 和成员，不能借聊天间接派执行任务；`/do` 只切回执行模式，需要明确新任务。

成员在登记的 Worktree 根工作，提示、项目指令、Skill、项目记忆和权限按实际根加载；用户域长期团队目录存放元数据、任务、邮箱、独立成员存档与缓存。暂停不释放长期引用，存档目录也不扩大工具根；缺失或协议不兼容的必要上下文明确阻塞，不能用空历史重新 spawn 掩盖。实际租约和团队引用保护成员目录不被普通 Worktree 清理删除。

恢复发现工具意图已有存档但结果缺失时，保留实际成果和旧领取预算，将成员标为待核查并阻塞旧任务。普通消息不会继续旧领取，也不重跑工具；Lead 核查后必须明确重新指派取得新 claim。不能确认旧进程已停止时，拒绝恢复接管。

任务更新检查 revision；`team_task claim` 在固定锁内原子领取，并发只有一位负责人。计划决定绑定成员、任务、claim 和版本，旧批准不能放行新计划。成员自然结束只是 `idle`；提交、Lead 接纳和代码整合分别记录。代码接纳必须提供 `validation_command` 并在实际成果目录运行，不能只填模型声称通过的结果。前置代码必须接纳并整合，再由下游成员 `team_task start` 安全同步实际目录并确认要求提交，依赖才解除；脏目录／未知内容阻塞，不自动 stash 或强制重置。

每目标有私有整合分支／目录。输入绑定不可变完整 commit，后续成员分支变化不改变成果凭据。整合串行持锁并保存 intent、检查点和结果；冲突交指定成员持真实租约解决，Lead 不直接编辑。失败只撤销当前受管操作，保留此前成功成果与所有成员分支；未知编辑、停止不确定或撤销失败转待核查。外部 shell、Hook、远端副作用不在 Git 回滚范围内。最终发布必须所有必需任务接纳、代码整合及候选验证成功，重新检查目标旧值、目录／索引和外部更新；不自动 push。崩溃恢复只对账，不重放 Git。

容量边界：每团队最多 4096 个任务，每邮箱最多 2048 条记录，单个 JSON 记录最多 8 MiB；正文 UTF-8 最多 64 KiB，摘要最多 256 个字符。超限明确拒绝，不丢弃未读消息。固定 inode 的 POSIX `flock` 不依据锁文件年龄或时间戳抢占。消息正文只作数据保存，tmux 使用不透明唤醒信号，不执行正文。

本轮证据及未执行项见 [真实模型双后端验收](docs/validation/agent-teams/e2e.md)、[81 个规格场景映射](docs/validation/agent-teams/spec-audit.md) 与 [Git 整合验证](docs/validation/agent-teams/integration.md)。
