# Web 规格场景核对

对应 [已归档增量规格](../../../openspec/changes/archive/2026-10-07-add-local-web-interface/specs/web-interface/spec.md) 的 16 项需求、40 个场景，已完整同步至 [web-interface 主规格](../../../openspec/specs/web-interface/spec.md)。测试文件为本次新增或本次重跑；真实组合验收范围以 e2e 报告为准。各层实际结果与已知限制见 [summary.md](summary.md)，不以映射本身代替通过证据。完整 Python 回归 2007 项通过，后续维护用量修正的 43 项定向通过；前端 32 项、Chrome 12 项通过，严格规格校验无问题。

| 需求与场景 | 本次证据入口 |
| --- | --- |
| 本机单项目启动：项目目录启动、端口/选项冲突、继续终端 | test_web_startup.py；runtime-report.md；terminal-e2e.md；Web真实服务 |
| 访问展示边界：跨站提交、恶意HTML及敏感诊断 | test_web_api.py、test_web_primitives.py、test_web_projection.py、test_web_approval.py；frontend安全Markdown/外部图片Chrome测试 |
| 聊天布局：收起详情、其他会话出现审批 | frontend/components与Chrome桌面/390px测试；真实浏览器三栏审批 |
| 输入草稿：粘贴、回执时编辑、忙/失败 | frontend/state/api/Chrome输入法与草稿测试；Web真实刷新保留草稿 |
| 只读导航：压缩历史、运行中新建、CLI占用 | test_web_history.py；test_web_manager.py浏览/新建无Provider；Chrome会话切换 |
| 显式继续：准备后不提交、恢复依赖缺失 | test_runtime_resources.py启动顺序与失败关闭；test_web_manager.py显式激活；Web重启后历史继续 |
| 串行占用：并发提交、主结束子仍活动、恢复维护收尾 | test_web_manager.py busy拒绝、实际子任务自动接续、真实MemoryManager维护、流关闭阻塞和准备阶段Stop |
| 幂等状态：响应丢失、重启旧请求 | test_web_primitives.py；test_web_api.py；frontend复制页冲突及原正文复核Chrome测试 |
| 连接恢复：中途刷新、慢客户端窗口 | EventHub snapshot/replay/queue/initial大快照测试；Chrome SSE/reducer/429后备；真实编辑批准后刷新 |
| 任务审阅：待审批、编辑结果、截断/回收 | test_web_projection.py；test_web_history.py结果分块及缓存归属；Web真实edit diff与after-web产物 |
| 精确审批：两标签、取消后旧答、规则/目标变更 | test_web_approval.py使用真实PermissionManager；Chrome双页竞争；root审批API边界 |
| 审批期限：刷新不续期、关闭浏览器到期 | test_web_approval.py可控clock/真实deny；Chrome身份/到期/分页；TTL固定600秒 |
| 停止关闭：重复停止、未知清理、服务退出 | test_web_manager.py真实异步流清理门禁/blocked/monitor接续/旧对象关闭；安装wheel打开SSE时SIGINT/SIGTERM；Web真实sleep取消 |
| 命令操作：模式按钮与命令、多行文字、终端专属 | test_command_service.py、test_web_manager.py；原slash/permission/team回归；Chrome控件；Web元信息terminal标注 |
| 历史与恢复：reset旧消息、进程中断未提交 | test_web_history.py与test_session_persistence.py；只读时间线合法记录校验，原restore投影不改变 |
| 分发：wheel无Node、静态缺失/陈旧 | test_web_startup.py、frontend manifest测试；uv build与临时无Node安装/真实服务证据 |

独立审查发现与修复见 review.md。真实模型未测试所有MCP/团队/两协议组合；这些边界使用本次实际重跑的核心测试，不能把替身网络或注入事件称为真实供应商验收。
