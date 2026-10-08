# Web 交互性能本次验收

变更：`optimize-web-interaction-performance`。日期：2026-10-08。用户选择直接修改当前工作区，基线为 `3443663`；23/23 任务完成。主规格已同步，变更已[归档](../../../openspec/changes/archive/2026-10-08-optimize-web-interaction-performance/tasks.md)，实现、正式资源、规格及验收材料随本次变更一并提交。

## 实现与结果

草稿由输入区独立订阅；相同实际正文复用解析结果。生成中展示完整可选择的原文，结束后自动恢复安全 Markdown 和代码高亮。折叠详情在首次展开时构建；控制先读取最新权威身份，再合并正文和会话列表刷新。

普通正文同步窗口为 100 ms，分别维护实际接收游标和快照覆盖进度。状态读取最多一个在途；列表通知独立消费。失败重试每次间隔至少 1 秒，一轮最多三次；后续真实通知可重新启动有退避的核对。既有幂等、未知回执、权限及运行占用约束保留。

确定性回归：每次草稿编辑对已有 Markdown 的解析次数从 101 降到 0；100 个被首次快照覆盖的事件从 100 次额外 `/state` 读取降到 1 次。慢列表及列表失败不再持续禁用有效停止。

## 正式构建的浏览器测量

同设备 Apple M3，macOS Darwin 25.6.0，Chrome 154.0.8037.99，Node 26.4.0，视口 1440×1000。加载正式静态资源，使用本机 HTTP/SSE 模拟接口；不调用模型或工具。固定 100 条历史，每条 1229 字节和 10 个小型 Python 代码块；当前正文 131075 字节；40 Hz 普通通知，持续至少 10 秒。

原始报告：[改动前](baseline.json)、[优化后](optimized.json)。两份报告包含日期、manifest SHA-256、设备、全部样本、响应字节和每次状态读取、完成时刻、CDP 指标及全部主线程长任务。

| 交互 | 样本数 | 改动前 P95 / 最大（ms） | 优化后 P95 / 最大（ms） |
| --- | ---: | ---: | ---: |
| 输入（含完成附近） | 65 | 8220.4 / 8824.3 | 69.6 / 124.5 |
| 会话导航 | 22 | 8622.4 / 8824.4 | 66.7 / 123.9 |
| 有效停止发出请求 | 20 | 20.7 / 23.1 | 1.4 / 1.6 |
| 完成附近输入（输入子集） | 15 | 3682.4 / 3682.4 | 119.7 / 119.7 |

三项参考 P95 均达到 100 ms 目标。优化后的输入和导航反馈全部正确，20 个停止使用独立复位的运行代次，服务端核对提交版本。基线有 10 个导航回馈在绘制前被后续动作覆盖，故基线未通过正确性验收；表中保留其全部延迟，不把这些动作算作正确完成。

输入从原生 `keyDown` 的发生时间戳计至两次 RAF，导航从原生 `click` 时间戳计至两次 RAF，停止计至实际调用 `fetch`。时间戳由浏览器 CDP 在外部到达时提供，包含焦点操作、主线程排队、事件处理和下一次绘制机会。50 次普通输入每 200 ms 到达，前一处理未完成时仍按时发送后续输入；另外保留完成附近 10 次及格式化后 5 次输入。导航共 22 次，停止 20 次。两次 RAF 是可见更新的浏览器采样边界，输入内容变化和选中属性同时核对；不计历史远端读取、服务响应或工具实际收尾。

人为阻塞 180 ms 的独立校验包含在原始报告中。旧的分发时钟测得 22.7 ms，校验失败；修正后优化构建测得 172.0 ms，基线 533.4 ms。该人为校验单独标记，未混入参考 P95；校验的排队延迟单独保存在 `samples.probe`，浏览器长任务观察器报告的应用任务原样保留。原先基于 `page.evaluate` 起始时刻的数字作废，最终报告全部由原生事件重测。

优化后输入最大 124.5 ms，完成附近输入最大 119.7 ms；完成阶段还有 2 个长任务（106、74 ms）。本次优化降低了延迟，最终同步格式化仍有短暂阻塞。基线流式阶段最大长任务 4283 ms，完成阶段 3333 ms；优化后流式阶段最大 132 ms。没有移除慢样本或减少语料。

全流程 `/state` 读取／响应字节：基线 183 / 52820878，优化后 158 / 45604558。基线主线程阻塞导致流式阶段只来得及发出 6 次读取，大量积压在完成／控制阶段消费；优化后流式阶段正常发出 96 次读取。全流程计数不用于证明正常每秒上限；该上限及 100 个积压事件的减少由可控时钟和真实 App 回归验证。完整快照传输成本仍随历史增长。

复现：

```sh
npm --prefix frontend run build
node frontend/scripts/measure-performance.mjs /path/to/baseline-static /tmp/baseline.json
MEW_PERF_ASSERT=1 node frontend/scripts/measure-performance.mjs src/mewcode/web/static /tmp/optimized.json
```

基线静态资源可从 `3443663` 的 `src/mewcode/web/static/` 取出。脚本只启动本机模拟接口；测量期间不并行运行测试或其他浏览器验收。

## 自动化、构建和交付

| 本次检查 | 结果 | 证据 |
| --- | --- | --- |
| 前端完整测试 | 56 passed | [日志](frontend-tests.txt) |
| TypeScript | 通过 | [独立 typecheck](typecheck.txt) |
| 既有浏览器功能 | 12 passed，20.2 s | [日志](browser-tests.txt) |
| Python 完整回归 | 2009 passed，403.03 s | [日志](pytest.txt) |
| 前端构建与 manifest | 通过 | [构建](build.txt)、[一致性](verify-build.txt) |
| wheel / sdist | 通过 | [构建](package-build.txt) |
| 安装包无 Node 启动 | 通过；入口和静态资源完整 | [安装包检查](package-check.txt) |
| 打开 SSE 后信号退出 | SIGINT 0.291 s，SIGTERM 0.235 s | [安装包检查](package-check.txt) |

首次受限环境中的 Python 测试有 21 项本机端口绑定失败；在允许本机监听的环境完整重跑后 2009 项通过。浏览器首次执行遇到环境挂起导致超时，恢复后重跑完整 12 项通过。测试通过以最终日志为准。

实际命令：`npm --prefix frontend test`、`npm --prefix frontend run test:browser`、`UV_CACHE_DIR=/private/tmp/mewcode-web-performance/uv-cache uv run --no-sync pytest -q`、`npm --prefix frontend run build`、`npm --prefix frontend run verify:build`、`UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv build --offline`。安装包检查使用新构建 wheel、临时目录及不含 Node 的 PATH；测试配置不调用模型。

## 真实授权模型和 tmux

真实配置为本机 `.env`，模型 `ark-code-latest`；真实密钥和 Web 启动片段未写入材料。工具产物仅位于报告列出的临时目录。

- Web：[本次结果](real-web.json)。真实读取 README 并完整审批；捕获生成中的回复；生成时浏览另一会话、独立起草并刷新；验证最终标记；审批后实际启动 60 秒命令，再停止，`cancel-start` 存在而 `cancel-end` 不存在。
- 终端：[本次结果](real-cli.json)。使用 tmux 和 `uv run --project [repo] --no-sync mewcode --config [repo]/.env --permission-mode strict`，真实读取、严格一次批准、运行后 Ctrl+C、取消后继续、退出及显式 `--resume`；恢复新增记录只有 `maintenance_finished` 和 `session_resumed`，没有任务或工具重放。
- [流式中间画面](terminal-streaming.txt)、[完整回复](terminal-stream-complete.txt)、[读取授权](terminal-read-approval.txt)、[实际取消](terminal-stopped.txt)、[显式恢复](terminal-resumed.txt)。原读取回复较短，最初没有捕获中间片段；补充长回复时检测误包含用户问题里的结束标记，修正范围后实际捕获生成片段。

Web 重启恢复最终通过：旧凭据 HTTP 401，A/B 两个新草稿保持，重启不自动执行，显式继续后收到 `WEB_RESUME_OK`；[实际页面](real-web.png)。后续补测在未加载会话时的退出为 336 ms。已加载真实会话后的首次退出等待曾超过 90 秒，之后服务实际退出；原因未在本变更中确定，仍作为限制保留。补测脚本还修正了认证卡片选择器及只改变 hash 未加载新页面的问题；全部失败记录保留在 JSON。无会话退出耗时不代表真实会话收尾耗时。

## 审查与范围

一次独立完整审查发现两项 Important：三次同步失败后活 SSE 无法恢复、性能时钟漏算主线程排队。均经复现修正，前端完整 56 项通过；未进行重复审查。Minor 保留：旧列表请求迟到失败可能显示过时的局部错误；列表数据和控制身份仍受请求／实例保护。详见[审查与裁决](review.md)、[全部场景映射](spec-audit.md)。

本次未执行：不同设备、其他浏览器和其他供应商的真实性能比较，真实超长模型输出达到 128 KiB；长正文验收由固定生产构建压力语料提供。没有声明全平台性能或取消跨进程项目占用。
