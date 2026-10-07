# enhance-terminal-interaction 本次验收

日期：2026-10-07。在用户指定的当前工作区实施；主规格已同步，变更已归档，Git 改动尚未提交。五项交互均已实现：增量 Markdown 排版、主对话摘要与 F2 单调用详情、跨轮历史、运行中唯一草稿、常驻上下文及准确按键提示。

## 验证结果

| 项目 | 本次结果 | 证据 |
| --- | --- | --- |
| 完整自动化 | **1889 passed，379.09 秒** | [完整输出](full-tests.txt) |
| 最终交互定向 | **252 passed，20.73 秒**；最后思考身份修复相关 **45 passed，9.03 秒** | [终端与接续](final-targeted-tests.txt)、[稳定身份](final-identity-tests.txt) |
| 帮助／上下文 | 67 passed | [输出](help-context-tests.txt) |
| 确定性真实 PTY | **14 组检查通过，13 份屏幕证据**，最终代码重跑退出 0 | [结果](deterministic-result.json)、[运行输出](deterministic-run.txt)、[可复用驱动器](deterministic-pty.py) |
| 真实模型 tmux | 4 个工作轮、2 个新建会话；6 次工作请求（2／1／1／2），strict 明确批准合成文件读取及临时命令 | [首三轮审计](real-audit.json)、[草稿审计](real-draft-audit.json)、[最终长回复审计](real-final-audit.json) |
| 独立审查 | 6 项确认问题全部修复并复验；最后列表优先级与思考身份增量复查 3 项通过 | [评审及复验](review.md) |
| 构建与包内容 | sdist／wheel 成功；新增模块齐全，全部 Python 模块与当前源码一致，没有真实配置、私密记录或已知真实密钥 | [构建输出](build.txt)、[包审计](package-audit.json) |
| OpenSpec | strict 通过，41 个场景有映射 | [校验输出](openspec-validation.txt)、[逐场景映射](spec-audit.md) |

全量通过后仅调整列表字段显示次序、帮助文案及思考记录的稳定选择 ID，分别运行上述最终定向回归和独立复查；没有把不同范围的测试数量相加成全量数量。基线终端回归 85 项通过，首轮汇合终端相关 222 项通过。新增草稿、表头预览、围栏超长行、身份碰撞及不完整计数等均先复现失败再验证修复。

## 真实终端过程

使用本轮新建的 `/private/tmp/mewcode-terminal-real-jvo88pxv`，README、alpha、beta 都是本轮编写并逐字核对的合成夹具。实际命令为 `uv run --project <仓库> --no-sync mewcode --config <仓库>/.env --permission-mode strict`；在专用 socket `mewcode-enhance-real`、会话 `mewcode-e2e` 中执行，最终代码在独立 final 窗口重启复验。现有配置使用 `ark-code-latest`，OpenAI 兼容服务 `ark.cn-beijing.volces.com`。真实配置未复制到临时项目、证据或构建包。

- 首轮三次明确批准本次读取；[主对话](real-04-response.txt)显示三文件单条摘要、粗体／行内代码和逐行三列表格。[调用列表](real-05-call-list.txt)、[单次详情](real-06-call-detail.txt)、[原始 JSON](real-07-raw.txt)及[45 列详情](real-08-narrow-detail.txt)均可读。
- 第二轮明确批准 `printf 'READY107' > started.txt && sleep 30 && printf 'DONE' > should-not-exist.txt`。标记出现后在运行期起草并按 Enter，[草稿保持未发送](real-10-running-draft.txt)，Ctrl+C 后[原文继续留在输入区](real-11-cancel-preserved.txt)。工作任务数保持 2；明确再按 Enter 后才产生第三轮，回复 [DRAFT_EXPLICIT_107](real-12-explicit-reply.txt)。取消超过 30 秒后 started.txt 仍为 READY107，should-not-exist.txt 不存在。
- 第三轮后[轮次列表](real-13-turns.txt)及[首轮调用](real-14-old-call.txt)仍可回看；[/clear 保留](real-15-clear-preserves.txt)、[/reset 清空](real-16-reset-empty.txt)，本地浏览和控制命令未增加工作任务。
- 最终代码另做一轮只读请求：[12 行中文长表格与缩进代码](real-17-final-long-response.txt)完成，输出 3870 token，代码未执行。最后实机复验[调用列表](real-18-final-call-list.txt)、[轮次状态](real-19-final-turn-list.txt)和[45 列状态优先](real-20-final-turn-list-45.txt)。早期 capture13 中长标题掩盖状态的问题已修复，旧证据保留以记录发现过程。

首次草稿尝试到达时程序已经处于审批阶段，被作为无效选项拒绝，没有误批准。随后以 started 文件作为同步点重做并通过。最终长回复的 25 秒观察窗口未收到表格，实际模型在 115.7 秒完成；因此没有完成这轮“流中缩屏”，仅验证了最终长回复和完成后缩屏。未来表格行按新宽度转换由自动化测试验证。

两轮长回复后观察到后台自动记忆提取超时，既有笔记保留；工作轮均已正常结束。本变更不修改后台记忆维护。工作请求计数来自终端任务／迭代及本次存档审计，后台维护请求不混入工作请求总数。

## 边界与可复用证据

确定性驱动器使用真实 tmux PTY、EnhancedTerminal、TerminalState 和 DetailsBrowser，注入展示事件以稳定控制时序；它不是实际模型或完整后台 Agent 编排。覆盖运行 Enter 与 idle 接收者竞争、跨阶段分裂粘贴、自动接续后最新草稿、F2 搜索被审批抢占、连续授权残留、11 个结束段淘汰、默认 8 MiB 正文容量、110×34／45×15／18×6 缩放与终端回显恢复。

本次没有真实 Anthropic、外部 MCP、后台团队／Skill 自动接续或所有取消阶段的模型实机组合；这些边界由本次完整测试与定向回归覆盖其已有路径，逐项范围见场景映射。plain 保留逐行协议与安全原文；F2 历史仅在当前进程内，当前段加最近 10 个结束段。历史终端滚动区不回溯重画，新的表格行及活动 F2 视图响应尺寸变化。

早期代理重复的全量尝试已中断，不作为通过证据；沙箱内 HTTP fixture 绑定失败改用获授权的本地测试环境重跑，最终完整输出无失败。第一次构建因沙箱 DNS 限制无法获取 setuptools，保留[原始失败](build-sandbox.txt)，放行标准构建依赖下载后构建成功。真实请求最初被自动审批拒绝，原因是具体文件外发授权不明；逐字核对三份本轮合成夹具和已授权服务目的地后重新审核通过，没有扩大文件读取范围。

## 归档记录（2026-10-07）

用户选择先同步主规格再归档。归档位置：[2026-10-07-enhance-terminal-interaction](../../../openspec/changes/archive/2026-10-07-enhance-terminal-interaction/)。schema 为 spec-driven，规划文件齐全，28/28 项任务完成。interactive-chat 同步4条修改、1条新增，保留其余25条需求及原有场景，总计30条需求、156个场景。所有30份主规格严格校验通过。

归档前后5个规划文件（含 .openspec.yaml）的 SHA-256 完全一致；活动变更列表已无本变更。详见[归档核验](archive-verification.json)与[主规格校验](archive-specs-validation.json)。本次仅同步规格、移动变更目录及更新文档链接，运行时代码未改动，未重复运行功能测试。
