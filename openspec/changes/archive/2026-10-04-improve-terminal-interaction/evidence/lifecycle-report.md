# 终端生命周期真实 tmux 验收

日期：2026-10-04。针对 `improve-terminal-interaction` 的独立本次验收。使用专用 `tmux -L mewcode-ui-lifecycle`，140×45 PTY，独立临时项目 `/private/tmp/mewcode-ui-lifecycle-20261004/{startup,hang,eof}`。未访问或操作根代理的 `mewcode-ui-e2e` socket，未修改产品代码、tasks 或 checklist。

入口为仓库 `.venv/bin/mewcode --config <仓库>/.env.claude`，读取已有已授权真实配置；配置文件及凭据未复制到临时项目或证据。`hang`、`eof` 均发起本次真实模型请求，终端显示 `claude-demo`。`startup` 在模型请求前取消。stdio Server 由当前 `tests/mcp_fixture.py` 启动，真实 JSON-RPC 消息原样记录；不是模拟 Provider 或历史会话重放。

| 场景 | 判据及本次实际观察 | 结果 | 证据 |
| --- | --- | --- | --- |
| 初始化发现中 raw Ctrl+C | fixture 配置 `list_delay:10`；wire 已收到首次 `tools/list` 且第二页尚未请求。屏幕仍显示“启动中 / Ctrl+C 取消启动”。发送 raw Ctrl+C 前直接读取 PTY termios，echo=false、icanon=false。按键后 0.119 秒退出，exit_code=0；应用 PID 16748 与 fixture PID 16749 均不存在。 | 通过 | lifecycle-startup-before-cancel.txt、lifecycle-startup-after-exit.txt、lifecycle-startup.json |
| 忽略关闭的主子进程有界回收 | fixture 配置 `hang_exit:true, child:true`；主 PID 16775，子 PID 16776，进程组 16775。真实模型收到“不要调用工具，只回复 LIFECYCLE-HANG-READY。”并完成相同回复，结束原因 model_done。发送 `/exit` 后 3.380 秒 exit_code=0；主、子及应用 PID 16769 均不存在。 | 通过 | lifecycle-hang-after-request.txt、lifecycle-hang-after-exit.txt、lifecycle-hang.json |
| 授权中 Ctrl+D 未发送并退出 | 新项目、新会话请求外部 lifecycle_demo/echo，独特参数 `{"text":"LIFECYCLE-EOF-20261004-UNSENT"}`。模型真实生成外部调用，屏幕进入带 Server、原名、稳定别名、安全连接描述及精确授权范围的授权页。在授权输入为空时 Ctrl+D；0.179 秒退出，exit_code=0。最终输出明确“cancelled：任务取消，工具未启动 / 未启动”，本轮结束 cancelled。wire 只有 PROCESS_START、server/discover、两页 tools/list，`tools/call` 为 0，无该调用发往 Server。应用 PID 17079 与 fixture PID 17083 均不存在。 | 通过 | lifecycle-eof-wait.txt、lifecycle-eof-final-screen.txt、lifecycle-eof.json |
| 终端恢复与清理 | 三场景应用退出后直接读取同一 PTY：echo=true、icanon=true，与应用启动前相同。最后专用 socket 返回 no server running；`ps` 查询所有正式及早期驱动采样记录的应用/fixture/子进程均无结果。 | 通过 | 各场景 JSON、lifecycle-manifest.json |

## 记录与测量方法

`lifecycle-{startup,hang,eof}.json` 包含本次完整 wire 消息数组、消息计数、应用及 fixture PID、按键时间、exit_code、termios 前后完整字段和进程存在性检查。时间采用单调时钟；Ctrl+C/Ctrl+D 从发送 tmux 按键前计至应用 `wait()` 返回，`/exit` 从 tmux 文本及 Enter 提交完成后计至 `wait()` 返回，后者存在毫秒级提交开销差异。全部结果均在本次实际执行后采集。`lifecycle-manifest.json` 保存被测 app/terminal/fixture 文件 SHA256 和最终清理状态。

termios 的 echo/icanon 恢复已直接证明。完整 lflag 与初始值差异仅为 macOS `PENDIN` 位（536870912），因此不声称所有 termios 字段完全相等；该差异不影响本次要求的回显及 canonical 输入恢复。未执行 HTTP 场景、官方 OpenAI 服务或其他生命周期路径，不将它们计为本报告通过。

## 验收驱动事件

初始 sandbox 会话没有留下可读 pane；后续真实 PTY 操作使用已授权的 sandbox escalation。早期一次驱动在应用退出后把 termios 的 bytes 数组直接 JSON 序列化，导致驱动异常退出并丢失收尾屏幕。修正为 hex 字符串后全新重跑 startup，清空早期 wire/actions；正式 startup.json 只有一组 PROCESS_START 和按键记录，旧 fixture PID 16526、16675 也已检查回收。此驱动失败不作为产品失败或产品通过证据。

另一次驱动等候文字“等待输入”，当前界面实际使用“空闲”，因此驱动等待超时；当时正常界面与后续真实模型回复可见于 lifecycle-hang-timeout.txt 和 lifecycle-hang-after-request.txt。改用实际 `你>` / 授权提示判定后完成后续场景，未调整产品行为。

`lifecycle-driver.txt`、`lifecycle-runner.txt` 保存驱动代码以供审查。所有本次创建的 tmux 会话和 fixture 均已清理。临时项目只含无凭据的 fixture 配置、wire 与结果，未复制 `.env.claude`。
