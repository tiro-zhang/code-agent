## MODIFIED Requirements

### Requirement: 下一工作请求的提示词注入

prompt 动作 SHALL 包含非空 `text`，SHALL 同步将其加入下一次实际工作请求的待注入上下文。请求前 Hook 产生的文本 SHALL 与此前排队的注入共同参与整体上下文预算检查及压缩后的复查，SHALL NOT 修改固定系统提示或本地许可。专用摘要、恢复和记忆模型请求 SHALL NOT 消耗该队列。预算预览及发送前取消 SHALL NOT 消耗注入；实际工作请求开始后 SHALL 消耗本次选定文本，即使该请求失败。成功完成的交互 SHALL 随既有上下文提交和压缩，失败流 SHALL NOT 伪造成功历史。需要每次工作请求持续提醒时 SHALL 通过 before_request 规则重复注入。

提示词队列 SHALL 按触发任务身份隔离，子只消费自身队列，父和兄弟不能抢占；同一父任务的新执行段 SHALL 保留该父未消费注入，不因 run_id 更新丢弃或交给其他父任务。规则与会话 once 仍共享；子结束只清理自身队列，不关闭会话引擎。

#### Scenario: 注入使请求超过预算
- **WHEN** Hook 文本使准备的工作请求超过上下文预算
- **THEN** 系统按现有有界压缩或 context_blocked 路径处理，不直接发送超出保护边界的请求

#### Scenario: 摘要不抢先消耗注入
- **WHEN** 待注入内容存在，而准备工作请求需要先进行摘要
- **THEN** 摘要不消费注入，最终工作请求仍包含原选定文本，重新估算不重复追加文本

#### Scenario: 发送前取消
- **WHEN** 请求已准备注入但实际工作请求尚未开始即被取消
- **THEN** 不消耗选定文本或请求次数，不伪造成功交互

### Requirement: 会话内最多尝试一次

once 为 true 的规则 SHALL 在本次会话内最多提交一次动作尝试。条件未匹配、模式禁止或后台动作尚需人工批准而被跳过 SHALL NOT 消耗标记；提交尝试前 SHALL 原子取得标记，执行或前台授权失败 SHALL 不返还标记。并发事件、独立 Skill 和新的 Agent 子运行 SHALL 共用该会话标记。reset SHALL 保留标记，进程重启和显式恢复 SHALL 不恢复旧标记。标记 SHALL NOT 被解释为持续拒绝策略。

#### Scenario: 并发触发同一规则
- **WHEN** 两个工具事件同时匹配 once 规则
- **THEN** 只有一个动作尝试被提交，失败也不在本会话自动重试

#### Scenario: 重置与重启
- **WHEN** 已运行一次的规则经历 reset，随后重启并恢复同一存档
- **THEN** reset 后仍不重跑，重启后的首次匹配可重新提交

## ADDED Requirements

### Requirement: 子运行 Hook 权限与生命周期
Hook 派发 SHALL 使用触发运行身份、权限模式、取消和当前规划限制；新的 Agent 子运行 Hook 命令 SHALL 使用子批准副本及非交互判定，不向父请求审批，不借目标工具扩大许可。既有独立 Skill 保留原权限交互合同。子关闭不关闭会话资源。Hook subagent SHALL 继续按声明占位合同记录未实现，不成为另一委派入口或递归来源。

#### Scenario: 提示词不串扰
- **WHEN** 子产生 prompt 而父同时准备请求
- **THEN** 子下一请求包含它，父不包含，同一 once 规则仍最多提交一次

#### Scenario: Hook 缺少批准
- **WHEN** 新的 Agent 子运行 Hook 命令没有适用许可
- **THEN** 记录安全诊断并按失败隔离继续，不请求主输入

#### Scenario: 子结束
- **WHEN** 一子结束而父或兄弟仍活动
- **THEN** 共享派发器和客户端仍有效，子队列不泄漏到其他运行
