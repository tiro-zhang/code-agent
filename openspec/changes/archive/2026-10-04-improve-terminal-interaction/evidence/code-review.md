# improve-terminal-interaction 独立最终审查

审查日期：2026-10-04。已完成最终修复增量复核；本报告发现的问题均已修复，当前无未解决的 Critical、Important 或 Minor 项。以下保留发现、修复与验证过程。

## Strengths

- 输入、命令、状态、审批快照与应用编排已有清楚分工；增强／plain 共用参数与范围说明，预览不读取未批准文件。
- 已检查真实输入字节测试、取消／EOF、审批串行化与输出安全边界；新增粘贴首字节代际守卫覆盖跨阶段开头与同次读取中新旧粘贴，退出恢复原解析器方法。
- 本地命令与只读计划查询不会请求模型或消费计划；管道完整行队列与交互提前输入分别处理。

## Issues

### Critical

无。

### Important

1. **审批期间旁路结果隐藏（已修复并复核）**
   - 原位置：审批活动窗口 `height=1`，控制器把旁路结果追加在当前活动调用之后。
   - 触发：第二个工具正在等待授权时，先前已批准工具完成或超时。`state.active_lines()` 的当前待审批调用占第一行，`_deferred` 中的完成／失败摘要位于后续行，实际窗口只有一行，决定前无法看到。
   - 证据：使用真实 `EnhancedTerminal.Application`、24×100 `DummyOutput`，活动行分别为当前 `read_file · 等待授权` 和带 `UNIQUE-TIMEOUT` 的旁路失败摘要；渲染屏幕不含 `UNIQUE-TIMEOUT`。决定后日志虽会保留，但不满足增强模式在独立状态区立即显示旁路结果及异常的要求。
   - 已修复：活动区域最多三行、tiny 保留一行；摘要按显示宽度裁剪，旁路摘要优先，工具编号后先显示状态再显示名字；页码优先保留，详情页高重新核算。12／24 行真实 Application、6 行 tiny 与 45 列页码测试已通过。已读本轮真实模型 `lifecycle-followup-final-report.md` 及对应终端记录，确认 45×15 下 #1 成功、#2 待批、页码同屏及详情完整。
   - **第二轮发现的残余（现已修复）：原 `src/mewcode/terminal/controller.py:91` 的 `records[-1]`。** 同一审批期间只保留最新一条旁路摘要；较早的异常会被随后普通成功覆盖，数量提示不能查看异常。三个并发调用中 #3 待审批，#1 timeout、#2 success 紧接到达时可稳定复现。
   - 第二轮实证：使用真实 `TerminalController`＋`EnhancedTerminal.Application`、24×100 输出，连续注入 #1 `ToolResult.failure('timeout', 'UNIQUE-TIMEOUT')` 与 #2 成功后，活动区只有 `旁路 2> 工具> [#2] 成功 · read_file`、`工具> [#3] 等待授权 · read_file`；屏幕及滚动输出在决定前均不含 `UNIQUE-TIMEOUT`，决定后日志才出现该异常。这未满足增强模式异常立即可见、不被普通成功覆盖的合同。
   - 最终修复：`TerminalController.show()` 根据真实失败、截断、受限结果或权限 warning 保存 `_deferred_alerts`，状态区优先显示异常并保留数量，普通成功不会覆盖。增强审批新增 `results`，复用 `ApprovalView.page_text()` 在决定前分页查看全部旁路记录；页面缓存包含结果快照，新结果能刷新；当前决定关闭后仍完整输出一次，并清空当前审批的异常摘要。
   - 最终复核：已检查上述增量，实跑 `test_earlier_failure_stays_visible_when_later_tool_succeeds_during_review` 的真实 Controller＋Application 流程，确认先超时后成功时异常持续可见、results 同时包含两项、浏览不批准、敏感字段脱敏，以及决定后异常日志只输出一次。该问题收口。

2. **plain 交互任务结束后会消费提前输入（已修复并复核）**
   - 原位置：`src/mewcode/terminal/controller.py` 的 `set_phase()` 与 `readline()` plain 分支。
   - 触发：交互输入一次接收 `first\n/permissions mode bypass\n`，第一问完成后下一次 `readline()` 直接返回权限命令；任务运行期间进入 TTY 队列的文字也可成为下一问。
   - 修复：plain 阶段转换与开放新提示前调用 `reader.discard_pending()`，非交互协议队列由既有 `interactive` 条件保留。
   - 验证：新增 `test_plain_interactive_discards_typeahead_but_accepts_fresh_prompt_input` 使用真实 PTY 与 `TERM=dumb` 覆盖两种提前输入及新输入；本审查聚焦测试集已通过。父执行者另有 RED/GREEN 证据 `/tmp/mewcode-ui-plain-typeahead-red.txt`、`/tmp/mewcode-ui-plain-typeahead-green.txt`。

### Minor

1. **运行期已有用量仍显示累计未知（已修复并复核）**
   - 原位置：`TerminalState.update()` 的 usage／finished 分支。
   - 触发：一个请求已有完整 `usage` 事件，任务继续执行工具或发起下一请求。`_usage` 已记录数据，但 `_total_usage` 仅在 `finished` 赋值，运行期始终显示“累计已知 Token · 未知，尚无累计记录”。
   - 影响：与设计及 README 的运行期累计摘要不符；任务结束后与逐请求记录仍准确，因此未列为 Important。
   - 修复：`_refresh_running_usage()` 按 iteration 去重后的记录，补充缺失请求的未知占位，再复用 Agent `_total_usage` 累加；finished 值包括 None 均为权威，后续 usage 不覆盖最终累计。
   - 验证：已检查并实跑重复／更新用量、缺失请求及迟到记录、部分缓存字段、finished None、下一任务重置测试，累计值和完整性口径符合要求。

## 验证

- 已读 proposal、design、tasks 与变更 interactive-chat 规格，逐文件检查指定产品、测试与 README 范围。
- 实跑：`uv run pytest tests/test_terminal_app.py tests/test_terminal_approval.py tests/test_terminal_commands.py tests/test_terminal_controller.py tests/test_terminal_input.py tests/test_terminal_state.py tests/test_permission_terminal.py tests/test_permission_app.py -q`。
- 结果：**169 passed in 23.91s**，包括当时已落地的 plain 修复与粘贴守卫。活动区及累计用量后续修复不在该次运行中。
- 第二轮检查 `codegraph status` 显示索引最新，使用 `codegraph explore` 追踪控制器、布局与运行期累计调用，再结合源码核对增量。
- 第二轮实跑：`uv run pytest tests/test_terminal_controller.py tests/test_terminal_input.py tests/test_terminal_state.py -q`，**57 passed in 4.69s**。
- 已核对根代理本轮完整验证记录 `evidence/pytest-final.txt`：**690 passed in 136.42s**；根代理报告 build 与 strict 规格校验通过。完整套件不覆盖上述三调用异常被成功替换的残余情形。
- 最终旁路增量实跑：`uv run pytest tests/test_terminal_controller.py tests/test_terminal_input.py tests/test_terminal_approval.py -q`，**50 passed in 6.59s**，包含新增多结果先失败后成功回归。690 项结果属于该最后增量之前，最终全套与新增 tmux 证据由根代理继续记录，本报告不冒称已重跑。
- 未操作根代理 tmux、未调用真实模型、未改产品代码、tasks 或 checklist。

## Declined to judge

- 已归档 MCP 实现本体：属于既有基线；只检查本轮终端接线、身份与诊断呈现。
- 流式片段跨边界的完整 API key 脱敏：旧 Renderer 已逐片段脱敏，本轮迁移未引入该行为，不归入本次新增缺陷。
- API key 恰为单个引号／花括号时 plain 二次脱敏破坏 JSON 展示结构：仅展示问题且缺乏合理服务配置触发条件，参数绑定不变，不作为本轮阻断项。
- tmux 真实模型与全套最终验收：由根代理负责，本审查不以历史记录代替本轮证据。

## Assessment

**Ready to merge? Yes（代码审查范围）。**

plain 输入隔离、粘贴守卫、运行期累计，以及单／多旁路结果的决定前可见性均已修复并通过对应聚焦验证。当前审查范围内无待修复问题；根代理负责最后增量的完整测试及真实 tmux 验收收尾。
