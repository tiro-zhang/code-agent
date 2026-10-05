# optimize-terminal-ui 本轮验收

日期：2026-10-05。实施位置：当前工作目录；已同步主规格并[归档](../../../openspec/changes/archive/2026-10-05-optimize-terminal-ui/proposal.md)。此处记录本轮执行结果，历史验收不作为通过依据。

## 自动化与审查

- 完整回归：**1007 passed，0 failed，98.12s**，见 [pytest.txt](pytest.txt)。首次沙箱基线的 13 个失败均为本地 HTTP 端口绑定被拒；允许测试绑定本地端口后相关 27 项通过，最终完整回归也在该环境执行。
- `uv build --offline` 与 `openspec validate optimize-terminal-ui --strict` 通过，见 [build.txt](build.txt)、[openspec.txt](openspec.txt)。wheel 与 sdist 均包含 `details.py`、`projection.py`、`markdown.py`、`render.py`，未包含真实 `.env` 配置。
- 独立只读代码审查发现并修复窄屏导航裁切、活动区完成计数、命令菜单描述脱敏，以及任务结束后 F2 关闭问题。最后复核 34 项通过，无剩余 Critical / Important。
- 真实 tmux PTY 输入脚本 [input-pty.py](input-pty.py) 本轮 **16 项通过**，包含自动菜单、Enter 选择、草稿 Ctrl+C、F2 草稿／光标、数字／粘贴／Enter 隔离、授权抢占、窄屏 resize、跨代粘贴、授权取消、延迟清理、恢复输入及摘要输入阶段取消。退出后 ECHO、ICANON 恢复。见 [结果](input-isolation-pty-result.json)。脚本不请求模型或执行工具，摘要阶段的键盘行为与真实 Agent／摘要流取消回归分别验证。

## 真实服务与终端

使用独立 tmux server `mewcode-ui`、session `mewcode-e2e`，临时项目 `/private/tmp/mewcode-terminal-ui-e2e`（证据中保留该测试路径）；项目和用户可写状态均位于临时目录，自动记忆关闭。初始尺寸 110×34，最终代码另验证 45×15。入口 [launcher.py](launcher.py) 直接使用真实 Provider 和权限处理，不替换模型或工具。

默认 `.env` 的 OpenAI 兼容服务发生 TLS 连接失败，保留 [失败证据](initial-tls-failure.txt)。后使用另一份已有授权配置 `.env.claude` 的 Anthropic 兼容服务、模型 `deepseek-v4-pro`，原配置 `thinking=true`，未更改配置或关闭 TLS 验证。

| 场景 | 结果与证据 |
| --- | --- |
| 多轮问题保留、三次读取与一次搜索 | 通过；第二轮实际重新调用四个工具，合并为“已读取 3 个文件（3 次调用）、完成 1 次搜索”，随后回答和一次结束摘要：[aggregate-multiturn.txt](aggregate-multiturn.txt) |
| 真实读取授权 | 通过；核对三份临时文件和搜索范围，选择会话批准：[read-approval.txt](read-approval.txt)、`batch-approval-*.txt` |
| 编辑内容首屏及真实产物 | 通过；首屏展示 `return 1` → `return 11` 的完整请求 diff，实际 alpha.py 内容另作精确断言：[edit-approval.txt](edit-approval.txt)、[verification.json](verification.json) |
| 完整命令授权与失败独立提示 | 通过；初次 `python` 不存在，退出 127 单独显示；模型改用 `python3`，重新审批后断言成功：[command-error-and-retry.txt](command-error-and-retry.txt)、[edit-command-result.txt](edit-command-result.txt) |
| 独立 `/test` | 通过；原始命令回显、子身份一次、子命令聚合、子回答一次、主摘要一次；历史回流未再次打印整份结果：[isolated-skill.txt](isolated-skill.txt) |
| F2 调用／思考 | 通过；调用页可读：[f2-tools.txt](f2-tools.txt)。独立 Skill 的真实 API 思考在详情中可见，正文默认收起；仅记录布尔验证结果，不保存思考正文：[thinking-view-skill.json](thinking-view-skill.json)。前一次普通任务没有思考记录，界面没有伪造内容，见 `thinking-view.json` |
| 最终版本授权取消 | 通过；read_file 等待授权时取消，结果明确未启动；下一轮正常回答并受控退出 0：[approval-cancelled.txt](approval-cancelled.txt)、[after-approval-cancel.txt](after-approval-cancel.txt)、[exit.json](exit.json) |
| 最终版本模型生成取消 | 通过；仍在生成阶段发送 Ctrl+C，回到空闲：[model-cancelled.txt](model-cancelled.txt) |
| 最终版本长流式代码 | 通过；重新启动最终代码，真实模型生成标题、列表及完整 Python 围栏，完成后无整段重印：[final-long-code.txt](final-long-code.txt) |
| 最终版本窄屏授权与工具取消 | 见 `final-command-review.txt`、`final-review-narrow.txt`、`tool-running.txt`、`tool-cancelled.txt`、`tool-cancel-timing.json`；核对完整临时命令，实际启动后 Ctrl+C，0.848 秒内回到输入；下一轮真实回答 UI_CONTINUE_OK，125.6 秒后未出现延迟产物，见 [after-cancel-continue.txt](after-cancel-continue.txt) |

模型关于“无残留副作用”的自然语言不作为验收依据：Python 导入可以留下 `__pycache__`，实际检查以临时文件和进程执行结果为准。一次 `/compact` 取消尝试在发送 Ctrl+C 前已返回空闲，因此空草稿按规则退出；该次不计作真实摘要流取消通过，摘要取消由本轮确定性回归及真实 PTY 输入阶段测试覆盖。

## 13 项 requirement 对照

| Requirement | 结论 | 本轮主要覆盖 |
| --- | --- | --- |
| 区分思考与回答 | 通过 | projection 思考与用途测试；真实 Skill 思考页 |
| 工具状态可见 | 通过 | state / controller / permission_app；真实读、编辑、命令及失败 |
| 进度与用量展示 | 通过 | state 逐请求覆盖／未知／缓存部分字段测试；真实一次结束摘要 |
| 本地命令帮助与校验 | 通过 | command_registry / slash_app / slash_contracts / terminal_commands；元数据脱敏 |
| 工具状态的紧凑呈现 | 通过 | projection 批次、来源、乱序、重复、受限成功；真实 3 文件 + 1 搜索 |
| 单一授权审阅区域 | 通过 | terminal_approval / terminal_input / mcp_permissions；真实 diff、完整命令、45×15 |
| 输入阶段隔离与兼容退化 | 通过 | 16 项真实 PTY、permission_app SIGINT、plain 管道／默认拒绝回归 |
| 会话与记忆维护反馈 | 通过 | projection 维护独立通道，memory / context_manual / slash_app 相关回归；真实后台记忆未启用 |
| Skill 活动与独立执行反馈 | 通过 | skills_terminal / skills_isolated 及实际 `/test`；主子用量不重复 |
| 对话轮次与提交回显 | 通过 | app / slash_app 多行、空白、转义、提示词；真实多轮与原始 `/test` |
| 流式正文可读性 | 通过 | markdown 增量和跨分片密钥；controller 未换行可见／只提交一次；真实长代码 |
| 最近任务详情浏览 | 通过 | projection UTF-8 字节上限／淘汰／引用／新任务与 reset，command clear 回归；真实 F2 与 PTY |
| 斜杠命令自动发现 | 通过 | ui_keys / command_registry / skills_commands；实际 PTY 菜单、Enter、Esc／粘贴边界 |

真实 MCP Server 取消、磁盘故障、全部服务协议、全部终端型号没有逐一制造；相关协议、权限边界、永久批准保存失败、输出受限、超时及取消行为由本轮完整自动化测试覆盖。本报告不将这些标为真实模型 tmux 场景。

## 复验

先准备临时项目及已授权配置，再运行：

```sh
MEWCODE_E2E_ROOT=/private/tmp/your-project \
MEWCODE_E2E_CONFIG=/absolute/path/to/authorized.env \
PYTHONPATH=/absolute/path/to/repo/src \
/absolute/path/to/repo/.venv/bin/python docs/validation/terminal-ui/launcher.py

uv run python docs/validation/terminal-ui/input-pty.py
uv run pytest -q
uv build --offline
openspec validate --specs --strict
```

不要把真实配置、密钥或思考正文复制到验收目录。`verification.json` 汇总机器可读结果。
