# 追加真实模型验收：取消复用与并发审批（修前）

日期：2026-10-04。延续本次独立 `mewcode-ui-lifecycle` socket 验收，两个新临时项目 `cancelreuse`、`concurrent`；入口仍为仓库 `.venv/bin/mewcode --config <仓库>/.env.claude`，未复制模型配置或凭据，未修改产品代码。全部对话为已授权真实模型，不是受控 AgentEvent 或 ScriptedProvider。当前会话已清理，socket 无服务器；后续修复复验将另开会话。

| 场景 | 实际观察 | 结果 | 证据 |
| --- | --- | --- | --- |
| 已发送慢 MCP 调用 raw Ctrl+C | 真实模型调用 echo，精确参数 `{"text":"LIFECYCLE-SENT-CANCEL-20261004","delay":20}`。人工本次批准后，在 wire 已记录 tools/call id=4 时发送 Ctrl+C；按键前 PTY echo=false、icanon=false。界面明确 `cancelled：外部请求已取消；远端可能仍在执行或已有副作用`，本轮 cancelled、回到空闲。 | 通过 | lifecycle-cancelreuse-before-cancel.txt、lifecycle-cancelreuse-after-cancel.txt、lifecycle-cancelreuse.json |
| 取消后同连接下一轮成功 | 请求新参数 `{"text":"LIFECYCLE-AFTER-CANCEL-REUSE-20261004"}`，重新批准后 wire tools/call id=5，模型实际报告独特回显及 PID 18285。同一完整 wire 只有一次 PROCESS_START(PID18285)、一次 server/discover、一次两页 tools/list；慢调用只发一次，取消通知 requestId=4，后续成功调用只发一次。 | 通过 | lifecycle-cancelreuse-reused-result.txt、lifecycle-cancelreuse.json |
| 同批并发 read_file 审批串行 | 临时项目 ask read_file(**)规则，两条真实中文长路径文件。真实模型在一批请求1/20生成 #1/#2 read_file；第一个批准后，第二个独立 request ID 等待授权。第二个决定前第一项已从活动列表移除，无 #1 成功终态滚动日志插入审批选项；批准第二项后 #1/#2 两条成功日志均刷入，模型报告 `READ-CONCURRENT-RESULT-1-20261004`、`READ-CONCURRENT-RESULT-2-20261004`，本轮新 model_done 请求2/20。 | 串行审批、日志保留通过；旁路结果即时可见未通过 | lifecycle-pre-fix-concurrent-wait.txt、lifecycle-pre-fix-concurrent-second-approval-with-first-result.txt、lifecycle-pre-fix-concurrent-final-model-result.txt |
| 45×15窄屏长详情阅全 | 第二项审批仍未决定时 resize，实际 targets(1页)、arguments(2页)、all(6页)逐页 next；在最后页 back 与前一页正文完全相等。将目标/参数页正文连起来，完整真实绝对路径及完整参数相对路径均存在，未丢失中文尾部文件名。查看期间未授权，恢复140×45后才选2。 | 完整正文可达、next/back/all通过；页码可见未通过 | lifecycle-pre-fix-concurrent-pages.json、lifecycle-pre-fix-concurrent-narrow-*.txt |
| 最终恢复清理 | 两会话 /exit 均0，echo=true、icanon=true；应用和fixture PID均回收，独立socket无剩余会话。 | 通过 | lifecycle-cancelreuse.json、lifecycle-pre-fix-concurrent.json |

## 修前发现的问题

审批布局的活动区 height=1，而控制器把 deferred 旁路结果接在待审工具行之后。第一项真实工具已完成时，当前待审 #2 行占据唯一可见行，导致“旁路结果（决定后保留日志）”不立即可见。日志确实在第二项决定后保留刷出，原有“不插断选项/不丢失”语义通过；增强区的完成结果即时可见不能据此计为通过。根代理正在修复，后续报告独立记录修后复验。

45列宽度审阅标题包含完整32字符request ID和section，再加页码，单行末尾被裁切；窄屏截屏显示 `审阅 [fb4e198bf19d4d6c8e8ae8679bcc71f8] · tar` 或 `· all`，看不到页码。实际next/back仍能访问全文，已用正文比较独立验证，但页码显示未通过。

## 驱动和证据边界

初次并发请求用 tmux send-keys -l 发送含真实换行的文本，第一处换行触发发送，路径未进入同一消息；真实模型据此要求补全路径。驱动改用单行JSON路径数组后，下一轮实际生成两个同批read_file。该驱动问题不是模型或产品执行失败，修前截屏保留了真实过程。

窄屏首次驱动依赖“第x/y页”正则，因上述产品标题裁切无法解析而停止；后续改按9行正文在next之后不再改变判定页尾，并逐页保存及验证back正文。未改产品代码或绕过人工决定。

真实并发场景无需额外受控AgentEvent重放。判定采用新批次编号、request1/20到request2/20、两个新独特文件标记及新model_done，未以旧空闲footer作为完成条件。MCP工具按副作用串行边界调度，因此并发工具使用内置只读read_file，而没有把两个MCP调用伪称并发。

`lifecycle-pre-fix-concurrent-*` 保留这次修前全套证据；取消复用证据独立保存，修后无需重跑此已通过场景。
