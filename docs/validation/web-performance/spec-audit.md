# 本变更 26 个场景的本次证据

日期：2026-10-08。范围为 `optimize-web-interaction-performance/specs/web-interface/spec.md` 的全部 26 个场景。前端 56 项、浏览器 12 项、Python 2009 项均为本次执行；模拟、生产压力和真实服务分别标明。

| # | 场景 | 本次结果和证据 |
| ---: | --- | --- |
| 1 | 粘贴代码后发送 | 自动化通过：components 多行／末尾换行／IME；browser 原样提交一次 |
| 2 | 等待回执期间继续编辑 | 自动化通过：drafts exact text/version，browser 延迟回执后新编辑和刷新保留 |
| 3 | 任务忙或请求失败 | 自动化通过：components 超限仍可编辑，API 业务拒绝／未知回执／冲突保留，Python busy 不排队 |
| 4 | 长历史下继续编辑 | 生产通过：100 历史＋128 KiB 当前正文，65 输入；performance 两个输入解析计数为 0；IME 由组件及 browser 验证；滚动由 browser 验证 |
| 5 | 切换会话后的迟到回执 | 自动化通过：drafts 不匹配版本／文字不能清除，app-sync A/B 草稿，browser 不同正文复制页及迟到回执 |
| 6 | 回复一半时刷新 | 真实 Web 通过：实际未完成回答期间刷新、草稿保持、末片段正确；browser 刷新只有一次提交及回复；Python 刷新身份一致 |
| 7 | 慢客户端超出事件窗口 | 自动化通过：browser 快照替换／重复 SSE／缺失提示，Python old_event_cursor_replaces_snapshot_and_slow_subscriber_does_not_block |
| 8 | 长回复采用轻量流式展示 | 生产通过：当前 128 KiB 全文，完成恢复高亮；rendering 生成不解析、末正文和安全边界；browser 代码复制保持缩进和末尾换行；真实 Web 捕获生成片段 |
| 9 | 完成时还有待展示正文 | 自动化与生产通过：rendering、app-sync 连续替换最终正文；15 个完成附近／之后输入，保留长任务及真实完成状态 |
| 10 | 同一快照覆盖积压普通事件 | 自动化通过：sync 与真实 App performance 的 100 条通知仅一次额外读取 |
| 11 | 状态读取期间又有新事件 | 自动化通过：sync 在途追加与末事件补读、同时仅一个请求；显式核对不复用操作前请求 |
| 12 | 被快照覆盖的独立列表通知 | 自动化通过：sync 快照序号高于接收游标时仍消费 sessions_changed，替换补列表 |
| 13 | 请求失败后恢复同步 | 自动化通过：sync 有限且至少一秒退避；app-sync 一次失败及三次失败后活 SSE 新通知恢复，停止提交最新版本 |
| 14 | 服务重启后的迟到响应 | 自动化通过：sync replace/dispose 使旧请求失效；API 不重发旧操作；rendering 旧结果所属身份变化丢弃；App history epoch 及 browser 旧首页迟到；真实重启通过：旧凭据401、双草稿保持、显式继续，见 real-web.json |
| 15 | 工具等待批准 | 自动化及真实 Web／tmux 通过：审批前不执行，所有分区／分块完整审阅；approval 过期拒绝和 browser 双标签只一次决定 |
| 16 | 查看编辑结果 | 自动化通过：details JSON 参数 edit 明确标为未执行局部预览；rendering 首次展开构建 diff；Python 区分结果真实状态 |
| 17 | 内容被截断或回收 | 自动化通过：browser 登记结果分页、来源截断；rendering 结果所属身份；Python projection/result 保留预算与路径安全合同 |
| 18 | 折叠工具详情的状态变化 | 自动化通过：rendering 未展开不建参数／diff、摘要独立变化，展开后真实结果；完整审批仍单独审阅 |
| 19 | 等长正文或元信息替换 | 自动化通过：rendering 同 ID 等长替换、complete／truncated／result_id 更新；复制反馈不因同正文快照重置 |
| 20 | 无关更新期间继续审阅 | 自动化通过：rendering 展开状态／读过结果保留，无关通知不重复请求；approval/browser 审阅关联有效原身份 |
| 21 | 发送后列表响应缓慢 | 自动化通过：performance App 故意阻塞列表，停止在必要核对后仍可用；Python busy、清理和审批校验保持 |
| 22 | 已确认操作后的列表刷新失败 | 自动化通过：app-sync 独立列表错误／重试，停止可用，一次已确认操作未重发，未知操作 gate 由 API/browser 验证 |
| 23 | 控制状态先于正文展示更新 | 自动化通过：app-sync 停止 state_version=9，失败恢复后=11；sync 审批急同步；App 审批 id/session/generation/run 与权威 ref 核对，approval/browser 旧审批不可再决定 |
| 24 | 长历史与持续输出下进行交互 | 生产通过：optimized 三项 P95 69.6／66.7／1.4 ms，保留最大值和正确反馈；browser 向上阅读保持位置、分页锚点及停止所属会话 |
| 25 | 完成格式化附近存在慢样本 | 生产通过：15 个完成输入，最大 119.7 ms；完成长任务 106／74 ms 全保留，未用延时和只测流式隐藏 |
| 26 | 真实功能回归与性能证据分开记录 | 分别保存 baseline/optimized JSON、真实 Web、真实 tmux、自动化与打包日志；服务退出等待及脚本检测错误如实记录，未执行平台／模型另列 |

自动化入口：[sync](../../../frontend/tests/sync.test.ts)、[app-sync](../../../frontend/tests/app-sync.test.tsx)、[drafts](../../../frontend/tests/drafts.test.ts)、[rendering](../../../frontend/tests/rendering.test.tsx)、[performance](../../../frontend/tests/performance.test.tsx)、[components](../../../frontend/tests/components.test.tsx)、[API](../../../frontend/tests/api.test.ts)、[browser](../../../frontend/tests/browser/workbench.spec.ts)、[Python primitives](../../../tests/test_web_primitives.py)、[Python manager](../../../tests/test_web_manager.py)、[Python projection](../../../tests/test_web_projection.py)。运行结果见[汇总](README.md)。

边界：IME 是自动化组合输入事件，未做不同系统真实输入法手工验收；128 KiB 是生产构建固定压力语料，未让真实供应商输出相同长正文。快照窗口失效、不同正文复制页、存储失败及旧响应由自动化验证，不冒充真实网络断连故障注入。真实重启以 `real-web.json` 最终结果为准。
