## Why

MewCode 目前只能调用内置工具，接入新的外部能力需要修改产品代码。新增 MCP 客户端后，用户可以通过配置声明 Server，在启动时发现并注册其工具，继续使用现有 Agent Loop、权限交互和结构化工具结果完成任务。

## What Changes

- 使用官方 Python MCP SDK 接入 stdio 和 Streamable HTTP，自动协商新旧协议；JSON-RPC 编解码和请求／响应配对交由 SDK，不另写协议栈。
- 新增用户级、项目级 MCP 配置：`mcpServers` 按 Server 名合并，同名由项目级整项替换；支持在 `env`、`headers` 值中展开 `${VAR}`。
- 启动时连接、分页发现并注册工具；每个 Server 独立持有运行期间复用的连接，故障互不传播，应用退出回收资源。
- 使用稳定命名与结果适配接入工具注册表；扩展执行器以在父进程异步调用 MCP，保留内置工具的工作进程执行方式，Agent 不识别传输类型。
- 外部工具沿用 strict/default/bypass 和本次／会话／永久授权，补齐外部工具的规则、授权范围与展示；默认非只读、串行执行，不在 `/plan` 中开放。
- MewCode 不重启失效 Server、不重建失效会话、不自动重试工具调用；允许 SDK 恢复已发请求的 SSE 响应流，避免高层接口隐式重发或续调工具。
- 启动诊断、超时、取消、退出清理及提示中的工具范围适配外部工具，普通 Server 失败不阻止内置工具和其他 Server 使用。

## Capabilities

### New Capabilities

- `mcp-configuration`：两层 YAML 配置、整项覆盖、变量展开、字段校验、错误范围及配置快照。
- `mcp-client`：官方 SDK 与协议兼容、两种传输、启动发现、连接复用、故障隔离、取消及关闭。
- `mcp-tools`：外部工具命名、描述和 Schema 适配、结果转换、权限规则及精确授权范围。

### Modified Capabilities

- `tool-system`：同时支持内置进程工具和外部异步工具，统一执行前检查，并区分本地终止与远端取消语义。
- `tool-permissions`：允许稳定 MCP 工具标识的规则和批准记录，补充绑定 Server 身份与完整参数的外部工具授权范围。
- `agent-loop`：任务取消回收本地工作进程并结束远端请求等待，不把远端错误误判为用户取消。
- `plan-mode`：执行模式使用完整已注册工具集，规划模式继续仅允许三个内置只读工具，权限不能越过模式限制。
- `runtime-context`：执行提醒不再把可用工具数量固定为六个，规划提醒维持既定边界。
- `interactive-chat`：启动 MCP 状态、外部工具授权展示和生命周期反馈，不暴露连接凭据。

## Impact

- 新增 `src/mewcode/mcp/`；影响工具注册／执行、权限配置及存储、`app.py`、`session.py`、`prompts.py`，沿用现有 Provider 工具协议转换与调用／结果配对。
- 新增官方 MCP SDK 及其 HTTP 客户端依赖；实现前确认可安装发布版本满足 auto 协商、低层调用和流恢复约束，并锁定依赖。
- 建议配置位置为 `~/.mewcode/mcp.yaml` 与 `<root>/.mewcode/mcp.yaml`；保留供应商 `--config .env`，不改变权限 YAML 的三来源 deny > ask > allow 规则。
- 依赖已归档的 `2026-10-03-add-permission-system`；相关 delta 以已同步的主规格和当前代码为基础，保留既有权限约束，不修改主规格或归档内容。
- 后续实现需增加 SDK 合同／集成测试、真实 tmux 对话验收和独立 MCP checklist；本次仅生成规划产物，不宣称功能或验收已完成。

## Non-Goals

- 不提供 resources、prompts、sampling、elicitation、roots 或异步 tasks 等非工具能力，不自动读取工具结果中的资源链接。
- 不新增 OAuth 登录流程、旧式 HTTP+SSE 传输回退、配置热加载或工具清单动态刷新。
- 不做健康检查、应用层自动重连、Server 重启、工具自动重试或副作用回滚。
- 不实现外部 Server 的操作系统沙箱，也不把其文件访问误称为受 MewCode 本地路径沙箱保护。
