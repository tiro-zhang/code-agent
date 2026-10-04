"""终端安全文字与事件状态合同。"""

from mewcode.types import AgentEvent, TokenUsage, ToolCall
from mewcode.tools.base import ToolResult
from mewcode.permissions.runtime import ApprovalRequest


def state_for(run_id="run-a"):
    from mewcode.terminal.state import TerminalState

    state = TerminalState(secret="private-key")
    state.update(AgentEvent("progress", run_id=run_id, iteration=1, phase="model", max_iterations=7))
    return state


def tool_event(kind, identifier="call-one", *, run_id="run-a", name="read_file", **fields):
    return AgentEvent(kind, run_id=run_id, iteration=1, tool_call_id=identifier,
                      tool_name=name, **fields)


def status(state):
    return state.status_text(root="/项目", model="test-model", mode="plan",
                             permission_mode="strict", has_pending_plan=True)


def test_terminal_text_redacts_before_escaping_and_limiting():
    from mewcode.terminal.text import terminal_text

    text = terminal_text("密钥 abc\x1b[31m\r\n\t末尾", "abc", multiline=True)
    assert "abc" not in text
    assert "[已隐藏]" in text
    assert "\x1b" not in text and "\r" not in text
    assert "\\x1b[31m\\r\n\t" in text
    assert terminal_text("abc123", "abc", limit=6) == "[已隐藏]1…"
    assert terminal_text("a\nb\t", "") == "a\\nb\\t"


def test_usage_text_keeps_unknown_partial_and_cache_write():
    from mewcode.terminal.text import usage_text

    text = usage_text(TokenUsage(200, 9, True, 800, 15, 1000, 200, True))
    assert "总输入 1000" in text and "命中率 80.0%" in text
    assert "写入 15" in text
    text = usage_text(TokenUsage(100, incomplete_fields=frozenset({"input_tokens"})))
    assert "总输入 未知（基础输入 100（部分））" in text
    assert "输出 未知" in text and "写入 未知" in text
    assert "统计不完整" in text and "缓存统计不完整" in text


def test_empty_status_reports_no_task_without_inventing_usage():
    from mewcode.terminal.state import TerminalState

    text = status(TerminalState())
    assert "暂无任务记录" in text
    assert "/项目" in text and "test-model" in text
    assert "规划" in text and "strict" in text and "可用" in text
    assert "Token 0" not in text


def test_tools_have_stable_number_across_model_requests_and_out_of_order_results():
    state = state_for()
    for identifier, path in (("prefix-one", "one.py"), ("prefix-two", "two.py")):
        assert state.update(tool_event("tool_call", identifier,
                                      call=ToolCall(identifier, "read_file", '{"path":"' + path + '"}'))) == []
    assert "#1" in state.active_lines()[0] and "one.py" in state.active_lines()[0]
    assert "#2" in state.active_lines()[1]
    state.update(AgentEvent("progress", run_id="run-a", iteration=2, phase="model"))
    lines = state.update(tool_event("tool_result", "prefix-two", result=ToolResult.success({})))
    assert len(lines) == 1 and "#2" in lines[0] and "成功" in lines[0]
    assert state.update(tool_event("tool_result", "prefix-two", result=ToolResult.success({}))) == []
    assert state.update(tool_event("tool_started", "prefix-two")) == []
    assert len(state.active_lines()) == 1 and "#1" in state.active_lines()[0]
    text = status(state)
    assert "#1" in text and "prefix-one" in text
    assert "#2" in text and "prefix-two" in text
    assert state.iteration == 2


def test_result_before_call_does_not_regress_terminal_state():
    state = state_for()
    lines = state.update(tool_event("tool_result", result=ToolResult.failure("timeout", "工具执行超时")))
    assert len(lines) == 1 and "超时" in lines[0]
    state.update(tool_event("tool_call", call=ToolCall("call-one", "read_file", '{"path":"late.py"}')))
    state.update(tool_event("tool_started"))
    assert state.active_lines() == []
    assert "late.py" in status(state) and "超时" in status(state)


def test_new_task_resets_numbers_and_ignores_old_run_events():
    state = state_for()
    state.update(tool_event("tool_started"))
    state.update(AgentEvent("progress", run_id="run-b", iteration=1, phase="model"))
    state.update(tool_event("tool_call", "call-two", run_id="run-b"))
    assert "#1" in state.active_lines()[0]
    assert state.update(tool_event("tool_result", result=ToolResult.success({}))) == []
    state.update(AgentEvent("progress", run_id="run-a", iteration=1, phase="model"))
    assert len(state.active_lines()) == 1 and "call-one" not in status(state)


def test_usage_records_are_per_request_and_finished_total_is_authoritative():
    state = state_for()
    state.update(AgentEvent("usage", run_id="run-a", iteration=1,
                            usage=TokenUsage(100, 2, True, 30, 4, 100, 70, True)))
    state.update(AgentEvent("usage", run_id="run-a", iteration=1,
                            usage=TokenUsage(100, 2, True, 30, 4, 100, 70, True)))
    state.update(AgentEvent("usage", run_id="run-a", iteration=2, usage=TokenUsage(50)))
    partial_total = TokenUsage(150, 2, False, 30, 4, 100, 70, False,
                               frozenset({"output_tokens", "cache_read_tokens", "cache_write_tokens"}))
    state.update(AgentEvent("finished", run_id="run-a", iteration=2, reason="stream_error",
                            text="统计尚未完整", usage=partial_total))
    text = status(state)
    assert text.count("请求 1 ·") == 1 and text.count("请求 2 ·") == 1
    assert "总输入 100" in text and "总输入 未知（基础输入 50）" in text
    assert "累计" in text and "写入 4（部分）" in text
    assert "stream_error" in text and "统计尚未完整" in text
    summary = state.summary()
    assert "最近" in summary and "累计" in summary and "写入 4（部分）" in summary
    assert "统计不完整" in summary
    state.update(AgentEvent("usage", run_id="run-a", iteration=2,
                            usage=TokenUsage(999, 99, True, 100, 90, 999, 899, True)))
    cumulative = state.summary().splitlines()[2]
    assert "写入 4（部分）" in cumulative and "写入 90" not in cumulative


def test_running_summary_sums_only_known_fields_without_inventing_missing_counts():
    state = state_for()
    state.update(AgentEvent("usage", run_id="run-a", iteration=1, usage=TokenUsage(15, 3)))
    cumulative = state.summary().splitlines()[2]
    assert "基础输入 15" in cumulative and "出3" in cumulative
    assert "命中 未知" in cumulative and "写入 未知" in cumulative
    assert "统计不完整" in cumulative and "缓存统计不完整" in cumulative
    assert "累计已知 Token · 总输入 未知（基础输入 15），输出 3" in status(state)


def test_running_cumulative_usage_deduplicates_and_replaces_each_iteration():
    state = state_for()
    first = TokenUsage(100, 2, True, 30, 4, 100, 70, True)
    updated = TokenUsage(120, 3, True, 40, 5, 120, 80, True)
    second = TokenUsage(50, 1, True, 10, 2, 50, 40, True)
    for usage in (first, first, updated):
        state.update(AgentEvent("usage", run_id="run-a", iteration=1, usage=usage))
    state.update(AgentEvent("usage", run_id="run-a", iteration=2, usage=second))
    cumulative = state.summary().splitlines()[2]
    assert "入170 出4" in cumulative
    assert "命中 50" in cumulative and "未命中 120" in cumulative and "写入 7" in cumulative
    assert "命中率29.4%" in cumulative and "完整含缓存" in cumulative
    assert "累计已知 Token · 总输入 170，输出 4" in status(state)


def test_running_cumulative_marks_missing_requests_until_late_usage_arrives():
    state = state_for()
    state.update(AgentEvent("progress", run_id="run-a", iteration=3, phase="model"))
    state.update(AgentEvent("usage", run_id="run-a", iteration=3,
                            usage=TokenUsage(300, 30, True, 100, 3, 300, 200, True)))
    cumulative = state.summary().splitlines()[2]
    assert "入300（部分） 出30（部分）" in cumulative
    assert "请求用量缺失 1、2" in cumulative and "统计不完整" in cumulative
    assert "命中率未知" in cumulative
    assert "请求 1 · 用量缺失" in status(state) and "请求 2 · 用量缺失" in status(state)
    state.update(AgentEvent("usage", run_id="run-a", iteration=1,
                            usage=TokenUsage(100, 10, True, 20, 1, 100, 80, True)))
    state.update(AgentEvent("usage", run_id="run-a", iteration=2,
                            usage=TokenUsage(200, 20, True, 40, 2, 200, 160, True)))
    cumulative = state.summary().splitlines()[2]
    assert "入600 出60" in cumulative and "写入 6" in cumulative
    assert "请求用量缺失" not in cumulative and "完整含缓存" in cumulative


def test_running_cumulative_retains_partial_cache_fields_from_supplied_usage():
    state = state_for()
    state.update(AgentEvent("usage", run_id="run-a", iteration=1,
                            usage=TokenUsage(100, 2, True, 40, 5, 100, 60, True)))
    state.update(AgentEvent("usage", run_id="run-a", iteration=2,
                            usage=TokenUsage(50, 1, True, None, None, None, None, False)))
    cumulative = state.summary().splitlines()[2]
    assert "入100（部分） 出3" in cumulative
    assert "命中 40（部分）" in cumulative and "写入 5（部分）" in cumulative
    assert "缓存统计不完整" in cumulative and "命中率未知" in cumulative


def test_finished_usage_overrides_running_total_even_when_none_and_late_usage_arrives():
    state = state_for()
    state.update(AgentEvent("usage", run_id="run-a", iteration=1,
                            usage=TokenUsage(100, 2, True, 40, 5, 100, 60, True)))
    assert "入100 出2" in state.summary().splitlines()[2]
    state.update(AgentEvent("finished", run_id="run-a", iteration=1, reason="model_done", usage=None))
    cumulative = state.summary().splitlines()[2]
    assert "未知" in cumulative and "入100" not in cumulative
    state.update(AgentEvent("usage", run_id="run-a", iteration=1,
                            usage=TokenUsage(120, 3, True, 40, 5, 120, 80, True)))
    assert "未知" in state.summary().splitlines()[2] and "入120" not in state.summary().splitlines()[2]
    assert "请求 1 · 总输入 120" in status(state)


def test_permission_waiting_is_not_tool_started_and_warning_is_visible_once():
    state = state_for()
    request = ApprovalRequest("request-one", "read_file", {"path": "a"}, ("a",), "需批准", "default")
    event = tool_event("permission_requested", permission_request=request)
    state.update(event)
    state.update(event)
    assert len(state.active_lines()) == 1 and "等待授权" in state.active_lines()[0]
    assert "执行中" not in state.active_lines()[0]
    assert "等待授权" in state.summary()
    resolved = tool_event("permission_resolved", permission_request=request,
                          permission_decision="permanent", warning="永久批准保存失败 private-key")
    lines = state.update(resolved)
    assert len(lines) == 1 and "本次批准" in lines[0] and "永久" in lines[0]
    assert "private-key" not in lines[0]
    assert state.update(resolved) == []
    state.update(event)
    assert "等待授权" not in state.active_lines()[0]
    assert "永久批准保存失败" in status(state)


def test_permission_denial_remains_visible_in_tool_result():
    state = state_for()
    state.update(tool_event("permission_resolved", permission_decision="deny"))
    lines = state.update(tool_event("tool_result", result=ToolResult.failure(
        "permission_denied", "用户拒绝", details={"not_started": True})))
    assert len(lines) == 1 and "权限拒绝" in lines[0] and "未启动" in lines[0]


def test_limited_and_truncated_success_remains_visible():
    state = state_for()
    lines = state.update(tool_event("tool_result", result=ToolResult.success(
        {"permission_limited": True, "skipped_files": 3}, truncated=True)))
    assert "成功" in lines[0] and "输出已截断" in lines[0]
    assert "搜索范围受限" in lines[0] and "3" in lines[0]
    assert "输出已截断" in status(state)


def test_mcp_cancel_timeout_and_unavailable_preserve_remote_risk_and_identity():
    state = state_for()
    name = "mcp__server_a__write__deadbeef"
    for index, code in enumerate(("cancelled", "timeout", "mcp_unavailable")):
        details = {"side_effects_may_have_occurred": True} if index < 2 else {"not_started": True}
        lines = state.update(tool_event("tool_result", f"external-{index}", name=name,
                                      result=ToolResult.failure(code, "Server 连接失败", details=details)))
        assert name in lines[0] and code in lines[0]
        if index < 2:
            assert "远端可能仍在执行或已有副作用" in lines[0]
        else:
            assert "未启动" in lines[0]


def test_finished_clears_active_flags_but_preserves_tool_details():
    state = state_for()
    state.update(tool_event("tool_started"))
    state.update(AgentEvent("finished", run_id="run-a", iteration=1, reason="cancelled",
                            text="用户取消任务", usage=TokenUsage()))
    assert state.active_lines() == []
    assert "call-one" in status(state) and "cancelled" in status(state)
    assert "终态未知" in status(state)
    state.update(tool_event("tool_started"))
    assert state.active_lines() == []


def test_older_permission_resolution_does_not_clear_newer_pending_request():
    state = state_for()
    first = ApprovalRequest("request-one", "read_file", {}, (), "需批准", "default")
    second = ApprovalRequest("request-two", "read_file", {}, (), "复检", "default")
    state.update(tool_event("permission_requested", permission_request=first))
    state.update(tool_event("permission_resolved", permission_request=first, permission_decision="once"))
    state.update(tool_event("permission_requested", permission_request=second))
    state.update(tool_event("permission_resolved", permission_request=first, permission_decision="once"))
    assert "等待授权" in state.active_lines()[0] and "等待授权" in state.summary()
    assert "request-two" in status(state)


def test_duplicate_older_permission_request_does_not_replace_current_request():
    state = state_for()
    first = ApprovalRequest("request-one", "read_file", {}, (), "需批准", "default")
    second = ApprovalRequest("request-two", "read_file", {}, (), "复检", "default")
    state.update(tool_event("permission_requested", permission_request=first))
    state.update(tool_event("permission_requested", permission_request=second))
    state.update(tool_event("permission_requested", permission_request=first))
    assert "request-two" in status(state) and "request-one" not in status(state)


def test_recent_run_guard_has_bounded_memory_and_rejects_late_recent_runs():
    state = state_for()
    for number in range(100):
        state.update(AgentEvent("progress", run_id=f"task-{number}", iteration=1, phase="model"))
    assert len(state._seen_runs) <= 16
    state.update(AgentEvent("progress", run_id="task-98", iteration=1, phase="tools"))
    assert "最近任务> task-99" in status(state)


def test_status_contains_full_cumulative_usage_independent_of_compact_summary():
    state = state_for()
    state.update(AgentEvent("finished", run_id="run-a", reason="model_done",
                            usage=TokenUsage(200, 9, True, 800, 15, 1000, 200, True)))
    assert "累计已知 Token · 总输入 1000，输出 9 · 统计完整" in status(state)
    assert "命中 800，未命中 200，写入 15，命中率 80.0% · 缓存统计完整" in status(state)


def test_summary_reduces_usage_length_without_discarding_cache_fields():
    from mewcode.terminal.text import usage_text

    state = state_for()
    usage = TokenUsage(200, 9, True, 800, 15, 1000, 200, True)
    state.update(AgentEvent("usage", run_id="run-a", iteration=1, usage=usage))
    recent = state.summary().splitlines()[1].split(" · ", 1)[1]
    assert len(recent) <= len(usage_text(usage)) - 15
    assert "命中 800" in recent and "未命中 200" in recent and "写入 15" in recent
    assert "80.0%" in recent and "完整" in recent


def test_late_progress_and_usage_do_not_regress_latest_request_summary():
    state = state_for()
    state.update(AgentEvent("progress", run_id="run-a", iteration=3, phase="model", max_iterations=7))
    state.update(AgentEvent("usage", run_id="run-a", iteration=3, usage=TokenUsage(300, 30)))
    state.update(AgentEvent("progress", run_id="run-a", iteration=1, phase="tools", max_iterations=7))
    state.update(AgentEvent("usage", run_id="run-a", iteration=1, usage=TokenUsage(100, 10)))
    assert state.iteration == 3 and state.phase == "model"
    assert "基础输入 300" in state.summary() and "基础输入 100" not in state.summary()
    assert "请求 1 ·" in status(state) and "请求 3 ·" in status(state)


def test_same_full_call_id_is_independent_in_a_new_task():
    state = state_for()
    state.update(tool_event("tool_result", result=ToolResult.success({})))
    state.update(AgentEvent("finished", run_id="run-a", reason="model_done", usage=TokenUsage()))
    state.update(AgentEvent("progress", run_id="run-b", iteration=1, phase="model"))
    state.update(tool_event("tool_started", run_id="run-b"))
    assert len(state.active_lines()) == 1 and "执行中" in state.active_lines()[0]
    lines = state.update(tool_event("tool_result", run_id="run-b", result=ToolResult.success({})))
    assert len(lines) == 1 and "#1" in lines[0]


def test_elapsed_is_monotonic_and_frozen_after_finish(monkeypatch):
    import mewcode.terminal.state as state_module

    ticks = iter((10.0, 12.5, 11.0, 15.0, 100.0))
    monkeypatch.setattr(state_module.time, "monotonic", lambda: next(ticks))
    state = state_for()
    assert state.elapsed == 2.5
    assert state.elapsed == 2.5
    state.update(AgentEvent("finished", run_id="run-a", reason="model_done", usage=TokenUsage()))
    assert state.elapsed == 5.0
    assert state.elapsed == 5.0


def test_status_safely_escapes_every_untrusted_display_field():
    state = state_for()
    state.update(tool_event("tool_call", "id\x1bprivate-key", name="tool\rprivate-key",
                            call=ToolCall("id\x1bprivate-key", "tool\rprivate-key",
                                          '{"path":"private-key\\u001b[31m"}')))
    state.update(AgentEvent("finished", run_id="run-a", reason="stream_error",
                            text="private-key\x1b[31m", usage=TokenUsage()))
    text = state.status_text(root="/private-key\r", model="private-key\x1b", mode="execute",
                             permission_mode="private-key\x1b", has_pending_plan=False)
    assert "private-key" not in text and "\x1b" not in text and "\r" not in text
    assert "[已隐藏]" in text and "\\x1b" in text and "\\r" in text


def test_bound_mcp_identity_is_visible_without_permission_events():
    from mewcode.mcp.tools import MCPTool, tool_alias

    state = state_for()
    server = "完整的 Server/private-key\x1b"
    original = "完整的工具原名/private-key\r"
    alias = tool_alias(server, original)
    state.bind_tools((MCPTool(alias, "外部工具", {"type": "object"}, server, original, "fp"),))
    state.update(tool_event("tool_started", name=alias))
    active = state.active_lines()[0]
    assert "Server 完整的 Server/[已隐藏]\\x1b" in active
    assert "原始工具 完整的工具原名/[已隐藏]\\r" in active
    lines = state.update(tool_event("tool_result", name=alias, result=ToolResult.failure(
        "mcp_unavailable", "所属 MCP Server 不可用", details={"not_started": True})))
    assert "Server 完整的 Server/[已隐藏]\\x1b" in lines[0]
    assert "原始工具 完整的工具原名/[已隐藏]\\r" in status(state)
    assert "private-key" not in status(state) and "\x1b" not in status(state)


def test_bound_identity_survives_task_reset_and_rebinding_removes_stale_tools():
    from mewcode.mcp.tools import MCPTool

    state = state_for()
    tool = MCPTool("mcp__alias", "外部工具", {}, "server-full", "original-tool", "fp")
    state.bind_tools((tool,))
    state.begin_task(mode="execute", permission_mode="bypass", max_iterations=5)
    state.update(tool_event("tool_started", run_id="run-b", name="mcp__alias"))
    assert "server-full" in state.active_lines()[0] and "original-tool" in state.active_lines()[0]
    state.bind_tools(())
    state.begin_task(mode="execute", permission_mode="bypass", max_iterations=5)
    state.update(tool_event("tool_started", run_id="run-c", name="mcp__alias"))
    assert "server-full" not in state.active_lines()[0]


def test_explicit_task_start_replaces_previous_success_when_first_event_is_cancelled():
    state = state_for()
    state.update(tool_event("tool_result", result=ToolResult.success({})))
    state.update(AgentEvent("finished", run_id="run-a", iteration=1, reason="model_done",
                            usage=TokenUsage(100, 10)))
    state.begin_task(mode="plan", permission_mode="strict", max_iterations=9)
    assert "run-a" not in status(state) and "call-one" not in status(state)
    state.update(AgentEvent("finished", run_id="run-b", iteration=0, mode="plan",
                            permission_mode="strict", max_iterations=9, reason="cancelled",
                            text="用户预取消任务", usage=TokenUsage()))
    assert state.run_id == "run-b" and state.iteration == 0 and state.max_iterations == 9
    assert state.mode == "plan" and state.permission_mode == "strict"
    assert "cancelled" in state.summary() and "model_done" not in state.summary()
    assert "用户预取消任务" in status(state) and state.active_lines() == []


def test_task_reset_rejects_previous_run_before_first_new_event():
    state = state_for()
    state.begin_task(mode="plan", permission_mode="strict", max_iterations=9)
    assert state.update(tool_event("tool_result", result=ToolResult.success({}))) == []
    state.update(AgentEvent("progress", run_id="run-a", iteration=2, phase="model"))
    assert state.run_id is None and state.active_lines() == []
    assert state.mode == "plan" and state.permission_mode == "strict" and state.max_iterations == 9
    state.update(AgentEvent("finished", run_id="run-b", mode="plan", permission_mode="strict",
                            max_iterations=9, reason="cancelled", usage=TokenUsage()))
    state.update(AgentEvent("finished", run_id="run-a", reason="model_done", usage=TokenUsage(100, 10)))
    assert state.run_id == "run-b" and "cancelled" in state.summary()


def test_first_non_progress_event_sets_actual_modes_and_request_limit():
    from mewcode.terminal.state import TerminalState

    state = TerminalState()
    state.update(AgentEvent("finished", run_id="cancelled-first", mode="plan", permission_mode="strict",
                            max_iterations=4, reason="cancelled", usage=TokenUsage()))
    assert state.mode == "plan" and state.permission_mode == "strict" and state.max_iterations == 4
    assert "规划" in state.summary() and "strict" in state.summary() and "请求 0/4" in state.summary()


def test_explicit_task_elapsed_starts_before_first_event_and_freezes(monkeypatch):
    import mewcode.terminal.state as state_module

    ticks = iter((10.0, 12.0, 15.0))
    monkeypatch.setattr(state_module.time, "monotonic", lambda: next(ticks))
    state = state_module.TerminalState()
    state.begin_task(mode="plan", permission_mode="strict", max_iterations=4)
    assert state.elapsed == 2.0
    state.update(AgentEvent("finished", run_id="cancelled-first", mode="plan", permission_mode="strict",
                            max_iterations=4, reason="cancelled", usage=TokenUsage()))
    assert state.elapsed == 5.0
