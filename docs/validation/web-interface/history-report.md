# 只读历史域实施记录

本轮范围：`src/mewcode/sessions/browse.py` 与 `tests/test_web_history.py`；不改变 CLI 的 `scan_sessions` 或模型恢复 `build_projection`。

## 公共接口

`ArchiveBrowser(root, secret='')` 提供异步接口，阻塞文件操作通过 `asyncio.to_thread` 执行：

- `list_sessions(cursor=None, limit=50)` 返回 `{items,next_cursor}`，按最后活动倒序、ID 打破同值；最多保留四个列表快照，每份最多 4096 会话。
- `create(protocol, model)` 返回 `{session_id}`；只创建空 Journal 并关闭，不创建 Provider、MCP、Hook 或 ChatSession。
- `history(identity, cursor=None, limit=50)` 返回 `{items,next_cursor,warnings}`，每项包含 `id,kind,role,text,seq,run_id` 以及适用的 `call,result,truncated`。主消息 ID 保留原身份，子消息加运行命名空间。助手 `call` 为调用数组，工具 `call` 为 `{id}`。
- `result(result_id, cursor=None)` 返回 `{id,session_id,content,next_cursor,truncated,warnings}`，正文为安全展示文本或 JSON 文本。大正文、完整调用参数和已登记缓存通过 `item.result.result_id` 读取。
- 无效身份、过期或越界游标、目录／文件替换、无效缓存归属等抛出已有 `SessionError`，由 Web 路由映射。

## 实现与边界

历史依据合法记录构建展示时间线，保留压缩／重置之前的提交及任务／摘要／重置标记；检查点不会重复增加原聊天。孤立工具结果和与实存结果冲突的提交被排除；不完整交互展示已有证据并明确警告。上下文和保存的任务输入维持独立来源。

每页最多 50 项、JSON 正文总量控制在 512 KiB 内；结果正文块最多 128 KiB。JSON 读取器逐块解析，超长字符串只保留 4 KiB 预览和来源范围，不把完整 JSONL 或大单行读入内存。单记录最多 65536 个结构节点、2 MiB 短字符串驻留；记录及消息身份索引各最多 65536 项，越界明确提示。警告最多 32 项，未完成并行交互最多 32 份。

历史和结果签发身份绑定当前服务、会话、稳定完整行边界、inode 和前缀摘要；后续追加不混入当前分页，边界之前的修改使游标失效。列表只读检测外部写锁并禁止把占用会话标记为可恢复。存档／缓存打开使用目录 fd、`O_NOFOLLOW`、普通文件／用户归属验证；结果接口只接受签发身份，不接受文件路径。缓存还验证 source 与 tool_call_id。供应商内部续接字段不进入投影，已知配置密钥在字符串解码后及分块边界处脱敏。

## 红绿与回归

命令均使用 `UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run --no-sync`。

1. 初始 `pytest tests/test_web_history.py -q`：收集失败，`ModuleNotFoundError: mewcode.sessions.browse`，确认新功能缺失。
2. 首轮实现后：9 passed。
3. 无效创建时间用例红灯：1 failed、14 passed；修复身份头的原子验证后转绿。
4. 任务边界用例红灯：1 failed、17 passed；补充独立任务／运行边界后转绿。
5. 最终 `pytest tests/test_web_history.py tests/test_session_persistence.py -q`：44 passed。

测试覆盖空创建／只读无副作用、压缩重置、外部锁、稳定分页、尾部残缺、未完整工具组、秘密跨块脱敏、缓存归属及符号链接、跨会话游标／结果游标、原地修改、列表活动顺序、子运行命名空间、50 项／512 KiB 限制和事件循环可调度。12 MiB 单记录的 tracemalloc 峰值要求小于 5 MiB，本轮通过。

## 集成待验

本子任务不启动 Web／真实模型；由主任务统一验证浏览器导航、API 错误映射、真实 tmux 回归和完整验收清单。独立子运行工作目录的缓存不能经主会话路径越权读取；主历史未登记回流的子缓存会明确报不可用。
