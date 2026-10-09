# Agent evaluation 本次验收

2026-10-08 在当前项目完成 add-agent-evaluation 实现。测评器复用真实 ChatSession、Agent、六个工具和权限模块；所有模型能力数字均来自明确启动的真实服务，网络替身只验证测评器正确性。

## 最终结果

| 检查 | 本次结果 | 证据 |
| --- | --- | --- |
| 测评器定向测试 | 44 passed | [日志](targeted-final.log) |
| 全量 Python 回归 | 2053 passed，382.42 秒 | [日志](full-final.log) |
| wheel／sdist | 通过；wheel 注册 mewcode-eval，sdist 含可信任务 | [构建日志](build-final.log) |
| OpenSpec strict／diff 检查 | 通过 | [命令记录](verification.md) |
| 最终真实基线 | Anthropic 兼容 deepseek-flash，8/8，40 个任务请求 | [报告](runs/final-baseline/report.md)、[清单](runs/final-baseline/manifest.json) |
| 最终真实候选 | 同配置再次运行，8/8，37 个任务请求 | [报告](runs/candidate/report.md)、[清单](runs/candidate/manifest.json) |
| OpenAI 兼容本次抽测 | ark-code-latest，修复／规划／失败检查 3/3，13 个任务请求 | [报告](runs/openai-final/report.md) |
| 真实命令正在运行时取消 | started 存在，late 不存在；当前 cancelled，后续 not_run；退出 130 | [报告](runs/cancel-v2/report.md)、[工具轨迹](runs/cancel-v2/trials/cancel-command/1/trace.jsonl) |
| 正常终端回归 | tmux 真实 read_file、一次批准、流式回复和退出完成 | [脱敏终端输出](terminal.txt) |
| 保存结果离线比较 | 控制兼容，两侧 8/8；没有版本或配置变化 | [比较](comparison/report.md) |
| 控制不兼容的比较 | 首次任务配置与最终任务不同，拒绝总体比较，退出 2 | [差异报告](incompatible/report.json) |

每次完整运行每例一次，串行；请求预算 20、试跑期限 600 秒、工具默认 30 秒、收尾 5 秒。最终基线与候选使用相同实际源码、任务、模型和环境，目的是验证可重复取证与比较机制，不能据此宣称能力改善、统计显著性或服务端权重固定。OpenAI 兼容只抽测三例，其他五例不算该协议的实测覆盖；本次没有实际远端模型服务失败，失败分类由确定性故障注入覆盖。

## 独立审查与回归修复

一次新上下文只读审查发现四项 Important，全部先补失败测试再修复，最终全量通过；没有未处理的 Critical／Important 或延后 Minor。未追加第二轮评审。

- 被测模块或函数 SystemExit(0) 会绕过评分断言：可信包装器捕获提前退出，只有正常完成才有回执；顶层和调用阶段反例均失败。
- 不合作的关闭任务会卡住 asyncio.run 退出：CLI 使用明确有界循环收尾，独立进程验证能返回错误码。
- 评分子进程逃离进程组并持有输出管道：整个回收与排空有期限；未知副作用记 undetermined／harness_error，停止后续检查和尝试。
- 原始文件副本绕过脱敏：原始执行域与持久证据分开，评分后处理所有持久普通文件，保留真实产物指纹与 redacted_files；无法确认停止的原始执行域留在独立临时位置，不作为可分享证据。

具体回归见 test_grader_rejects_early_exit_in_module_and_function、test_cli_exits_when_a_close_task_ignores_cancellation、test_escaped_grader_child_does_not_block_cleanup、test_persistent_work_files_are_redacted_after_scoring。参考实现只用于评分器校准，另一种合法实现也通过。

## 证据抽查与人工维度

本助手核对了三例的实际文件、工具结果、停止原因与答复，并将抽查线索记录为 unreviewed；它不代替用户人工语义评分，也没有调用模型裁判。

- 修复：limits.py 上界分支改为返回 upper；check.py 保持，真实命令退出 0，独立七组功能断言通过；答复与该事实对应。
- 规划：两轮都有完整计划文本，修订文本明确空串／非数字串 ValueError、负数和小数及测试；快照与每步只读检查未发现修改。计划质量仍未评价。
- 失败报告：第一次追加 echo 的命令被拒，未开始；之后原始命令真实退出 3，脚本保持，答复明确检查失败并说明前次拒绝。

[待人工记录](runs/candidate/reviews.jsonl) 单独关联运行身份、维度、时间与证据；[离线比较](comparison/report.json) 重新合并这些记录，自动分数保持不变。每次完整运行有 10 个人工维度，当前用户人工已评为 0，不能把自动 8/8 描述成规划或答复质量均已通过。

## 保留的早期失败

[首次八例](runs/baseline/report.md) 为 2/8、退出 1：任务遗漏读取放行声明，真实权限按默认拒绝读取。修正声明后补了先失败后通过的读取授权回归，并使用新目录运行，没有覆盖原失败。[修正后、审查前的八例](runs/baseline-v1/report.md) 为 8/8；[同期 OpenAI 三例](runs/openai/report.md) 为 3/3。它们的测评器身份不同，不与最终评分混为同一基线。

[第一次取消](runs/cancel/report.md) 退出 130，但取消发生在命令完成后、模型报告阶段，late 已产生；因此它不能证明运行中命令回收。随后使用独立新夹具实测正在运行时取消，保存于 cancel-v2，确认无 late；没有删除前一记录。

普通沙箱中的既有本机端口测试有环境性 PermissionError；获准本机端口环境重跑基线 2009 passed，最终同样环境完整 2053 passed。构建首次联网遭沙箱 DNS 失败，复用已缓存依赖执行 uv build --offline 成功。创建 Git worktree 的自动审批超时，改用独立本地 clone 实施；只把本变更同步回当前项目，没有覆盖其他工作。

## 覆盖边界

本次真实模型请求使用现有已授权的两个兼容服务，不代表官方 Anthropic／OpenAI 或所有供应商均通过。Skill、子 Agent、团队、MCP、复杂真实项目和系统级沙箱不在首期能力范围；既有功能以本次完整自动化回归检查。正常终端的自动记忆候选被校验拒绝保存，已记在终端输出中，未据此声称记忆维护全部通过；测评装配关闭自动记忆。

工作目录和用户域提供状态隔离，不能阻止 shell／被测代码访问目录外。只运行可信小型任务；未确认停止的副作用明确未知。最终轨迹省略思考、请求头、配置全文与流式正文（早期诊断记录保留其当时行为），完整答复和工具结果另存；秘密脱敏或输出裁剪均有标记。原始内容指纹与脱敏文件引用指纹分别保留，不能声称脱敏副本完全等同于原始产物。
