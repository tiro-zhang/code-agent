# 命令注册与分发：本次验收

变更：`add-slash-command-registry`。日期：2026-10-04。所有以下结果均来自本轮运行；历史验收材料保持原样。

## 规格同步与归档

用户确认同步后，六份增量规格已合入主规格：新增 10 条需求、修改 12 条、移除 2 条，并按迁移说明更新 `plan-mode` 的 Purpose。逐条核对增量与主规格一致，未涉及的需求及场景保留。

变更已按 `spec-driven` 流程归档至 [2026-10-04-add-slash-command-registry](../../../openspec/changes/archive/2026-10-04-add-slash-command-registry/)，全部产物完成、23/23 项任务勾选。移动前后 10 个文件的 SHA-256 一致，包含 `.openspec.yaml`。归档时运行 `openspec validate --specs --strict --no-interactive`（19 passed）和 `openspec validate --archived --strict --no-interactive`（10 passed）；活动变更列表为空。

## 自动化、构建及审查

- 完整测试：**928 passed，91.22 秒**，见 [pytest-final.txt](pytest-final.txt)。初次最终套件 926 passed；独立审查补两项回归修复后重新完整运行。
- `UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run --offline pytest -q`。现有 MCP HTTP 合同测试需绑定本机回环端口，因此完整套件在获准的沙箱外执行；没有跳过这些测试。
- `UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv build --offline`：源码包和 wheel 成功，包含新的 `commands` 包，见 [build-final.txt](build-final.txt)。
- `openspec validate add-slash-command-registry --strict`：通过，见 [openspec-final.txt](openspec-final.txt)。
- 独立只读审查发现的输入历史重用及 `/session` 模式展示问题，均以失败回归用例证明后修复。详情见 [review.md](review.md)。无遗留审查问题。

| 行为 | 本次自动化证据 |
| --- | --- |
| 不可变定义、冻结、全部名称／别名碰撞、隐藏项、大小写、解析保真、未知命令、参数零副作用 | `test_command_registry.py`、`test_terminal_commands.py`、`test_slash_app.py` |
| `/do` 只切模式、重复周期、规划失败／取消、存档失败保原状态、恢复授权不重放 | `test_plan_mode.py`、`test_prompt_context.py`、`test_slash_contracts.py`、`test_permission_app.py`、`test_memory_integration.py` |
| `/session` 当前及列表、扫描诊断、身份不变；两域记忆、禁用仍可读、完整安全正文 | `test_slash_app.py`、`test_slash_contracts.py` |
| 记忆缺失、损坏、索引忽略、symlink／hardlink／FIFO／身份不符、权限及文件内容不变 | `test_memory_queries.py`，并回归 `test_memory_store.py` |
| 多候选菜单首次 Enter、Esc、大小写／隐藏过滤、窗口缩放、审批／粘贴隔离 | `test_terminal_input.py`、`test_slash_contracts.py` |
| 清屏保留历史、身份、授权、用量和模式；plain 无 ANSI；输入历史保留原始转义 | `test_slash_app.py`、`test_slash_contracts.py` |
| 固定 review 单次提交、实际任务文本、统一流关闭／取消、维护用量独立及未知统计 | `test_slash_app.py`、`test_app.py`、`test_context_manual.py`、`test_terminal_state.py` |

## 真实模型 tmux 环境

使用独立 tmux server `mewcode-slash`、session `mewcode-e2e`，110×34 终端，工作项目和用户根均位于 `<E2E_ROOT>`。模型为已有授权配置中的 `deepseek-v4-pro`，Anthropic 兼容协议；临时配置仅关闭 thinking，保留实际服务、模型和窗口配置。没有更改原配置，密钥未写入证据。

启动器调用生产 `app.run`，`ObservedProvider` 仅计数并原样转发真实 Provider 流及关闭，不替代供应商、工具、权限、终端或存档。通过已有 `memory_enabled=False` 和 `user_root` 参数隔离后台维护与个人笔记，以便证明本地命令零请求。记忆文件、Git 初始加减法差异及长消息均为明确的验收夹具，不冒充模型产物。工作对话、审查、授权、摘要、编辑、执行与取消均走真实代码。

默认审查结束后受控退出（退出码 0），使用最终审查修复后的代码恢复同一存档；模式保持 plan，旧工具未重放。下列证据已替换本机路径，保留终端实际用量和失败事实。

| 场景 | 本次结果 | 证据 |
| --- | --- | --- |
| 帮助、未知命令、大小写、权限别名、session／memory、裸 plan/do | 通过，累计模型请求为 0 | `01-local-commands.txt` |
| Tab 多匹配及首次 Enter | 通过，可见 `/permission`、`/permissions`；首次 Enter 只确认草稿，模型请求仍为 0 | `02-tab-menu.txt`、`03-tab-confirm-only.txt` |
| 默认 review | 通过，一次工作任务经 5 次正常 Agent 请求完成 9 个真实工具调用，指出 demo.py:2 的减法回归；未改源文件 | `04-review-approval.txt`、`06-review-default-result.txt` |
| 恢复后 do／重复 do | 通过，同一 ID 从 PLAN→DEFAULT，模型累计请求仍为 5 | `07-restored-do-no-request.txt` |
| 显式 review 目标及规划约束 | 通过，参数中的双空格保留；仅读工具，报告明确说明无法执行 shell 验证；未修改文件 | `08-review-explicit-plan.txt`、`request-audit.json` |
| clear 仅清屏 | 通过，可控显示重绘 PLAN／输入／原用量，请求仍为 8；随后真实模型从原上下文答出 SLASH_CONTEXT_73 | `09-clear-visible.txt`、`10-clear-context-recalled.txt` |
| 禁用维护时两域记忆查看 | 通过，同 ID 分域列出且显示完整正文；请求仍为 9，字节、权限、mtime、目录项均不变，无锁／索引修复 | `11-memory-readonly.txt`、`memory-query-proof.json` |
| plan 任务及 do 不执行 | 通过，plan 任务一条工作请求；两次 do 均保持请求数 12，源文件仍为减法 | `13-plan-task.txt`、`14-do-no-execution.txt`、`do-zero-request.json` |
| 明确执行及授权 | 通过，重新读取、授权编辑及 Python 断言后 add(2,3)==5；独立核查实际文件及 SHA256 | `15-execution-approval.txt` 至 `18-execution-result.txt`、`artifact-proof.json` |
| compact 独立维护 | 分发／隔离／失败保护通过；两次真实摘要生成均未通过既有格式校验，每次恰好 1 次无工具请求，工作状态保留 | `12-compact-rejected-history-kept.txt`、`19-compact-second-result.txt`、`journal-audit.json` |
| review 授权阶段取消并继续 | 通过，工具记录 cancelled／未启动；下一条普通消息返回 CANCEL_RECOVER_OK | `20-review-before-cancel.txt`、`21-review-cancelled.txt`、`22-after-cancel.txt`、`cancel-proof.json` |
| 缩放及 Esc | 通过，50×14 菜单候选可达，恢复 110×34 后 Esc 保留 /per 草稿，请求数仍为 19 | `23-menu-narrow.txt`、`24-menu-escape.txt`、`menu-proof.json` |
| 最终状态及退出 | 状态页仍区分工作／手动摘要实际用量和估算，摘要失败 2/3；受控退出结果见退出记录 | `25-final-status.txt`、`exit-proof.json` |

本轮合计 19 次真实 Provider 调用：17 次工作请求、2 次独立摘要；存档中 8 个工作任务（含 1 个取消任务）。`/review` 的多次调用来自正常工具循环，一次命令只创建一个展开后的工作任务；没有生成提示词的额外请求。`request-audit.json` 与 `journal-audit.json` 记录请求形状、工具集、模式和任务文本摘要；长消息只导出长度及 SHA256。

## 已观察的失败与范围限制

- 首次真实 `/compact` 独立调用 1 次、无工具，实际总输入 33342／输出 3；模型输出未满足既有摘要格式，应用明确拒绝并保留历史、原模式和最近工作统计，失败计数为 1。见 `12-compact-rejected-history-kept.txt`。第二次明确手动尝试总输入 5352／输出 79，也被格式校验拒绝，历史继续保留、失败计数为 2，见 `19-compact-second-result.txt`。不能标为“摘要成功”；既有格式校验和失败原子性保持。成功摘要和取消原子性由本次自动化覆盖，本轮真实服务未获得成功摘要。
- 真实服务仅验证已有 DeepSeek Anthropic 兼容配置；本轮不声称验证官方 Anthropic、官方 OpenAI 或 Ark 服务端。协议及取消等边界由本次完整自动化覆盖。
- 磁盘耗尽、恶意并发、异常文件类型、纯文本兼容流、授权 EOF 等通过本轮自动化故障注入验证，未将其写为全部经过真实模型 tmux。
- `/clear` 不承诺清除终端模拟器的全部滚动历史，也不删除存档；`09-clear-visible.txt` 展示的是实际可控屏幕。


## 对照现有 checklist 的范围

| 既有章节 | 本次复核方式与边界 |
| --- | --- |
| 启动与配置、DeepSeek Anthropic 兼容对话 | 本次真实加载、流式输出、上下文和退出；非法配置由完整自动化覆盖 |
| OpenAI 兼容对话 | 本次适配器及原始协议自动化通过；真实 OpenAI/Ark 服务端未执行，不沿用历史通过结论 |
| 工具系统、Agent Loop | 本次真实读取、搜索、编辑、命令和授权取消；错误、超时、调度等由完整自动化覆盖 |
| 系统提示、权限 | 本次 plan/execute 工具集、提醒、权限独立及审批；提示不可伪造、拒绝优先、永久撤销异常等由自动化覆盖 |
| MCP 客户端 | 本次完整 SDK／双传输合同测试通过；真实模型 MCP 工具未重复执行，命令没有改变 MCP 生命周期 |
| 增强终端 | 本次真实 Tab／Enter／Esc／缩放／清屏／状态；多行粘贴、残留隔离、plain 和 EOF 由自动化覆盖 |
| 上下文管理 | 本次真实手动摘要失败保护及独立实际用量；成功格式／摘要提交／取消原子性由自动化覆盖，不冒充真实成功 |
| 项目记忆 | 本次同存档恢复、同 ID 两域查询、关闭维护仍可查、无文件写入；本轮关闭后台提取，未重复真实模型记忆生成／恢复摘要 |

自动化完整套件包含旧章节相关模块；此表不把自动化等同于所有平台和服务端的手工验收。历史 `/do` 自动执行、待执行计划或恢复后要求新计划的条目均保留为旧版本证据，当前行为以本次命令章节为准。

## 复验方式

在独立临时 Git 项目启动 `uv run --project <REPO> mewcode --config .env`。输入 `/help`、`/per` 后 Tab／Enter／Esc，执行 `/plan <任务>`、`/do` 后另发明确任务；`/review` 和 `/compact` 需要真实授权模型。通过默认权限的当前审批逐次确认临时操作，结束输入 `/exit`。本次 `launcher.py` 的生产入口参数及观察器实现随 `launcher.py` 保存，临时根目录改由 `MEWCODE_E2E_ROOT` 环境变量提供，不包含配置密钥。
