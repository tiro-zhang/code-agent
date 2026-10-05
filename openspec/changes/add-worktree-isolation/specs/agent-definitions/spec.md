## MODIFIED Requirements

### Requirement: 有界 Markdown 角色定义
角色 SHALL 使用 UTF-8 Markdown、YAML frontmatter 和非空正文，入口不超过 64 KiB；正文 SHALL 作为定义式子 Agent 全生命周期的固定系统指令。frontmatter SHALL 接受 name、description、allowed-tools、disallowed-tools、model、max-iterations、permission-mode、isolation，拒绝重复键、未知字段、不安全 YAML 及非法类型。name 和单行非空 description SHALL 必填，name SHALL 匹配 `[a-z][a-z0-9_-]{0,63}`。工具列表 SHALL 使用精确名称，省略白名单表示不增加限制，空列表表示不允许工具；黑名单优先。model SHALL 接受 inherit、haiku、sonnet、opus；permission-mode SHALL 接受 inherit、strict、default、bypass；两者缺省 inherit。max-iterations SHALL 为正整数，缺省 20，布尔值 SHALL NOT 当作整数。

隔离声明 SHALL 仅接受字符串 worktree；省略 SHALL 保持共享工作根，显式空值、其他字符串和错误类型 SHALL 使角色入口无效，不部分加载或回退开放模式。声明 SHALL 只控制 defined 运行的工作副本，不改变模型、工具白黑名单和权限模式，不为 Fork 增加角色参数。

#### Scenario: 非法入口
- **WHEN** 角色文件含重复键、非法类型、空正文或超过文件预算
- **THEN** 诊断来源并跳过该项，不部分加载或默认开放工具

#### Scenario: 空白名单与黑名单
- **WHEN** 一个角色白名单为空，另一个角色同时白名单和黑名单包含 read_file
- **THEN** 前者没有可执行工具，后者不能执行 read_file，系统入口不豁免角色限制

#### Scenario: 合法隔离声明
- **WHEN** 有效角色声明 isolation: worktree
- **THEN** 发现结果保留声明，使用该角色的 defined 任务要求独立工作副本，agent 工具 Schema 不改变

#### Scenario: 非法隔离值和缺省兼容
- **WHEN** 一个角色省略 isolation，另一个填写 false、null 或未知字符串
- **THEN** 省略项保持原行为，非法项诊断并跳过，不把非法值当作共享模式

### Requirement: 有效目录校验和冻结
系统 SHALL 在工具发现完成后校验有效角色引用的全部工具名称；未知名称 SHALL 明确阻止启动或候选更新发布，不能静默扩大范围。已注册但受模式或全局禁止的名称 SHALL NOT 被当作未知工具。角色目录 SHALL 在非空用户输入边界完整更新；更新失败保留上一完整目录。子任务注册时 SHALL 冻结角色、来源、隔离声明、父范围和配置，中途修改文件 SHALL NOT 改变已接受任务。

#### Scenario: 未调用角色拼错工具
- **WHEN** 有效角色引用 read_flie，用户尚未调用它
- **THEN** 工具发现后的目录校验明确失败并报告角色与来源

#### Scenario: 排队期间修改定义
- **WHEN** 子任务已接受并排队，用户随后修改角色正文或白名单
- **THEN** 该子任务仍使用注册时快照，后续成功更新目录的新任务使用新定义

#### Scenario: 排队期间改变隔离声明
- **WHEN** 已接受隔离任务后角色删除 isolation 或改为非法值
- **THEN** 该任务仍使用接受时的隔离快照，后续目录更新按完整候选校验发布
