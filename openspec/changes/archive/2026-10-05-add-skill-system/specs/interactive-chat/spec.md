## MODIFIED Requirements

### Requirement: 本地命令帮助与校验
启动交互提示 SHALL 告知 `/help` 入口。`/help` SHALL 从注册元数据展示全部可见命令的名称、别名、描述、用法、类型和参数提示，以及发送／换行手势及聊天与授权阶段的取消／EOF 含义，并说明启动参数 `--resume <id|latest>` 与 `--list-sessions` 的用途。增强交互终端在单行草稿的命令位置 SHALL 提供大小写不敏感的斜杠名称、别名和已知子命令补全；单匹配直接补齐，多匹配显示可选择及退出的菜单；隐藏命令及其别名 SHALL 不参与帮助和补全。接受补全 SHALL 仅修改草稿，不提交命令。

系统 SHALL 仅将单行草稿中的斜杠前缀识别为本地命令，判定时可忽略命令外侧空白。系统 SHALL 按 `slash-commands` 合同提供有效控制命令、/reset、/skills 管理及全部有效 Skill 的短命令，并保留 /exit 和 /permissions 别名。帮助、补全和实际分发 SHALL 在输入边界整体更新为同一代快照，不先激活才提供 Skill 候选。`/plan [任务]` SHALL 保留进入只读规划及可选任务语法，`/do` SHALL 仅返回执行模式，不再要求或自动执行有效计划。`/help`、`/status`、`/session`、`/memory` SHALL 为本地只读查看；`/clear` SHALL 仅清屏；`/review [目标]` SHALL 加载有效 review Skill，内置定义共享主对话，覆盖定义按其声明模式运行。/skills 查看及停用 SHALL 不请求模型，/reset SHALL 清空模型历史及激活状态，/clear SHALL 继续仅清屏。`/compact` SHALL 是无参数的状态维护操作，可发起一次独立摘要调用但不作为用户问题提交，不切换模式或修改权限。未知命令和已知命令的非法参数 SHALL 显示中文错误及正确用法，不调用模型、不进入模型历史、不改变模式或权限。单行 `//` 前缀 SHALL 表示普通消息，去掉一个开头的 `/` 后发送；多行草稿 SHALL 整体作为普通消息，不执行其中任一行的控制命令。

#### Scenario: 查看与补全命令
- **WHEN** 用户输入 `/help`，或在单行 `/per` 草稿上选择补全
- **THEN** 前者显示本地帮助，后者只补全草稿；二者均不调用模型，帮助包含 `/permissions revoke session` 与 `/permissions revoke permanent` 的完整语法

#### Scenario: 未知命令与错误参数
- **WHEN** 用户提交 `/hepl`、`/do extra` 或 `/permissions mode invalid`
- **THEN** 系统在本地显示未知命令或用法错误，不把文字发送给模型，也不切换模式或修改权限

#### Scenario: 普通消息以斜杠开头
- **WHEN** 用户提交单行 `//tmp/example` 文本
- **THEN** 模型收到普通问题 `/tmp/example`，系统不将其解释为控制命令

#### Scenario: 多行正文包含控制指令
- **WHEN** 用户提交一个包含 `/exit`、`/permissions mode bypass` 或 `/plan` 文本行的多行草稿
- **THEN** 系统将完整草稿作为普通消息，不退出、不切换权限或规划模式

#### Scenario: 压缩命令帮助和非法参数
- **WHEN** 用户查看 /help、补全 /comp 或输入 /compact extra
- **THEN** 帮助和补全包含 /compact；非法参数提示正确用法，不调用模型、不改历史或熔断状态

#### Scenario: 发现启动恢复入口
- **WHEN** 用户查看帮助准备接续旧任务
- **THEN** 可看到 --resume 和 --list-sessions 的用途，帮助本身不切换会话

#### Scenario: Skill 帮助和补全一致
- **WHEN** 下一输入边界成功发现新 Skill 后用户查看 /help 或补全其名称
- **THEN** 帮助显示该说明和模式，补全来自同代命令表；补全只修改草稿，仍需明确提交才运行

## ADDED Requirements

### Requirement: Skill 活动与独立执行反馈
终端 SHALL 展示加载成功或失败、当前激活名称和有效普通工具范围，并提供本地停用及重置语法；热更新的局部跳过、整体候选拒绝、来源变化及自动停用 SHALL 可辨认。独立执行 SHALL 以 Skill 和子运行关联标识展示进度、实际工具、授权、结果和最终摘要，明确其工作目录仍是当前项目；子完成 SHALL 不提前结束顶层状态或覆盖父激活。请求次数 SHALL 反映主子共用上限，实际 usage SHALL 按关联记录且不重复累计。增强和 plain 模式 SHALL 保持同样执行、授权及取消语义，复用单一输入拥有者，输出 SHALL 沿用脱敏和控制字符安全展示。

#### Scenario: 独立执行等待授权
- **WHEN** 子运行调用需要人工批准的普通工具
- **THEN** 终端显示对应 Skill、工具和本次授权范围，批准前工具未启动，Ctrl+C 按顶层可信取消收尾

#### Scenario: 热更新拒绝仍可诊断
- **WHEN** 输入边界候选白名单包含未知工具
- **THEN** 界面显示名称与来源及刷新失败，上一合法目录和命令快照保持，不展示半更新成功

#### Scenario: 重置后状态刷新
- **WHEN** 用户成功 /reset
- **THEN** 界面显示无激活和新上下文状态，不显示旧任务仍在运行或把历史清空当成副作用撤销
