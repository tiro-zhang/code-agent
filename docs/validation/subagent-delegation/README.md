# 子 Agent 委派本次验收

日期：2026-10-05。变更：add-subagent-delegation。直接在用户指定当前工作区实施。主规格已同步，变更已归档至 `openspec/changes/archive/2026-10-05-add-subagent-delegation/`。

## 环境与证据

使用已有授权的 OpenAI 兼容服务 ark-code-latest，窗口 256000；密钥只在被忽略的配置与权限为 0600 的临时配置中。真实工作目录为 /private/tmp 下独立验收目录，所有副作用只作用于合成文件。tmux 专用服务与会话 mewcode-e2e，通过 `uv run --project <仓库> --no-sync mewcode --config .env` 启动；cwd 是临时目录。

验收角色 tester 白名单为七个后台默认工具，max-iterations=6，模型继承。精确准备 agent、README 等读取、*.txt 写入/编辑及 `python3 verify_task.py *` 命令许可；其他目标没有授权。verify_task.py 写 <tag>.started、追加 <tag>.runs 一次，等待指定秒数后写 <tag>.txt=done；取消用例检查 started/runs 保留而结束产物不存在，并检查命令没有存活。

sitecustomize.py 只旁路观察真实 SDK 请求，不替换模型或结果，仅保存工具/系统/消息哈希及供应商原始用量。cache-evidence.json 保存真实父与首次 Fork 的前缀和正数命中。终端证据在先读取核对后人工脱敏；不保存请求密钥、URL、头、真实配置或未审核原始输出。随机任务身份在节选中替换为标签。

## 已验证的真实场景

| 场景 | 本次结果和独立核验 |
| --- | --- |
| 定义式前台 | explore 真实读取 README.md，返回银杏314 和启动命令；子正文不直接混入主流 |
| Fork 强制后台 | 返回一次凭据；主段结束后子继续，完成自动接续；父请求计数由 2/20 延续到 3/20 |
| 显式后台副作用 | tester 真实 write/read/edit/command；authorized.txt=beta，explicit.txt=done，explicit.runs 只有 once 一行 |
| 30 秒软期限 | timeout 40 秒命令从 foreground 转 background，task_id 不变；timeout.txt=done，runs 一行，无重启 |
| Ctrl+B 手动转换 | manual 40 秒及 manualdraft 90 秒在 started 后按键，同一身份后台；各 runs 一行，txt=done |
| 未提交草稿 | manualdraft 自动接续前在空闲输入草稿保留314（未提交），提交完成后原草稿仍在；最后明确 Ctrl+C 清除并退出 |
| 主动父取消 | parentcancel 90 秒命令 started 后 Ctrl+C；父 cancelled、子 cancelled，started/runs 保留而 txt 不存在；恢复空闲并能继续下一真实任务 |
| 单子取消 | singlecancel started 后 /tasks cancel；子 cancelled 回流，未取消父自动接续至 3/20，报告取消和可能副作用，不重跑；txt 不存在 |
| /plan 清理 | plancontrol 执行中提交 /plan，子 cancelled 后界面发布 PLAN；结束 txt 不存在 |
| 裸 /do | /do 再 /plan 前后 SDK 请求数相同，没有复活旧任务 |

| 规划子只读 | 实际读取 README 银杏314；子 SDK 声明仅 read/glob/search/load_skill，未声明写入或命令，无审批；planblocked.txt 不存在 |
| 非交互权限不足 | tester 真正尝试 write_file unapproved.bin，返回 approval_required、not_started；本地 /tasks show 核对；文件不存在 |
| 防嵌套硬拦截 | Fork 实际尝试 agent(type=fork)，返回 tool_not_allowed、not_started；本地 /tasks show 核对，未创建下一层子 |
| reset | resetcontrol started 后重置，子 cancelled，结束产物不存在；Journal reset 检查点空历史；旧终态仍可 /tasks show，无旧目标接续 |
| clear | clearcontrol 活动时 /clear，原 task_id 继续自然结束并接续，txt=done，runs 一行 |

| 退出 | exitcontrol started 后 /exit，应用窗口关闭、父 cancelled 一次；txt 不存在；取消各命令进程均为空；没有远端回滚承诺 |
| 恢复不重放 | 新进程 --resume 本次含后台凭据存档，恢复 9 条工作消息；/tasks list 没有子任务；真实模型请求数0，exitcontrol.runs 仍一行，无结束产物 |

最终所有本次 tmux 窗口、MewCode 进程已关闭，临时复制的密钥配置已删除，仓库原 .env 未修改。

## 本次发现及修复

首轮真实 Fork 的正数缓存命中成立，但接续提交遇到 task_resume context_kind 未登记 codec，导致 context_blocked。已加入持久化接续与恢复测试并修复，在新真实进程重新接续成功；不把首轮失败算作接续通过。

只读代码审查复现并修复了 Fork 注册范围扩张、退化终端草稿被审批读取、已提交 Future 与输入包装任务竞态、预算耗尽父的 execute 子在 plan 前漏取消、Hook 副作用误报 none，以及直接独立 Skill 父日志重复和提前记忆。对应红态测试在修复后通过；最后定向审查 47 项通过，无剩余 Critical/Important。

真实成功父的异步记忆多次超时，工作结果和输入恢复不受影响；本次不以记忆超时声明工作失败，也不声称记忆维护成功。

## 范围与限制

本次真实服务是 OpenAI 兼容端点。官方 Claude 的消息块缓存扩展、签名保留及兼容服务无扩展由自动化测试覆盖，没有官方 Claude 实机授权配置；不同远端模型别名切换由共享客户端并发测试覆盖。MCP 身份、非白名单拦截及单请求取消由本地 SDK/HTTP fixture 测试覆盖，未调用真实外部 MCP 服务。容量、同时完成竞态、坏角色更新、消息内对象隔离、摘要超限、子压缩与缓存寿命、权限拒绝优先、永久批准撤销、并发 once 和恢复不重建任务均在本次全量自动化中覆盖。

工作目录共享、团队编排和跨会话后台持久化按已批准范围不实现。取消保留实际效果，远端副作用可能未知。

## 最终自动化、构建与审查

- `uv run pytest -q`：1263 passed，132.62 秒；日志 pytest.txt。
- `uv build`：源码包和 wheel 成功；日志 build.txt。最终 wheel 在 /private/tmp 实际安装并脱离源码发现 explore/general，通过。
- `git diff --check`：通过。
- `openspec validate add-subagent-delegation --strict`：通过；场景映射见 spec-audit.md，共 133 场景。
- 最后只读审查 47 项定向验证通过；发现项均已修复，无剩余 Critical/Important。
