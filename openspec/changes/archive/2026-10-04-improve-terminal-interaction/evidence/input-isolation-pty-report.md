# 本次真实 PTY 输入隔离验收

验收时间：2026-10-04 08:50（Asia/Shanghai）。结果：**8 项通过，无失败项**。

本次使用独立 `mewcode-ui-isolation` tmux socket、100×32 终端和一个持续存活的真实 `EnhancedTerminal`。驱动通过 tmux 向 PTY 写入真实按键字节，使用阶段信号文件同步，并检查实际审批 future、输入草稿及最终返回值；同时保存 tmux 文本。这是输入边界验收，没有调用模型、执行文件工具或写入持久授权，不能替代项目的真实模型端到端验收。

| 场景 | 实际动作与结果 | 状态 |
| --- | --- | --- |
| 部分粘贴开头跨阶段 | 在 running 写入 `ESC[20`，确认解析器收到后切换审批，再写入 `0~2\n3\nESC[201~`；审批保持等待、答案为空 | 通过 |
| 运行期普通提前输入 | 在 running 写入 `queued while running` 和回车；该文本未进入审批或后续聊天 | 通过 |
| 数字多行粘贴 | 审批时完整粘贴 `2\n3\n`；只形成草稿，不提交审批 | 通过 |
| 多行答案提交 | 对上述草稿按 Enter；提示无效选项，审批仍等待且草稿清空 | 通过 |
| 明确单次批准 | 输入 `2` 和 Enter；第一请求返回 `once` | 通过 |
| 连续决定残留 | 同一批输入中的额外 `3` 和 Enter 未进入第二请求；第二请求保持等待、答案为空 | 通过 |
| 取消审批 | 第二请求按 Ctrl+C，返回 `deny`，未被当作 EOF | 通过 |
| 取消后输入恢复 | 下一聊天草稿为空，输入“取消后仍可输入”和 Enter，返回完整原文 | 通过 |

关闭应用前后，PTY 的 ECHO 和 ICANON 均为 `true`。专用 tmux server 已清理，未操作其他验收 socket。

材料：

- [可复现驱动](input-isolation-pty-driver.py)
- [结构化断言结果与阶段记录](input-isolation-pty-result.json)
- [跨阶段旧粘贴被丢弃](input-isolation-pty-02-stale-paste.txt)
- [新粘贴形成草稿](input-isolation-pty-03-fresh-paste-draft.txt)
- [多行答案未批准](input-isolation-pty-04-multiline-rejected.txt)
- [第二请求保持等待](input-isolation-pty-05-second-pending.txt)
- [取消后下一输入](input-isolation-pty-06-after-cancel.txt)
- [结束时终端输出](input-isolation-pty-07-done.txt)

复现命令：在仓库根目录执行 `.venv/bin/python openspec/changes/improve-terminal-interaction/evidence/input-isolation-pty-driver.py`。运行前确保同名专用 socket 没有其他任务；该驱动结束时清理此 socket。
