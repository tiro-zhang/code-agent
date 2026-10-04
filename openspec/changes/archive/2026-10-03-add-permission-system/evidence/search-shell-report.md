# 搜索与 shell 权限真实端到端验收

日期：2026-10-03（Asia/Shanghai）。覆盖任务 8.4、8.5。

使用独立项目 `/private/tmp/mewcode-permissions-e2e-search`、独立 tmux socket `mewcode-perm-search`，启动真实 CLI：`.venv/bin/python -m mewcode --config <repo>/.env.claude`。权限模式为 default，连接已有真实模型。未修改产品代码、用户 HOME 或用户级规则。

| 场景 | 终端与文件核验结果 |
| --- | --- |
| 内容搜索部分受限 | 只返回获批公开文件，`permission_limited=true`、`skipped_files=1`；受限文件名和内容探针未出现 |
| 内容搜索全部受限 | 成功返回空 matches，`permission_limited=true`、`skipped_files=2`；模型正确说明不能据此判断全项目无匹配 |
| 文件 glob 部分受限 | 只返回获批公开路径，跳过 1 个文件 |
| 文件 glob 全部受限 | 成功返回空 paths，跳过 2 个文件 |
| Bash glob allow：简单 printf | 无需审批直接执行，stdout 与退出码正确 |
| Bash glob allow：含重定向和分号的复合命令 | 等待完整精确调用审批；审批前标记文件不存在；选择本次批准后两段命令正确执行 |
| 复合命令中无害子命令 deny | 返回 `permission_denied`、`not_started=true`；前缀写入与被拒绝子命令的两个标记文件均不存在 |
| 安全黑名单载体 | `printf '%s\\n' 'rm -rf /'` 只包含字面危险文本；原始文本黑名单拒绝，`root_recursive_delete`、`not_started=true`；没有执行危险操作 |
| 完整 exact allow：复合命令 | 直接执行且不再询问；stdout 为 exact-finished，文件内容为 exact-shell-ok 加换行 |

共完成 9 轮真实模型对话，18 次模型请求。终端记录未出现受限文件名探针与受限内容探针。本次验收使用静态权限规则，不依赖 shell OS 沙箱，也未追踪脚本内部行为。

另外重新运行 3 项 spy 测试，结果 **3 passed in 0.10s**：权限前仅枚举元数据，权限后内容搜索仅把获批 canonical 绝对文件传给 rg，禁止通过扫描目录后过滤结果实现权限。真实 CLI 与自动化 spy 合并覆盖了可见结果和内容读取范围。

完整脱敏记录见 [search-shell-tmux.txt](search-shell-tmux.txt)。验收结束后已关闭本任务独立 tmux 会话。
