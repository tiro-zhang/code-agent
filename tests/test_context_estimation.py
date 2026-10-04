"""估算覆盖实际回传内容，并以工作总输入而非缓存未命中值校准。"""
from dataclasses import replace

from mewcode.context.estimate import Estimator, estimate_text, snapshot
from mewcode.types import Message, TokenUsage, ToolCall


def test_chinese_code_and_usage_anchor_only_count_append():
    assert estimate_text('abcd') == 1
    assert estimate_text('中文') == 2
    estimator = Estimator('openai')
    initial = [Message('user', '原问题')]
    request = estimator.snapshot(initial, '系统', ())
    estimator.observe(request, TokenUsage(total_input_tokens=50000, output_tokens=20000,
                                          input_tokens=10000, cache_read_tokens=40000))
    added = Message('assistant', 'abcd中文')
    expected_delta = snapshot([added], '', (), 'openai').message_tokens
    assert estimator.estimate(initial + [added], '系统', ()) == 50000 + expected_delta
    assert estimator.estimate(initial, '新系统', ()) < 50000
    assert estimator.estimate([replace(initial[0], content='变更')], '系统', ()) < 50000


def test_missing_usage_and_summary_do_not_supply_work_anchor():
    estimator = Estimator('openai')
    messages = [Message('user', '问题')]
    snap = estimator.snapshot(messages, '系统', ())
    estimator.observe(snap, TokenUsage(input_tokens=80000))
    assert estimator.estimate(messages, '系统', ()) == snap.tokens
    estimator.observe(snap, TokenUsage(total_input_tokens=80000), purpose='summary')
    assert estimator.estimate(messages, '系统', ()) == snap.tokens


def test_provider_reasoning_counted_only_when_actually_returned():
    plain = Message('assistant', '回复', provider_content=({'type': 'openai_reasoning', 'reasoning_content': '中' * 1000},))
    call = replace(plain, tool_calls=(ToolCall('c', 'read_file', '{}'),))
    assert snapshot([call], '', (), 'openai').tokens > snapshot([plain], '', (), 'openai').tokens + 1000
    native = Message('assistant', '不应重复计数' * 100, provider_content=({'type': 'text', 'text': '短'},))
    assert snapshot([native], '', (), 'anthropic').tokens < 100
