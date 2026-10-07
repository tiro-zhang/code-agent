# 本次真实模型团队验收

本次在临时 Git 项目和私有 tmux server 中调用已授权服务（现有 `.env.claude`，Anthropic 兼容协议、`deepseek-flash`）。TLS 校验开启；未修改真实配置或输出密钥。启动包装器仅指定临时用户域、80 次 Lead 预算、两个并行槽和显式后端；工具权限为 bypass，明确 deny 与计划门禁仍有效。普通默认授权、MCP、Hook 和故障竞争由实际运行器自动化另验，不能把本次 bypass 当作人工授权交互通过。

终端正文包含模型报告；结论同时核对了实际任务板、邮箱、成员 Journal、Git 提交／分支及文件。`model_done`、idle 和成员自报 verified 均不作为独立验收依据。选定状态快照见 [e2e-state.json](e2e-state.json)，原配置和持锁令牌未导出。

| 本次场景 | 结果与独立证据 |
| --- | --- |
| 显式 inprocess，alice/bob 并行 | 通过。A 实现 add、B 写文档并行；Alice 需要审批，真实 plan_request 与 Lead 精确 plan_decision v1 放行；Bob 与 Alice 双向消息的 sender/recipient 是成员，邮箱已读，Lead 未中转。见 [终端](inprocess-terminal.txt) |
| A→C 依赖、成员再派工 | 通过。A 提交 `f97a801`，整合后 Bob 同一身份／目录承接 C；C 提交 `bd8baee`，实际目录先同步到含 A 的提交。Lead 独立运行 4 个 unittest，再接纳、整合及发布。最终提交 `2a9aaafc4c36ca758db6da1c386ff6820d331239` |
| 双真实实例原子领取 | 通过。新 Goal 仅创建一个未指派非代码共享任务；Tom/Jerry 同时收到普通 text 后各调用 claim，同一 task_id 下只有 Tom 成功，Jerry 返回“任务已领取或不可领取”。未 reassign，唯一 claim/owner，经 start/submit、Lead 独立验收及 finalize 完成，用户 HEAD 不变。见 [终端](claim-terminal.txt) 与 [双方真实 Journal 核验](claim-state.json)；可控竞锁另由双进程自动化验证 |
| inprocess 退出／显式恢复 | 通过。退出实际停止成员，保留资源；恢复同一 Lead Journal、49/80 原预算、成员身份和历史，无自动模型请求／成员启动。见 [恢复终端](inprocess-resume.txt) |
| 显式 tmux 完整实例 | 通过。tom/jerry 是独立进程及窗格，固定工作根；A multiply、B 文档、C 依赖测试完成，Jerry 在 B 后再次承接 C，实际 8 个 unittest 通过，发布 `b529c8026525441c0eabea277b9a44698bf390ab`。见 [终端](tmux-terminal.txt) |
| tmux 退出／恢复／新目标 | 通过。正常退出清理受管成员窗格，借用 server 保留；显式恢复零自动请求／窗格。随后同一 tom/jerry、branch、目录与 session_ref 完成新的 T2/J2，start 自动同步冻结基线，无 Lead 手工 merge；双向直接 text 已落盘且已读。见 [恢复](tmux-resume.txt) 和 [再次派工](tmux-reuse.txt)。最终 `c7262784973de88d1d9248bd60330eefc2aea8a9`，独立复跑 8 测试通过 |
| coordinator 配置一把锁 | 通过。实际请求仍含 write_file/edit_file/agent；状态 coordinator=false。见 [终端](locks-capability.txt) |
| coordinator 环境一把锁 | 通过。实际请求仍含 write_file/edit_file/agent；状态 coordinator=false。见 [终端](locks-environment.txt) |
| coordinator 两把锁 | 通过。Lead 实际模型声明无 write_file/edit_file/agent，保留读类、shell 和协作工具；状态 coordinator=true。见 [终端](locks-both.txt) 与 [真实请求工具名](coordinator-model-tools.jsonl)；成员合法编辑已在上述流程实测 |
| 可解冲突 | 通过。独立项目 red/blue 从同一基线分别写 alpha/beta；第二整合冲突，coordinator 把真实整合目录排他租约交 Blue，成员写入两行并提交 `04df8682f90527b95dcc67bfb01da0f1319607e8`；Lead 独立验证、发布，用户 master 两行齐备，原成员提交保留。见 [终端](conflict-resolved.txt) |
| 不可解冲突／单操作回滚 | 通过。新目标 red=gamma、blue=delta；第一整合 `dbd1d99` published，第二冲突只 rollback 第二操作，第一仍保留，成员提交 `523595c`/`2a6ed1a` 保留；未 finalize。用户 master 仍 `04df868`，原 untracked 文件 SHA256 为 `67feeea08584dc39bbf648b8393f4865030ed903883b71da516ed56ec53f2652`，未改变。见 [终端](conflict-rollback.txt) |
| 真实活动工具取消 | 通过。最新实例 Bob 的 shell 实际写出 cancel-final-before.txt 后，本地 /tasks cancel-parent 收尾进程组；工具结果 cancelled、Goal cancelled、Member stopped。已发生文件保留，延迟核查 cancel-final-after.txt 仍未出现；用户代码与旧成果保留。见 [终端](cancel-terminal.txt) 与 [状态](cancel-state.json) |
| 本地取消与冲突成果保护 | 通过。修复后恢复原冲突 Goal，再执行本地取消，实际 Goal cancelled；用户 HEAD、未提交文件指纹、两位成员提交及第一整合不变。恢复中没有启动的成员保持安全空闲，未谎称已启动后停止。见 [终端](rollback-cancel-terminal.txt) |
| plan / 裸 do | 通过。真实 tmux /plan 收尾所有受管成员窗格，随后 /do 保持 paused/execute、没有新增模型请求或窗格。见 [终端](plan-do-terminal.txt)，成员只读消息及间接操作拒绝另由自动化验证 |

## 失败尝试及后续验证

- 初选现有 OpenAI 兼容配置出现 TLS 连接失败，未伪造成功；改用另一个已授权现有配置后真实读工具与团队流程成功。
- 初期临时启动包装器缺少 `__main__` 保护、`/tmp` symlink 不满足私有存储边界，均修正验收脚本后重启；没有放宽产品安全校验。
- inprocess 初期暴露 terminal Task 晚到审计覆盖成果／预算续接等问题，已修复并增加对应回归；表内成功流程为修复后的独立目标，旧失败目标和证据保留。
- tmux 首轮 Jerry 曾出现缺失工具结果，随后旧 claim 工具多次报告工作进程异常、预算耗尽；Lead 明确停止、重新指派新 claim 后 B/C 成功。首次进程异常的底层原因未定位，不能声明已修复；后续两位新目标复用均正常。新恢复隔离测试通过真实 SIGKILL 验证未确认工具结果不重放，需 Lead 核查并明确重指派。
- 冲突首轮的验收命令相对路径错误，验收明确失败；重新指派后用实际成员目录内验证成功。不可解冲突流程当时暴露新目标 baseline sync 只接受已发布整合提交，Lead 手工安全快进后完成回滚验证；产品已修复，并由后续 tmux T2/J2 真实 start 自动同步重新验证。
- T2/J2 验收后 Lead 请求出现 TLS 流失败，无 integrate 意图；明确继续同一 Goal 后预算由 10/80 累计至 20/80，最终真实发布，没有自动重试／刷新预算。
- 开发过程中旧 Lead 类与后续懒加载新运行器混用，临时 inprocess 取消验收遇缺少恢复 helper；有序退出并用同一版本重新恢复验证，不作为热更新能力验收。
- 本地 `/tasks cancel-parent` 初验曾只停止普通父任务、未停止团队目标；真实任务板核查发现后，已补修复及定向回归。最终取消证据见本轮汇总，旧终端成功提示不计通过。

## 自动化覆盖及未真实注入的范围

模型并发抢同一 claim、广播部分锁失败、存档提交后的 read 对账失败、Git 已执行但状态发布失败、外部分支 CAS 竞争、未知旧运行／停止超时、Hook/MCP 兄弟资源与真实子进程 SIGKILL 均有本次自动化。真实模型流程没有人工精确触发这些持久化／竞争窗口：它们须可控暂停位置和故障注入，随机制造不能证明边界。具体测试、故障范围及未覆盖窗口见 [81 场景映射](spec-audit.md) 与 [整合记录](integration.md)，不宣称全部任意崩溃窗口均已验证。
