# 本轮原终端 tmux 回归验收

日期：2026-10-07。验证当前 Web 变更下的原终端入口，不借用 `checklist.md` 中历史勾选作为本轮通过证据。

## 环境与结论

- 使用现有已授权配置 `.env`，未读取或输出配置中的密钥；终端展示协议配置 `openai-compatible-demo`、模型 `ark-code-latest`。
- 工作目录：`/private/tmp/mewcode-cli-web-regression-20261007`。README 启动说明与 sample.txt 均为本次独立临时夹具，不修改仓库业务文件。
- 启动当前环境已安装的 `[repo]/.venv/bin/mewcode --config [repo]/.env --permission-mode strict`，实际传入配置绝对路径。恢复时追加 `--resume 20261007-190246-2776`。
- 仅使用专用 tmux 会话 `mewcode-cli-web-e2e`；从未操作主任务的 Web 验收会话。结束后两次 CLI 退出码均为 0，专用 tmux 会话已删除。
- 本次要求的启动、流式回复、读取审批、一次批准编辑、运行中取消后继续、退出、显式恢复及旧工具零重放均通过。

## 本轮实际操作

| 场景 | 结果 | 本轮证据 |
| --- | --- | --- |
| 原终端正常启动 | 通过；新建普通存档，strict 模式，等待输入 | [读取审批](terminal-read-approval.txt) |
| README 真实读取与审批 | 通过；目标为临时 README.md，选择 `2` 仅本次；真实读取后正确答出三条步骤与 `CLI_READ_20261007` | [读取审批](terminal-read-approval.txt)、[读取结果](terminal-read-result.txt) |
| 真实流式输出 | 通过；`正在回答` 阶段从“根据”逐步出现正文，随后完成并回到空闲 | [pipe-pane 增量摘录](terminal-streaming-observation.txt) |
| 临时文件编辑、一次批准边界 | 通过；read → edit → read；三个调用分别请求授权，均选择 `2`。编辑审批完整展示 `-status=before`／`+status=after`，marker 不变 | [编辑审批](terminal-edit-approval.txt)、[编辑结果](terminal-edit-result.txt) |
| 产物独立验证 | 通过；文件精确为 `status=after\nmarker=CLI_EDIT_20261007\n`，SHA256 为 `fdc103251ff2414db0856c1a3185af82e67cbd5202ddd7d9f2823d87dc289c68` | [存档与产物审计](terminal-journal-audit.txt) |
| 运行中 Ctrl+C | 通过；一次批准 Python 长命令，观察真实 cancel-start/PID 后才发送 Ctrl+C；工具结果 `cancelled`，终端回到空闲，明确保留已有副作用说明 | [运行中](terminal-cancel-running.txt)、[取消结果](terminal-cancel-result.txt) |
| 真正清理长命令 | 通过；记录 PID 已退出；超过原定 25 秒（首次核验 28.2 秒、最终审计 193.6 秒）仍无 cancel-end；已有 cancel-start 保留 | [存档与产物审计](terminal-journal-audit.txt) |
| 取消后继续 | 通过；无需工具即根据已提交上下文正确回答 `status=after` 和 `CLI_AFTER_CANCEL_OK` | [继续对话](terminal-after-cancel.txt) |
| 正常退出 | 通过；`/exit` 显示资源清理后回到 shell，实际退出码 0 | [退出证据](terminal-exit.txt) |
| 显式恢复 | 通过；同 ID 恢复 17 条工作消息，保持空闲；`/session`、`/status` 是本地查询，显示暂无新任务记录 | [恢复证据](terminal-resume.txt) |
| 恢复不重放工具、不自动运行旧任务 | 通过；恢复前 38 条、恢复后 39 条，唯一新增记录为 session_resumed，新增工具结果、工作运行及维护记录均为 0 | [存档与产物审计](terminal-journal-audit.txt) |
| 恢复实例退出与 tmux 清理 | 通过；第二次 `/exit` 退出码 0；关闭前 tmux 仅余 zsh，随后专用会话查询返回不存在 | [恢复退出](terminal-resume-exit.txt) |

所有涉及文件读写和命令执行的批准只作用于上述临时项目。长命令为：写 cancel-start/PID → 输出并 flush 启动标记 → sleep 25 → 写 cancel-end；取消验证没有对空闲提示符发送 Ctrl+C，也没有把工具尚未启动的取消误记为运行中清理。

## 从本次 JSONL 提取的计数

只提取记录身份、种类、调用名称、结果状态、用量完整性和计数，没有复制 provider_content、原始供应商续接块或配置凭据。

| 工作轮 | 模型请求／持久响应证据 | 工具 | 终态 |
| --- | --- | --- | --- |
| 读取 README | 2 | read_file ×1 | model_done |
| 编辑并复读 sample.txt | 4 | read_file ×2、edit_file ×1 | model_done |
| 长命令取消 | 1 | execute_command ×1 | cancelled |
| 取消后继续 | 1 | 无 | model_done |
| 合计 | 8 | 5 次；4 成功、1 主动取消 | 4 个工作轮 |

工作模型计数按合法 interaction_started/history_commit 中助手消息 ID 去重，从 JSONL 得到 2+4+1+1，与终端每轮实际请求计数一致。另有 3 条 memory 维护结束记录，其中一条用量不完整；这与普通工作请求分别统计，不把维护记录冒充新用户任务。本轮没有底层 HTTP 抓包，因此不从存档推断 SDK 的 HTTP 重试次数。

## 实际未通过与限制

后台记忆维护没有全部成功：第一轮出现“自动记忆候选的结构、范围、来源或长度不合法，已跳过”；编辑轮后出现“自动记忆提取超时，既有笔记保留”，对应维护用量不完整。它们不改变已验证工具和工作轮结果。本轮保留这些诊断，不宣称自动记忆成功率或所有维护行为已验收，也未为它们修改代码。

此次仅使用现有 `.env` 的一个真实 OpenAI 兼容入口，没有实测官方 Anthropic、官方 OpenAI、另一供应商端点或外部 MCP。没有将旧协议测试、旧清单结果或其他变更的日志计入本轮。

## checklist.md 全部既有章节的本轮覆盖映射

“部分通过”仅指本表明确列出的子集，其余条目均未执行；历史勾选保持原样，不代表本轮重新通过。

| 既有章节 | 本轮状态与覆盖 | 本轮未执行及原因 |
| --- | --- | --- |
| 启动与配置 | 部分通过：有效配置、strict 启动、普通新存档、显式恢复、退出 | 缺失配置／错误配置／非法参数等负例未执行，本次聚焦原入口实际运行回归 |
| DeepSeek Anthropic 兼容对话 | 未执行 | 本次指定 `.env` 为 OpenAI 兼容入口，没有切换配置 |
| OpenAI 兼容对话 | 部分通过：多轮、流式、上下文、正常退出、显式恢复 | 其他兼容端点与网络故障负例未执行；不以旧章节“再次启动不恢复”替代现有显式恢复合同 |
| 验收记录 | 已更新本轮独立材料 | 本报告及链接证据为本轮记录，未复用旧记录 |
| 工具系统验收（add-tool-system） | 部分通过：read_file、edit_file、execute_command、真实取消、后续对话 | write_file、搜索、glob、超时、参数错误和其他失败分支未执行 |
| Agent Loop 验收（add-agent-loop） | 部分通过：连续 read→edit→read、实际请求计数、取消收尾 | 并发读组、预算耗尽、错误自动修订、规划、上下文恢复等专项未执行 |
| 结构化系统提示验收（add-structured-system-prompt） | 未执行专项 | 本轮不抓取或比较系统提示包，不核验周期完整／精简注入 |
| 权限系统验收（add-permission-system） | 部分通过：strict、完整目标／差异、单次批准、读与改分开、复读重新批准 | deny 优先、会话／永久批准、撤销、EOF、规则变更等专项未执行 |
| MCP 客户端验收（add-mcp-client） | 未执行 | 本临时项目未配置外部 MCP，本轮无 MCP 服务调用 |
| 增强终端交互验收（improve-terminal-interaction） | 部分通过：增强布局、流式、状态、唯一审批栏、Ctrl+C 后继续 | 多行编辑、粘贴隔离、Tab、F2、窄屏、plain、EOF 等交互矩阵未执行 |
| 上下文管理验收（add-context-management，2026-10-04） | 部分通过：取消后上下文仍可用，恢复和本地状态展示 | 压缩、溢出缓存、大上下文、熔断、/compact 等专项未执行 |
| 项目记忆验收（add-project-memory，2026-10-04） | 部分通过：普通存档、显式恢复、旧工具零重放、关闭；记忆维护存在上述失败 | 记忆抽取成功合同、过期清理、缓存恢复、跨协议恢复等未执行，失败诊断不改记为通过 |
| 命令注册与分发验收（add-slash-command-registry，2026-10-04） | 部分通过：/session、/status 本地查询，/exit，--resume 身份保持 | 命令冲突、别名、补全、/plan、/do、/compact、/clear、/reset 等未执行 |
| Skill 系统验收（add-skill-system，2026-10-05） | 未执行 | 本轮没有激活或运行 Skill |
| 终端信息与交互优化（optimize-terminal-ui，本轮已验收） | 部分通过：工作阶段、工具状态、用量、取消与清理提示 | F2、搜索、详情翻页、resize、草稿切换等专项未执行 |
| 生命周期 Hook（add-lifecycle-hooks，本轮验证） | 未执行 | 本临时项目没有定义验收 Hook，不声称验证 Hook 副作用和生命周期边界 |
| 子 Agent 委派（add-subagent-delegation，2026-10-05 本次验证） | 未执行 | 本轮未创建子 Agent；普通父任务显示不等于委派验收 |
| Worktree 隔离（add-worktree-isolation，2026-10-06 本次验证） | 未执行 | 临时夹具不是 worktree 验收仓库，未创建隔离工作树 |
| 持久 Agent Teams（add-agent-teams，2026-10-06） | 未执行 | 本轮未启动团队、成员或团队 tmux 后端 |
| 终端五项交互优化（enhance-terminal-interaction，2026-10-07 本次验证） | 部分通过：聚合工具摘要、流式、取消后继续、/status、正常退出 | 表格、F2 详情、草稿与粘贴隔离、clear/reset 等专项未执行；旧“本次验证”标题不作为当前通过证据 |

本报告只覆盖原终端回归；Web 浏览器／API 验收由主任务的独立材料记录。
