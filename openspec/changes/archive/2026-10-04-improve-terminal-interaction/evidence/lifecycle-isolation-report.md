# 增强终端 MCP 故障隔离真实验收

日期：2026-10-04。最终UI代码落地后的新独立项目 `/private/tmp/mewcode-ui-lifecycle-20261004/isolation`，专用 `tmux -L mewcode-ui-lifecycle`、140×45 PTY。入口仓库 `.venv/bin/mewcode --config <仓库>/.env.claude`，真实已授权模型配置未复制或输出凭据。未修改产品代码、tasks/checklist，不重跑其他通过场景。

临时MCP配置同一批声明两个Server：`broken_demo` 的stdio command为确实不存在的 `/private/tmp/mewcode-ui-lifecycle-command-does-not-exist-20261004`；`lifecycle_demo` 为当前 `tests/mcp_fixture.py` 提供的真实stdio JSON-RPC Server。

启动增强终端实际显示：

```text
MCP> broken_demo · 配置 · 无法解析可执行程序
MCP> lifecycle_demo · 就绪 · 已注册 2 个工具
MewCode · claude-demo · 权限 default · /help 查看帮助
```

真实模型接到只调用正常echo请求，人工选2本次批准后，wire中恰好一次 `tools/call`、原始工具echo、精确参数 `{"text":"LIFECYCLE-BROKEN-ISOLATION-OK-20261004"}`。界面显示工具成功，真实模型回报相同回显和PID22911，新 `model_done` 请求2/20。没有调用或尝试拉起broken_demo，正常Server不受其配置失败影响。

随后 `/exit` 约0.196秒正常退出，exit_code0；同一PTY的echo/icanon均恢复true，应用PID22905、fixturePID22911均不存在。独立socket最终无遗留会话。结论：本次增强终端启动诊断、失败隔离、健康Server真实模型调用及正常退出通过。

证据：`lifecycle-isolation-startup-diagnostics.txt`、`lifecycle-isolation-real-model-result.txt`、`lifecycle-isolation-after-exit.txt`；`lifecycle-isolation.json`含完整本次wire、termios、PID和逐项检查。此次故障发生在command解析配置阶段，不声称覆盖连接后进程崩溃或HTTP断线。
