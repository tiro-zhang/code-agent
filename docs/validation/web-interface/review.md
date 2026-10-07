# 独立审查及修复记录

审查对象：未提交的 add-local-web-interface 工作树；审查者使用独立上下文，只读核对规格、源文件，并在临时目录复现。

| 发现 | 修复 | 本轮证据 |
| --- | --- | --- |
| 关闭超时后 Stop 复活旧运行对象 | Stop 等待原关闭链，完成后卸载，未确认时保留占用 | test_stop_cannot_reactivate_resources_already_closing_during_switch，先红后绿 |
| 监控器内自动接续不受 Stop 等待 | 监控器只登记，独立 _job 持有接续流 | test_monitor_resume_is_owned_until_stop_cleanup_finishes |
| 密钥跨流片段可重新拼出 | 对累计展示正文和思考脱敏 | test_secret_split_between_stream_chunks_is_redacted_in_accumulated_view，先红后绿 |
| 复制标签页共享操作身份后永久冲突 | 明确拒绝后登记独立客户端，保留草稿且不自动重发；未确认操作仍核查原身份 | frontend API/Chrome 回归，详见 frontend-report.md |
| GET 回执可能来自复制页的不同内容 | 恢复时用原身份原正文再次核对已存在回执，再清对应草稿 | frontend 复制页丢失响应回归 |
| 激活历史丢失调用和结果入口 | seed 保留公开 call/result/result_id | Projection 回归 |
| 拒绝、取消、超时和未知副作用都显示失败 | 按真实 code/details 映射状态，保留原结果 | Projection 回归 |
| 撤销会话批准按钮漏传范围 | 提交 revoke 与 session | 前端点击确认测试先红后绿 |
| 准备早期无法停止 | preparing 关联目标会话，构造前取消不创建运行对象 | test_stop_can_cancel_activation_before_runtime_is_constructed，先红后绿 |
| 刷新后临时与持久消息身份重复 | 已提交消息改用原 Message.id，保持后续事件唯一关联 | test_saved_and_live_messages_have_same_identity_after_refresh，先红后绿 |
| 会话切换尚在关闭旧对象时 Stop 遗失新对象 | Stop 先等待激活所有者，旧对象关闭后再次检查取消，不再构造目标会话 | test_stop_during_switch_waits_activation_owner_without_constructing_target，先红后绿 |
| 真实维护通知覆盖工作卡片并沿用工作用量 | 维护使用独立用途身份，保留父运行关系，只展示自身实际用量 | 新增两项 projection 回归先红后绿；相关 43 项通过 |

独立最终复核通过：原有问题和切换竞态均已修复，没有剩余 Critical／Important；复核者独立运行 manager 与 projection，27 passed。审查未替代真实模型、浏览器、tmux、安装包或完整回归；各层结果另见 summary.md。
