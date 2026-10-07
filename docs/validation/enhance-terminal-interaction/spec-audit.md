# enhance-terminal-interaction：41 个规格场景证据映射

规格：[interactive-chat/spec.md](../../../openspec/changes/archive/2026-10-07-enhance-terminal-interaction/specs/interactive-chat/spec.md)。记录日期：2026-10-07。本表逐项列出可复查入口与本次实际覆盖边界；测试函数链接表示测试断言位置，不单独表示该测试的执行结果。

## 证据范围

- 自动化测试：本次 [targeted-tests.txt](targeted-tests.txt) 记录222 passed（14.39s），[full-tests.txt](full-tests.txt) 记录1889 passed（379.09s）。这些是本次重跑结果，不使用历史验证替代。
- 确定性真实 PTY：[驱动](deterministic-pty.py)通过 tmux 真实按键与窗口变化运行增强终端，注入展示事件，无模型请求、无真实工具执行、无完整 Agent／Provider 编排。[deterministic-result.json](deterministic-result.json) 为 pass，14 项检查通过，保存13份capture；其输入Future、草稿／光标、选择／锚点、预算和终端恢复断言比纯屏幕截图更精确。
- 真实模型：OpenAI兼容服务 `ark-code-latest`、strict、临时工作目录，首个会话3个工作轮，最终代码另开会话完成第4个只读长回复工作轮。第一轮分别明确授权读取README／alpha／beta，输出三文件摘要及中文Markdown表格；第二轮明确授权命令，started标记出现后起草并运行Enter，再Ctrl+C取消；第三轮仅在新空闲Enter后回复标记。[real-draft-audit.json](real-draft-audit.json)记录取消前后工作任务数2→2、草稿保留、started.txt内容及延迟文件未出现。capture09中的首次草稿操作实际到达审批阶段，显示无效且未批准；有效运行草稿是capture10中的第二次操作。[real-audit.json](real-audit.json) 补充3轮输入／实际结果、30秒后延迟文件仍不存在，以及本地浏览、clear/reset增加工作轮数为0。
- 自动记忆维护在第一轮后超时，capture如实保留提示；这不是工作轮失败，也不能据此声称维护成功。真实模型未验证Anthropic或其他兼容服务、MCP服务器、所有异常类型和所有阶段组合。
- “PTY01”“真实10”等简称对应下列capture索引。表中的“未执行”是本次端到端覆盖边界，不是推断实现不存在或宣称单测失败。

## Capture索引

| 简称 | 本次证据 |
| --- | --- |
| PTY 01 | [deterministic-01-running-enter-idle-pending.txt](deterministic-01-running-enter-idle-pending.txt) |
| PTY 02 | [deterministic-02-auto-new-draft.txt](deterministic-02-auto-new-draft.txt) |
| PTY 03 | [deterministic-03-cross-phase-paste.txt](deterministic-03-cross-phase-paste.txt) |
| PTY 04 | [deterministic-04-f2-search.txt](deterministic-04-f2-search.txt) |
| PTY 05 | [deterministic-05-approval-zero-stale-decisions.txt](deterministic-05-approval-zero-stale-decisions.txt) |
| PTY 06 | [deterministic-06-second-approval-pending.txt](deterministic-06-second-approval-pending.txt) |
| PTY 07 | [deterministic-07-eleventh-turn-eviction.txt](deterministic-07-eleventh-turn-eviction.txt) |
| PTY 08 | [deterministic-08-eight-mib-body-eviction.txt](deterministic-08-eight-mib-body-eviction.txt) |
| PTY 09 | [deterministic-09-resize-110x34.txt](deterministic-09-resize-110x34.txt) |
| PTY 10 | [deterministic-10-resize-narrow.txt](deterministic-10-resize-narrow.txt) |
| PTY 10 | [deterministic-10-resize-tiny.txt](deterministic-10-resize-tiny.txt) |
| PTY 10 | [deterministic-10-resize-wide.txt](deterministic-10-resize-wide.txt) |
| PTY 11 | [deterministic-11-restored.txt](deterministic-11-restored.txt) |
| 真实 01 | [real-01-approval.txt](real-01-approval.txt) |
| 真实 02 | [real-02-approval.txt](real-02-approval.txt) |
| 真实 03 | [real-03-approval.txt](real-03-approval.txt) |
| 真实 04 | [real-04-response.txt](real-04-response.txt) |
| 真实 05 | [real-05-call-list.txt](real-05-call-list.txt) |
| 真实 06 | [real-06-call-detail.txt](real-06-call-detail.txt) |
| 真实 07 | [real-07-raw.txt](real-07-raw.txt) |
| 真实 08 | [real-08-narrow-detail.txt](real-08-narrow-detail.txt) |
| 真实 09 | [real-09-command-approval.txt](real-09-command-approval.txt) |
| 真实 10 | [real-10-running-draft.txt](real-10-running-draft.txt) |
| 真实 11 | [real-11-cancel-preserved.txt](real-11-cancel-preserved.txt) |
| 真实 12 | [real-12-explicit-reply.txt](real-12-explicit-reply.txt) |
| 真实 13 | [real-13-turns.txt](real-13-turns.txt) |
| 真实 14 | [real-14-old-call.txt](real-14-old-call.txt) |
| 真实 15 | [real-15-clear-preserves.txt](real-15-clear-preserves.txt) |
| 真实 16 | [real-16-reset-empty.txt](real-16-reset-empty.txt) |
| 真实 13 | [real-13-turns.txt](real-13-turns.txt) |
| 真实 14 | [real-14-old-call.txt](real-14-old-call.txt) |
| 真实 15 | [real-15-clear-preserves.txt](real-15-clear-preserves.txt) |
| 真实 16 | [real-16-reset-empty.txt](real-16-reset-empty.txt) |

## 多行草稿与整块粘贴

| 序号／规格场景 | 自动化断言入口 | 本次PTY／真实模型证据与边界 |
| --- | --- | --- |
| 1. 粘贴代码和报错栈 | [test_terminal_input.py::test_bracketed_paste_never_submits_and_preserves_whitespace](../../../tests/test_terminal_input.py#L24) | 无本次专门 PTY／真实模型证据；单测验证 CRLF、空行、缩进、末尾换行与显式一次返回，不等于真实模型请求计数。 |
| 2. 手动编写多行需求 | [test_terminal_input.py::test_manual_newline_history_and_completion_edit_without_submit](../../../tests/test_terminal_input.py#L43)<br>[test_terminal_drafts.py::test_running_draft_enter_requires_fresh_idle_enter](../../../tests/test_terminal_drafts.py#L17) | PTY 01 使用 Esc+Enter 起草两行，但处于运行期；空闲手动两行提交由单测覆盖。 |
| 3. 重用先前输入 | [test_terminal_input.py::test_tab_really_completes_and_history_navigation_keeps_multiline_cursor](../../../tests/test_terminal_input.py#L146)<br>[test_terminal_input.py::test_manual_newline_history_and_completion_edit_without_submit](../../../tests/test_terminal_input.py#L43) | 本次没有真实历史重用操作；单测验证行内移动、边界历史选择及修改后提交。 |
| 4. 空白草稿 | [test_terminal_commands.py::test_parse_preserves_messages_and_recognizes_valid_commands](../../../tests/test_terminal_commands.py#L40)<br>[test_app.py::test_prompt_loop_accepts_two_turns_empty_input_and_exit](../../../tests/test_app.py#L32) | 本次没有真实空白提交；解析测试含空格／制表符／换行，应用测试验证空输入不增加请求。 |
| 5. 运行中起草并按回车 | [test_terminal_drafts.py::test_running_draft_enter_requires_fresh_idle_enter](../../../tests/test_terminal_drafts.py#L17) | PTY 01 及真实 10→11；真实草稿为单行，PTY 补足多行、光标与未完成输入 Future 断言。 |
| 6. 结束后明确提交一次 | [test_terminal_drafts.py::test_running_draft_enter_requires_fresh_idle_enter](../../../tests/test_terminal_drafts.py#L17) | PTY checks 2–3；真实 11→12 与 real-draft-audit.json：取消后工作任务数仍为2，新空闲 Enter 后第三轮仅回复 DRAFT_EXPLICIT_107。 |
| 7. 失败或取消仍保留追问 | [test_terminal_drafts.py::test_approval_and_cancel_preserve_unique_live_draft](../../../tests/test_terminal_drafts.py#L45)<br>[test_context_manual.py::test_enhanced_summary_phase_supports_cancel_and_preserves_draft](../../../tests/test_context_manual.py#L80) | 真实 10→11 覆盖运行命令取消和草稿保留，started.txt=READY107、延迟文件未出现；模型失败／请求限额时草稿保留未做本次完整真实端到端验证。 |
| 8. 自动接续不覆盖最新草稿 | [test_agent_terminal.py::test_auto_resume_suspends_receiver_but_keeps_editable_live_draft](../../../tests/test_agent_terminal.py#L34)<br>[test_terminal_drafts.py::test_approval_and_cancel_preserve_unique_live_draft](../../../tests/test_terminal_drafts.py#L45) | PTY 02 注入自动接续段并验证 save_draft 后编辑；真实模型未触发后台自动接续。 |

## 输入阶段隔离与兼容退化

| 序号／规格场景 | 自动化断言入口 | 本次PTY／真实模型证据与边界 |
| --- | --- | --- |
| 9. 粘贴内容不跨越输入阶段 | [test_terminal_drafts.py::test_running_paste_is_editable_but_cross_phase_paste_is_discarded](../../../tests/test_terminal_drafts.py#L81)<br>[test_terminal_input.py::test_paste_started_while_running_is_discarded_after_approval_transition](../../../tests/test_terminal_input.py#L100)<br>[test_terminal_input.py::test_paste_opener_split_across_phase_does_not_turn_payload_into_decisions](../../../tests/test_terminal_input.py#L240) | PTY 03（running→idle 分裂 opener）和05（审批隔离）；未在真实模型中执行该粘贴序列。 |
| 10. 多个授权答案的粘贴 | [test_terminal_input.py::test_paste_and_typeahead_cannot_approve_following_request](../../../tests/test_terminal_input.py#L73)<br>[test_terminal_input.py::test_stale_paste_and_fresh_paste_in_same_read_keep_separate_generations](../../../tests/test_terminal_input.py#L286) | PTY 05→06：残留零决定，本次明确2+Enter仅批准一次，同批3+Enter未批准下一请求；真实01→03仅覆盖分别明确批准，不覆盖多答案粘贴。 |
| 11. 管道与捕获输出 | [test_terminal_controller.py::test_redirected_output_uses_plain_and_preserves_input_protocol](../../../tests/test_terminal_controller.py#L109)<br>[test_permission_app.py::test_default_noninteractive_denies_tool_but_keeps_agent_loop](../../../tests/test_permission_app.py#L138)<br>[test_permission_app.py::test_injected_approval_responder_can_approve_without_stdin_channel](../../../tests/test_permission_app.py#L148) | 本次没有真实管道／重定向模型运行；单测覆盖plain无光标序列、非交互拒绝及注入可信审批。 |
| 12. 管道预读多个问题 | [test_terminal_app.py::test_pipe_preloaded_questions_survive_task_cancel](../../../tests/test_terminal_app.py#L29)<br>[test_terminal_controller.py::test_redirected_output_uses_plain_and_preserves_input_protocol](../../../tests/test_terminal_controller.py#L109) | 本次没有真实模型管道运行；应用单测使用真实os.pipe及供应商替身，验证取消后第二问及退出保留。 |
| 13. 取消后继续使用终端 | [test_terminal_drafts.py::test_approval_and_cancel_preserve_unique_live_draft](../../../tests/test_terminal_drafts.py#L45)<br>[test_permission_app.py::test_ctrl_c_at_approval_returns_to_prompt_and_accepts_next_question](../../../tests/test_permission_app.py#L204)<br>[test_app.py::test_real_sigint_during_stream_closes_stream_and_continues_input](../../../tests/test_app.py#L80)<br>[test_terminal_input.py::test_paste_guard_preserves_escape_timeout_and_restores_parser_on_close](../../../tests/test_terminal_input.py#L310) | 真实10→12覆盖工具取消后继续提交；PTY结果记录echo/canonical前后均true。未在本次真实模型中取消生成或审批，也未逐项采集所有异常退出光标状态。 |
| 14. MCP 启动诊断与启动取消 | [test_terminal_app.py::test_startup_interrupt_is_not_reset_before_mcp_start](../../../tests/test_terminal_app.py#L59)<br>[test_mcp_manager.py::test_startup_failure_isolation_no_partial_registration](../../../tests/test_mcp_manager.py#L99)<br>[test_mcp_manager.py::test_complete_discovery_or_zero_tools](../../../tests/test_mcp_manager.py#L124)<br>[test_mcp_integration.py::test_startup_sigint_closes_mcp_and_provider](../../../tests/test_mcp_integration.py#L173) | 本次真实模型未配置MCP服务器，未做增强终端MCP启动诊断／启动取消；已有测试分别覆盖失败隔离、无工具与取消清理，不构成完整终端诊断可见性验证。 |
| 15. MCP 授权 EOF 与关闭诊断 | [test_permission_app.py::test_approval_eof_cancels_then_exits_without_next_model_request](../../../tests/test_permission_app.py#L185)<br>[test_mcp_integration.py::test_scheduler_read_mcp_read_and_cancel_pairing](../../../tests/test_mcp_integration.py#L104)<br>[test_mcp_manager.py::test_unresponsive_owner_does_not_hold_other_cleanup_or_main_loop](../../../tests/test_mcp_manager.py#L193)<br>[test_terminal_input.py::test_actual_input_eof_during_running_cancels_without_waiting_for_readline](../../../tests/test_terminal_input.py#L192) | 本次没有MCP授权EOF／关闭失败PTY或真实模型验证；已有测试覆盖一般授权EOF、MCP取消结果配对和关闭未确认事件，各部分不能冒充MCP授权EOF完整应用链路已验证。 |
| 16. 有草稿时取消编辑 | [test_terminal_ui_keys.py::test_idle_ctrl_c_clears_even_whitespace_then_exits](../../../tests/test_terminal_ui_keys.py#L34) | 本次没有专门PTY／真实模型空闲Ctrl+C序列；单测含纯空白草稿清空后第二次退出。 |
| 17. 取消立即反馈且等待清理 | [test_terminal_drafts.py::test_approval_and_cancel_preserve_unique_live_draft](../../../tests/test_terminal_drafts.py#L45)<br>[test_context_manual.py::test_enhanced_summary_phase_supports_cancel_and_preserves_draft](../../../tests/test_context_manual.py#L80) | 真实11显示取消后已发生副作用可能存在；正在停止期间禁编辑／保留光标由单测验证。本次没有真实长清理、重复取消及所有阶段的立即反馈截图。 |
| 18. 详情被授权抢占 | [test_terminal_browser_keys.py::test_real_keys_layers_search_approval_and_draft_are_isolated](../../../tests/test_terminal_browser_keys.py#L19)<br>[test_terminal_ui_keys.py::test_details_preserves_draft_cursor_ignores_input_and_closes_on_approval](../../../tests/test_terminal_ui_keys.py#L53) | PTY04→05→06：F2搜索被审批抢占、残留零批准、之后重新打开恢复选择；本次真实模型没有浏览中收到新审批。 |
| 19. 运行期回车与结束事件相邻 | [test_terminal_drafts.py::test_running_draft_enter_requires_fresh_idle_enter](../../../tests/test_terminal_drafts.py#L17)<br>[test_agent_terminal.py::test_submitted_input_wins_when_backend_future_precedes_wrapper_task](../../../tests/test_agent_terminal.py#L62) | PTY01和checks2–3通过受控相邻事件验证旧Enter不完成新idle Future；真实10→12验证较宽时间窗口的运行Enter及明确重交，不能据此证明所有竞态时序。 |

## 流式正文可读性

| 序号／规格场景 | 自动化断言入口 | 本次PTY／真实模型证据与边界 |
| --- | --- | --- |
| 20. 围栏分片与中断 | [test_terminal_markdown.py::test_fenced_code_preserves_every_character_and_flush_resets_style](../../../tests/test_terminal_markdown.py#L68)<br>[test_terminal_markdown.py::test_markdown_styles_preserve_heading_list_code_diff_and_unclosed_fence](../../../tests/test_terminal_markdown.py#L8)<br>[test_terminal_controller.py::test_partial_answer_visible_live_and_committed_as_complete_line](../../../tests/test_terminal_controller.py#L128) | 本次真实模型没有围栏中断回复；解析及控制器单测分别覆盖未闭合围栏、字符保持和无尾换行实时预览。 |
| 21. 完成和尺寸调整不重复正文 | [test_terminal_controller.py::test_partial_answer_visible_live_and_committed_as_complete_line](../../../tests/test_terminal_controller.py#L128)<br>[test_terminal_browser_keys.py::test_resize_keeps_browser_selection_and_reachable_close](../../../tests/test_terminal_browser_keys.py#L77)<br>[test_terminal_browser.py::test_search_raw_and_logical_anchor_survive_resize_updates](../../../tests/test_terminal_browser.py#L121) | 真实04→08覆盖回答后开详情、raw及窄屏，历史自然折行可见；PTY09→10覆盖浏览逻辑锚点。完成只追加一次由控制器单测断言，截图本身不证明所有正文去重。 |
| 22. 不可信控制内容 | [test_terminal_markdown.py::test_split_secrets_controls_and_raw_markdown_remain_safe_in_plain](../../../tests/test_terminal_markdown.py#L23)<br>[test_terminal_state.py::test_status_safely_escapes_every_untrusted_display_field](../../../tests/test_terminal_state.py#L344)<br>[test_terminal_result_view.py::test_raw_and_formatted_views_redact_decoded_json_and_controls](../../../tests/test_terminal_result_view.py#L48) | 本次没有向真实模型／PTY注入ANSI、OSC、HTML及伪造角色攻击；单测覆盖安全文本与脱敏，不声称所有恶意来源端到端通过。 |
| 23. 粗体和行内代码分片 | [test_terminal_markdown.py::test_complete_inline_formatting_removes_delimiters_with_distinct_styles](../../../tests/test_terminal_markdown.py#L44)<br>[test_terminal_markdown.py::test_inline_preview_is_raw_and_does_not_commit_partial_delimiters](../../../tests/test_terminal_markdown.py#L52)<br>[test_terminal_markdown.py::test_inline_escaping_identifiers_and_code_are_not_reinterpreted](../../../tests/test_terminal_markdown.py#L61) | 真实04正文无已识别粗体／行内代码定界符；tmux纯文本capture不保留字体属性，具体分片和样式断言由单测提供。 |
| 24. 中文长表格与窄屏 | [test_terminal_markdown.py::test_wide_table_wraps_chinese_and_long_paths_without_truncation](../../../tests/test_terminal_markdown.py#L109)<br>[test_terminal_markdown.py::test_table_handles_escaped_and_code_pipes_without_losing_column_links](../../../tests/test_terminal_markdown.py#L100)<br>[test_terminal_markdown.py::test_table_resize_changes_only_subsequent_rows_to_labelled_fields](../../../tests/test_terminal_markdown.py#L123) | 真实04为中文三列表格并折行；[最终真实17](real-17-final-long-response.txt)为12行长表格和缩进代码，真实08为完成后历史自然折行；未做真实模型表格流中缩屏，转字段、竖线及后续行不重印由单测验证。 |
| 25. plain 输出保留原始格式 | [test_terminal_markdown.py::test_split_secrets_controls_and_raw_markdown_remain_safe_in_plain](../../../tests/test_terminal_markdown.py#L23)<br>[test_terminal_controller.py::test_redirected_output_uses_plain_and_preserves_input_protocol](../../../tests/test_terminal_controller.py#L109) | 本次没有相同真实回复的plain对照；单测验证Markdown原文、安全处理及无ANSI。 |

## 最近任务详情浏览

| 序号／规格场景 | 自动化断言入口 | 本次PTY／真实模型证据与边界 |
| --- | --- | --- |
| 26. 聚合后查看调用 | [test_terminal_projection.py::test_success_batch_flushes_before_answer_and_deduplicates](../../../tests/test_terminal_projection.py#L22)<br>[test_terminal_projection.py::test_limited_success_and_warning_remain_individual_between_batches](../../../tests/test_terminal_projection.py#L45)<br>[test_terminal_browser.py::test_default_selection_layer_returns_and_reopening_keep_identity](../../../tests/test_terminal_browser.py#L81)<br>[test_terminal_result_view.py::test_file_and_command_keep_copyable_body](../../../tests/test_terminal_result_view.py#L13) | 真实04聚合3个文件，05→07对应调用列表、文件正文及raw，01→03提供授权身份；异常类型主对话独立可见由单测覆盖，真实只额外覆盖命令取消。 |
| 27. 浏览不损失草稿 | [test_terminal_browser_keys.py::test_real_keys_layers_search_approval_and_draft_are_isolated](../../../tests/test_terminal_browser_keys.py#L19)<br>[test_terminal_browser.py::test_search_raw_and_logical_anchor_survive_resize_updates](../../../tests/test_terminal_browser.py#L121) | PTY04→06及11覆盖浏览／审批边界草稿保留，PTY09→10覆盖翻页与缩放；真实05→08覆盖F2详情但当时为空草稿，不能据此证明真实非空草稿浏览。 |
| 28. 详情容量不足 | [test_terminal_history.py::test_cross_turn_budget_evicts_oldest_finished_body_before_current](../../../tests/test_terminal_history.py#L116)<br>[test_terminal_history.py::test_shared_body_view_and_page_caches_are_bounded_and_invalidated](../../../tests/test_terminal_history.py#L148)<br>[test_terminal_browser.py::test_actual_history_tiny_cache_retains_reference_and_eviction_reason](../../../tests/test_terminal_browser.py#L238)<br>[test_terminal_history.py::test_metadata_limit_covers_thinking_and_long_ids_and_evicted_calls_stay_absent](../../../tests/test_terminal_history.py#L176) | PTY08在真实8MiB预算下注入正文触发淘汰；单测采用缩小限额验证单条／缓存与元信息边界。真实模型未生成8MiB／64KiB／256KiB上限材料。 |
| 29. 重置清屏与维护 | [test_terminal_app.py::test_work_turn_titles_clear_retention_and_reset_are_app_lifecycle](../../../tests/test_terminal_app.py#L73)<br>[test_terminal_history.py::test_background_status_and_maintenance_notices_do_not_start_turns](../../../tests/test_terminal_history.py#L302)<br>[test_context_manual.py::test_enhanced_manual_events_keep_recent_work_record](../../../tests/test_context_manual.py#L64) | 真实13→15可在三轮后及clear后查看首轮文件详情，16在reset后显示暂无轮次；real-audit.json记录本地浏览及命令新增工作轮为0。真实04自动记忆提取超时是维护提示；后台维护无新轮及思考不落盘还需单测／既有会话持久化合同，不能由截图单独证明。 |
| 30. 命令输出按正文查看 | [test_terminal_result_view.py::test_file_and_command_keep_copyable_body](../../../tests/test_terminal_result_view.py#L13)<br>[test_terminal_browser.py::test_search_raw_and_logical_anchor_survive_resize_updates](../../../tests/test_terminal_browser.py#L121)<br>[test_terminal_result_view.py::test_invalid_result_and_eviction_retain_reason_and_raw_fragment](../../../tests/test_terminal_result_view.py#L59) | 真实07仅为read_file原始JSON；真实第二轮命令被取消，未获取多行stdout/stderr详情。正文分区、退出码与raw往返位置由单测验证。 |
| 31. 搜索与异常定位 | [test_terminal_browser.py::test_list_search_exception_filter_and_unavailable_notice](../../../tests/test_terminal_browser.py#L103)<br>[test_terminal_browser.py::test_actual_history_view_budget_raw_search_and_runtime_stay_independent](../../../tests/test_terminal_browser.py#L221)<br>[test_terminal_browser.py::test_exception_filter_includes_incomplete_argument_snapshot](../../../tests/test_terminal_browser.py#L252) | PTY04执行搜索、08显示被移出正文；真实模型未执行异常筛选或无匹配搜索。单测禁止Path.read_text并验证保留快照搜索、异常及无匹配提示。 |
| 32. 追问后查看上一轮 | [test_terminal_history.py::test_two_turns_retain_single_snapshots_and_distinct_call_identity](../../../tests/test_terminal_history.py#L27)<br>[test_terminal_browser_keys.py::test_real_keys_layers_search_approval_and_draft_are_isolated](../../../tests/test_terminal_browser_keys.py#L19)<br>[test_terminal_browser.py::test_selected_call_position_restores_after_switching_rounds](../../../tests/test_terminal_browser.py#L196) | 真实13展示三轮列表，14在第三轮结束后重新查看首轮文件详情；第二轮仍运行时切换上一轮由真实按键单测覆盖，本次模型capture未记录该运行中组合。 |
| 33. 十一个已结束任务 | [test_terminal_history.py::test_retention_reset_and_incomplete_segment_close](../../../tests/test_terminal_history.py#L99)<br>[test_terminal_browser.py::test_evicted_body_and_removed_turn_do_not_silently_jump](../../../tests/test_terminal_browser.py#L147) | PTY07检查11个已结束轮次后保留10个、旧选择移出提示并选最近轮；真实模型总共4个工作轮（两个会话），未真实生成11轮。 |
| 34. 浏览位置不被更新抢占 | [test_terminal_browser.py::test_search_raw_and_logical_anchor_survive_resize_updates](../../../tests/test_terminal_browser.py#L121)<br>[test_terminal_history.py::test_confirmed_late_result_only_updates_old_record_without_current_statistics](../../../tests/test_terminal_history.py#L79)<br>[test_terminal_browser.py::test_selected_call_position_restores_after_switching_rounds](../../../tests/test_terminal_browser.py#L196) | PTY08→10验证选中旧调用未跳转、当前运行段未改、逻辑锚点在45×15／18×6／110×34保持；自动接续和后台通知同时到达的完整真实链路未执行。 |
| 35. 历史浏览不改变当前状态 | [test_terminal_history.py::test_two_turns_retain_single_snapshots_and_distinct_call_identity](../../../tests/test_terminal_history.py#L27)<br>[test_terminal_browser.py::test_actual_history_view_budget_raw_search_and_runtime_stay_independent](../../../tests/test_terminal_browser.py#L221) | PTY08浏览已结束旧轮时state仍为当前model；旧轮取消而当前运行的精确组合由单测覆盖，本次真实模型未采集该组合。 |
| 36. 自动接续来源可辨 | [test_terminal_history.py::test_child_same_id_and_auto_resume_have_explicit_source](../../../tests/test_terminal_history.py#L62)<br>[test_agent_terminal.py::test_auto_resume_suspends_receiver_but_keeps_editable_live_draft](../../../tests/test_agent_terminal.py#L34)<br>[test_agent_terminal.py::test_parent_final_status_is_visible_separately_from_segment](../../../tests/test_agent_terminal.py#L89) | PTY02注入自动接续段验证新编辑不覆盖；来源／父身份及父最终状态区分由单测覆盖；真实模型未触发自动接续。 |
| 37. 调用索引也有界 | [test_terminal_history.py::test_call_metadata_and_current_active_index_are_bounded](../../../tests/test_terminal_history.py#L132)<br>[test_terminal_history.py::test_1500_distinct_calls_are_counted_exactly_with_bounded_tombstones](../../../tests/test_terminal_history.py#L212)<br>[test_terminal_history.py::test_identity_budget_saturation_is_explicit_and_reset_preserves_browser_reference](../../../tests/test_terminal_history.py#L229)<br>[test_terminal_browser.py::test_saturated_identity_index_displays_lower_bound_and_omitted_events](../../../tests/test_terminal_browser.py#L11)<br>[test_terminal_history.py::test_index_saturation_keeps_independent_failure_visible](../../../tests/test_terminal_history.py#L247) | 本次没有PTY或真实模型调用索引饱和；单测验证1500身份精确去重、饱和后数量下界／省略事件提示及失败仍独立可见。 |

## 常驻工作上下文与阶段提示

| 序号／规格场景 | 自动化断言入口 | 本次PTY／真实模型证据与边界 |
| --- | --- | --- |
| 38. 输入前辨认工作上下文 | [test_terminal_context.py::test_context_uses_effective_session_and_sanitizes_model_and_root](../../../tests/test_terminal_context.py#L13)<br>[test_terminal_context.py::test_context_shortens_middle_of_directory_preserving_project_name](../../../tests/test_terminal_context.py#L32)<br>[test_terminal_commands.py::test_help_lists_full_commands_and_phase_specific_controls](../../../tests/test_terminal_commands.py#L52) | 真实04及12在空闲显示ark-code-latest、临时目录、DEFAULT、strict和发送／换行／F2提示；空闲无旧轮耗时、请求数。 |
| 39. 窄屏仍能判断授权状态 | [test_terminal_ui_keys.py::test_narrow_approval_keeps_navigation_and_decisions_visible](../../../tests/test_terminal_ui_keys.py#L112)<br>[test_terminal_input.py::test_narrow_review_header_shows_page_before_long_request_id](../../../tests/test_terminal_input.py#L395)<br>[test_terminal_input.py::test_small_review_pages_do_not_skip_lines_hidden_by_other_regions](../../../tests/test_terminal_input.py#L208) | 真实01→03为宽屏审批，08为窄屏详情；本次没有真实窄屏审批。单测覆盖45列与小高度审阅／决定入口；PTY10覆盖窄／极小详情而非审批。 |
| 40. 运行提示与草稿行为一致 | [test_terminal_drafts.py::test_running_draft_enter_requires_fresh_idle_enter](../../../tests/test_terminal_drafts.py#L17)<br>[test_context_manual.py::test_enhanced_summary_phase_supports_cancel_and_preserves_draft](../../../tests/test_context_manual.py#L80) | 真实10显示可起草、Enter不发送、F2与取消；PTY01补足多行，summary／压缩由单测覆盖，本次未真实手动压缩。 |
| 41. 切换和清屏刷新 | [test_terminal_context.py::test_context_uses_effective_session_and_sanitizes_model_and_root](../../../tests/test_terminal_context.py#L13)<br>[test_terminal_app.py::test_work_turn_titles_clear_retention_and_reset_are_app_lifecycle](../../../tests/test_terminal_app.py#L73)<br>[test_permission_app.py::test_permission_commands_do_not_request_model_or_change_plan_history](../../../tests/test_permission_app.py#L121)<br>[test_terminal_app.py::test_escaped_slash_is_a_message_and_status_keeps_mode](../../../tests/test_terminal_app.py#L47) | 真实15覆盖clear后重新打开第一轮详情，当前仍为DEFAULT／strict／空闲；本次没有plan／execute及权限切换的完整组合。单测分别验证真实会话配置同步、本地命令无模型请求与clear历史保留。 |

## 最终增量验证

全量之后帮助／列表字段优先级的最终交互定向252项通过；思考来源安全标签相同仍能分别选中这一增量修复相关45项通过，并获独立复查。轮次列表长标题挤掉状态的问题已修复，最后实机捕获见 [110列](real-19-final-turn-list.txt)、[45列](real-20-final-turn-list-45.txt)。最终真实长回复见 [12行表格与缩进代码](real-17-final-long-response.txt)，工作迭代2次且read_file1次。观察窗25秒内未出现表格，模型115.7秒后完成；未完成该轮流中缩屏，不以完成后缩屏代替。
