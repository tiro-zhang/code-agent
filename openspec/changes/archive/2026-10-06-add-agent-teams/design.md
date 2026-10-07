## Context

动机和范围见 [proposal.md](proposal.md)。这是跨运行、跨进程的团队层，不是给普通子任务多加一个名字。

- `agents/service.py` 将普通委派绑定到当前父任务；`ChildRuntime.run_loop()` 在单次执行结束后关闭 Agent、Hook 和借用的 Provider。`TaskManager` 的记录及结果邮箱在进程内，恢复普通会话不重建队列。
- `ChatSession` 已有单主运行锁、请求边界通知消费、合法历史检查点和控制状态恢复；这些机制可以复用，但成员需独立会话归属。
- `PermissionManager.fork()` 只允许收紧模式，换 root 不重绑原批准；现有子运行无人工授权通道。计划批准必须是另一个门禁，不能变成授权。
- Worktree 管理器已有仓库验证、明确根身份、初始化、运行租约及成果保护。一次性退出与过期清理不能直接用于团队长期目录。
- 供应商配置使用 `dotenv_values(..., interpolate=False)` 读取指定文件，没有把文件内容当作进程环境；coordinator 主动启用需单独读取真实环境。
- 本次设计适用条件成立：新增跨进程运行模式、持久数据模型和权限边界。当前已完成的 `add-worktree-isolation` 是代码基础；本变更不代为归档或同步它。

## Goals / Non-Goals

**Goals:**

- 将 Team、Member、Goal、Task、Run、Message 和 IntegrationOperation 分成不同身份，避免用一次性 task_id 承担长期成员身份。
- 两种后端使用相同协作、存档和失败合同；执行器保持逐运行实际 cwd，团队存储不扩大文件工具根。
- 持久化在副作用前形成可恢复边界，并区分消息保存、通知、消费、运行结束、成果提交及验收整合。
- 在现有 deny 优先、规划上限、非交互许可和有限预算之内开放成员能力。

**Non-Goals:**

- 不改变普通 defined／fork 的禁止嵌套及一次性语义，不复活普通后台队列。
- 不提供全面文件系统禁写沙箱，不将 coordinator 保留的 shell 描述为只读。
- 不复制父未提交源码，不自动 stash、push、强制覆盖用户分支或删除成果。
- 不新增脱离 Lead 存活的服务，不实现跨机器通信、消息实时流或条件依赖表达式。

## Decisions

### 1. 新团队层和能力接口

新增 `src/mewcode/teams/`，建议分为 `models`、`store`、`tasks`、`mailbox`、`runtime`、`backends`、`service` 和 `integration`。这些是内部模块建议，外部工具和状态合同由规格约束。

```text
ChatSession / Team Lead
          |
          v
     Team Service
          |
    +-----+-----------+------------+
    |                 |            |
    v                 v            v
Team Store       Member Runtime  Integration
    |                 |            |
    +--> Tasks        +--> Agent   +--> Git / Worktrees
    +--> Registry     +--> Journal
    +--> Mailboxes    +--> Backend
```

Team Service 提供创建、恢复、暂停、成员管理和身份能力视图。Lead 单次模型执行仍走现有 Agent 循环；任务拆解和冲突判断由模型负责，存储校验、领取、审批门禁、后端启动及 Git 状态核查由应用负责。不能仅靠提示词保证并发及权限。

替代方案是在 `TaskManager` 中把 completed 改为 idle：这会混淆运行终态与成员身份，还会使普通子任务恢复及父取消规则发生全局改变，因此采用独立团队运行类别。普通子 Agent 可以继续使用，但不获得团队工具；coordinator 禁止普通委派旁路。

### 2. 身份、目录与私有存储

团队固定绑定经过验证的 `common_git_dir` 和创建工作区；同时登记目标分支和完整基线提交。允许同一仓库合法 Worktree 内的显式接续，不能因路径相似接受其他仓库。团队名称建议只接受 `[a-z][a-z0-9_-]{0,63}`，用户域同名拒绝覆盖。

```text
~/.mewcode/teams/<name>/
  team.json                 # 团队及成员名称注册表的权威快照
  tasks.json                # 当前及历史目标的共享任务
  lead.lock                 # 活动 Lead 所有权
  state.lock                # 团队元数据短事务
  tasks.lock                # 任务读改写短事务
  inboxes/<member-id>.json
  locks/inbox-<member-id>.lock
  locks/member-<member-id>.lock
  members/<member-id>/
    runtime.json            # 运行代次及可观察状态，非角色授权来源
    session.jsonl
    cache/
  integration/<operation-id>.json
```

应用目录 0700、普通记录 0600，逐级拒绝链接和特殊文件，按现有安全目录接口发布。`team.json` 同时保存成员 ID 与名字映射，避免登记与注册表双文件更新不一致。`runtime.json` 不是路由或授权依据；目标仍须与权威登记及实际运行所有者核对。

核心记录：

| 记录 | 关键字段 |
| --- | --- |
| Team | version、team_id、name、repository、workspace_root、lead、status、members、active_goal、revision、时间 |
| Member | member_id、name、role 与指纹／冻结正文、workspace_root、branch、backend、require_plan_approval、session_ref |
| Goal | goal_id、原始目标、基线、目标／整合分支、Lead 预算、状态 |
| Task | task_id、goal_id、描述、owner、claim_id、depends_on、state、revision、成果／整合引用 |
| Run | run_id、member_id、goal_id、task_id、claim_id、backend_generation、预算、真实停止原因 |
| Message | message_id、type、sender_id、recipient_id、body、summary、timestamp、read、关联及 protocol_version |
| IntegrationOperation | operation_id、输入提交、pre_head、实际结果、状态、验证证据、回滚结果 |

角色首次实例化采用已发现合法定义，并将本成员固定角色正文与指纹存入私有记录；恢复不因同名角色热更新换掉既有职责。新的角色或模型变更只能由 Lead 在空闲成员上显式更新并形成新版本。当前项目指令、Skill 及权限仍从实际工作根重新加载，冻结正文不是冻结旧批准。配置仅保存角色模型选择和非敏感摘要，凭据从本次指定配置重新读取。

暂不采用 SQLite：文件邮箱是用户要求，现有项目已有私有 JSON、JSONL 和跨进程锁基础。单机有界团队可使用小快照；任务和邮箱存储达到公布容量时返回明确错误，不无限读写大文件。存档记录可分页查看，邮箱已消费内容可在验证消费检查点后归档，未读不删除。

### 3. 锁及事务边界

所有短事务使用固定锁文件上的 `flock`；保留锁文件 inode，不按时间 unlink 后重建。锁竞争使用可取消的异步退避，建议从 20ms 增至最多 200ms，总等待最多 5 秒。超限为可重试 busy；元数据年龄用于诊断，不替代实际锁。

持锁期间重读和验证版本，写同目录临时文件、flush／fsync、原子 replace 并同步父目录。长期 Lead 所有权和成员运行所有权使用单独锁，不能拿长期锁充当其他进程必须取得的元数据事务锁。

原则上不同时持有两个短事务锁。名称解析先得到稳定成员 ID，再在邮箱事务前复查权威登记；暂停、移除、发送竞争以稳定身份及状态校验处理。一个 ID 在同一团队中不重用，改名不重分配邮箱。跨邮箱广播、Git、会话和共享任务发布不宣称全局事务，使用消息 ID、操作 ID 和可核查状态连接步骤。

### 4. 工具及身份矩阵

采用固定工具名与动作 Schema，不按成员名生成工具：

| 入口 | 普通主入口 | Lead | 成员 | 普通子 Agent |
| --- | --- | --- | --- | --- |
| `team` 创建／恢复／查看／暂停 | 受限允许 | 允许 | 禁止 | 禁止 |
| `team_member` 派生／停止／查看 | 禁止 | 允许 | 禁止 | 禁止 |
| `team_task` CRUD／领取／提交 | 禁止 | 允许 | 按动作允许 | 禁止 |
| `team_task` 验收／重新指派 | 禁止 | 允许 | 禁止 | 禁止 |
| `team_message` 点对点／广播／协议 | 禁止 | 按协议允许 | 按协议允许 | 禁止 |
| `team_integrate` 整合／核查／最终合并 | 禁止 | 允许 | 禁止 | 禁止 |

team_id、sender_id 和实际存储位置由运行绑定注入，不能让模型任填邮箱路径或冒充 Lead。team 的创建成功是普通主入口升级 Lead 的可信边界；同一响应后续调用需重新检查更新后的身份，但普通子 Agent 不随父升级而变为成员。

执行层在 system_handlers 前验证身份，再验证具体动作、计划门禁、模式及权限。混合动作工具保守声明为非只读串行，只有明确只读调用路径在能正确分类时才并发；不能将 CRUD 整体标成只读。普通文件工具禁止修改团队控制存储，shell 的现有可访问范围继续如实说明，身份控制不是同用户恶意进程的安全隔离。

coordinator 自用范围移除 write_file、edit_file、普通 agent、独立 Skill 和 MCP；保留内置读类、受限共享 Skill 读取、shell 及团队工具。创建成员时使用团队授权能力上限，不使用 coordinator 本人过滤后的工具集合；仍与角色、Skill、当前父策略、后台约束和 plan 取交集。这解决“Lead 不编辑而队员可编辑”，并防止把范围分离误当成提权。

### 5. 两类后端及完整实例启动

运行器提供 `probe`、`start`、`notify`、`stop`、`inspect` 接口。

- **inprocess**：独立协程，持有自己的 Agent、消息、权限视图、ToolContext、Skill 和 Hook scope；Provider／MCP 可复用现有受控共享设施，所有权不能随单个成员结束释放。
- **tmux**：在受管窗格启动完整成员实例，独立初始化 Provider、MCP、Hook、会话及工具。启动使用本次可信配置的绝对路径和成员登记身份，不在 argv 携带密钥。内部成员启动入口需要登记运行代次、启动凭证和排他成员锁，不能靠可任填 CLI 参数取得 Lead 身份。

auto 优先探测 tmux 命令、server／socket 和受管会话能力。已有 TMUX 环境时定位经验证的目标会话；否则建立应用自有会话并展示查看／连接方式。实际建立窗格属于启动，必须在成员记录与权限检查后执行，不能把探测冒充实际成功。探测无能力时展示原因选择协程；创建窗格或就绪握手失败不降级。

成员持久登记实际后端，恢复继续使用该后端；需要切换时由 Lead 在确认停止的空闲成员上明确发起，不由环境变化隐式迁移。受管 pane ID、socket、会话、成员 ID 和随机运行代次联合验证，不能只用 PID 或窗格标题确认归属。

tmux 通知采用应用专属 channel 的 `wait-for`／`wait-for -S` 唤醒运行器，不使用 send-keys 注入正文。该机制唤醒同 channel 等待客户端，见 [tmux 官方手册](https://man.openbsd.org/tmux.1#wait-for)。成员收到控制唤醒后自行读邮箱；信号不是消息事实源。重启等待与通知竞争由请求／空闲边界重查邮箱及低频存活检查补足，不靠实时流。启动命令即使需要经过 tmux 的 shell 参数，也由可信参数安全引用构造，用户名称与正文不拼入命令。

容量建议沿用 4 个运行、32 个排队默认；状态等待或计划审批不持续占用模型并发槽。进程后端 Lead 退出仍必须停止成员，不能因 tmux 自身可以持续存活就改变已确认生命周期。必须确认受控进程和关联工具真正停止，才能释放成员所有权；超时进入 needs_review。

### 6. 成员状态、计划和预算

```text
registered --> starting --> idle --> planning --> awaiting_approval
                            ^                         |
                            |                    approved
                            |                         v
                            +----- checkpoint <-- running
                                      |
                                 storage failure
                                      v
                                  needs_review

running --> stopping --> stopped
failed / budget exhausted --> blocked
```

状态与任务状态分离。自然 model_done 后先提交历史及缓存、等待受控动作收尾、保存 idle，再通过可靠协议通知 Lead。资源可以释放；下一次唤醒从磁盘创建执行对象，稳定 Member 与 Session 不变。tmux 窗格中的运行器可以保持无模型请求的待命状态；暂停团队时运行器也退出。

需要计划审批的成员可只读探索，通过 `plan_request` 提交任务、计划 ID、版本和正文。等待不持续请求模型。`plan_decision` 仅接受 Lead 来源及当前版本，决定先持久保存后改变门禁；驳回恢复规划，修订递增版本并清除旧批准。执行中实质改变计划也必须返回门禁。task、claim 和计划三者绑定，不能将上一任务批准移到新指派。

个人计划门禁允许计划通信及任务状态查询，禁止 task 的执行性变更、普通副作用和指派。团队级 `/plan` 更严格：先停止 execute 运行及整合，禁止派生、任务变更、执行批准和同步；合法规划通信只能唤醒只读运行。`/do` 只切模式，明确用户目标或 Lead 指派才重新开始执行。

每个 Task 执行保存预算使用量，消息和计划阶段消耗同一任务的预算；等待不会补额度。Run 用于记录一次实际执行段，不是刷新任务预算的入口。自然完成后的新指派建立新任务预算；已耗尽或失败任务需 Lead 明确重新指派。普通消息可以继续保存，不能自动复活耗尽任务。Lead 通知接续使用该 Goal 原剩余预算，沿用单主运行和输入优先，耗尽后只显示待决事项。

### 7. 名称解析、邮箱及消费协议

发送流程：验证真实发送者与动作 → 解析 team.json 中名字到稳定 ID → 取得邮箱锁并复查登记 → 校验上限及消息 ID → 保存 → 通知运行器。点对点路径不经过 Lead。广播取发送时有效参与者快照，排除自己，使用同一 broadcast_id 加接收者身份形成幂等投递键，逐邮箱返回保存／通知状态。

建议正文上限 64 KiB、摘要 256 字符；摘要默认正文摘取，不调用模型。ID、timestamp、read 和身份均由应用生成。协议 v1 包括 `text`、`task_assignment`、`plan_request`、`plan_decision`、`idle`、`result_submitted`、`shutdown_request`、`shutdown_ack`。严格校验类型专用字段和发起者权限，协议状态不从 text 内容推断。发送工具的重复调用也使用持久调用身份形成去重键，不以重新生成 ID 掩盖不确定提交。

消费流程：读取未消费消息 → 按 ID 去重并形成带来源 context → 提交合法历史与消费 ID → 更新邮箱 read → 在实际请求边界注入。跨两个文件的最后一步失败时，存档消费记录是事实源；恢复修复 read，不重注入。read 只表示进入合法上下文，业务结果另用协议回复。流式请求输入不受后来消息改变。

成员事件导致 Lead 接续时，先持久进入 Lead 目标上下文并按消息 ID 去重，再沿用单主运行锁和原目标预算。暂停、目标取消或 generation 失效阻止自动唤醒。必要通知应能从成员结果和任务记录补查，而不是完全依赖一次信号。

### 8. 共享任务、依赖与接纳

tasks.json 在专用锁内完成校验和发布，使用 revision 乐观冲突检查。简单 DAG 限于同目标任务 ID 列表；缺失、自依赖、重复和环均拒绝，活动任务不能改依赖以偷跑。删除只允许无引用且未活动任务，历史成果不级联销毁。

```text
pending --> claimed --> running --> submitted --> accepted --> integrated
               |          |                            |          |
               v          v                            v          v
            blocked     failed                      blocked     completed
```

task 的 state 描述工作推进；是否可运行由依赖、整合引用和成员同步事实计算，不把 blocked 当作复杂依赖表达式。成员只更新自己的领取身份与结果；Lead 才能验收、退回或重新指派。非代码任务 accepted 后可 completed，但依赖若需要代码仍必须关联真正的整合提交。

领取原子检查目标、owner、claim_id 及前置结果。代码任务领取后先同步所需团队 HEAD，记录实际同步提交，失败仍保留领取及 blocked 原因；只有同步成功才给执行工具。成员已有未知修改时不 force 或自动 stash。Lead 重新指派先确认旧运行收尾，旧 claim 的迟到结果只保留审计，不更新新状态。

### 9. 串行整合、冲突和单操作回滚

每个 Goal 生成受管团队分支及专用整合 Worktree，从登记基线开始；成员提交通过真实 Git 检查归属和不可变 commit 身份。Lead 接纳后登记输入，再串行整合。合并产生的实际提交、验证证据及可用性先持久确认，才发布依赖解除；发布中断可按操作 ID 对账，不重做合并。

整合前专用目录须干净，并登记 `pre_head`、输入及受管操作 ID。冲突时暂时停止其他整合；普通 Lead 可在现有权限内修复，coordinator 将冲突文件、预期决策和证据交给专门成员。冲突处理使用该整合目录的排他租约交接，同一时刻只有 Lead 或指定处理成员持有写入所有权，不能另一个 Worktree 的修改被误当作本目录冲突已解决。

冲突尚在进行时先使用受控 `git merge --abort` 并核对 HEAD、索引及目录。若已经形成未发布但验证失败的提交，只能在确认应用拥有的专用目录、输入、检查点且没有外部改动时恢复到登记点；不能将同样的重置用于用户工作区。无法证明范围时保留 needs_review，停止调度。Git 官方说明未提交修改可能使 abort 无法恢复，因此要求操作前干净且核查结果，见 [git-merge 官方说明](https://github.com/git/git/blob/master/Documentation/git-merge.adoc)。回滚只覆盖当前 operation，不撤销先前接纳提交，不删除成员成果，不承诺 Hook 或远端副作用回滚。

最终所有必需任务 completed 后，核查目标分支仍等于登记基线，用户目录干净且验证适用。先在独立最终候选目录构造并验证合并结果；在获准的最终发布阶段重新比较旧值并更新目标，保护并发外部更新。不能在检查和发布之间无条件覆盖分支，不能直接对用户目录执行检查点硬重置。目标被其他进程更新时终止并要求核查，不自动换新基线。目标分支已检出时必须通过正常工作区更新流程及当前权限执行，不能只改 ref 留下旧索引。失败保留团队成果，最终发布开始后状态不确定按真实 Git 状态核查，不盲目重复或回滚用户修改。

选择 merge 保留成员分支历史，不默认 rebase 或 squash；代码任务成员先提交成果，纯修改目录不是合并输入。多次整合不批量清理分支，最终不自动 push。

### 10. 生命周期、清理保护与交互入口

团队成员和整合目录在现有 Worktree 记录中登记持久 team_owner／goal_owner 引用。运行锁只保证活动排他，持久保护保证 idle／paused 不被清理。团队存档与缓存也登记长期引用；不能靠打开句柄保护跨进程暂停。移除成员需先停止、核查成果、保留审计和解除引用，再走原有安全清理流程；普通 TTL 扫描跳过团队持有资源。

Lead 创建或恢复取得长期所有权，整个团队只允许一个活动 Goal。退出依次关闭新提交 → 禁止自动唤醒 → 停止队员及整合 → 等待受控操作 → 保存暂停状态 → 关闭自有服务并释放锁。任何未确认进程不进入可恢复 idle；恢复需检查实际所有权和损坏记录。收到消息不自动启动暂停团队。

建议本地 `/team list|status|create <name>|resume <name>|pause|stop <member>` 入口；查看不创建模型或 tmux。启动可增加显式 `--team <name>`，与普通 `--resume` 互斥且保留 --config 必填。CLI 与模型 team 工具调用同一 Service，不使用单独弱校验路径。reset 停止当前 Goal 调度并清空 Lead 工作历史，不删除成员身份、共享任务或邮箱；新目标显式提交。

### 11. 配置默认值与双开关

配置建议：`team_backend=auto`、`team_max_running=4`、`team_max_queued=32`、`team_coordinator_enabled=false`。恢复按成员保存的实际后端核查；团队默认仅影响新成员。布尔严格 true／false，容量分别为正整数及非负整数。

coordinator 有效条件：本次指定配置字段为 true AND 真实 `os.environ['MEWCODE_COORDINATOR']` 恰为 `'1'` AND 身份为 Lead。不从 dotenv、团队元数据或模型消息读取主动启用。成员可以继承环境但身份使条件不成立；状态显示实际结果和缺失条件。存档中的旧 coordinator 仅供审计，不控制本次启动。

## Risks / Trade-offs

- [文件快照增长及锁竞争] → 有界输入、容量与可取消重试，已消费邮箱按持久记录归档，后续可替换存储而保持协议。
- [Git、多个邮箱和存档不能构成一个原子事务] → 各自操作 ID、先意图再副作用、提交后对账，不宣称 exactly-once 工具执行。
- [tmux 窗格消失或通知遗漏] → 消息先保存、登记握手及代次、边界重查和存活诊断；后端失败不自动降级。
- [两个后端服务所有权不同] → 独立进程初始化自有服务，协程仅释放自己的 scope，分别测试取消及兄弟存活。
- [计划获准而队员没有新目录许可] → 前置说明权限准备，按真实 root 受限返回，Lead 只作计划决定，不授予权限。
- [长期保护占用磁盘] → 显式可查看保留原因和引用，暂停不删除成果，解除保护后仍执行原成果检查。
- [依赖同步或最终分支外部变化] → 同步成功才开工，最终重新核查，不覆盖未知修改或隐式更新基线。
- [coordinator shell 仍能写文件] → 明确工具层分工，禁止直接编辑及独立派生旁路，提示和验收检查开发交给成员；不宣称全面禁写。
- [同用户 shell 可以访问用户存储] → 内置文件路径保护和可信协议来源只约束应用入口，不承诺抵御同用户恶意程序。
- [停止／回滚结果不确定] → 保存 needs_review 并阻止重复接管、重放和下游运行，保留所有实际证据。

## Migration Plan

1. 本轮只创建本变更规划。实施前核对既有 Worktree 变更主规格及代码状态，不在团队实施过程中隐式归档它。
2. 先建立持久身份、锁和存储，再完成任务／消息、身份过滤及成员会话；先验证 inprocess 语义，再接 tmux 同合同。
3. 接入 Worktree 团队保护、计划门禁、依赖同步、串行整合及最终发布，最后完成 coordinator 和用户控制入口。
4. 默认普通启动不创建团队；旧供应商配置、普通会话和子任务保持原行为，新增入口及配置公开说明。
5. 用供应商替身与临时真实 Git 仓库验证确定性状态、竞争及故障，再以真实授权模型和 tmux 验收，按 checklist 记录本次通过／失败／未执行原因。
6. 回退时停止团队运行、禁用新入口但保留用户域团队存储、成员目录、分支及引用；不能为了回退删除成果或让普通 TTL 接管未归档目录。

## Open Questions

没有阻塞架构或任务拆分的开放问题。上述未在探索中逐项选择的模块名、工具动作、目录布局、容量和退避参数是本提案的工程默认，评审可以调整；实现前须按规格验证本机 tmux 启动／通知能力及 Git 最终发布边界，不能将当前仅能定位 tmux 可执行文件当作后端验收。
