## MODIFIED Requirements

### Requirement: 三来源规则无覆盖顺序合并

系统 SHALL 从用户级 `~/.mewcode/permissions.yaml`、项目级 `<root>/.mewcode/permissions.yaml` 和本地级 `<root>/.mewcode/permissions.local.yaml` 读取 rules，其中 root 是所属运行固定并解析后的工作根目录，主会话采用启动目录，隔离子采用进入时绑定目录，与供应商 .env 所在目录无关。所有命中规则 SHALL 合并为一个集合，按 deny 高于 ask、高于 allow 决定结果；规则来源、文件读取顺序、列表顺序及 exact 比 glob 更具体都 SHALL NOT 改变该优先级。没有任何命中 SHALL 保留为未命中状态，随后由权限模式处理。文件不存在 SHALL 等同于该来源没有规则。

隔离子 SHALL 按自身真实工作根确定项目与本地规则来源，同时保留原父工作根的当前规则作为上限。相同业务相对目标 SHALL 在各自真实根下规范化后用于对应规则判定，不把子目录容器前缀当作绕过父 src/** 等规则的路径。父和子来源的明确拒绝 SHALL 始终优先；子 allow SHALL NOT 放开父 ask、deny 或其他上限。父当前规则无法读取或规范化时 SHALL 按配置／检查失败拒绝，不只使用创建时复制的旧规则。

#### Scenario: 三来源互相冲突
- **WHEN** 用户级存在匹配 allow、项目级存在匹配 deny、本地级存在匹配 ask
- **THEN** 结果为 deny，调换三个文件的内容或加载顺序后结果不变

#### Scenario: 精确放行不能盖过通配询问
- **WHEN** 同一目标同时命中精确 allow 和 glob ask，且没有 deny
- **THEN** 合并结果为 ask，而不是因为精确规则更具体就放行

#### Scenario: 项目根独立于供应商配置
- **WHEN** 用户在项目 A 启动会话，但 --config 指向项目 B 下的供应商配置
- **THEN** 项目级和本地级权限文件仍从 A 读取

#### Scenario: 父新增拒绝仍限制子目录
- **WHEN** 活动子调用 src/private.py，而父当前规则新增对应工具的 src/** deny
- **THEN** 子调用在副作用前拒绝，不能用子容器前缀或复制的旧规则绕过

#### Scenario: 子放行不能覆盖父询问
- **WHEN** 父规则要求 ask、子规则配置 allow，且没有对子实际 root 的有效批准
- **THEN** 返回 approval_required，不因子 allow 自动放行

### Requirement: 子授权快照与权限模式上限
共享同一实际工作根的子 Agent 注册时 SHALL 复制父已有本会话批准并独立追踪权限，本次批准不复制，后续父本会话批准或撤销不修改子副本。隔离子 SHALL 不把父文件、命令或 MCP 批准改写或重绑到新的实际 root；仅实际 root、工具及目标／参数均适用的批准才可满足子调用。子 SHALL NOT 自动写入新批准；规则与永久批准每次执行重新读取，明确拒绝和硬限制始终优先。角色模式 SHALL 默认为 inherit，按 strict > default > bypass 取父启动模式与角色模式中较严格者，不扩大工具范围。

初始化复制权限规则 SHALL 不迁移批准记录。隔离子 SHALL 保持非交互，权限不足返回 approval_required 和 not_started=true，不请求父审批，不自动保存新批准；agent 启动批准 SHALL NOT 授予普通子工具权限。

#### Scenario: 副本独立
- **WHEN** 共享同一实际工作根的父子在注册后新增或撤销父本会话批准
- **THEN** 子副本保持注册时记录，父及兄弟没有共享可变批准对象，用户可单独取消子运行

#### Scenario: 角色只能收紧
- **WHEN** 父 default 而角色 bypass，或父 bypass 而角色 strict
- **THEN** 前者使用 default，后者 strict，有效批准可满足要求但明确拒绝不能被放开

#### Scenario: 最新存储复查
- **WHEN** 最新规则新增 deny，或子依赖的唯一永久批准已撤销
- **THEN** 前者明确拒绝，后者在需批准时返回 approval_required，不使用过期存储快照

#### Scenario: 父批准不迁移到同名子文件
- **WHEN** 父已批准编辑 src/app.py，隔离子请求编辑自身同名文件且规则要求批准
- **THEN** 旧批准不匹配新真实 root 和目标，返回 approval_required，父子文件均不因失败被修改

#### Scenario: 命令和 MCP 也不跨根重绑
- **WHEN** 父已有精确命令或 MCP 批准，隔离子在不同真实 root 请求相同参数
- **THEN** 不通过复制批准表获得许可，只有子实际身份适用的规则或批准可放行
