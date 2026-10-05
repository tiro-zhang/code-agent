## Context

动机见 [proposal.md](proposal.md)，可观察行为见 [生命周期规格](specs/lifecycle-hooks/spec.md)。本变更跨越会话、代理、提示词、权限和工具调度模块，且需要处理执行副作用、并发和取消，因此需要独立设计文档。

当前接入条件：

- `ChatSession` 管理真实工作根、模式、会话恢复、Skill、独立维护和关闭；`ask()` 与顶层 Skill 入口需要共用用户任务边界。
- `Agent.run()` 可以在一次任务内请求模型多次，包含预算检查、摘要、流收集、工具调度和成对历史提交。UI 的事件消费者不适合作为执行控制入口。
- `PromptState` 区分预算预览、实际请求开始和成功提交；固定系统提示与工作请求上下文独立。
- `ToolExecutor.execute()` 在实际进程或 RPC 启动前完成参数、模式范围和权限检查；`ToolScheduler` 为每个调用补齐结果，连续只读调用最多四个并发，其他调用形成串行边界。
- `optimize-terminal-ui` 已完成开发，终端展示通过 `TerminalController`、`TerminalProjection` 和 `terminal/render.py` 统一处理，授权与输入共用唯一控制器。最近任务详情、成功工具聚合、F2／`/status`、维护事件隔离及普通终端降级是 Hook 接入的现有基线；已有验收材料见 [终端 UI 验收记录](../../../docs/validation/terminal-ui/README.md)，Hook 集成仍须另做本次验证。
- 权限配置只有 exact/glob，没有 regex、反向或 all/any；路径 glob 与字符串 glob 不同，shell 的规则合并和静态结构限制位于匹配器之外。
- 现有 shell 关闭 stdin，不能直接满足 Hook 的 JSON 输入契约。MCP 传输已经使用 httpx2，锁文件当前包含 2.13.1；Hook 使用独立基础客户端，不继承 MCP 的会话协议或重连逻辑。

探索中已确认三要素、显式拒绝协议、下一工作请求注入、会话内一次性尝试及权限边界。配置来源、事件清单和以下运行时细节是这份提案选定的首版默认设计，尚未实施，可在评审时调整。

## Goals / Non-Goals

**Goals:**

- 用小而集中的 Hook 模块解释配置、匹配条件、执行动作并隔离故障，生命周期模块只派发事件和处理必要结果。
- 复用权限和资源清理能力，区分目标工具、Hook 命令和后台维护的许可及取消归属。
- 使注入预算、工具拒绝配对、一次性并发和同步副作用的时序可验证。

**Non-Goals:**

- 不建立通用脚本沙箱、任意表达式求值器、插件依赖图或重试框架。
- 不迁移权限 YAML，不把动作返回文本作为新的权限或模式控制指令。
- 不重构现有记忆维护为用户 Hook，不让 subagent 占位调用独立 Skill 代替；范围排除项见 proposal。

## Decisions

### 1. 显式派发与职责划分

新增 `src/mewcode/hooks/`，建议划分如下；类名和文件拆分可在实现中沿用邻近代码风格调整。

| 部分 | 职责 | 主要依赖 |
|---|---|---|
| models / events | 不可变规则、事件快照、动作结果和事件字段目录 | 公共类型、JSON 编码 |
| config | 严格 YAML、全文件校验、编译条件及动作字段 | PyYAML、共用匹配器 |
| conditions | 字段选择、扁平 all/any、缺失与类型判定 | 事件目录、共用匹配器 |
| actions | 命令、HTTP、提示词及 subagent 占位 | 权限、受控进程、专用 HTTP 客户端 |
| runtime | 声明顺序、一次性标记、后台任务、关闭与异常隔离 | 已校验快照、动作执行器 |
| prompts | 待注入文本与请求意图的预览、消费 | 工作请求准备及上下文检查 |

在会话和 Agent 核心使用可 await 的显式派发，而非监听终端事件后执行动作。前置拦截必须在目标启动前取得结果；终端消费者关闭或替换后行为仍需相同。派发返回结构化内部结果，生命周期入口只关心是否拒绝、提示词准备和诊断。

### 2. 单一项目来源与可审阅的 YAML

启动时读取真实工作根的 `.mewcode/hooks.yaml`，只解析一次，不在当前会话热重载。来源路径与列表索引组成内部规则身份，不要求用户增加第四个必填字段，也不新增显式 priority。不存在文件使用空快照；任一错误停用整个文件，逐条收集能安全获得的错误，不部分启用看似有效的规则。

```yaml
version: 1
hooks:
  - event: session.start
    once: true
    action:
      type: prompt
      text: "修改完成后运行与改动相关的检查。"

  - event: tool.before
    if:
      all:
        - field: tool.name
          match: exact
          value: edit_file
        - field: tool.target_path
          match: glob
          value: "src/**/*.py"
    action:
      type: command
      command: "python .mewcode/hooks/check_edit.py"
      timeout_seconds: 10

  - event: tool.after
    if:
      all:
        - field: tool.name
          match: exact
          value: edit_file
        - field: tool.result.ok
          match: exact
          value: true
        - field: tool.target_path
          match: glob
          value: "src/**/*.py"
    action:
      type: command
      command: "python .mewcode/hooks/format_changed_file.py"

  - event: turn.end
    async: true
    action:
      type: http
      url: "https://example.invalid/mewcode-events"
      method: POST

  - event: turn.end
    action:
      type: subagent
      agent: reviewer
      prompt: "检查本轮修改。"
```

示例脚本和端点不随此规划文档创建；formatter 从 stdin 中自行读取固定事件契约。运行默认 command 超时 30 秒，上限 120 秒；HTTP 总时限 30 秒。一次性和后台默认 false，action 按类型严格接收字段：command / timeout_seconds、text、url / method / headers、agent / prompt。

与分层配置或逐次热重载相比，单一快照减少来源合并、身份漂移和执行期间更换动作的复杂度。用户层及本地层可在另一个变更中加入。

### 3. 共享底层匹配，保留权限的独立解释

将纯匹配能力抽到公共模块，提供字符串／路径 glob、完整 regex 及完整 exact。权限模块仍解析原有 `effect/rule/match`，继续处理 Bash 别名、MCP 规范 JSON、deny > ask > allow、原始命令及可见子命令、glob_allow 和授权记录；它只复用字符串或路径的基础匹配，不能接受新的 Hook DSL。

Hook 条件只接受 `field/match/value/negate`，组合只接受一层非空 all 或 any；不支持 Python、shell 或模板表达式求值。exact 按 JSON 值及类型比较；字符串大小写敏感。glob 与 regex 仅作用于字符串，regex 采用 fullmatch，用户需要子串匹配时显式写出 `.*`。缺失字段或不兼容类型先返回不匹配，不进入反向处理，避免 `negate` 意外覆盖不具有该字段的事件。

事件目录集中列出顶层字段及各事件的 message、tool、context、mode 字段。工具参数允许声明点分的嵌套对象字段，列表下标和可执行选择器不在首版支持范围。事件必定不存在的已知字段作为配置错误；动态工具参数在运行时缺失则不匹配。本地 tool.target_path 使用 checked_path 得到真实项目相对路径；原始 arguments 保持独立，MCP 参数不自动套用本地路径身份。

### 4. 生命周期事件与子运行关联

| 层级 | 事件 | 接入及边界 |
|---|---|---|
| 会话 | session.start / end | 新建或恢复且初始化完成后幂等开始；关闭时统一结束，包含 new/resume 和实际关闭原因 |
| 轮次 | turn.start / end | 顶层普通或 Skill 用户任务，成功、取消、错误、预算耗尽都收尾；控制指令不创建任务 |
| 消息 | message.user | 顶层用户任务的真实输入一次，不将提示词上下文或工具结果计作用户输入 |
| 消息 | message.before_request | 工作请求意图创建后、请求上下文预算检查前；预览和压缩重估不重新创建意图 |
| 消息 | message.after_response | 工作流正常完整收集后、工具调度前；部分流不触发 |
| 工具 | tool.before | 当前范围与权限通过后、进程／RPC／系统处理启动前 |
| 工具 | tool.after | 结果确定并清理后，统一在保存和发布工具结果前派发，覆盖未进入执行器的拒绝和取消 |
| 系统 | mode.changed | 成功提交实际模式变更后；失败或重复保持同一模式不派发 |
| 系统 | context.before_compact / after_compact | 自动、手动及恢复摘要尝试，携带 purpose、成功／失败／取消／无操作结果 |

使用运行时生成的稳定会话关联，即使未启用存档也能区分会话；已有存档 ID 单独保留为来源关联。快照包含 event、时间、mode、permission_mode，适用时包含顶层任务、当前 run、parent run、请求意图、当前预算信息和工具 call ID。只复制适用的用户文本、完整助手结果或有效工具参数，不默认复制全历史、签名思考块或服务连接配置。

独立 Skill 子运行继承父 Hook runtime、规则与 once 状态，并使用自身消息／工具关联。会话、顶层 turn 和真实用户输入只由顶层所有者派发，避免子 finished 导致重复轮次结束。此机制不启用 subagent 动作。生命周期幂等状态位于会话／运行所有者；为直接使用 Agent 或无终端消费者的路径提供同一派发入口。

模式切换目前由 `SessionCommandContext.set_mode()` 调用同步会话方法，需要将该适配入口及对应命令处理器改为可 await，统一等待后台清理、模式提交和变更 Hook；不要在终端投影中补发事件。持久化失败仍保持原模式，不发布变更 Hook。控制指令等待期间由应用绑定自己的取消归属，不创建普通用户任务；status、预算展示及纯环境刷新不能误触发 request Hook。

### 5. 工具拦截及同步副作用调度

目标工具的既有检查先完成，Hook 不能运行在被规划范围或显式 deny 禁止的目标前面。流程是：

```text
prepare + current range + permission
                 |
                 v
           tool.before
                 |
       +---------+---------+
       |                   |
       v                   v
  valid deny          no valid deny
       |                   |
       v                   v
 hook_denied         execute + cleanup
       |                   |
       +---------+---------+
                 |
                 v
            tool.after
                 |
                 v
        save + publish result
```

前置命令只在 exit_code 为零时解析完整 stdout JSON 对象；HTTP 只在 2xx 时解析响应对象。`decision: deny` 必须有非空 reason；allow 可无 reason；对象不含 decision 不形成拒绝。出现 decision 时，未知值、错误类型或缺少拒绝原因是故障；非 JSON 输出在前置事件只产生诊断。首个有效拒绝短路本调用剩余前置规则，返回结构化 hook_denied、not_started 及拒绝来源，不设置父 cancel 信号。其他事件不解释输出为拦截，更不会修改既有结果。

after 放在调度器统一结果出口，包含非法参数、未知工具、权限及 Hook 拒绝、正常完成、超时和未启动取消。通过结果对象和 call ID 做至多一次通知；执行器不再另发 after。取消或关闭时仍形成事件与配对结果，但命令／HTTP 动作被安全状态跳过。

首版只要配置存在同步 command/http 的工具 Hook，就将工具批次保守设为单并发；条件结果可能依赖工具结果，因此不靠目标工具只读属性推断 Hook 只读。没有此类规则保留现有只读并发四个与非只读串行边界。后台动作不提供上述顺序保证，格式化默认同步。此选择比提前推断每条规则是否能匹配更容易验证，代价是部分条件实际不匹配时也减少并发。

### 6. 动作执行、许可和防递归

命令使用独立 action runner；复用 shell 的 analyze_command、PermissionManager 和可终止进程组能力，不调用带 Hook 的普通工具调度入口。执行固定 `/bin/sh -c`，stdin 为事件 JSON，cwd 为真实项目根；持续有界收集输出，超时和取消都 TERM/KILL 并回收进程组。支持 stdin 的进程能力与普通工具的默认 DEVNULL 模式分开，不能改变现有模型命令的输入契约。

前台命令使用现有交互授权，执行时限从批准后实际启动计时；若授权拒绝，仅产生 Hook 故障，已独立获准的目标工具继续。后台命令采用非交互判定：允许规则或有效批准可运行，需要 ask 时跳过并诊断，不使用假的自动批准，也不改变现有权限配置。实际启动前重查模式、关闭、取消与许可；Hook 放行或动作成功不顺带批准目标工具；人工明确选择的会话／永久批准仍按既有项目根及完整精确命令范围复用，来源只是审阅身份，不另建授权域。

前台授权复用 `TerminalController.approve()` 的唯一授权界面，在不可变展示快照中标明 Hook 来源、规则位置与事件；命令本身仍接受原权限判定。授权可发生在启动或控制指令阶段，不能沿用当前退出授权后固定回到 running 的假设：保存所属阶段及取消归属，结束时优先保留当前取消／关闭状态，否则恢复所属阶段。控制指令等待期间使用独立 control 输入阶段，审批后仍禁止输入，直到清理结束才回到 idle；不重置最近任务。Hook 不另建输入读取器，也不通过进入普通任务来获得授权界面。

HTTP 使用专用 httpx2 基础异步客户端及独立总期限，明确声明直接依赖；不复用供应商客户端或 MCP 的 LifecycleHTTPClient。支持 GET、HEAD、POST、PUT、PATCH、DELETE、OPTIONS，默认 POST；使用 request 的 JSON 请求体发送快照，其他方法的接收端需接受该事件协议。url 固定为绝对 HTTP(S)，不接受凭据 userinfo 或 fragment；headers 为明确字符串映射，不从供应商连接复制。响应最多收集 64 KiB，不自动重发、不跟随 3xx。前置事件解析决策，其他事件只处理请求成功或失败，不把响应作为提示词。网络取消结束本地等待，不保证远端停止。

prompt 仅提交静态 text，不新增模板执行语言；subagent 校验 agent/prompt 后返回明确的未实现诊断，不能转接现有 Skill runner。诊断只记录规则位置、关联和安全的错误类型，不默认包含原始正文、headers、stdin、stdout 或 stderr；拒绝 reason 作为模型可见工具结果另行处理。

规划模式跳过 command/http，不能用 allow、旧授权或 bypass 放开。切入 plan 前先禁止新的副作用提交并有界清理执行模式的后台命令与 HTTP 等待；远端已发生的效果不被描述为撤销。模式切换失败保持原模式，不自动重试已经取消的后台动作。

### 7. 注入队列和工作请求意图

将待注入文本作为会话运行时队列，条目保留规则与触发来源。下一次主或独立 Skill 的实际工作请求均可消费；摘要、恢复、记忆维护使用独立请求准备，不消费该队列。完成 turn.end 的注入可等待下一任务，关闭时未消费文本直接释放，不为其请求模型。

工作请求准备先检查可信取消和剩余预算，再建立 request_id，派发 before_request、收集注入并预览完整请求。上下文处理可落盘或摘要，但同一 request_id 不再派发前置消息 Hook；摘要期间新入队文本在最终工作请求估算中补入，再检查整体预算。新工作重试建立新 request_id。纯 peek 不增加序号、不消费队列，也不执行 Hook。

实际请求开始时消费选定队列并沿用原预算计数；发送前取消保留文本，已开始请求失败也算一次消费。将文本并入可配对提交的 context 消息，成功流随原交互原子提交，异常流不伪造历史。固定系统提示、激活 SOP、模式说明、工具结果配对及专用摘要指令保持原职责。注入过大走现有有界恢复、预算耗尽或 context_blocked 路径；不静默跳过预算。

### 8. 一次性提交与后台生命周期

once key 使用已加载来源及规则索引，集合只在会话内存保存；reset 不清空，重启或恢复生成新集合。独立 Skill 共享该集合。条件不匹配、模式禁止、取消或后台缺少非交互许可时不提交尝试；提交前原子 claim，前台授权失败、普通执行失败、超时或占位返回都不返还。两个并发触发不能同时执行同一 once 规则。

后台任务注册到会话 runtime，保存独立的取消归属及原始快照。采用内部有限并发和有限待处理队列，容量不足记录未提交诊断，不阻塞主 Agent、不消耗 once；首版建议最多四个运行和三十二个等待，不新增用户级执行控制字段。队列任务实际启动前复查当前安全状态和命令许可。前台取消只停止自身关联动作，其他已独立提交的后台任务继续；模式切换和会话关闭是统一的清理边界。

会话关闭先禁止后台提交，再至多一次派发 session.end；关闭阶段的规则只可在剩余关闭期限内同步处理，不能等待新的人工授权或启动新的后台工作。随后在整体有界宽限内结束请求等待并回收本地进程，再关闭 Hook 专用客户端及既有会话资源。原任务结果和模型 usage 不受收尾故障影响。

终端控制器保持可用直到会话与 Hook 清理完成，并在关闭开始时进入 closing。任务取消期间持续保留“正在停止”，清理、授权结束或迟到的后台通知都不能提前恢复输入阶段；所属任务收尾后才由原入口恢复可输入状态。

普通 Exception 在条件／动作／诊断边界隔离，日志处理自身异常也不能逃逸。asyncio.CancelledError 与可信 cancel_event 按所属任务清理并保留取消状态，不能用宽泛捕获把用户取消变成继续执行；后台任务自己的取消不得设置新任务的父 cancel_event。

### 9. 终端展示、授权与输入阶段适配

Hook 执行由核心生命周期控制，终端只接收必要的展示和授权信息。需要展示的 Hook 信息带有明确来源和原始会话／任务关联，经统一控制器及安全文本处理后输出；Hook 自身故障仍只记日志，命令 stdout／stderr、HTTP 响应及 prompt 入队不自动打印到会话正文。不直接写终端流，也不把 Hook 通知包装成普通工具、模型 usage 或 memory_update。

独立 Hook 通知在进入普通任务状态更新前单独路由，不调用 begin_task、不重置 TerminalProjection 或最近任务详情，不进入成功工具聚合及任务工具计数；启动通知和上一任务的后台通知都不能取代当前 F2／`/status` 所展示的任务。`hook_denied` 仍是原目标工具的失败结果，保留原 call ID、拒绝原因和 not_started，沿既有异常工具结果路径即时展示，不为 Hook 额外生成一个工具结果。

授权仍优先占用现有授权界面，并沿用详情面板让位、输出暂存和安全渲染规则；关闭授权后恢复所属阶段。后台任务不能抢占输入或请求交互授权，其展示通知保留输入草稿、光标和当前授权状态，也不能用原任务的取消信号控制后一任务。

实现时复用现有投影、授权及输入测试，增加 Hook 来源的聚合隔离、详情保留、启动／控制阶段授权恢复、取消收尾、后台通知和普通终端降级用例。既有终端 UI 验收只作为集成基线，不计作本变更的完成证据。

## Risks / Trade-offs

- Hook 按要求故障放行，检查脚本坏掉时仍可能执行已获许可的工具。缓解：拒绝必须是显式成功结果，基础限制继续由既有权限与硬限制承担，诊断说明实际失败。
- 同步工具 Hook 可能有未声明的副作用，原只读批次因此不能安全并发。缓解：首版保守切为单并发；后台动作明确不保证与后续工具的顺序。
- shell 权限约束命令文本而不是脚本内容，且现有 shell 不是工作根沙箱；HTTP 配置是明确声明的外部动作。缓解：保持既有限制和日志边界，不宣称新隔离保证；共享配置示例不含真实凭据。
- 一次性状态未持久化，恢复会话会再次尝试；HTTP 取消后远端可能仍有副作用。缓解：文档明确边界，不自动重发，请求接收端可使用会话／任务／规则关联做自己的幂等处理。
- 过长注入或反复触发的压缩 Hook 可能占满窗口或预算。缓解：计入完整工作请求估算，复用原有摘要预算和熔断，不重复派发同一请求意图。

## Migration Plan

1. 先抽取共用纯匹配能力并运行现有权限回归；权限 YAML 不迁移。
2. 加入规则校验、动作运行时和请求准备接口，再按会话、代理、调度及系统维护边界接入。
3. 没有 hooks.yaml 时使用空运行时，保留原有调用路径；用户自行创建已审阅的项目规则后启用。
4. 同步 README、checklist 和命令／HTTP 接收事件的示例；代码实现后运行完整 pytest、构建及本次真实模型 tmux 验收，同时检查 Hook 接入后的授权、详情、输入和取消行为。
5. 撤除或重命名 hooks.yaml 并重启可停用自动化；不删除会话历史、权限批准或已发生的动作效果。
