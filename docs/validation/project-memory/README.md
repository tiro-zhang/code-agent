# add-project-memory 本次验收

日期：2026-10-04。范围：三层手写指令、JSONL 会话、恢复及缓存生命周期、两级自动笔记、终端入口与维护收尾。下面所有结果来自本次实现及本次真实服务请求，未用历史验收替代。

## 自动化与审查

最终全套、构建、严格校验结果分别保存于 `pytest-final.txt`、`build-final.txt`、`openspec-final.txt`；最终测试结果：**896 passed，92.20 秒**。

执行命令：

```sh
UV_CACHE_DIR=/tmp/mewcode-uv-cache uv run --no-sync pytest -q
UV_CACHE_DIR=/tmp/mewcode-uv-cache uv build --offline
openspec validate add-project-memory --strict
git diff --check
```

`--no-sync` 使用已安装且锁定的环境，`--offline` 使用已有构建依赖缓存。沙箱禁止本地监听导致初次全套中现有 HTTP 合同测试失败；允许本地临时端口后完成全套。未绕过断言或跳过合同测试。wheel 已核查包含 instructions、memory、sessions 新模块。

旧工作流测试通过专用辅助入口关闭持久化和后台提取，避免测试响应脚本消耗额外请求或把模拟会话留在开发项目；新增集成测试直接调用生产入口，在临时目录验证真实存档和维护。此次此前产生的 136 个 `test-model` 测试存档已移至私有临时备份，未删除其他会话。

最终复跑曾遇到既有 `test_approval_wait_is_outside_execution_timeout` 的 0.5 秒 spawn 启动竞态：授权等待阶段仍未超时，批准后的真实工具进程才超时；单独复验通过。测试将执行预算设为 2 秒、授权等待设为 2.1 秒，继续证明等待超过预算但不耗执行额度，并补文件内容断言。生产工具超时设置未改；该模块复验 4 passed，失败尝试保留于 `timing-failure.txt`。

独立只读审查覆盖持久边界、恢复、供应商关闭以及笔记存储和提取；没有遗留 Critical／Important。审查及真实验收发现的问题均补回归后修复：最终答复交互关联、落盘后重新估算、真实 fsync 失败收尾、坏缓存头、用户偏好误分类／跨逗号误拒、双重取消中断流关闭、索引外层标题预算、恢复用量归属、启动恢复消息数与立即排队反馈。

## 八份规格的当前证据

| 规格 | 实现及自动化证据 | 真实终端证据 |
| --- | --- | --- |
| project-instructions | `tests/test_instructions.py`：三层来源、共同 visited、深度 5／6、256,000 字节、代码围栏、空格、FIFO、坏编码、符号链接及越界 | 两协议新会话及恢复计划采用根目录中文／结尾规则，保留低层补充“月桂19”，更新 include 标识为“菠萝84” |
| session-persistence | `tests/test_session_persistence.py`：排他 ID／活动锁、严格往返、追加坏尾行、扫描去重、检查点、反序／冲突／孤立结果、完整组重建、兼容及 TTL | 列表只扫描；两个显式恢复 ID 不变、坏尾行可见、原会话授权为 0、旧 `/do` 拒绝、重新计划后继续修改 |
| automatic-memory | `tests/test_memory_store.py`、`test_memory_manager.py`：四类范围、原话、私有原子文件、索引合计预算、版本竞争、手工编辑、全文限制、失败／取消／超时及关闭 | 正常任务生成项目／用户笔记，普通新会话首次请求已有用户偏好；维护超时、输出截断、非法候选与退出未完成均独立显示 |
| context-management | `tests/test_memory_integration.py`、既有 context 测试：先检查点后历史替换、持久缓存重接管、缺失／坏头、真实磁盘故障、熔断恢复 | 独立恢复摘要成功后分页重读旧缓存，当前 read_file 重新审批，返回 `cache-proof-739` |
| runtime-context | `test_prompt_context.py`、集成测试：当前环境／规则重载、完整提醒、索引版本变动不重置序号、24 小时严格边界 | 恢复读到新标识，普通新会话没有旧工作历史；跨度检查见 `projection-audit.json` |
| system-prompt | `test_prompts.py`、`test_prompt_context.py`、两协议适配测试：固定前缀、可选内容、冲突顺序和工具边界 | 三层英文／中文冲突以项目根为准；计划不能改写权限 |
| interactive-chat | `test_app.py`、`test_terminal_app.py`、controller／permission 测试及集成测试：帮助、消息数、独立状态、输入所有权、关闭顺序 | 恢复消息数和新旧身份、授权提示、正常退出、后台维护期间退出码 0 |
| agent-loop | `test_agent_loop.py`、`test_tool_scheduler.py`、集成测试：持久意图先于调度、真实副作用保留、后续工具停发、自然结束固定快照、独立预算 | 两协议真实 read_file／write_file／验证读取；维护失败后工作仍为 model_done、下一任务预算重新计数 |

## 真实 tmux 流程

使用独立 tmux server `mewcode-memory`，工作根和用户指令／笔记均在 `<E2E_ROOT>` 临时目录。验收入口仅通过生产 `app.run` 的 `user_root` 参数隔离用户文件；模型适配器、终端、权限、执行器、存档和记忆均为生产实现，无供应商／工具替身。启动器有 main guard，满足工具工作进程的 spawn 约定。真实配置密钥仅存在于被忽略的本地配置和权限为 0600 的临时配置；保存的证据逐项检查不含真实密钥，并替换本机绝对路径。

服务采用已有授权 DeepSeek：Anthropic 兼容入口与 OpenAI 兼容入口，模型使用 deepseek-v4-pro；新会话和缓存专项也使用同服务 deepseek-flash。兼容协议支持参见 [DeepSeek 官方文档](https://api-docs.deepseek.com/)。不宣称验证了 OpenAI／Anthropic 两家的官方服务端。

| 阶段 | 本次结果 | 证据 |
| --- | --- | --- |
| Anthropic 正常任务 | 真实读取 README，权限会话批准，最终中文并以【记忆验收】结束；随后保存项目知识与带真实原话的通用偏好 | `anthropic-first.txt` |
| OpenAI 正常任务 | 真实读取 README、授权和最终答复；首次维护 30 秒超时，工作仍成功；后来真实维护保存项目笔记 | `openai-first.txt`、`openai-continue-final.txt` |
| 普通重启 | 新 ID、空工作历史；第一请求答出用户偏好“简短中文，最多三个要点”，无需读取记忆工具；三层指令＋include 已生效 | `anthropic-new-session.txt`、`openai-new-session.txt`、`projection-audit.json` |
| 列表 | 正式 CLI 返回标题、扫描消息数、活动状态；不请求模型、不启动 MCP | `anthropic-session-list.txt`、`openai-session-list.txt` |
| 显式恢复 | 两协议原 ID 恢复，坏尾行警告，规划模式保留，旧计划和会话批准清除；24 小时以上跨度为独立 context | `*-restore-startup.txt`、`*-restore-control.txt`、`projection-audit.json` |
| 当前规则和继续修改 | 恢复后只读新计划答出“菠萝84”和“月桂19”；/do 后 write_file 与 read_file 分别重新授权；两项目文件实际均为 11 字节 `resumed-ok\n` | `*-write-approval.txt`、`*-continue-final.txt`、`artifacts.json` |
| 恢复预算与缓存 | 真实无工具摘要，工作仍暂无任务；摘要版本 1、失败重置为 0；随后 read_file 只读缓存前 3 行，审批后返回标记 | `openai-cache-read-approval.txt`、`openai-cache-read-final.txt`、`openai-restore-usage-final.txt` |
| 后台更新期间退出 | 新任务自然结束后立即 /exit；等待有界收尾、显示“会话已保存，笔记更新未完成”，pane 退出码 0 | `openai-background-exit.txt`、`shutdown-audit.json` |
| 最终终端复验 | 两协议恢复摘要显示 14 条工作消息；/help 含两个启动参数；自然任务立即显示“已排队”，随后运行／无变化，用量独立 | `anthropic-final-ui.txt`、`openai-final-ui.txt`、`*-restore-final-startup.txt` |

跨度／坏行通过退出后的存档夹具设置：时间统一提前 48 小时，追加一条不完整 JSON，保留原始副本，然后运行真实恢复流程。缓存专项用合法 Journal 检查点与 ResultCache 生成 48 条历史消息和登记缓存夹具，夹具明确标注并非真实历史操作；恢复摘要、后续授权／分页读取均为本次真实模型与工具。前 3 行读取属于分页，`truncated` 标记保留；不声称读取了整个缓存或还原采集层已截断内容。

## 失败尝试与边界

- 原 `.env` 的 Ark OpenAI 兼容入口本次多次 TLS EOF，未取得正常答复；无凭据 TLS 探测也失败。证据为 `ark-tls-failure.txt`。协议验收改用已授权 DeepSeek 官方 OpenAI 兼容入口，未降低 TLS 检查或改写真实配置。
- 首次 thinking 开启的 Anthropic 请求在已完成工具后长时间等待，手动 Ctrl+C，终态 cancelled，保留工具结果、不触发笔记。后续临时配置关闭 thinking 完成正常流程；`anthropic-cancelled-attempt.txt` 保留本次失败。
- 缓存专项首次恢复摘要输出达到 4096 上限而未正常结束，存档记录真实不完整 usage 和失败计数，原检查点保留。仅调整临时配置为窗口 60000、输出 24000 后恢复成功：实际总输入 13121、输出 6632；用量归属修复后的再次真实复验输出 2002。每次启动最多一次摘要，无自动多次重试。
- 本次观察到自动记忆维护超时、服务输出截断和非法候选；合法旧笔记保留，最近工作终态不改变。语义提取和去重依赖模型，不保证每轮都有笔记或每次格式正确。
- 实际磁盘耗尽／掉电、跨进程竞争、symlink／FIFO／hardlink、恰好 24 小时及 30 天边界、明确 deny、授权 EOF／Ctrl+C 与重复取消由本次自动化故障注入和现有真实组件回归覆盖，未声称全部在真实模型 tmux 中逐一制造。没有向量库、RAG 或团队同步。

## 复验入口

```sh
uv run mewcode --config .env --list-sessions
uv run mewcode --config .env --resume latest
uv run mewcode --config .env --resume <id>
```

普通启动仍新建会话；恢复后旧待执行计划无效，先 `/plan` 再 `/do`。真实模型验收应在临时工作目录执行并核验实际产物。临时含凭据配置在本次验收结束时删除，仓库仅保留脱敏输出和断言结果。
