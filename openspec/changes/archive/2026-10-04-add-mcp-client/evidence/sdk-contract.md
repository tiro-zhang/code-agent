# MCP SDK 发布合同验证与生命周期适配

日期：2026-10-04。实现位置：现有工作目录，用户已明确同意保留既有改动。

## 发布依赖

- Python：3.11.14。
- 官方 SDK：`mcp==2.3.0`，已从 PyPI 安装到独立临时 target，并通过 `uv sync` 更新项目依赖和锁文件。
- 发布页：https://pypi.org/project/mcp/2.3.0/
- PyPI 发布身份对应源码：`2118f14f8a19bc158d8a1cf90af58d85d187f849`。
- 采用 `Client(mode="auto", cache=None)`；HTTP 使用 `httpx2`；低层 `client.session.call_tool(..., allow_input_required=True)` 返回输入续调要求，由 MewCode 报不支持，不自动续调。

## 已验证

执行：`.venv/bin/python -m pytest tests/test_mcp_sdk_contract.py -q`。HTTP fixture 仅监听 127.0.0.1，需要本地端口权限。

- stdio 和 Streamable HTTP 均自动兼容 `2025-11-25` 与 `2026-07-28`。
- 两页工具发现、同连接乱序响应、stdio 通知穿插和请求 ID 配对正确。
- 取消单次请求后，同一连接的下一次工具调用成功；stdio 只启动一个进程。
- 低层工具调用面对 HeaderMismatch 和 InputRequired 不隐式再次发送 tools/call。
- SSE 用 GET 恢复，不重复 POST 工具调用。
- 新旧协议的取消和期限均停止恢复。旧协议初次失败及修复证据见下文。

## 修复前反例：旧协议 HTTP 恢复未随调用结束

修复前结果：**8 passed, 2 failed**。失败用例为

```text
test_recovery_stops_on_deadline_or_cancel_and_connection_survives[legacy-False]
test_recovery_stops_on_deadline_or_cancel_and_connection_survives[legacy-True]
AssertionError: 已取消请求的响应恢复仍在运行
assert 5 == 9
```

fixture 在工具 POST 返回 SSE 事件标识和 `retry: 20` 后断流；GET 恢复再次仅返回事件标识。客户端在 0.1 秒期限或主动取消后结束调用，但随后 0.12 秒内 GET 数量仍从 5 增至 9。两个失败均可复现；关闭整个 Client 上下文才能停止遗留恢复。

发布源码 `mcp/client/streamable_http.py` 的 `_consume_modern_cancellation` 仅对现代协议取消相应 POST scope；旧协议发送取消通知，继续保留原 POST／恢复流程。`asyncio.wait_for` 或 `task.cancel` 只结束调用者等待，不能证明旧协议 HTTP 后台恢复已结束。

这违反 mcp-client 中“恢复受原请求期限和取消控制”，对应任务 1.4、3.4。不能用关闭整个 Server 连接规避，因为规范要求单次取消后复用共享连接。

## 已批准的设计修订

保留原功能合同，扩展 SDK 边界的 HTTP 请求生命周期适配：

1. 由 MewCode 为一次工具调用建立独立取消状态和固定期限，通过 SDK 支持的自定义 HTTP client 关联该请求及其恢复响应。
2. 取消或超时时结束该请求持有的响应读取，并禁止该请求后续恢复 GET 发出；不关闭共享 Server 连接，不重发 tools/call，不自行实现 JSON-RPC 配对或协议协商。
3. 增加真实 SDK 的新旧协议适配测试，证明无后续恢复 GET、下一调用仍成功、其他 Server 不受影响。
4. 若公开 HTTP client 扩展点不足以满足合同，继续报告证据，不静默改用 SDK 私有接口、关闭共享连接或放宽验收。

替代方案是放宽旧协议合同，允许调用者已结束后 SDK 继续响应恢复，直至收到响应或会话退出；这会改变已写入 specs 的行为，不建议默认采用。

用户已确认“补充生命周期适配，保留原合同”。设计、任务与取消场景已同步；继续实现和真实 SDK 验证。

## 适配后结论

真实 SDK 合同测试 **10 项全部通过**，原来的两项反例转绿，未删除、跳过或标记 xfail。`LifecycleHTTPClient` 通过公开 `send` 与响应流扩展点传递单次调用的 ContextVar 生命周期；到期或取消后截断本次 I/O，并阻止其后续恢复 GET。没有修改 SDK 私有状态，没有自行实现 JSON-RPC 配对或协议协商。

旧协议的 `notifications/cancelled` 有独立 50 毫秒发送期限；取消通知 HTTP 响应延迟 1 秒时，下一次调用仍能在自己的期限内发送并成功。普通调用、取消和恢复均没有隐式重发 tools/call。

实现已接入应用、Agent、工具中心和权限；本次真实 tmux 已覆盖双传输、两轮复用、取消后继续和清理。最终证据见 `verification.md`、`pytest-final.txt`、`tmux-report.md`。

## 验证历史

- 修改前全套基线：`449 passed in 34.57s`。
- 配置及工具适配：`.venv/bin/python -m pytest tests/test_mcp_tools.py tests/test_mcp_config.py -q` → `33 passed in 0.48s`。
- 上述 33 项是早期纯适配阶段的结果，不能替代最终全套回归及真实 tmux 验收。
