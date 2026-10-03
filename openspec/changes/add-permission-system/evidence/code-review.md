# 权限系统最终代码审查

日期：2026-10-03。基线：`4caf578`，审查对象为当前未提交改动及新增文件。依据：本变更的 `design.md`、`tasks.md` 和 `specs/**`。独立审查按 requesting-code-review 的 Critical／Important 分级进行；本报告不代替仍在进行的 tmux 验收。

## 审查结论

当前代码没有 remaining Critical 或 Important。发现的四项 Important 均已修复并有失败复现及修复后回归。重点复核了配置重读、授权范围、规范路径复查、目标进程真实启动、取消清理、结果配对回灌与终端输入所有权。未把已批准的 shell 文本权限检查扩大为操作系统隔离。

## 已闭环的问题

| 问题 | 修复及证据 |
| --- | --- |
| 动态参数遮蔽静态命令词 | 原先 `git 'push' "$BRANCH"` 无法产生静态前缀表示，可能漏掉 deny／ask；动态参数也可能遮蔽静态危险前缀。`shell.py:136` 起逐词规范化已知静态部分，保留未知部分原文；动态命令仍不能使用 glob allow。`test_permission_review.py` 的对应规则与黑名单用例先失败后通过。 |
| 静态重定向目标遮蔽磁盘写入 | 引号拆分设备路径，以及 `>|`／`>&` 操作符可能漏掉已知磁盘写入黑名单。`shell.py:39` 和 `shell.py:145` 起检查规范化目标与写入操作符；输入重定向不误标为写入。测试只分析文本，从未执行危险命令。两类 shell 问题合计初始 10 个失败、修复后最初 12 项 review 用例全部通过。 |
| 上阶段提前输入误批新请求 | 真实 PTY 的输入队列或 `InputReader` 内部缓冲中的旧 `2\n` 被新审批消费。两个真实 PTY 用例先失败；`terminal.py:123` 在新请求首次展示前清缓冲及 TTY 队列，请求内翻页／非法选项不清输入，三个针对性用例通过。 |
| 外部 SIGINT 未及时唤醒审批循环 | 真实 tmux 发现同步信号 handler 直接 `Event.set()` 不唤醒阻塞的 selector，需下一次输入才取消。`app.py:133` 起通过 `loop.call_soon_threadsafe` 唤醒。`test_external_sigint_wakes_idle_child_event_loop_at_approval` 使用真实 PTY 与子进程，父进程发送 SIGINT，子进程没有定时器或额外输入：旧代码 2 秒超时失败，修复后独立回归通过，回提示符且无工具开始事件。真实 tmux 复验记录由主验收报告保存。 |

## 独立测试证据

环境为 macOS、项目 `.venv`、Python 3.11.14。命令退出码均为 0。

- 审查初次联合回归：`test_permission_review`、`test_permission_execution`、`test_permission_runtime`、`test_permission_search`、`test_permission_terminal`、`test_permission_app`、`test_tool_scheduler`、`test_tool_session`、`test_tool_execution`、`test_plan_mode`：**108 passed in 28.44s**。
- 补充任务验证后，`tests/test_permission_review.py`：**26 passed in 1.80s**。
- 任务 7.2 的协议、提示上下文、会话、工具会话、权限应用及 review 联合回归：**92 passed in 5.50s**。
- SIGINT 修复后，外部 SIGINT 单项、权限终端及 review 联合回归：**42 passed in 3.36s**。
- 最后重跑上述初次联合回归的同一组文件，包含新增参数绑定等 11 项及外部 SIGINT 1 项：**120 passed in 30.14s**。

主代理最终完整回归的落盘日志已独立读取：**449 passed in 34.52s**，见 [pytest-final.txt](pytest-final.txt)；OpenSpec strict 校验通过，见 [openspec-validation.txt](openspec-validation.txt)。此前 437 项通过是新增测试前快照，不作为最终完整测试数字。

## tasks 1–7 的验证证据复核

| 任务范围 | 证据与复核结果 |
| --- | --- |
| 1.1–1.3 | shell AST 纯分析正反例、注册与供应商工具定义回归；补充一次批准时修改调用方或展示对象的完整 `path`／`content` 参数四项，均拒绝为 `permission_check_failed` 且 `not_started`，不产生授权或文件副作用；拒绝来源与错误序列化另有双协议回归。 |
| 2.1–2.5 | config／rules 覆盖严格 YAML、别名、来源排列、十二格模式矩阵、运行中坏配置恢复及批准后新增 deny；复核读取失败封闭拒绝和批准后重新读取。 |
| 3.1–3.4 | shell／review 覆盖原文黑名单优先、AST 可见子命令、动态词与重定向、复杂命令不能 glob allow；执行器测试证明组合命令拒绝时无前缀副作用。 |
| 4.1–4.5 | grants／config／runtime 覆盖精确范围、重启、并发冲突、撤销、失败降级和路径变化；补充真实历史裁剪后会话批准保留，以及配置链接在 default／bypass 下通过真实 write／edit 执行器均受保护四项。 |
| 5.1–5.6 | execution／search／scheduler／tool_session 回归覆盖等待无启动、deadline 后置、取消回收、获准路径复查、仅元数据先枚举、获准显式内容文件列表、受限成功结果及拒绝后的模型继续；拒绝不取消同批合法工具。 |
| 6.1–6.4 | terminal／app／review 回归覆盖单一 stdin 所有者、请求串行、旧输入隔离、完整详情、默认拒绝、权限状态展示和本地命令不调用模型。6.5 当时未勾选，与真实 SIGINT／EOF 验收尚在收尾一致；SIGINT 代码缺陷现已闭环。 |
| 7.1–7.4 | plan、工具提供者、提示周期、会话及上下文裁剪回归；补充真实权限拒绝在 Anthropic／OpenAI 结果序列中保留调用标识、`source`、`not_started`，模型工具定义仍是既有六个工具且没有内部权限字段。README 两个 YAML 示例经严格 loader 读取通过；`git check-ignore --no-index .mewcode/permissions.local.yaml` 返回匹配，退出码 0。7.5 当时未勾选，由最终完整测试证据闭环。 |

补充上述验证后，已勾选任务未发现剩余实质验证缺口。没有修改产品代码来扩展权限范围，也没有改动原 `test_provider_errors.py`。

## 边界

本审查确认项目路径检查、已知 shell 黑名单与可见 AST 规则符合批准设计；不声明 shell 文件系统隔离，不追踪脚本内部或动态生成命令，不增加设计明确排除的外部恶意进程替换／硬链接防御。tmux 8.4–8.7 的完成状态由各自真实验收证据决定。
