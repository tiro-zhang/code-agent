# 结构化系统提示验收记录

日期：2026-10-02。变更：`add-structured-system-prompt`。

## 条件与复现材料

- 旧版 Git：`1fbb91ea969dd49a4bd578081a6316cc0e5b9ad8`，通过 `git archive HEAD` 保存到临时目录，未增加产品旧提示开关。
- 配置：本地 `.env.claude`；请求模型标识 `deepseek-v4-pro`，`https://api.deepseek.com/anthropic`，Anthropic 协议，thinking=true，任务预算 20。记录的是请求标识，不据此推断服务端路由所用的权重版本。
- 同一实际工作目录：`/private/tmp/mewcode-prompt-validation/workspace`。每次完整对比前恢复 [fixtures](fixtures)；配置位于项目目录，不影响工具根目录。
- 同一输入：[prompts.json](prompts.json)，8 个真实用户任务，含 `/plan`、普通修订、`/do`；最后 `/exit`。
- 两个有效完整对比均在 tmux 中启动 MewCode，通过 stdin 依次输入固定对话。模型和工具实际执行，未使用模拟 Provider。比较由人工阅读完成，没有行为自动评分。
- [capture_session.py](capture_session.py) 是验收采集脚本：接收 `--source <版本的src目录>`、`--config <本地配置>`、`--trace <输出jsonl>`，从当前目录启动真实 MewCode；不记录密钥、请求头或思考正文。可以在 tmux 中交互输入，也可将固定请求转换为文本文件重定向到 stdin。此脚本只适用于本章 Anthropic 兼容基准。
- 原始完整运行材料还保留在上述临时目录。仓库内终端记录移除了思考正文并清理行尾空格，保留工具状态、最终答复、用量和失败信息。

## 真实字段与缓存

探针 [deepseek-usage.json](deepseek-usage.json) 的实际起始与结束用量：

| 事件 | input_tokens | cache_read_input_tokens | cache_creation_input_tokens | output_tokens |
| --- | ---: | ---: | ---: | ---: |
| message_start | 90 | 1024 | 0 | 0 |
| message_delta | 90 | 1024 | 0 | 3 |

实测中基础输入为未命中分项，缓存读取单独返回，总输入为 1114，不能把 90 用作命中率分母。返回的 creation=0 未证明自动缓存实际写入量，本章保守显示写入未知。未收到 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` 别名；其归一化由原始协议回归覆盖，不能称作本次真实响应字段。

DeepSeek 的缓存自动启用、公共前缀匹配和尽力而为语义见[官方缓存说明](https://api-docs.deepseek.com/guides/kv_cache/)；`cache_control` 被兼容接口忽略见[官方兼容说明](https://api-docs.deepseek.com/guides/anthropic_api/)。原生 Claude 的创建计数口径只用于官方地址，不套用到 DeepSeek。

有效新版对比的前三次连续请求：

| 模式请求序号 | 提醒 | 总输入 | 命中 | 未命中 | 命中率 | 首个可展示片段等待（秒） |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 完整 | 1900 | 896 | 1004 | 47.2% | 3.235 |
| 2 | 精简 | 2222 | 1920 | 302 | 86.4% | 0.988 |
| 3 | 精简 | 2423 | 2176 | 247 | 89.8% | 1.520 |

这些数据来自 [trace-new.jsonl](trace-new.jsonl) 的实际 usage，按请求合并累计更新，不将 start/delta 相加。[metrics-new.json](metrics-new.json) 保存全 22 次记录；只有工具参数、没有可展示思考或正文的响应，首片段时间为 null，不用完整工具返回时间替代。

新版同模式固定文本与工具定义指纹：执行 `b387843fe4b205753e2c316ccf4db04480ac5d190278aa61bc98abb5ee8fdcfb`，规划 `60c6c2f76243080def7da7642d1315f0cb0b80492012580516808dc739a76890`。这是应用层 system 和完整有序工具定义的 SHA-256；模式内保持一致，切换三/六工具会改变指纹。固定七模块自身不随模式改变。

完整提醒出现在全局请求 1、6、11、13、16、21：对应初始 execute 的 1/6/11，plan 的第 1 次，以及 `/do` 后 execute 的第 1/6 次。历史里的旧提醒原样保留。终端未出现额外用户输入或独立标签答复。

首轮已有命中，不能称为强制冷缓存；等待时间是客户端从发起请求到首个可展示片段的观察值，不是服务内部 prefill 时延。两版任务路径与输出长度不同，本次不计算费用节省或宣称速度提升。

## 人工对比

完整工具调用参数和最终答复分别见 [旧版 trace](trace-old.jsonl)、[新版 trace](trace-new.jsonl)；逐步工具成功/失败状态见 [旧版终端](terminal-old.txt)、[新版终端](terminal-new.txt)。以下序列按调用顺序展开，同一请求可包含多个工具。

| 场景 | 旧版 | 新版 | 人工观察 |
| --- | --- | --- | --- |
| 定位 add，暂不修改 | 3 请求；search→read | 3 请求；search→glob→read | 均使用专用工具并准确指出减法实现，没有修改 |
| 修复 add 并验证 | 4 请求；edit→command→command | 4 请求；edit→command→command | 均使用上一任务已读、尚未改变的当前内容；python 不存在后改用 python3，真实检查通过，答复说明失败及恢复 |
| 歧义编辑后调整 | 5 请求；read→edit失败→edit成功→read | 同左 | 均遇到 multiple_matches 后补足唯一上下文，最终 left=2/right=1；两版都没有在失败与重试之间再调用 read，**新版重读引导未稳定遵守** |
| /plan 新建文件 | 1 请求，无工具 | 2 请求；glob、read、read | 均输出完整目标、步骤、验证；新版额外探索现有风格；工具范围始终仅三种只读工具 |
| 普通修订中文问候 | 1 请求，无工具 | 1 请求，无工具 | 均输出完整新计划，保持只读；新版对已明确的问候格式仍留了多余“待确认”，简洁性有改进空间 |
| /do 执行最新计划 | 5 请求；glob→write→read→command | 4 请求；glob、write→read、command | 均真正创建中文问候并通过断言，旧规划上下文没有阻止执行；新版在允许范围内合并了调用 |
| 运行预设失败检查 | 2 请求；command | 2 请求；command | 均报告退出码 1 / AssertionError，未误称通过，也未修改失败脚本 |
| 会话总结 | 1 请求，无工具 | 1 请求，无工具 | 均准确汇总已完成改动和未通过验证；新版未单独解释 context 标签 |

有效完整对比总计：旧版 22 次模型请求、14 次工具调用；新版 22 次模型请求、18 次工具调用。不能据此宣称新版总体更快、工具更少或行为质量普遍提高。验证了结构、模式与真实缓存可用，也保留了重读与简洁性不足的观察。

独立核验 [旧版结果文件](result-old) 与[新版结果文件](result-new)：`add(2,3)==5`、`add(-2,3)==1`、`left()==2`、`right()==1`、`greet('猫')=='你好，猫'` 均成立；`verify.py` 与 `failing_check.py` 和初始文件字节相同。

## 未隐藏的失败与复验

- 首次旧版采集包装脚本遗漏 multiprocessing 主入口保护，工具工作进程异常结束。这是验收脚本问题，该轮作废；加上主入口保护、恢复初始文件后才得到上面的旧版基线。作废日志保留在临时目录 `invalid-harness-*`。
- 新版首次运行的第 1 请求发生服务连接失败，未收到 usage；输出为未知，未保存残缺任务，下一请求仍完整补发。其余场景继续完成。完整记录：[trace-new-first.jsonl](trace-new-first.jsonl)、[terminal-new-first.txt](terminal-new-first.txt)。因为第一场景缺失，恢复初始文件做了一次完整复验作为表中对比，未丢弃首次失败记录。
- 未观察到精确编辑失败后必定重读。提示和工具描述已双重说明，执行器仍按本章范围不增加读取记录门禁。
- 官方 Claude、官方 OpenAI 的真实 API 本章未执行；Claude 缓存请求块、OpenAI 序列化与缓存用量由替换网络传输的协议测试覆盖，不能用 DeepSeek 结果替代。

## 确定性验证与审查

新增测试覆盖固定模块、显式可选位置、context 身份/协议、11 次请求周期、失败补发、裁剪、取消、计划边界，以及按服务的缓存总量、部分字段、SDK 缺失默认值、矛盾分项与累计比率。既有文件工具算法、Schema、预算和权限保持回归覆盖。这些是程序契约测试，不是自动化模型行为评估。

独立只读审查发现：Agent 的请求包装无条件调用 `aclose()`，会拒绝符合 AsyncIterator 合同但无关闭方法的 Provider。新增回归先复现再通过，改成可选关闭；审查者复核通过。自查另补齐 Claude 已知 miss 的保留、OpenAI 单独缓存分项及矛盾总量处理，4 个回归均先失败后通过。

## 最终版本的独立缓存实验

审查修复后的最终版本另在 tmux 中执行一次只读任务：先 search_code 定位 add，再 read_file，最后说明当前行为，共 3 次连续模型请求。此实验独立于上面的人工对比；实际发送的 system/tools 字段在 SDK 请求入口采集，三次 SHA-256 完全相同：`8c6da0f08f05c201b38474a27943cb72ef4fa33988a65b20ccc198620d7b67c9`。原始字段及响应模型标识见 [cache-final.jsonl](cache-final.jsonl)。

| 请求 | 总输入 | 命中 | 未命中 | 首片段等待（秒） |
| --- | ---: | ---: | ---: | ---: |
| 1 | 1935 | 1664 | 271 | 1.967 |
| 2 | 2168 | 2048 | 120 | 1.601 |
| 3 | 2367 | 2176 | 191 | 1.709 |

三次均未发送 cache_control；实际返回的 creation=0 保留于原始证据，终端仍将实际写入量标为未知。累计总输入 6470、命中 5888、未命中 582，加权命中率 91.0%。

最终完整回归：`UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run pytest -q`，**193 passed in 25.57s**，退出码 0；[原始测试输出](final-suite.log)。临时 UV_CACHE_DIR 用于适应沙箱权限，不改变测试逻辑。

OpenSpec：`openspec validate add-structured-system-prompt --strict --json` 返回 valid=true、issues=[]；`git diff --check` 通过。25 项实施任务均完成，人工行为局限与未执行的真实服务验证保留在本报告及 checklist 中。
