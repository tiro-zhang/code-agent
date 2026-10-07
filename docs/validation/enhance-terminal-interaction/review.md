# 独立集成评审报告

评审日期：2026-10-07。评审对象为本次未提交的终端交互实现及 `openspec/changes/enhance-terminal-interaction/` 的设计、规格与任务。

## 范围与方法

先使用 CodeGraph 定位终端与 Session/Agent 调用链；新模块和结果不足部分补查当前源码。重点检查 history/details/state/projection/browser/result_view/input/controller/markdown/app 的真实接口、调用归属、容量、脱敏、输入阶段、审批隔离和流式输出。未修改实现，未运行全量 pytest，未派生代理。以下复现为短 Python 脚本，不请求模型、不读取用户文件；Markdown 控制器复现以最小 backend 替身采集入队片段。

读取了 browser-report.md 与 markdown-report.md；评审期间 history-report.md 尚不存在。实现由其他代理并发修复，因此区分“首次发现”与“复验状态”。

## 发现与当前状态

### R1 — P1：超长调用身份重复归一化导致结果展示崩溃（已复验修复）

位置：`terminal/history.py:bounded_id`、`terminal/projection.py:accept`、`terminal/state.py:update/_tool`。

最初 `bounded_id` 对超过 512 字符的值返回 480 字符前缀、一个省略号和 64 字符摘要，共 545 字符。projection 与 state 再次处理时生成不同摘要，随后 projection 用上一层 ID 访问 `_tools`，触发 KeyError。

复现：对新的 TerminalProjection 调用 `accept(AgentEvent('tool_result', run_id='r', tool_call_id='x'*600, tool_name='read_file', result=ToolResult.success({})))`。

最新短脚本验证：无异常、单条历史记录存在、成功摘要可 flush。当前实现返回不超过 512 字符的稳定值。

### R2 — P1：展示索引饱和会吞掉实际超时／失败提示（已复验修复）

位置：`terminal/history.py:record`、`terminal/state.py:update`、`terminal/projection.py:accept`。

达到身份上限后 history.record 不创建新索引，state._tool 返回 None，原实现立即返回空列表；projection 因没有状态行而不展示失败。展示容量限制影响了必须独立可见的实际异常。

复现：`history.tombstone_limit=1`，先输入一条成功结果，再输入另一 ID 的 `ToolResult.failure('timeout','timeout marker')`。首次输出仅有先前成功摘要，没有 timeout marker。默认上限下同一路径出现在第 4097 个无法确认的新身份，索引淘汰后迟到结果也应保持独立异常可见。

最新短脚本验证：省略身份的超时正文已独立可见。实施方新增了针对索引未保留结果的回退展示。

### R3 — P2：64 KiB 未结束代码行使后续代码丢失原字符（已复验修复）

位置：`terminal/controller.py:write` 的未完成行容量分支、`terminal/markdown.py:flush`。

原实现达到 64 KiB 时调用完整 flush；该调用重置代码围栏，因此同一个未闭合代码块后续内容被当作普通 Markdown 解析。

复现依次写入 ` ```python\n `（去除两侧空格）、`'x'*65536`、`'\n**literal**\n'`。首次提交片段只剩 literal，两个 `**` 被删除，fence 为空。

最新短脚本验证：提交片段仍包含完整 `**literal**`。

### R4 — P2：脱敏后的调用 ID 被用作浏览主键，导致不同调用碰撞（已复验修复）

位置：`terminal/history.py:record` 构建 CallRecord.id，`terminal/browser.py:_change_call/_call`。

复现：secret 为 `secret`，同一轮分别收到原 ID 为 `secret` 与 `[已隐藏]` 的两个调用。首次两条 CallRecord.id 都是 `turn-1:[已隐藏]`；F2 按向下后 `_call().number` 仍为 1，第二条无法独立查看。

最新短脚本验证：向下选择第 2 条成功。当前选择身份加入原身份摘要，显示脱敏不再造成主键碰撞。

### R5 — P2：F2 将不完整累计数显示为精确总数（已复验修复）

位置：`terminal/browser.py:render` 调用列表的 index_evicted 提示，评审时约第 372 行。

此处硬编码“累计 N 次”，未使用 `total_calls_complete` 与 `unindexed_events`。与 `history.text()` 正确显示的下界语义不一致，也违反规格“累计至少 N 次调用”和无法确认身份省略事件数量的明确要求。

复现：`history.tombstone_limit=1` 后输入两个不同 ID，打开 F2，画面显示 `部分调用索引已移出；累计 1 次，不能恢复`，无“至少”或省略事件数量。

修复前短脚本检查 `browser.render(200,10)` 为 FAIL；收到根任务更新后重新运行全部六项，最新 F2 文本同时包含“至少”和省略事件数量，PASS。窄屏视觉可达性仍由根任务实际 PTY 验证。

### R6 — P2：结果移出后丢失 error.details 中的副作用未知信息（已复验修复）

位置：`terminal/history.py:record` 的 references 提取，`terminal/browser.py:_body`。

真实 executor 会把取消副作用标记放在 `error.details.side_effects_may_have_occurred`。原实现只保存 data 的引用字段，error.details 仅影响 attention；正文移出后 F2 只剩“取消”和容量提示，无法判断是否可能已改变实际状态。

复现：记录 `ToolResult.failure('cancelled','命令已取消', details={'side_effects_may_have_occurred':True})`，临时将 body_limit 设为 1 并 `_evict()`，恢复到 8000 后打开当前详情。首次内容不含副作用提示。

最新短脚本验证：存在“可能已有副作用”；当前实现把有界 error.details 标记保存进 references。

## 复验与结论

末次六项短脚本输出：

```text
R1 long ID: PASS
R2 omitted failure: PASS
R4 redacted ID collision: PASS
R5 lower-bound UI: PASS
R6 evicted side effect: PASS
R3 long fenced line: PASS
```

另执行 `git diff --check`，退出 0。以上 PASS 仅证明相应最小复现路径已修复，不替代实施方的回归与实际终端验证。

草稿、审批与搜索缓冲有独立所有权；运行期 Enter 的阶段/Future/代次约束、取消冻结、增强终端不恢复陈旧草稿的实现路径未发现额外确定性阻塞问题。共享正文和当前视图缓存接口已接上，历史选择未反向替换当前 TerminalState。没有执行本次真实服务 tmux/PTY、完整测试、构建或 OpenSpec 验证，不能据此宣称 7.x 验收完成。

本评审确认的六项问题均已通过最终最小复现复验，当前没有尚未解决的确定性发现。由根任务继续统一完成规划要求的完整回归、真实模型 tmux 和定向 PTY 证据。

## 最终增量复查

仅复查根任务随后修改的列表字段优先级、ISO 时间缩写及思考身份键，不重复全量验收。

- `browser._call_label` 先显示调用编号、真实状态和工具，再分配来源及操作摘要；`_call_search_text` 独立使用完整安全元信息，因此 45 列显示裁剪不会缩小搜索范围。`_turn_label` 从实际 ISO 开始时间提取时分秒，并在长标题前保留结束状态、来源和自动接续父标识尾部。未发现新的确定性问题。
- `TurnRecord.thoughts` 以完整存储键的 `sha256(repr(key))` 作为选择身份，显示标题继续安全脱敏。同一段、来源及请求的键在追加正文时不变；两份脱敏后同名来源仍得到不同身份。浏览器无需用显示文字反查身份，也未改变工具调用计数或正文内容。
- 精确运行三个测试：`test_long_turn_titles_keep_state_source_and_resume_parent_visible`、`test_long_call_operation_preserves_status_and_searches_full_metadata`、`test_redacted_thinking_sources_keep_distinct_selection_ids`，结果 **3 passed in 0.41s**。
- 额外短脚本验证：选择第二份同名思考后追加正文、关闭重开，所选身份不变且完整追加内容可见；`total_calls` 仍为 0，工具调用集合仍为空。结果 PASS。

根任务告知完整 1889 项及相关 252 项已通过；这是根任务提供的验收信息，本复查没有自行重跑，也不将其记为独立执行证据。此增量复查没有新增待修发现。
