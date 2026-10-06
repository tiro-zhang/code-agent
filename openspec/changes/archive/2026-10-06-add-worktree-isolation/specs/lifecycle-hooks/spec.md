## MODIFIED Requirements

### Requirement: 子运行 Hook 权限与生命周期
Hook 派发 SHALL 使用触发运行身份、权限模式、取消和当前规划限制；新的 Agent 子运行 Hook 命令 SHALL 使用子批准副本及非交互判定，不向父请求审批，不借目标工具扩大许可。既有独立 Skill 保留原权限交互合同。子关闭不关闭会话资源。Hook subagent SHALL 继续按声明占位合同记录未实现，不成为另一委派入口或递归来源。

隔离子 SHALL 共享原会话规则快照、once 状态和派发设施，但其命令动作默认 cwd 和本地 tool.target_path SHALL 绑定子实际工作根，使用子权限视图；共享 snapshot 中的父目录 SHALL NOT 被用作子命令 cwd。子关联异步动作 SHALL 保有可辨认运行归属和目录保护，退出或删除 SHALL 等其实际收尾；HTTP 等远端副作用不被描述为已经回滚。

#### Scenario: 提示词不串扰
- **WHEN** 子产生 prompt 而父同时准备请求
- **THEN** 子下一请求包含它，父不包含，同一 once 规则仍最多提交一次

#### Scenario: Hook 缺少批准
- **WHEN** 新的 Agent 子运行 Hook 命令没有适用许可
- **THEN** 记录安全诊断并按失败隔离继续，不请求主输入

#### Scenario: 子结束
- **WHEN** 一子结束而父或兄弟仍活动
- **THEN** 共享派发器和客户端仍有效，子队列不泄漏到其他运行

#### Scenario: 子 Hook 使用实际目录
- **WHEN** 隔离子工具触发写入相对产物的获准 Hook 命令
- **THEN** 产物位于子实际工作根，目标路径相对子根表达，父同名文件不被默认 cwd 操作覆盖

#### Scenario: 异步动作保护目录
- **WHEN** 子模型已最终回复而子 Hook 命令尚运行
- **THEN** 目录仍受活动保护，完成或取消收尾后才允许退出删除，父和兄弟 Hook 服务继续可用
