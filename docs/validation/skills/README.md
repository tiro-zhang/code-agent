# Skill 系统本次验收

日期：2026-10-05。变更：`add-skill-system`。实现位于 `feat/skill-system` 隔离工作树。以下结果均来自本次运行。

## 自动化与打包

- 完整回归：`PYTHONPATH=src UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run --no-sync pytest -q`，**989 passed，96.77 秒**，见 [pytest.txt](pytest.txt)。复用原仓库虚拟环境，因此显式设置源码路径；MCP 集成测试在允许本机临时端口的环境运行。
- `uv build --offline` 成功生成源码包和 wheel；两种产物均包含三个内置 Markdown。将 wheel 重装到临时虚拟环境，在源码目录外发现并展开 commit/review/test，见 [packaging.txt](packaging.txt)。
- `openspec validate add-skill-system --strict --json` 通过，十一份增量规格无校验问题。
- 独立只读审查覆盖定义／资源、白名单、命令、模型预算、历史、取消、审计和缓存。发现并修复子摘要检查点失败后继续执行、通知背压取消时丢失回流证据两项问题；新增测试均先复现再通过，审查复核后无未解决的重要发现。
- 其他新增回归覆盖实际子进程取消、授权等待、共享五次请求预算、独立模型参数及客户端归属、子审计失败、缺失回流恢复、缓存存续、reset 事务、整代热更新及完整 SOP 超限。旧 MCP 取消测试在基线单次运行出现过计数时序失败，单独重跑通过；本次最终完整回归全部通过。

## 真实终端与请求证据

临时工作项目为 `/private/tmp/mewcode-skills-e2e`。首次使用正式 CLI 在 `mewcode-e2e` 中启动；原 `.env` 的 OpenAI 兼容端点发生 TLS 失败，保存 [initial-tls-failure.txt](initial-tls-failure.txt)。随后改用现有已授权 `.env.claude` 的 Anthropic 兼容服务、`deepseek-v4-pro`，未关闭 TLS 校验。配置只复制到临时目录，未纳入仓库。

后续启动 [launcher.py](launcher.py)，它只观察请求边界，内层仍为真实 `make_provider`；真实工具、权限、终端及存档不替换。关闭后台记忆维护以隔离此次验收，最初两轮保留 thinking，后续仅在临时配置关闭 thinking。先在 default 模式实际批准读取／加载、拒绝一次加载，再切换 bypass 测试临时文件操作；白名单和资源边界仍强制执行。

共 **31 个真实模型请求、8 个独立运行**。主会话 ID 为 `20261005-012336-9b3a`。原始请求和 Journal 留在临时项目，脱敏提取见 [request-audit.json](request-audit.json)，本地命令请求计数见 [steps.jsonl](steps.jsonl)。[audit.py](audit.py) 对真实记录进行断言，验证下表的输入、状态和计数，不依赖模型自述。

| 场景 | 本次结果 | 证据 |
| --- | --- | --- |
| 自然发现与两阶段加载 | 未激活请求只有索引；“项目指南能力”触发 load_skill 审批，批准后全文与两个普通工具生效；精简轮次仍有完整 SOP | `natural-skill-approval.txt`、`natural-skill-success.txt`；请求 3→4→5 |
| 加载拒绝与短命令 | `/narrow` 被拒绝时零模型请求、不改变原 guide；批准后的短命令只用一次工作请求 | `narrow-denied.txt`、`after-deny-active.txt`、`narrow-loaded.txt` |
| 多 Skill 交集 | guide 和 narrow 同时激活只剩 read_file；模型说明没有写入能力，实际没有 forbidden.txt | `intersection.txt`、`refused-write.txt`；请求 6、7 |
| clear／管理 | `/clear`、`/skills active` 和停用均零模型请求；clear 保留两项激活，停用恢复普通工具集合 | `clear-preserved.txt`、`deactivate.txt` |
| 内置独立 test | 实际执行 `python3 -m unittest test_calc.py`，一个测试通过；两次子请求、一次主摘要，无额外主总结 | `isolated-test.txt`；请求 8、9 |
| 历史范围 | history=1 只导入最近用户轮次的 29 标记；0 无背景；all 同时含 17、29。保留角色／来源，子步骤不进入主投影 | `history-*.txt`、`request-audit.json` |
| 指定模型与父状态 | historyall 定义显式选定 deepseek-v4-pro；子上下文只有自身激活，父 parent 保留。不同模型 ID 的窗口映射／客户端选择由自动化测试验证 | `history-all-fixed.txt`、`parent-after-cancel.txt` |
| 包资源 | pack 普通白名单为空，load_skill 仍读取 reference.txt；`../outside.txt` 返回 skill_resource_denied | `pack-success.txt`、`pack-boundary.txt`；请求 17–20 |
| 合法热更新 | 新短命令出现在帮助；guide V1→V2，原始双空格参数保留 | `new-help.txt`、`hot-body.txt`；请求 23、24 |
| 整体坏候选与删除 | 未知工具 read_flie 使候选整体拒绝，仍使用 V2；删除 guide 后自动停用并提示 | `hot-bad-preserved.txt`、`hot-bad-body.txt`、`hot-deleted.txt` |
| 真实子命令取消 | 命令写出 started 后 Ctrl+C；终态 cancelled，保留调用 ID／错误证据，父激活仍在；超过原命令等待时长后 leaked 仍不存在，下一轮成功 | `child-cancelled.txt`、`parent-after-cancel.txt`、`after-cancel.txt` |
| 用量与摘要归属 | 八个独立运行的主／子 task_finished 用量逐字段相等，没有重复累计；主历史不含子工具消息 | `request-audit.json` 的 children 与审计断言 |
| reset 与恢复 | reset 后同一 ID、空历史／空激活、原权限 bypass、无任务记录；退出后显式恢复为零条消息 | `reset*.txt`；Journal 检查点及恢复投影 |
| plain 终端 | TERM=dumb 恢复后实际执行 historyzero，显示子身份、子结束及一份主摘要，正常退出 | `plain-child.txt` |

真实产物检查：`started` 内容为 `started`；`leaked`、`forbidden.txt` 不存在。运行 Python 测试产生了 `__pycache__`；模型曾回答“没有残留副作用”，该说法不作为验收依据。

## 观察到的模型行为及未执行范围

- 首轮 historyone／historyzero 曾再次加载自身，被嵌套独立限制正确拒绝。已将子入口提示改成“已进入、SOP 已激活、直接执行”；修复后 historyzero 使用一次请求完成，见 `history-zero-fixed.txt`。
- historyall 的回答曾错误声称看不到 17；实际请求背景包含两个标记。历史范围按请求数据断言通过，模型对已提供历史的语义回答仍可能出错，不把它当作软件状态依据。
- 不同模型 ID 的真实远端切换、官方 Anthropic 服务、真实磁盘耗尽／掉电及 MCP 服务与 Skill 同时运行没有逐一制造；相应参数、协议、存档故障及共享服务边界由本次自动化测试覆盖。真实指定 model 使用已有授权配置中的 ID，未猜测其他服务或模型。
- 早期 `/help newcmd` 的尝试被现有无参数合同拒绝；后用 `/help` 验证新命令存在。首个产物核验脚本误将 dist 的忽略文件当作归档，修正为显式检查 wheel／tar.gz 后通过。

## 复验

使用获授权的配置准备临时项目及 `.mewcode/skills/`，然后运行：

```sh
MEWCODE_E2E_ROOT="$PWD" PYTHONPATH=/path/to/worktree/src \
  /path/to/venv/bin/python /path/to/worktree/docs/validation/skills/launcher.py
# 显式恢复在命令后加 --resume；TERM=dumb 使用 plain 终端。
PYTHONPATH=src .venv/bin/python docs/validation/skills/audit.py /private/tmp/mewcode-skills-e2e
```

审计脚本针对本次固定场景及输入顺序，复用时应同时复现对应夹具。不要将包含凭据的 `.env` 或完整真实服务配置复制进验收目录。
