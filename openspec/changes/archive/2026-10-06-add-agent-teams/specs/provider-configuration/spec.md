## ADDED Requirements

### Requirement: 团队运行配置与严格校验
指定供应商配置 SHALL 支持 `team_backend=auto|tmux|inprocess`，省略使用 auto；支持正整数 `team_max_running`、非负整数 `team_max_queued`，省略分别为 4 和 32。非法、空值或布尔预算 SHALL 在启动前指出字段并拒绝，不解释为无限容量。成员显式后端 SHALL 覆盖团队默认选择，但不能绕过可用性检查或失败报告；实际选择 SHALL 可查看。模型、窗口与输出预算 SHALL 沿用现有已配置角色模型合同，不接受成员提供任意服务凭据。

#### Scenario: 后端值非法
- **WHEN** 配置包含不支持的 team_backend 或非法容量
- **THEN** 启动前明确报错，不创建团队、窗格或模型请求

#### Scenario: 旧配置
- **WHEN** 合法配置没有团队字段
- **THEN** 普通启动保持，新创建团队默认使用 auto 和公开容量上限

### Requirement: coordinator 双开关与身份限制
配置 SHALL 支持 `team_coordinator_enabled=true|false`，省略为 false；用户 SHALL 通过真实启动进程环境变量 `MEWCODE_COORDINATOR=1` 主动启用。只有配置为 true、环境变量恰为 1 且当前运行身份为 Lead 时 SHALL 生效。指定 .env 文件中同名环境变量、历史正文、团队存档和模型工具参数 SHALL NOT 代替真实进程环境。恢复 SHALL 按本次两个来源重算，不恢复历史开关。成员进程继承环境变量 SHALL 不使成员变为 coordinator；当前开关及缺失条件 SHALL 可查看。

#### Scenario: 只开一把锁
- **WHEN** 只有配置允许或只有环境变量为 1
- **THEN** coordinator 不生效，状态指出缺失条件

#### Scenario: 文件伪装主动启用
- **WHEN** 配置文件写有 MEWCODE_COORDINATOR=1 但进程环境没有该变量
- **THEN** coordinator 不生效，不把配置文件值当作用户本次主动启用

#### Scenario: 成员继承启动环境
- **WHEN** Lead 为 coordinator 且成员进程继承相同环境变量
- **THEN** 成员仍按成员能力执行，文件编辑不因该变量被误剥夺
