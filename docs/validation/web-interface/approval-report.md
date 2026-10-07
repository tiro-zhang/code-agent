# Web 审批域实施记录

实现范围为 `src/mewcode/web/approval.py`、`tests/test_web_approval.py`。不修改权限核心或终端审批适配器。

## 接口

`ApprovalBroker(root=..., server_instance_id=..., context=..., notify=..., redact=..., ttl=600, clock=None)`；构造阶段不要求事件循环，不创建 Future 或任务。

- `await respond(request, cancel_event) -> str`：接收核心 `ApprovalRequest`，返回 `deny`、`once`、`session`、`permanent`。四种决定仍经原 `PermissionManager.authorize` 复核和应用。
- `summaries() -> list`：返回有效审批的 `id,session_id,generation,run_id,tool,reason,expires_at`。
- `preview(id, section='arguments', offset=0, limit=32768)`：返回身份、根目录、模式、分区名、UTF-8 字节分页正文、`next_offset` 和 `total_bytes`。分区为 targets、arguments、content、scope，硬上限 32 KiB；不切断多字节字符。
- `decide(id, decision, *, server_instance_id, generation, run_id=None)`：原子地接收唯一有效回答，返回 `{id,decision}`。已处理／过期／取消／身份不符抛出 `WebError`。
- `invalidate_all()`：使全部待审批进入取消，并唤醒原权限等待者。
- `notify` 是同步变更通知，参数为 `{kind:'approval',id,session_id,status}`；`redact` 为字符串脱敏函数；可选 `clock` 是单调时钟替身。

## 合同

深复制有效参数，固定完整参数、真实目标、外部身份、批准范围和 write/edit 内容／局部 diff；预览不读取当前目标文件、不执行工具。JSON 字符串先解码再脱敏，因此带引号／反斜线的秘密和 MCP 规范目标参数也会脱敏。连接 headers／env／原始连接描述不会附带进入审批展示。

审批绑定服务、会话、运行代次和任务，取得一次决定后从待审批集合移除，保留最多 256 个状态回执；后续回答不会再触发工具。审批等待最多十分钟，页面刷新／分块读取不延长期限。到期标记 `approval_expired` 并向原权限核心返回 `deny`，不把整个任务当成主动取消；停止、参数突变、运行代次变化才设置原取消事件并走 `cancelled`。取消与决定竞争时，原等待者返回前仍优先检查取消。

最多 32 份待审批，所有完整展示快照合计最多 8 MiB。超过容量明确拒绝启动工具，不裁剪完整参数后允许批准。单个完整快照同样限制 8 MiB，覆盖通常由核心 2 MiB 参数上限产生的写入／编辑内容与 diff；极端格式化膨胀会明确报 `approval_too_large`。

## 红绿与验证

命令前缀：`UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run --no-sync`。

1. 初始 `pytest tests/test_web_approval.py -q`：模块不存在，收集失败。
2. 基础实现：12 passed。
3. 对齐到期拒绝与 32 KiB 上限：新断言先得到 4 failed、10 passed；实现修正后 14 passed。
4. 转义凭据的外部目标 JSON：先 1 failed、17 passed；修复字符串解码脱敏后 18 passed。
5. 最终相关回归：`pytest tests/test_web_approval.py tests/test_permission_review.py tests/test_mcp_permissions.py tests/test_web_history.py tests/test_session_persistence.py -q`，105 passed（4.01 秒）。

覆盖四种决定、不可变快照、字节分页、全部参数与 diff、跨服务／代次／任务拒绝、一次决定、取消／停止、可控时钟到期和真实等待超时。使用真实 PermissionManager 验证批准后的新 deny 规则仍生效、符号链接目标变化需要第二份审批、永久批准存储的真实文件系统失败保留本次放行并报告原 warning、到期得到 permission_denied 而非 cancelled。

Web API／浏览器和真实 tmux 验收由主任务统一执行，本报告不替代这些验收。
