# 本机 Web 工作台交付验收

日期：2026-10-07。变更：`add-local-web-interface`。工作分支：`feat/local-web-interface`。45/45 项任务已完成，主规格已同步，变更已归档至 [2026-10-07-add-local-web-interface](../../../openspec/changes/archive/2026-10-07-add-local-web-interface/tasks.md)。实现、规格和验收材料在工作分支一并提交。

## 交付行为

`uv run mewcode --config .env --web` 启动仅监听 `127.0.0.1:8765` 的本机单项目工作台。聊天、多行草稿、历史会话、工具详情和局部 diff、四种审批、停止及重连都通过共用运行层访问现有 Agent；原终端入口保留。用户安装 wheel 后无需 Node.js。

浏览历史与显式继续分开；新建只创建空存档。一个 Web 服务只有一个项目执行槽，涵盖主子任务、所属团队、自动接续、Hook、摘要及未收尾维护。刷新不取消任务，重传使用原操作回执；服务重启不自动恢复旧任务或工具。审批 10 分钟到期，继续使用既有权限复核。

README 已同步启动、使用和前端构建说明。规格的 16 项需求、40 个场景及证据见 [spec-audit.md](spec-audit.md)。

## 本次验证结果

| 层次 | 实际命令／方法 | 结果 |
| --- | --- | --- |
| 完整 Python 回归 | `UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run pytest -q` | 2007 passed，375.28 秒；包含最终会话切换／停止竞态修复 |
| 后续维护显示修正 | `uv run pytest tests/test_web_projection.py tests/test_web_manager.py tests/test_web_api.py tests/test_runtime_resources.py -q` | 43 passed，3.53 秒；新增两项用量归属测试先红后绿，当前共收集到 2009 项测试；没有将本次定向结果冒充再次全量运行 |
| 前端单元／组件 | `npm --prefix frontend test` | 32 passed |
| TypeScript | `npm --prefix frontend run typecheck` | 通过 |
| Chrome 集成 | `npm --prefix frontend run test:browser` | 12 passed；受控 API fixture，详见 [前端报告](frontend-report.md) |
| 生产构建 | `npm --prefix frontend run build` | 通过；最终源码连续两次构建的 manifest 逐字节一致 |
| 静态一致性 | `npm --prefix frontend run verify:build` | 通过；Python 启动测试另覆盖缺失／损坏／陈旧产物 |
| Python 分发 | `uv build` | 最终 wheel、sdist 均构建成功；wheel 内 manager/projection 与当前源码逐字节一致 |
| 无 Node 运行 | 临时目录 `uv pip install --no-deps --target ... <wheel>`，运行时 PATH 为 `/usr/bin:/bin` | 本地 HTML、认证、state、SSE 成功；初始 unloaded，未创建运行会话 |
| 信号退出 | 安装后的真实服务保持 SSE 连接，分别 SIGINT／SIGTERM | 0.344 秒／0.236 秒内退出；退出码 0／-15；没有残留自动创建的会话 |
| Web 真实模型 | 专用 tmux + Chrome + 现有已授权模型 | 4 工作轮、7 次工作模型响应、4 次工具；编辑仅一次，停止和重启后继续通过 |
| 原终端真实模型 | 独立 tmux + strict 审批 | 4 工作轮、8 次工作模型响应、5 次工具；取消后继续、显式恢复、两次退出均通过 |
| 独立代码审查 | 独立上下文只读审查及复现 | 修复所发现问题，无剩余 Critical／Important；独立定向 27 passed，后续用量修复另有上述 43 项回归 |
| 规格校验 | `openspec validate add-local-web-interface --strict --no-interactive --json` | valid=true，issues=[] |
| 归档规格同步 | `openspec validate --specs --strict --no-interactive --json` | 31/31 份主规格通过；新建 web-interface 的 16 项需求、40 个场景与增量逐项一致，Purpose 保留 |
| 变更卫生 | `git diff --check`、新增／修改文件扫描、包内容检查 | 无空白错误、真实配置密钥或包内私密目录；访问片段凭据未写入验收材料 |

Python 输出保存在 [python-tests.txt](python-tests.txt)；最终安装／退出输出见 [package-check.txt](package-check.txt)。真实 Web 与终端过程分别见 [web-e2e.md](web-e2e.md)、[terminal-e2e.md](terminal-e2e.md)。两套 tmux 验收会话均已关闭。

最终 wheel SHA-256：`b71d97356a1d9d8a0a8f75169953445cd54a171d35d954bfb732fa2d020dbf24`。

最终 sdist SHA-256：`037e41f850f66b59d99044d98c0ac0b168e4dbbd505edcc8d57bd95e63bf6979`。

## 本轮限制与真实诊断

真实模型只使用现有一个 OpenAI 兼容入口，没有另行调用官方 Anthropic、其他供应商或外部 MCP。团队、子任务、Hook、审批期限和故障竞争通过本次自动化覆盖，不标为全部真实远端组合已验收。原终端清单全部既有章节的本轮通过／部分通过／未执行及原因已逐项映射在终端报告中。

真实运行观察到后台记忆候选非法被跳过及提取超时，维护用量按未知／不完整保留；后续 Web 维护成功保存笔记。工作轮和文件修改结果均已独立验证，不能据此宣称所有维护成功。取消轮重启后保留不完整工具交互提示，按合法存档恢复而不重放或补造记录。门禁仅协调本次 Web 服务，独立程序的项目写入仍需用户自行协调。

## 材料入口

- [运行层与分发](runtime-report.md)、[只读历史](history-report.md)、[审批](approval-report.md)、[前端](frontend-report.md)。
- [独立审查与修复](review.md)、[接口约定](implementation-contract.md)、[规格场景映射](spec-audit.md)。
- [真实浏览器截图](web-resume.jpg)、[停止后的工作台](web-cancel.jpg)、[脱敏存档计数](web-journal-audit.json)。
