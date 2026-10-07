# Web 实施接口记录

状态：已完成并归档的接口约定；验收结果另行记录。主规格见 openspec/specs/web-interface/spec.md，设计及变更记录见 openspec/changes/archive/2026-10-07-add-local-web-interface/。

## 文件归属

- runtime 子任务：src/mewcode/runtime/、app.py、commands/service.py、commands/adapter.py 以及相应测试。
- history 子任务：src/mewcode/sessions/browse.py 以及 tests/test_web_history.py。
- frontend 子任务：frontend/、src/mewcode/web/static/（生成物）、前端测试与构建脚本。
- 主代理：src/mewcode/web/ 其余文件、cli.py、pyproject.toml、uv.lock、集成测试、README、验收文档及任务勾选。

## HTTP JSON 约定

所有数据接口位于 /api/v1。GET /state 和 SSE 必须先认证。
POST /auth 接收 {token}，设置实例 cookie；GET /state 认证失败返回 401，前端提示使用启动链接。
POST /clients 注册本标签页操作身份，返回 {client_id,next_sequence:1,server_instance_id}。
变更请求外壳为 {server_instance_id,client_id,sequence,generation,state_version,...业务字段}。
相同序号重传按操作回执处理；GET /operations/{client_id}/{sequence} 查询，未知返回 404，过期返回 410。
错误统一 {error:{code,message}}，业务冲突使用 409；没有有效接收回执不消费草稿。
新建：POST /sessions -> {session_id}。列表：GET /sessions -> {items:[{id,title,last_activity,message_count,active,recoverable,warnings}],next_cursor}。
历史：GET /sessions/{id}/history?cursor= -> {items:[{id,kind,role,text,seq,run_id,call?,result?,truncated?}],next_cursor,warnings}。
显式继续：POST /sessions/{id}/activate -> 202 {operation_id,session_id}。
输入：POST /sessions/{id}/inputs，业务字段 {text} -> 202 {operation_id,run_id}，本地命令可返回 200 {operation_id,handled:true}。
控制：POST /sessions/{id}/controls，业务字段 {action,value?}，action 为 stop、plan、execute、compact、reset、permission_mode、revoke。
审批：GET /approvals/{id}?offset=&limit= -> {id,session_id,generation,run_id,tool,reason,expires_at,sections,section,content,next_offset,total_bytes}。sections 由后端提供，查询可加 section。
决定：POST /approvals/{id}/decision，业务字段 {decision}，取 deny、once、session、permanent。
结果：GET /results/{id}?cursor= 仅接受服务签发的结果 ID，返回有界内容及下页信息。
事件：GET /events?after=，SSE id=序号，data 为信封 {server_instance_id,seq,session_id,runtime_generation,run_id,kind,payload}。
首次／旧游标发 snapshot_replace，payload 为完整 state。其他 kind 为 state、agent、notice、approval、sessions_changed，前端可据事件合并或合并刷新 state。连接满额 429 时短轮询 /state，不能阻塞控制请求。

## State 约定

{
  server_instance_id, seq,
  project:{root,key,model},
  runtime:{session_id,generation,state_version,phase,busy,run_id,mode,permission_mode,reason,error,activities:[]},
  projection:{
    session_id,
    messages:[{id,role,text,run_id,kind,complete?,truncated?}],
    runs:[{id,title,phase,reason,text,thinking,calls:[{id,run_id,iteration,name,arguments,status,result,result_id?,truncated?}],usage,usage_by_purpose}],
    warnings:[]
  },
  approvals:[{id,session_id,generation,run_id,tool,reason,expires_at}],
  commands:[{name,aliases,description,usage,kind}]
}

phase 为 unloaded、preparing、idle、running、awaiting_approval、background、maintenance、cancelling、blocked、closing。
mode 使用核心 plan／execute；permission_mode 使用 strict／default／bypass。
运行态 projection 是当前运行会话的展示来源；查看其他会话使用 history。激活时将已保存历史作为 projection 初始消息，流式临时消息有独立稳定 ID，完成后进行身份合并避免重复。
工具 result 为公开白名单字段（ok、output、error、data、truncated 等实际可用信息），不得依赖内部 Python 对象。
前端保存每个会话草稿与原提交版本，确认回执只清除对应版本。浏览历史不 activate。调用 activate 后等待 idle 才允许新输入。

## 运行资源公共接口（由 runtime 子任务落实并报告）

期待公共资源对象接收 config、root、provider_factory、notify 和 approval_responder，负责创建／恢复 session、启动 MCP、准备恢复、启动 Hook 和关闭。
资源顺序与 CLI 完全保持，provider、session、mcp、registry 可供调用方使用。
后台唤醒抽取不应占有 Web 请求或终端输入，保留 next_parent 的单消费合同。
各子任务完成后在 docs/validation/web-interface/ 下提供实现报告与实际测试证据，任务勾选由主代理统一管理。


## 最终集成补充

- 接受的操作（含业务错误）均返回 operation:{client_id,sequence,next_sequence}；未知身份、序号或内容冲突不消费新操作。GET 回执返回 {status_code,body}，未完成为 HTTP 202 {pending:true}。
- 复制标签页可能继承客户端身份。结果不明时前端先查询原回执，已存在的完成回执再用原路由、原身份和原正文 POST 验证匹配，之后才清对应草稿；身份冲突不会自动发送新任务。
- 审批 sections 为 targets、arguments、content、scope，默认块及硬上限均为32KiB；到期返回deny并展示approval_expired，主动取消保留cancelled语义。
- /clear 和 /exit 仅在 Web 公开命令元信息中标 terminal，不修改共用核心定义。
- 事件信封只发送白名单身份和更新信号，前端合并后读取权威状态；首个snapshot_replace包含完整有界投影。
