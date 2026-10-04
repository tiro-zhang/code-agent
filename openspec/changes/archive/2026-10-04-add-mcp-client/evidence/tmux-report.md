# MCP 真实 tmux 验收

日期：2026-10-04。临时工作根 `/private/tmp/mewcode-mcp-e2e-20261004`。使用用户已有 `.env.claude` 对应的真实 Anthropic 兼容服务、模型 `deepseek-v4-pro`；未复制模型凭据。启动入口为 `uv run --project <仓库> --frozen mewcode --config <仓库>/.env.claude`；后续复验使用同一已安装 `.venv/bin/mewcode`。不是 ScriptedProvider。

`local_demo` 是本机 stdio Server（2026-07-28），`http_demo` 是本机 Streamable HTTP Server（2025-11-25）。两者均由 `tests/mcp_fixture.py` 提供真实 JSON-RPC 收发并记录消息；兼容性四种组合另由真实 SDK 合同测试覆盖。

| 场景 | 实际观察 | 证据 |
| --- | --- | --- |
| 双传输与跨轮复用 | 两轮分别请求 echo `MCP-E2E-ONE`；模型均使用外部工具并汇报真实回显与 PID。stdio 第一轮及第二轮 PID 都为 6394，HTTP PID 都为 6357。每个 Server 恰好两次 tools/call。 | 01-reuse.txt、stdio.jsonl、http.jsonl |
| 会话与永久批准 | 首轮 stdio 选 3 会话、HTTP 选 4 永久；第二轮同参数无授权提示。 | 01-reuse.txt |
| 参数变化及拒绝 | HTTP `MCP-DENY` 重新询问，选 1 后模型报告拒绝；Server 日志没有该参数的 tools/call，随后计划和调用继续。 | 02-permissions-cancel-failure-eof.txt、http.jsonl |
| plan / do | `/plan` 仅生成计划；`/do` 对 `PLAN-ONE` 独立审批，选 2 后调用成功。规划入口伪造 MCP 调用的强制拒绝另由集成测试覆盖。 | 02-permissions-cancel-failure-eof.txt |
| 进行中 Ctrl+C | `SLOW-CANCEL` 延迟 20 秒，批准并观察“开始”后 Ctrl+C，返回 cancelled 与副作用未知提示；下一轮同连接 echo 成功。日志一条慢调用和一条对应取消通知，无工具重发。 | 02-permissions-cancel-failure-eof.txt、http.jsonl |
| 单 Server 退出 | 显式批准 local_demo 的测试 `exit:true`；返回 mcp_unavailable。之后 HTTP 再次成功，未重新拉起故障 stdio。 | 02-permissions-cancel-failure-eof.txt、stdio.jsonl |
| 授权 EOF | 新参数 `EOF-CHECK` 等待授权时 Ctrl+D；返回未启动 cancelled 并退出，Server 日志没有该调用，HTTP 收到 DELETE。 | 02-permissions-cancel-failure-eof.txt、http.jsonl |
| 重启恢复 | HTTP 的同参数永久批准自动复用；stdio 的原会话批准失效、重新询问。两次真实调用成功后 `/exit`，进程退出码 0。 | 03-restart-and-exit.txt |
| 初始化 Ctrl+C | stdio 工具发现延迟 10 秒，观察到进程启动后 Ctrl+C；0.240 秒退出，PID 8416 已消失。 | 04-startup-cancel.txt、startup-cancel.jsonl、lifecycle-results.json |
| 卡住关闭 | stdio 忽略关闭并创建同组后代；`/exit` 后 3.419 秒退出，主 PID 8436 与后代 8437 均已消失。 | 04-hanging-close.txt、hanging-close.jsonl、lifecycle-results.json |
| 最终代码复验 | 审查修复后分别调用 `FINAL-STDIO`、`FINAL-HTTP`，两次均重新审批、成功回显；界面显示安全连接描述，随后 `/exit` 正常退出。 | 05-final-smoke.txt |

`wire-summary.json` 独立核对整个验收的实际请求：stdio 共 5 次、HTTP 共 8 次 tools/call，与上述显式请求逐一相等；拒绝和 EOF 请求从未发送。5 次 HTTP 会话关闭均有 DELETE。日志记录的全部 stdio 主进程及后代 PID 在结束后均不存在。测试创建的 tmux 会话及 HTTP fixture 已清理。

验收驱动曾因假定 tmux 会保留提示符尾部空格而发生一次等待超时。终端当时已完成拒绝并回到提示符；修正驱动匹配条件后从下一步继续，未修改产品行为来规避失败。正式捕获保留实际对话，不把驱动等待超时计作产品通过或模型重试。

真实对话仅声明验证此 Anthropic 兼容服务；OpenAI 与 Anthropic 两套工具声明、结果配对及既有回归均由自动化测试覆盖，不宣称连接过官方 OpenAI／Anthropic 生产服务。本次 MCP checklist 的要求均已验收。
