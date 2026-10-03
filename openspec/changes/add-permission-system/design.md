## Context

动机与范围见 [proposal.md](proposal.md)。现有调用链为 `app -> ChatSession -> Agent -> ToolScheduler -> ToolExecutor -> worker -> ToolRegistry -> tool`。`ToolExecutor.execute` 在父进程做参数预校验后启动 spawn 工作进程；同步工具在子进程里执行。调度器允许连续只读调用并发，副作用调用串行。普通 ToolResult 错误会回传模型，只有取消结果触发整轮取消。

文件工具已经使用 `Path.resolve()` 加 `is_relative_to(root)`；搜索默认不跟随链接。shell 使用 `/bin/sh -c`，cwd 为启动根目录，但进程可以访问目录外及网络。输入当前只在任务间使用同步 readline；授权需要引入任务进行期间的输入通道，并修正“排入执行任务即显示开始”的事件时机。

本变更涉及执行入口、会话状态、终端输入、搜索行为和持久化，具备跨模块、安全及新数据模型复杂度，因此创建此设计文档，不跳过 design。

## Goals / Non-Goals

**Goals:**

- 在父进程形成明确的授权决定，在实际文件访问处保留路径复核；授权时展示的对象与执行对象保持对应。
- 将纯规则决策、授权记录、人工交互及目标执行分离，使规则优先级与拒绝继续行为可以独立验证。
- 在现有并发、取消、结果配对和输入交互上接入授权，避免重复询问、提前执行和串答。
- 让搜索在读内容前得到获准候选集合，并保留部分搜索结果的明确语义。

**Non-Goals:**

- 不把 regex、shell AST、cwd 或路径 resolve 视为完整进程沙箱；不展开变量、不执行命令来求值、不审查脚本内部或间接启动的程序。
- 不承诺抵御另一个恶意进程持续替换路径祖先、硬链接或任意已获准 shell 的文件写入。现有文件操作的竞态限制仍适用。
- 不新增网络限制、资源配额、审计日志、供应商协议字段、跨设备授权同步或通用规则编辑器。

## Decisions

### 1. 决定状态与提案默认值

已在探索对话中逐项确认的语义是：文件／搜索专用沙箱；三来源 deny > ask > allow；三档模式矩阵且 deny 不可覆盖；独立授权可以满足 ask；Bash glob allow 仅适用于简单命令；deny / ask 检查显式子命令且整条批准；不支持的语法拒绝；搜索逐文件授权、跳过受限目标并说明范围变化。

以下为本提案选择的**建议默认值，供整体评审**，不表述为用户已逐项确认：配置文件名与 YAML 字段、Bash 别名、CLI／斜线指令名称、授权记录具体粒度、撤销入口、配置错误处理及直接文件工具对权限配置的写保护。这些默认值在 delta specs 中形成可评审的拟议合同；本次仅创建规划产物，不代表代码已获实施授权。

### 2. 统一入口与两阶段执行

增加职责清晰的权限模块：配置读取器产生不可变策略快照，工具适配器产生待检查目标，规则引擎返回决定与理由，授权存储管理批准记录，异步审批协调器连接终端。`ToolRegistry.prepare` 继续只做纯同步名称、模式和 Schema 校验，不在它被多次调用时触发审批。

父进程 `ToolExecutor` 在 prepare 之后、计算执行 deadline 和启动目标工作进程之前调用统一授权入口。ToolContext 只携带子进程可序列化的信息，不包含终端输入对象、Future 或审批协调器。工具落地访问时仍复核真实路径。

```text
ToolCall
   |
   v
Validate + normalize + enumerate targets
   |
   v
Hard guards + supported shell analysis
   |
   v
Merge matching rules: deny > ask > allow > no-match
   |
   v
Apply permission mode
   |
   +-- deny ------------------------------> ToolResult
   |
   +-- allow --+
   |           |
   +-- ask --> grants --> prompt if needed |
                           |               |
                           +-- reject ----> ToolResult
                           |               |
                           +-- approve ----+
                                           |
                                           v
                               Recheck policy and targets
                                           |
                                           v
                                  Start target operation
                                           |
                                           v
                                      ToolResult
```

搜索的候选枚举属于受控预检查，只允许在根目录内按现有隐藏／忽略／不跟随链接策略获取路径元数据，不得读取文件内容。只有获准的候选才进入最终搜索；因此“拒绝前不启动目标工具动作”不等于不允许内部路径枚举。预检查同样响应取消，展示权限检查状态，不伪装为目标工具已经执行。

替代方案：在各工具内部各自询问会重复实现交互和规则，放进 worker 则无法可靠共享会话批准和终端；仅在 Agent 筛选工具声明也挡不住参数级越界。统一父进程入口加访问时复核更符合现结构。

### 3. 配置格式、合并与刷新

规则读取位置：

| 来源 | 位置 | 作用 |
|---|---|---|
| 用户级 | `~/.mewcode/permissions.yaml` | 手工维护跨项目规则 |
| 项目级 | `<root>/.mewcode/permissions.yaml` | 可随项目共享的规则 |
| 本地级 | `<root>/.mewcode/permissions.local.yaml` | 当前用户在此项目的规则及永久批准，加入 Git 忽略 |

root 沿用启动时的真实工作根，不跟随供应商配置文件位置或某条 shell 命令的 cd。来源仅用于定位、解释及写回；不会让“更本地”的规则盖过另一来源的 deny。

```yaml
version: 1
rules:
  - effect: allow
    rule: "Bash(git *)"
    match: glob
  - effect: deny
    rule: "search_code(secrets/**)"
    match: glob
  - effect: ask
    rule: "edit_file(src/main.py)"
    match: exact

# approvals 仅用于本地级文件，由可信终端授权入口写入。
approvals:
  - id: "approval-id"
    root: "/absolute/project"
    tool: execute_command
    scope:
      kind: command
      value: "git status"
```

`version` 缺省为 1；缺少 rules 等同空列表，本地级缺少 approvals 亦为空。不存在或空文件等同没有配置。用户／项目级不接受 approvals；本章不从共享项目文件导入伪装为本机用户决定的批准记录。三个文件都只允许声明对应版本的字段。

使用 YAML 安全构造器，并在构造映射的过程中拒绝任意层重复键，再验证数据 Schema、工具名、表达式和授权范围；不能先转成 dict 再查重。候选为 PyYAML 的局部 SafeLoader 子类，不修改第三方全局加载行为。未知字段、非法版本／规则／批准、重复键及对象标签均报错。安全加载与重复键差异已根据 [PyYAML 官方文档](https://pyyaml.org/wiki/PyYAMLDocumentation) 和 [映射构造实现](https://github.com/yaml/pyyaml/blob/main/lib/yaml/constructor.py) 核对。

每次预检查及审批结束后读取三个文件形成完整新快照；配置小且调用频率受现有 Agent Loop 限制，本章先不增加文件监听器。运行中坏配置阻止该工具，不能退回空规则或旧的更宽松快照。批准等待前后用版本摘要判断是否需重新求值，执行时仍使用重新解析的真实目标。配置在最后检查之后被另一个进程修改的竞态不宣称已完全消除。

替代方案：按层字典覆盖违反来源无优先级要求；容忍重复键会让用户写出的 deny 静默丢失；只在启动加载会使新 deny 无法及时约束后续调用。

### 4. 匹配适配与 shell 结构检查

对外仍注册 read_file、write_file、edit_file、execute_command、glob_files、search_code；规则层把 Bash 规范化为 execute_command。解析 rule 时提取工具名及最外层括号内原文，保留命令内部括号与引号，不尝试执行或展开它们。

- 文件规则使用真实路径相对 root 的 POSIX 形式。沿用按片段匹配的 glob：`*` 不跨目录，`**` 支持零级或多级；不直接用全路径 fnmatch 代替路径语义。
- shell exact allow 只比较完整原始 command。shell glob allow 只在 AST 确认单个简单静态命令且没有重定向、连接、子 shell 或展开时比较整段原始 command。quoted 普通文字由解析器判断结构，不能见到引号内 `|` 就误判为管道。
- deny / ask 收集整段原文、所有显式简单子命令的源片段和不运行展开即可获得的静态词表示。静态表示用于识别 `git 'push'` 等写法；不剥掉命令路径、不假装解析了别名、环境变量或脚本语义。
- 嵌套命令替换中显式存在的命令也需遍历；管道或连接中的任一 deny 阻止整条启动，不能执行前缀后再检查后缀。完整 exact allow 不能压过子命令 ask / deny。

AST 解析候选是 bashlex，初步可遍历 command、list、pipeline、redirect、commandsubstitution。实现前验证其与 Python 3.11 及目标 POSIX 平台的兼容性，并复核依赖采用条件。不能用 `shlex.split` 或分隔符 regex 替代语法结构。依据 [上游解析 API](https://github.com/idank/bashlex/blob/master/bashlex/parser.py)、[AST visitor](https://github.com/idank/bashlex/blob/master/bashlex/ast.py) 及 [已知限制](https://github.com/idank/bashlex#limitations)，算术展开、复杂参数展开、heredoc 内部命令等不能仅凭“parse 没报错”就认定已检查完整。

本章对支持语法采用明确白名单：静态简单命令、连接列表、管道、普通重定向、可完整遍历的子 shell 与命令替换。变量或通配符等运行时展开不具备 glob 自动放行资格；能完整识别其语法且不隐藏显式命令时可以继续按完整 exact 规则或人工授权处理。解析错误、未知节点、未完整检查的展开结构、heredoc 等尚未支持的结构返回 permission_check_failed。候选依赖保持严格错误模式并遍历所有顶层节点；不能截断 AST 后使用残缺分析结果放行。

内置黑名单作为有标识的编译正则常量先检查原始文本，命中立即返回黑名单拒绝；未命中才解析 AST 并检查静态可见命令，因此黑名单与不支持语法同时出现时黑名单拒绝优先。第一批覆盖根／主目录递归删除、格式化／擦除磁盘、原始磁盘写入与已知 fork bomb。模式数据和正反例共同版本化；命中后永远拒绝，不提供 YAML 开关。它可能保守地误拦引用文本，但不宣称能发现任意编码、间接脚本或动态命令。

### 5. 模式与批准记录

| 规则结果 | strict | default | bypass |
|---|---|---|---|
| deny | 拒绝 | 拒绝 | 拒绝 |
| ask | 需授权 | 需授权 | 放行 |
| allow | 需授权 | 放行 | 放行 |
| 未命中 | 需授权 | 需授权 | 放行 |

模式在规则合并之后应用，硬限制始终先处理。需授权包括查询已有批准，因此 strict 也允许复用本会话或永久批准；它不是“永远逐次询问”模式。plan / execute 继续决定哪些工具根本可用，`/do` 不等于同意未来每个具体调用。

批准存储由 ChatSession 持有，与历史裁剪、请求计数、计划快照分离；一次运行创建一个会话批准集合。授权范围如下：

| 批准类别 | 绑定与有效期 |
|---|---|
| 本次 | 调用 ID、完整校验后参数和已展示目标集合；一次消费，不能套用到更改后的参数 |
| 会话／永久 Bash | root + execute_command + 完整精确 command；参数 timeout 不扩展命令能力，仍按 Schema 校验 |
| 会话／永久文件 | root + 具体工具 + 真实路径；允许以后在同一路径以该工具操作不同内容，UI 明示这个范围 |
| 会话／永久搜索 | root + 具体搜索工具 + 每个真实候选路径；可用于未来不同搜索表达式，新候选仍需判定 |

不自动从 git status 推导 git *，不从一个文件推导目录权限，也不让 read_file 批准成为 edit_file 批准。重新 resolve 到不同目标时原范围不命中。新增 ask 不自动撤销已有批准，需要新 deny 或显式撤销；这样才与已确认的“有效批准满足 ask”一致。

永久批准仅写本地 approvals。采用同目录临时文件、写前变更检测与原子替换，保留 rules 及不相关批准；在无法保留并发更新时报告冲突并重读，而不是覆盖。保存失败按明确的本次批准继续，并说明没有记住。`/permissions revoke session` 与 `/permissions revoke permanent` 清除本项目对应集合，永久撤销保留 rules；下一次操作重新判定。

文件写入／编辑工具保护管理中的三份 YAML 及链接别名，避免最直接的自授权路径。可信终端授权存储可以在此工具沙箱之外完成自身配置读写。由于 shell 没有 OS 沙箱，任意已获准 shell 仍可能间接改写配置；此限制必须在文档如实披露，不能把该直接写保护宣传为进程隔离。

替代方案：把批准写成 allow 会被 ask 压住；按整个工具长期批准范围过大；把文件内容加入所有存续授权会使正常反复编辑不断重新询问。本设计用明确展示的工具与目标范围换取可预测性。

### 6. 搜索先授权后读取

将 glob_files / search_code 分为候选枚举、逐目标权限判定、获准目标操作三个阶段。枚举保留现有根目录、隐藏／忽略规则及不跟随链接行为。只在候选路径集合中逐项评估该搜索工具的规则；search_code.pattern 仍是内容正则，绝不作为权限路径。

deny 的路径以及因缺少可用授权通道而无法满足 ask 的路径直接排除；需要询问的候选可在一个请求中展示明确列表与数量，用户决定只适用于展示的集合。普通拒绝排除对应候选，不取消其他可搜索文件；Ctrl+C 则按取消处理。新的候选不能在等待批准期间悄悄加入集合。搜索执行器只能收到获准的显式文件列表，不再对整个 root 搜索后过滤匹配结果。目录枚举和内容搜索均使用参数数组，不拼 shell。

结果在既有 data 中增加 `permission_limited` 与 `skipped_files`，不把权限过滤混同于输出 truncated。所有候选被排除时返回成功空结果但标明受限；没有候选时返回普通成功空结果。受拒路径的文件名和内容不进入结果或未授权审批列表。模型能够区分“完整范围内无匹配”和“受权限限制的范围内无匹配”。

替代方案：先用 rg 搜索全根再过滤行已读取受限内容；拒绝整个广泛查询则丢失合法文件结果，违背已确认的部分搜索语义。

### 7. 终端授权与取消

启动参数为 `--permission-mode strict|default|bypass`，默认 default。任务间支持 `/permissions` 查看当前模式，`/permissions mode <mode>` 切换及两个 revoke 指令。它们都是本地控制输入，不作为模型消息，不清空历史，不切换 plan / execute。规则文件只承载规则和本地批准，不混入“哪一层 mode 覆盖哪一层”的第二套优先级。

引入 permission_requested / permission_resolved 等事件及 request_id，把等待中的 Future 保留在父进程。审批协调器按队列串行展示；并发只读工具可以各自准备，但同一时间只有一个问题占用终端。新批准写入后，排队请求重新查批准和规则，避免反复问同一操作。

用户可选拒绝、本次、会话、永久，默认回车为拒绝。界面展示完整可审阅的命令、真实目标、写入内容或编辑差异、触发原因和拟记忆范围；长内容使用可翻阅详情，不只展示截断摘要就要求批准。控制字符安全转义且保留含义，沿用密钥脱敏规则；无法提供足够审阅信息时允许直接拒绝，不自动批准。调用参数和目标摘要绑定请求，只有有效的 UI 决定才能完成 Future，模型文本不能生成批准。

stdin 由单一输入所有者负责。POSIX 真实终端使用可注销的可取消读取路径；测试使用可注入输入适配器。不要在事件循环里阻塞 readline，也不要取消等待 to_thread(readline) 后任由后台线程吞掉下一轮输入。Ctrl+C 取消活动任务并清理待审批，EOF 取消等待且按终端关闭流程退出；无人工通道的程序化调用返回拒绝，不把后续任务文本误当批准。

批准等待不占工具执行 timeout；检查结束并实际启动后才发送 tool_started。现有有界事件队列仍需在取消和关闭时排空，所有调用补齐结果。拒绝用 permission_denied + `not_started: true`，不设置 cancel；实际取消保留 cancelled 语义。

### 8. 错误合同与模型恢复

保留现有 path_outside_workspace、tool_not_allowed、invalid_arguments 等错误。新策略拒绝使用 permission_denied，并以 details 中的 source 区分 blacklist、rule、user、protected_config 或 no_approval_channel；OS 文件权限失败仍维持既有错误语义。配置无效使用 permission_config_error；结构分析失败使用 permission_check_failed。未启动动作明确 not_started，不宣称有副作用；搜索部分过滤用成功结果字段说明。

把工具拒绝回灌现有 Agent Loop，不新增拒绝次数熔断；持续无进展仍受既有 max_iterations 限制。系统提示和工具描述引导模型调整任务范围、使用获准操作或说明受限，不鼓励换写法绕过相同 deny。批准请求和本地控制指令不伪装成新的用户任务，不破坏供应商调用／结果配对。

## Risks / Trade-offs

- [regex 与 shell AST 不能限制运行时行为] → 在 UI／README 明确覆盖可见结构；不宣称脚本内、动态生成命令或 shell 文件系统已被隔离。
- [解析器能返回部分 AST 但漏掉嵌套命令] → 明确支持的语法节点，对未完整检查结构拒绝；测试复杂展开、heredoc 和嵌套命令，不能只测试 parse 异常。
- [默认未命中需授权，首次搜索可能频繁询问] → 按已展示目标集合批量询问，复用精确存续批准；不给普通工具自动配置隐含 allow。
- [项目配置中的 allow 来自不可信仓库] → 本章三来源规则按用户指定合同合并，strict 可要求实际授权；项目来源可信性和导入流程不在本章扩展，文档不将仓库规则称为用户亲自批准。
- [授权到执行之间存在路径和配置竞态] → 执行前重新求值、访问时路径复核及现有原子文件操作；保留无法对抗恶意并发替换祖先的限制说明。
- [直接文件写保护无法约束任意获准 shell] → 如实披露；进程级文件隔离留到后续，而非用命令字符串检查作虚假保证。
- [授权持久化可能冲突、失败或留下误解] → 原子更新与冲突检测，失败仅使用本次批准并明确提示，撤销与规则修改各自独立。
- [搜索结果不完整但模型误判没有文件] → 独立 permission_limited / skipped_files 字段和终端说明，不能只返回普通空集合。

## Migration Plan

1. 先验证解析依赖、规则／授权纯逻辑及关键安全反例，再接入父进程授权；不修改供应商协议。
2. 保留现有 --config 和 plan / execute API，新增独立权限模式入口。无权限文件不报错，但 default 未命中需授权属于明确行为变化。
3. 新增权限专章说明配置、三档矩阵、永久范围与撤销，将本地权限文件加入 Git 忽略；不把历史 checklist 已完成项改写成新权限功能已经通过。
4. 回归工具、调度、取消、计划与历史配对测试，再进行 tmux 真实对话，观察拒绝后模型调整、逐文件搜索和不同授权有效期，按新增 checklist 逐项记录。
5. 回退时恢复变更前产品版本，并保留或由用户移除配置数据。切到 bypass 不是回退：它仍保留硬限制和 deny。旧版本不理解新权限文件，回退不保留本章安全保障，文档必须明确这一点。

## Open Questions

- shell AST 候选依赖的具体版本、目标平台兼容性与采用条件在首项依赖验证任务中确认；若候选不满足已定义语法覆盖，替换解析适配器，不能降低拒绝合同。该选择不改变对外语义或任务分解。
- 终端长内容详情的具体翻阅按键可以在交互实现时选择，但必须满足可完整审阅、可取消和输入不串答的规格。
