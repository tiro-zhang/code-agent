# 公共运行层与 Web 基础组件实施记录

日期：2026-10-07。分支：feat/local-web-interface。本文记录 runtime 子任务与随后追加的基础组件、启动分发工作；不代替主代理最终集成验收，也未修改 OpenSpec 任务勾选。

## 公共接口

`mewcode.runtime.RuntimeResources(config, *, root, provider_factory=None, notify=None, approval_responder=None, permission_mode='default', persistent=True, resume=None, team=None, memory_enabled=True, user_root=None, config_path=None)`：

- `validate()` 只校验项目与 Skill／命令定义，在 Provider、Journal、MCP 之前执行。
- `create_provider()`、`construct()`、`resume_team()`、`bind_mcp(cancel_event=...)`、`prepare(cancel_event=...)` 支持 CLI 保持原审批／终端／MCP／恢复／Hook 初始化顺序；`start(cancel_event=...)` 提供无界面的完整启动并处理失败清理。
- 公共属性 `provider`、`session`、`mcp`、`registry`、`background`。项目根通过 ToolContext 明确传入，不调用 chdir。
- `notify` 同步接收 AgentEvent，保留用途、主子身份及维护用量。MCP 诊断可以通过 `mcp_notify` 单独适配终端。
- `await aclose(cancel_event=None, timeout=10)` 返回 `CloseReport(complete, errors, pending)`。单一关闭任务按 Session → MCP → Provider 顺序执行；外部重复取消不会打断，超时仍由资源对象持有原任务。`complete=False` 不能作为卸载／释放执行槽的证据。MCP 仅报告“关闭未确认”时同样标记不完整。
- 默认 ChatSession 构造期间先取得半构造对象所有权，Hook 等后续初始化失败仍释放已创建 Journal；自定义 session_factory 保留原 callable 接口。

`BackgroundPump(session).wait_ready()` 等待 next_parent 的原父身份，实际结果消费与剩余预算继续由 ChatSession.resume_parent 完成。`notices(callback)` 单独转发 task_status。CLI `_idle_input` 仍优先处理已经提交的输入，并在自动接续前暂存草稿。公共层不注册信号、不导入终端 UI。

`SessionCommandService(session, config=None)` 提供 `set_mode`、`reset_session`、`status_text`、`permission_text`、`skills_text`、`agents_text`、`tasks_text`、`team_text`、`session_text`、`memory_text`；`.cancel_event` 可注入。SessionCommandContext 继承该服务，保留终端清屏、渲染、状态同步与 reset_task。

## Web 基础组件

- `OperationLedger(server_instance_id, max_clients=32, max_receipts=256)`：register、run、lookup、aclose。run 接收 envelope、完整业务 payload 与异步操作，返回 `(status_code, body)`；已接受的成功或业务失败均带 `operation` 回执。重复请求返回原结果，内容冲突／跳号／过期／旧实例明确拒绝。HTTP 取消不取消账本拥有的任务；关闭前尚未开始的任务也产生终止回执。
- `EventHub(server_instance_id, snapshot=...)`：publish、snapshot、subscribe、unsubscribe、close。默认环为 4096 条／8 MiB，实时订阅队列为 256 条／1 MiB，最多两条订阅。快照高水位与订阅登记间无 await；initial snapshot 独立发送一次，不挤占实时队列。慢订阅断开，发布不等待浏览器。信封包含 schema_version、实例、seq、会话、代次、run_id 与 parent_run_id。
- `LocalSecurity(port, server_instance_id=...)`：精确 Host／Origin 校验、实例独立片段凭据和 cookie。Redactor/public_fields 先白名单再去除内部字段和已知秘密；底层任意异常不直接成为操作错误正文。HTTP 的 HttpOnly／SameSite、请求大小、静态路径和 CSP 由主代理 API 层连接。

## 启动与分发

- CLI 新增 `--web` 与仅 Web 有效的 `--port`（默认 8765）；与 resume、team、list-sessions、team-worker 互斥，保留原 CLI options 传参。
- `web.server.run(config_path, permission_mode='default', port=8765)` 校验配置、定义和静态清单后绑定 127.0.0.1。端口冲突不换端口、不创建 manager。启动仅构造未加载 manager，不创建 Provider／ChatSession。
- LocalServer 使用单 worker、禁用访问日志和代理头。SIGINT／SIGTERM 先 begin_shutdown 关闭 SSE，再进入 Uvicorn drain／lifespan；shutdown 总等待设置为 30 秒。
- `assets.validate_assets(static_root=None, frontend_root=None)` 验证 manifest 版本、路径、全部产物摘要、入口、源码集合摘要与锁文件。开发源码存在时新增／修改任何非排除文件都会识别陈旧构建；安装环境不需要 Node.js 或 frontend 目录。
- pyproject package-data 与 MANIFEST.in 包含静态文件、前端源文件、构建脚本和锁文件，排除 node_modules 等生成目录。

## 测试证据

均使用 `UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run --no-sync pytest ...`。真实 socket 用例在允许本机回环的执行环境运行，未改测试规避 PermissionError。

| 验证 | 实际结果 |
| --- | --- |
| 新公共资源测试初始红灯 | 9 failed：runtime 模块尚未存在 |
| 公共命令服务初始红灯 | 2 failed：service 模块尚未存在 |
| 原终端／权限／slash／后台／团队／Hook 与公共测试组合 | 104 passed，21.90 秒 |
| 新 Web primitives 初始红灯 | 9 failed：三个模块尚未存在 |
| 补关闭未启动操作与快照失败槽位测试 | 先 2 failed，修复后通过 |
| 大 initial snapshot 与 malformed Unicode token | 先 2 failed，修复后 primitives 14 passed |
| startup 初始红灯 | 13 failed，9 原参数拒绝场景已通过 |
| runtime／command／primitives／startup 组合 | 48 passed，1.77 秒（半构造回收新增测试之前） |
| 半构造 Journal 回收 | 先失败：Journal.resume 报活动锁；修复后后续组合结果见追加记录 |
| 全 Python suite（追加启动域实现前启动本次运行） | 1941 passed，1 failed，394.72 秒。唯一失败为 `tests/test_web_history.py::test_task_boundaries_keep_saved_input_separate_from_context`；history 代理确认该次命中其 TDD 中间状态，随后该代理 history＋原存档 44 项通过。本记录不把该全套声明为全绿。 |
| `uv build` | 初次受 sandbox DNS 限制无法下载 setuptools；授权重跑成功，生成 wheel 与 sdist |
| 已安装 wheel、不含 Node 的 PATH、真实服务与打开 SSE 的 SIGINT | 通过；0.291 秒退出，code 0，运行态 unloaded，未创建 sessions |
| 相同条件 SIGTERM | 通过；0.237 秒退出，code -15（Uvicorn 清理后重发原信号） |
| wheel／sdist 内容检查 | 静态入口／assets／manifest 完整；sdist 包含 package-lock.json、manifest.mjs 且无 node_modules |

原始临时日志：`/private/tmp/mewcode-runtime-tests.txt`、`/private/tmp/mewcode-runtime-full-tests.txt`、`/private/tmp/mewcode-web-build.txt`、`/private/tmp/mewcode-package-check.txt`。无 Node 的验证脚本位于 `/private/tmp/mewcode-package-check.py`，本次安装目录 `/private/tmp/mewcode-package-7uoegbi3`。验收输出未包含启动 token 或模型密钥。

## 完成范围与后续

任务 1.1–1.4 的实现已落到公共层并接回 CLI，相关回归通过。Web primitives 与启动／分发已交主代理集成。真实模型 tmux CLI／Web 和完整规格逐项验收由主代理执行，本子任务未执行，也未用历史记录替代。

前端代理仍在补浏览器边界，已经观察到源码更新会触发本次 assets 校验的“构建陈旧”错误；最终须前端重新 build／verify，再由主代理重跑 uv build，将后续 manager／API／approval 修改纳入最终产物。

追加验证：半构造 Journal 回收修复后，`tests/test_runtime_resources.py tests/test_slash_app.py tests/test_permission_app.py tests/test_app.py tests/test_team_commands.py` 共 **74 passed，13.73 秒**。在 Hook 构造异常后同进程可以立即重新取得原 Journal 写锁，Provider 同时关闭。

最终局部组合：`tests/test_web_primitives.py tests/test_web_startup.py tests/test_runtime_resources.py tests/test_command_service.py` **49 passed**；`git diff --check` 无输出。未提交 Git。

## 展示投影预算与性能修正

追加范围为 `src/mewcode/web/projection.py` 与 `tests/test_web_projection.py`，manager／API 由主代理维护。已将 `_bound` 的每事件全量 JSON 扫描替换为按消息、运行与工具记录缓存的大小计数；正文、思考和元数据分别计数，仅修改的正文段增减大小，只有超限时裁剪最旧内容。已知秘密脱敏仅重新处理可能跨片段匹配的尾部；普通片段不序列化已有长文本。

- 正文总计 8 MiB，覆盖消息文字、历史 call／result、工具参数／结果和 run.text；思考独立共用 256 KiB；元数据独立 1 MiB。
- 每份参数／结果不超过 64 KiB，截断结果的 JSON 包装与转义开销也计算在限额内；预算不足时使用外层 truncated 标记而不生成未计数占位正文。
- 运行窗口总数至多 11、completed 至多 10；较早未完成身份同样受窗口约束，移出时警告，不伪造 completed。每段最多 1024 个调用。
- 历史 result_id 保留为独立身份，方便正文被回收后仍访问登记详情。seed 的 JSON 参数与参数键名均脱敏，保留审批独立性、用途用量、跨片段秘密脱敏以及拒绝／取消／超时／未知副作用状态。
- 新 `reconcile_message(item, message_id)` 同步更新已提交消息身份与元数据计数；主代理已连接 manager。缓存移除不改写协调器已拿到的候选消息正文。

红绿：首批 6 项新测试失败，分别复现正文漏计、JSON 截断包装超限、缺少独立 metadata 预算、未完成段无限累积、片段全量序列化、JSON 参数键名未脱敏；实施后通过。追加 1 项红灯复现元数据驱逐后外部候选引用正文被清空，已修复。最终 **15 项 projection 测试通过**；与 manager／API 合计 **29 passed，2.65 秒**。性能观测：2 MiB 历史＋4 MiB 当前回复时追加 1000 个 32 字节片段，耗时 **0.214 秒**（平均 **0.214 ms／片段**），保留正文 6,323,456 字节。回归还包含默认 1024 调用窗口、窗口驱逐后计数释放和 120 次混合事件后的逐步预算断言；序列化监测明确验证增量事件不再扫描已有长历史。

投影最终组合命令：`UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run --no-sync pytest tests/test_web_projection.py tests/test_web_manager.py tests/test_web_api.py tests/test_web_primitives.py -q`，**43 passed，2.34 秒**。`git diff --check` 无输出。

## 工作与维护用量归属修正

主代理本次真实 Web 验收发现，记忆维护卡片“已知用量”错误复用工作用量 5528／178，尽管按用途表中的 memory 696／477 正确。根因是记忆通知沿用工作 task_id，而投影只按 run_id 建卡；维护状态覆盖工作卡片，卡片 usage 却只接受 work 更新。

现按用途为非工作展示片段生成 `maintenance:{purpose}:{原 run_id}`，记忆片段以 parent_run_id 保留工作父任务关系，显式 parent_run_id 优先。工作 ID 保持原值；每段 usage 只取自身用途的实际数据，不推算或借用其他用途。尚未收到维护用量时保持空值，供应商未知的计数仍为 None。晚到工作结束事件不再改写维护状态。现有前端支持独立片段和父身份，无需改动。

新增两项回归先出现 **2 failed**，分别证明同父工作／记忆合并和多用途身份合并；修复后运行 `UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run --no-sync pytest tests/test_web_projection.py tests/test_web_manager.py tests/test_web_api.py tests/test_runtime_resources.py -q`，**43 passed，3.51 秒**。其中 projection 共 17 项，覆盖排队未知、实际工作／记忆数字、晚到工作完成、summary／restore／memory 独立用途和未知计数。`git diff --check` 无输出。真实重启验收由主代理继续执行。
