## MODIFIED Requirements

### Requirement: 统一命令定义与启动校验
每条命令 SHALL 登记名称、别名列表、简短描述、用法、执行类型和处理函数，并支持可选参数提示及默认关闭的隐藏标记。执行类型 SHALL 限于本地处理、状态操作和对话提示词。名称及别名 SHALL 使用不含斜杠或空白的非空命令标识，登记和查找 SHALL 使用相同的小写规范化规则。规范化后的全部名称及别名 SHALL 共享命名空间；重复名称、名称与别名冲突、别名之间冲突及同一命令内重复登记 SHALL 被拒绝，隐藏命令 SHALL 同样参与冲突检查。进入交互前 SHALL 完成全部内置命令注册及校验；冲突或非法定义 SHALL 显示明确启动错误并以非零状态退出，SHALL NOT 创建会话存档、启动供应商或连接 MCP。单代注册表 SHALL 保持不可变；系统 SHALL 在空闲输入提交边界按 skill-system 合同构造和整体替换包含有效 Skill 的新快照，不逐项修改使用中的注册表。有效 Skill 的控制命令名称冲突 SHALL 在创建会话资源前报告；工具名校验 SHALL 在 MCP 工具发现完成后且接受任务前完成。Skill 定义的局部解析错误 SHALL 跳过该文件并诊断，不当作静态控制命令定义错误终止所有发现。

#### Scenario: 名称与大小写别名冲突
- **WHEN** 已登记名称 `help`，另一命令登记别名 `HELP`
- **THEN** 启动报错说明规范化冲突标识及所属命令，不进入输入循环或启动会话资源

#### Scenario: 隐藏命令仍占用名称
- **WHEN** 一个隐藏命令的别名与另一个命令的名称相同
- **THEN** 启动拒绝该冲突，不因隐藏标记允许覆盖或选择先注册的处理函数

#### Scenario: 非法定义
- **WHEN** 定义缺少处理函数、执行类型非法，或名称包含斜杠或空白
- **THEN** 启动明确拒绝定义，不等待用户输入后才报错

#### Scenario: 新 Skill 的自动短命令
- **WHEN** 一个有效 Skill 已进入下一任务的目录快照，尚未激活
- **THEN** 同代注册表包含其规范名称短命令，帮助、补全与分发一致，不要求先运行一次加载工具

#### Scenario: 动态快照失败
- **WHEN** 下一输入边界的候选存在名称或工具名整体错误
- **THEN** 保留上一完整合法快照并展示错误，不在旧命令表上局部增加或删除 Skill

### Requirement: 三类执行路径
本地处理 SHALL 用本地读取或计算返回结果，不发起模型请求。状态操作 SHALL 直接调用界面或会话能力；`/compact` SHALL 复用独立摘要维护流程，可能发起一次模型请求，但不进入 Agent 工作对话，不覆盖最近工作统计。对话提示词 SHALL 使用预先定义文本或已发现 Skill 的加载请求并可附加用户参数，普通固定提示及共享 Skill SHALL 仅一次进入正常对话入口；独立 Skill SHALL 一次进入独立执行入口并将摘要回流。二者 SHALL 沿用当前模式和权限以及 skill-system 定义的流式输出、任务预算、取消、上下文维护和存档语义。展开文本 SHALL 不再次经过斜杠命令解析，原始命令文字 SHALL 不作为第二个任务提交。命令执行类型 SHALL 不被解释为工具授权。

#### Scenario: 本地查看
- **WHEN** 用户输入 `/status` 或 `/session list`
- **THEN** 显示本地信息，不提交用户任务或发起模型请求

#### Scenario: 摘要为独立维护
- **WHEN** 用户输入 `/compact` 且存在可摘要的历史
- **THEN** 按已有摘要校验和取消合同执行独立维护，用量归属摘要，模式和权限不因该操作改变

#### Scenario: 固定提示词恰好提交一次
- **WHEN** 用户输入 `/review Src/Main.py`
- **THEN** 有效 review Skill 按其定义加载，内置共享 review 的目标参数成为一个正常用户任务且 SOP 从激活上下文提供；正常提交后进入工作历史及存档，不再解析参数中的斜杠文字或重复提交原命令

#### Scenario: 独立短命令不启动第二次主任务
- **WHEN** 用户直接调用独立模式 Skill 的短命令
- **THEN** 执行一个独立任务并提交一次主历史回流，不先请求模型生成提示词或重复提交同一目标

### Requirement: 内置命令与兼容名称
系统 SHALL 提供以下命令语法：`/help`、`/compact`、`/clear`、`/plan [任务]`、`/do`、`/session [list]`、`/memory`、`/memory list [project|user]`、`/memory show <project|user> <id>`、`/permission` 及既有 `mode`／`revoke` 子命令、`/status`、`/review [目标]`。系统 SHALL 另提供 `/reset`、`/skills [list|active]`、`/skills deactivate <name|--all>` 和全部有效 Skill 的 `/<name> [参数]`，其中 review、commit、test 来自内置或更高层覆盖定义，不重复登记固定 review 控制命令。系统 SHALL 保留 `/exit`，并将 `/permissions` 登记为 `/permission` 的兼容别名；未指定的其他短别名 SHALL 不自动生成。无参数命令 SHALL 拒绝额外参数。`/exit` SHALL 使用既有受控退出流程，保留存档并收尾后台任务；输入历史 SHALL 不收录原始命令文字或自动展开的提示词，正常模型工作历史 SHALL 按实际提交的任务记录。

#### Scenario: 兼容退出
- **WHEN** 用户输入 `/exit`
- **THEN** 受控退出、保留存档和必要结果，并执行既有后台维护收尾

#### Scenario: 命令列表完整
- **WHEN** 用户输入 `/help`
- **THEN** 能看到有效控制命令、动态 Skill 短命令、`/exit` 及 `/permissions` 兼容别名，语法与实际分发一致

### Requirement: 固定审查提示词
`/review` SHALL 从三层 Skill 覆盖结果选择 review 定义并按其执行模式运行；内置 review SHALL 以共享模式审查当前 Git 未提交改动，`/review <目标>` SHALL 把原始参数传入 SOP，不通过额外模型调用生成提示词。内置 SOP SHALL 要求报告具体问题、位置、依据和未能验证的范围，不作为自动修复请求；合法用户或项目覆盖定义 SHALL 采用自身 SOP、模式和白名单。命令 SHALL 沿用当前会话模式及权限，SHALL NOT 自动切换模式或授予 shell、文件、MCP 权限。默认目标不存在、没有改动或当前工具范围不足时 SHALL 如实说明，SHALL NOT 静默改为审查整个项目或宣称已检查不可访问的改动。

#### Scenario: 默认审查
- **WHEN** 用户输入无参数 `/review`
- **THEN** 在没有更高层覆盖时激活内置 review 并提交当前 Git 未提交改动的目标，实际工具调用按当前模式、全部激活白名单及权限判断

#### Scenario: 指定目标
- **WHEN** 用户输入 `/review src/mewcode/session.py 的模式切换`
- **THEN** 目标文本原样传入有效 review 定义，按其模式运行一个任务，不额外请求模型生成提示词

#### Scenario: 规划模式下审查
- **WHEN** 用户在规划模式调用 `/review`，审查需要当前不允许的命令工具
- **THEN** 规划工具限制继续生效，模型说明未能检查的范围，不由命令自动解除只读限制

#### Scenario: 目录定义覆盖审查
- **WHEN** 项目目录包声明合法 review
- **THEN** 短命令只运行该包的定义，帮助显示其说明及模式，不再额外运行旧固定提示

## ADDED Requirements

### Requirement: 本地 Skill 管理与对话重置
`/skills` 或 `/skills list` SHALL 本地列出有效名称、说明、模式、来源及激活状态；`/skills active` SHALL 显示当前激活项与有效普通工具范围。`/skills deactivate <name>` 和 `/skills deactivate --all` SHALL 仅在空闲期通过会话能力停用激活，不清除对话历史、不执行模型。未知名称、非法子命令或额外参数 SHALL 在操作前本地报错。`/reset` SHALL 为无参数的本地状态操作，按 session-persistence 合同原子清空当前模型历史和激活状态并重置上下文估算及摘要失败状态；保持会话身份、模式、权限、存档和长期记忆，不把重置描述为撤销实际操作或删除存档。`/clear` SHALL 继续只清屏，且保留全部 Skill 激活。

#### Scenario: 停用恢复工具范围
- **WHEN** 用户空闲时停用一个导致工具交集收窄的 Skill
- **THEN** 成功保存状态后下一请求使用其余激活项的交集，历史和其他激活保持，不调用模型

#### Scenario: 清屏和重置区别
- **WHEN** 对话已有激活和历史，用户先 /clear 后 /reset
- **THEN** 清屏后历史及激活仍在；重置持久提交后当前模型历史和激活为空，下一请求使用完整当前环境提醒

#### Scenario: 重置非法参数
- **WHEN** 用户输入 /reset extra
- **THEN** 本地显示正确用法，不清历史或激活，不调用模型
