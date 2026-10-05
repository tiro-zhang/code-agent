## Why

用户需要反复输入相同的审查、测试和提交提示词，现有固定 `/review` 又无法承载可复用的操作步骤、资源和工具范围。引入本地 Markdown Skill，让 Agent 先发现能力，再按需加载 SOP，并按任务需要共享当前对话或在独立对话执行。

## What Changes

- 支持 YAML frontmatter 加 Markdown SOP，提供名称、说明、工具白名单、执行模式、独立历史范围和可选模型；支持用户参数的字面占位符替换。
- 支持项目 `.mewcode/skills/`、用户 `~/.mewcode/skills/` 和随包内置三层来源，同名按项目高于用户高于内置覆盖；支持单文件及以 `SKILL.md` 为入口的目录包，单文件解析失败只产生局部诊断。
- 两阶段加载：对话初始只注入名称及一句说明；系统工具 `load_skill` 按需读取完整 SOP 或包内参考资源。该工具不受 Skill 白名单限制，普通操作仍遵守模式、权限和路径约束。
- 共享模式维护多个激活 Skill，在每次工作请求上下文开头重建完整 SOP；工具范围取所有激活白名单的交集，并在声明和执行两处检查。
- 独立模式使用独立历史和激活状态，继承主对话当前工具范围上限及模式／权限，仅把最终摘要与真实停止状态回流；可选择历史范围，只有该模式可指定模型。
- **BREAKING**：将固定 `/review` 迁移为可覆盖的内置 Skill；保留无参数审查当前 Git 改动的默认行为，但实际工具范围由 Skill 定义收窄。新增内置 `commit`、`test` 样板。
- 所有发现的有效 Skill 自动注册为 `/<name> [参数]`，帮助与补全共用注册快照；下一任务开始时原子热更新目录、激活内容与命令表，当前任务固定使用已加载定义。
- 新增 `/skills` 查看和停用能力，以及 `/reset` 清空模型历史和激活状态；`/clear` 继续只清屏。存档记录激活和重置边界，恢复不重跑独立任务或旧操作。
- 不实现市场分发、安装器、Skill 版本管理、任意模板执行或跨服务模型路由。

## Capabilities

### New Capabilities

- `skill-system`：定义格式、三层发现与覆盖、目录资源、两阶段加载、激活生命周期、两种执行模式、白名单交集、任务边界热更新及三个内置样板。

### Modified Capabilities

- `slash-commands`：Skill 命令注册快照、`/review` 迁移、`/skills` 管理与 `/reset`。
- `runtime-context`：每次工作请求置顶完整激活 SOP，并准确表达当前工具范围和系统加载入口。
- `system-prompt`：Skill 发现／激活位置与明确的指令冲突规则。
- `tool-system`：系统加载入口、动态白名单双重校验及独立执行的期限边界。
- `plan-mode`：允许系统 Skill 加载，普通工具仍受只读范围及白名单交集约束。
- `agent-loop`：加载串行边界、主子运行的统一请求预算与取消／用量归属。
- `session-persistence`：激活状态、对话重置和独立执行审计的可恢复投影。
- `context-management`：钉住 SOP 的预算计算与重置后的摘要状态。
- `provider-configuration`：同一服务下独立 Skill 模型的显式窗口与输出预算配置。
- `interactive-chat`：动态命令帮助、Skill 运行反馈、子任务关联及重置交互。

## Impact

- 新增 `src/mewcode/skills/`，接入 `commands/`、`agent.py`、`session.py`、`prompts.py`、`tools/`、`sessions/`、`config.py` 与终端适配；MCP 和权限服务复用现有生命周期。
- 复用现有 PyYAML 和供应商适配，不引入文件监听服务；内置 Markdown 及目录资源需要纳入 wheel／源码包。
- 更新对应 pytest 契约、README、配置示例和 `checklist.md`；功能开发后执行真实授权服务的 tmux 验收并记录本次证据。
- 本次产物仅为方案、设计、增量规格与任务清单，不修改产品代码、主规格或现有配置。
