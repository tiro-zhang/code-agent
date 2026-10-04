# add-context-management 本次验证

日期：2026-10-04。实现采用 OpenSpec `spec-driven`，30项任务已完成。保留工作区既有改动，未将其他阶段的修改当作本次功能新增。

## 自动化与构建

- 最终全量：**752 passed，83.66秒**，见 [pytest-final.txt](pytest-final.txt)。运行 `UV_CACHE_DIR=/tmp/mewcode-uv-cache uv run --no-sync pytest -q --tb=short`；MCP HTTP测试在允许本地监听的环境执行。
- 上下文新增测试43项通过；另有配置、两种协议及现有会话／权限／模式测试的新增或迁移断言。源码缓存阈值、分区、摘要原子性、真实超限恢复、熔断、手动维护和取消均有确定性覆盖。
- `uv build` 成功产生源码包和wheel，并检查wheel包含全部六个 `mewcode/context` 模块，见 [build-final.txt](build-final.txt)。首次沙箱DNS失败后获准下载构建依赖，最终离线构建成功。
- `openspec validate add-context-management --strict` 通过，见 [openspec-validation.txt](openspec-validation.txt)。
- 初始基线677通过、14失败；13项是沙箱监听限制，1项是既有终端40ms固定等待的时序问题。最终两项终端画面断言改为等待目标页实际绘制，未改变分页产品逻辑。

## 真实 tmux 验收方式

使用 `/tmp/mewcode-context-e2e/` 内独立项目，通过 `tmux -L mewcode-context-e2e` 启动 `uv run --project <repo> --no-sync mewcode --config <临时配置> --permission-mode bypass`。内置工具、真实供应商适配器、流收集和终端均实际执行。

为有限时间内触发自动摘要，使用 [tmux-fixture.py](tmux-fixture.py) 预装明确标注的**合成压力历史**，不是声称曾真实执行的操作。窗口刻意设为64000、输出4096、任务请求上限12；它们是本次应用压力测试预算，不是测得的服务最大窗口。夹具只预装历史并记录事件元信息／已接受的正式摘要，不替换模型、工具或摘要响应，不存储草稿／内部思考。

配置从既有已授权 `.env` / `.env.claude` 临时复制，不改原配置，所有临时凭据副本已在验收后删除。缓存内容均为本次合成测试数据；真实修改仅限临时 `demo.py`。本次使用bypass隔离人工审批流程；明确deny、规划只读、待执行计划保护由实际权限组件的确定性测试验证。

| 场景 | 结果与证据 |
| --- | --- |
| Anthropic自动压缩 | DeepSeek `deepseek-v4-pro`；前两次摘要未通过校验，第三次成功；估算48643→13506。见 [终端](anthropic-tmux-final.txt)、[事件](anthropic-events.jsonl)。 |
| Anthropic工具结果落盘与继续修改 | 读取big.txt和demo.py，大结果落盘1项；edit_file将减法改为加法，KEEP原样保留；真实execute_command输出CHECK_OK。随后按第2行起2行重读缓存，再读当前代码并验证add(3,4)==7。见 [产物](anthropic-demo.py)。 |
| Anthropic手动摘要 | 最终明确格式Prompt下单次成功，估算48543→13335；实际输入20761、输出650；/status保留“暂无任务记录”，单独显示手动用量。见 [终端](anthropic-manual-final-tmux-final.txt)。此前两个格式拒绝样本另存为 anthropic-manual-*，未掩盖失败。 |
| OpenAI兼容自动压缩 | 同一授权DeepSeek服务，`base_url=https://api.deepseek.com`，`model=deepseek-v4-pro`。单次摘要成功，估算48688→13569；读取、两项结果落盘、修改、CHECK_OK断言均成功。见 [终端](openai-deepseek-tmux-final.txt)、[事件](openai-deepseek-events.jsonl)、[产物](openai-deepseek-demo.py)。 |
| OpenAI兼容缓存分页及下一任务 | 显式start_line=2、max_lines=2读取缓存，重新读取demo.py，真实命令验证add(3,4)==7。分页读取的truncated表示还有后续行，与“已落盘”状态不同。 |
| OpenAI兼容手动取消与恢复 | 活动摘要时发送Ctrl+C，关闭流并恢复提示符，失败数保持0。再次/compact单次成功，估算48585→13380；实际输入20824、输出1824。取消请求用量未知，不填零。见 [终端](openai-manual-tmux-final.txt)。 |
| 原Ark入口 | `ark-code-latest` 本次TLS连接失败；自动3次熔断、显式一次探测仍失败，未启动工作请求。最小独立请求确认TLS错误，未绕过验证。见 [诊断](openai-ark-tls.txt)、[终端](openai-tmux-final.txt)。不声称该入口通过。 |
| 正常退出缓存清理 | 六个本次tmux会话均经/exit退出；逐一检查工作目录，不再有本会话缓存子目录；共享忽略文件允许保留。临时凭据副本已删除。 |

DeepSeek官方文档确认同一服务提供两个兼容入口：[首次调用 API](https://api-docs.deepseek.com/zh-cn/)。本次未把凭据发送到新的第三方服务。

实际累计用量（仅服务已报告值）：

| 验收会话 | 工作请求 | 摘要请求 | 工作输入／输出 | 摘要输入／输出 |
| --- | ---: | ---: | --- | --- |
| Anthropic自动及后续 | 7 | 3 | 68332／712 | 62295／2904 |
| OpenAI兼容自动及后续 | 8 | 1 | 87741／1416 | 20861／2926 |
| Anthropic最终手动 | 0 | 1 | 无请求 | 20761／650 |
| OpenAI兼容手动 | 0 | 2（含取消） | 无请求 | 已知20824／1824；取消请求未知 |

## 摘要内容复核

两种协议接受的正式摘要均逐字保留“只修改 demo.py；保留 KEEP 原样；未运行测试不得说通过”，来源ID通过真实用户文本子串校验。摘要明确标注合成压力记录不代表实际操作，尚未读取／修改／验证；未将计划写成完成事实。后续模型确实先读取当前demo.py，再编辑并运行断言。正式摘要样本见 `anthropic-history-metadata.json`、`openai-deepseek-history-metadata.json`；工具及正文元信息之外没有草稿或内部思考记录。

语义复核属于本次小样本观察，不证明模型永不遗漏。若格式、原话、文件索引或预算不合格，系统实际保留历史并按有界失败规则处理。

## 规格与验证对应

| 规格 | 主要验证 |
| --- | --- |
| provider-configuration | test_config：必填窗口、8192默认、非法数值和等号边界；配置模板迁移 |
| llm-providers | test_openai_provider、test_anthropic_provider：输出限制和无工具；双协议真实请求 |
| context-management | test_context_estimation、spill、partition、summary：字符／usage、原子缓存、配对原文、六节结构、引用索引与原子提交 |
| agent-loop | test_context_agent及原循环测试：统一次数、3次熔断、受控停止、实际超限恢复与无工具重放 |
| interactive-chat | test_context_manual、terminal_*、app：命令、状态、最近任务独立、输入隔离／取消；真实/compact、/status、Ctrl+C |
| runtime-context | test_prompt_context及摘要集成：摘要不推进模式周期，下一工作完整提醒，边界在配对组外 |

## 审查与限制

独立审查的3项Important均已先复现再修复，见 [code-review.md](code-review.md)。没有遗留Critical/Important。

精确tokenizer、递归分块摘要、旧原始输出补采、跨进程恢复与机器学习策略均不在本次范围。不可压缩的原文／近期区太大、必要引用原话超过2K、缓存失效等确定性情况会明确停止，不承诺任意大小单条输入都能继续。

现有原始 `.env` 缺少新必填窗口；使用者需要按真实服务能力补充 `context_window` 后启动。未自动猜测或修改真实配置。旧清单其他阶段的手工项目没有逐一重跑，逐项标记在 [checklist-audit.md](checklist-audit.md)，不使用过去记录冒充本次结果。
