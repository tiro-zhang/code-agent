"""统一事件在组件边界保留工具关联及未知用量。"""

def test_event_preserves_call_and_unknown_usage():
    from mewcode.types import AgentEvent, ProviderEvent, TokenUsage, ToolCall

    call = ToolCall("r1", "read_file", '{"path":"a"}')
    event = AgentEvent("tool_call", run_id="run", iteration=2, call=call, tool_call_id=call.id)
    assert event.call.arguments == '{"path":"a"}'
    assert event.run_id == "run" and event.tool_call_id == "r1" and event.iteration == 2
    usage = ProviderEvent("usage", usage=TokenUsage(input_tokens=5))
    assert usage.usage.output_tokens is None and not usage.usage.complete
