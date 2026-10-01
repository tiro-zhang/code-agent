## 1. 基础契约、配置与验证准备

- [x] 1.1 依据 [design.md](design.md) 和六份增量规范，在 `src/mewcode/types.py` 定义 `AgentMode`（execute/plan）、五种 `StopReason`、`TokenUsage(input_tokens: int | None, output_tokens: int | None, complete: bool)`、`ProviderEvent`、`AgentEvent` 与 `CollectedResponse(message: Message, usage: TokenUsage)`，保留 `Message`、`ToolCall` 的现有字段；TokenUsage 的可选缓存字段 `cache_read_tokens`、`cache_write_tokens` 缺省为 None。将 Provider 契约明确为 `stream(messages, *, tools=(), tool_choice="auto", system_prompt="") -> AsyncIterator[ProviderEvent]` 和异步 `aclose()`。事件包含 `run_id`、`iteration`，工具事件包含调用 ID，用量缺失字段使用 `None`；验证类型可导入且不依赖终端对象，后续组件使用同一套名称和字段。
- [x] 1.2 在 `tests/conftest.py` 提供可控异步 Provider 与流关闭观察用的共享替身，迁移两个 Provider 测试文件中的客户端替身为异步请求和原始异步流；使用现有 pytest 和 `asyncio.run()` 驱动测试，支持阻塞、分段、异常及请求记录。验证替身能在同一事件循环中观察片段到达、请求选项和流关闭，不增加运行时 Agent 框架或异步测试插件依赖。
- [x] 1.3 在 `src/mewcode/config.py` 的现有配置对象增加 `max_iterations: int = 20`，从指定配置文件解析十进制正整数，保留原有必填字段和 `thinking`；在 `tests/test_config.py` 验证旧配置默认 20、自定义 5，以及零、负数、空值、非整数被启动前拒绝，错误不泄露凭据。运行 `.venv/bin/python -m pytest tests/test_config.py -q` 验证通过。
- [x] 1.4 在 `src/mewcode/tools/base.py`、`registry.py`、`files.py`、`search.py`、`command.py` 增加保守的 `read_only` 元信息；登记三个读类工具为 true，其余为 false，缺少声明按 false。提供 `names(*, read_only: bool | None = None) -> frozenset[str]`、`definitions(*, allowed_tools: frozenset[str] | None = None)`，并使 `prepare()`、`invoke()` 支持同名 `allowed_tools` 参数，在解析参数及执行前拒绝模式禁止工具；通过 `tests/test_tool_registry.py` 验证三/六工具筛选、默认非只读、重名拒绝，以及 `unknown_tool`、`tool_not_allowed`、`invalid_arguments` 的区分。

## 2. 两种 Provider 的异步流与多工具协议

- [x] 2.1 将 `src/mewcode/providers/openai.py` 迁移为原生异步客户端和原始流，实现任务 1.1 的接口及资源关闭，禁用 SDK 隐式重试；保持独立参数拼接、既有上限和结束校验，使用 `tool_choice=auto`、`parallel_tool_calls=true`，把独立 `system_prompt` 映射为系统消息。迁移 `tests/test_openai_provider.py` 与相关工具协议断言，验证交错多调用、普通答复、无效 ID、截断响应和取消网络等待；运行对应 Provider 测试文件验证通过。
- [x] 2.2 将 `src/mewcode/providers/anthropic.py` 迁移为原生异步客户端和原始流，实现同一接口及资源关闭，禁用 SDK 隐式重试；自动工具选择去掉单工具限制，独立系统提示映射到协议的系统字段。通过 `tests/test_anthropic_provider.py`、`tests/test_tool_providers.py` 验证 Claude/DeepSeek 配置策略、多个工具块、非法 JSON 可回灌，以及签名、不可展示思考块和原始顺序完整保留。
- [x] 2.3 在两个 Provider 中归一化 `TokenUsage`：OpenAI 请求 `stream_options.include_usage=true` 并读取 `choices=[]` 的结束附近用量块，Anthropic 更新累计计数时覆盖为最后有效值；缓存附加字段与基础计数分开，缺失和中断用量不填零。在 `tests/test_tool_providers.py` 增加 `test_openai_usage_after_finish_is_not_extra_content`、`test_anthropic_usage_is_cumulative` 和缺失/部分统计用例，断言 5、9 两次累计输出最终为 9，并验证正常内容不因缺少用量失败。
- [x] 2.4 检查并按多调用需求调整 `src/mewcode/providers/tool_messages.py` 及 `providers/__init__.py`，保持每个 ID 恰好一个结果、原始调用顺序和协议续接块，禁止把工具输出提升为系统指令；通过 `tests/test_tool_providers.py` 验证乱序完成的成功/失败/取消结果在两种协议中仍有序配对，过滤后的定义不包含本地只读字段。运行三个 Provider 测试文件完成这一层回归。

## 3. 双路流式收集器

- [x] 3.1 新增 `src/mewcode/collector.py`，实现 `StreamCollector(stream: AsyncIterator[ProviderEvent])` 和 `events(*, run_id, iteration, mode) -> AsyncIterator[AgentEvent]`；即时转发文本和实际思考，同时累计完整文本、原始助手消息及用量，提供 `response: CollectedResponse | None` 和当前 `usage: TokenUsage`。在 `tests/test_collector.py` 用阻塞后续片段的流验证首段已被消费者收到，最终文本和顺序完整。
- [x] 3.2 在收集器中检查唯一正常完成、结束后额外内容及缺失结束；流失败不产生完整响应，不因参数已经拼好而执行工具。在 `tests/test_collector.py` 验证仅工具响应有效、普通空答复被拒绝、参数完整后流失败仍无可执行响应，以及实际思考不被拼入最终回答。
- [x] 3.3 收集器维护单次请求最终或部分用量，异常时保留已知字段供 Agent 收尾，避免将累计更新相加；通过 `tests/test_collector.py` 验证已知输入/未知输出、缺少用量和完整统计。运行 `.venv/bin/python -m pytest tests/test_collector.py -q` 验证这一层通过。

## 4. 异步工具执行与可终止进程

- [x] 4.1 将 `src/mewcode/tools/executor.py` 的入口改为 `async execute(name: str, raw: str, *, allowed_tools: frozenset[str] | None = None, cancel_event: asyncio.Event | None = None) -> ToolResult`；保留同步工具和 `spawn` 工作进程，在启动前执行任务 1.4 的模式及参数检查，异步观察管道和进程退出，不在事件循环常规路径阻塞轮询/等待。迁移 `tests/test_tool_execution.py`，验证实际六工具行为及执行期间另一异步观察任务能够推进。
- [x] 4.2 实现异步期限与单工具清理：默认 30 秒，命令仍接受 Schema 中的 1 至 120 秒，超时终止本工具及同组子进程，保留有界部分输出和截断标记。通过 `tests/test_tool_execution.py` 的实际子进程用例验证没有延迟写入泄漏、超量和无效 UTF-8 输出仍正确标记截断，其他独立活动工具不被该超时取消。
- [x] 4.3 让 `cancel_event` 和异步调用取消均进入受控清理，取消后不再继续执行；移除每个并发执行安装 SIGINT 处理器的做法，保护终止、强制回收及管道关闭。通过 `tests/test_tool_execution.py` 验证取消确实停止工作进程与受控子进程、保存可用输出和副作用说明，而不是仅停止等待。
- [x] 4.4 在 `tests/test_tool_execution.py` 增加进程尚未发送 ready 时取消、结果已返回但清理期间取消、清理期间重复取消的回归；验证未误杀父进程组、实际完成数据和状态仍保存、资源全部回收。运行 `.venv/bin/python -m pytest tests/test_tool_execution.py tests/test_file_tools.py tests/test_tool_registry.py -q` 验证既有拒绝覆盖、唯一匹配、路径边界、输出限制及取消修复继续成立。

## 5. 按安全性分批的工具调度器

- [x] 5.1 新增 `src/mewcode/tools/scheduler.py`，提供 `ToolScheduler(executor, *, max_parallel=4)` 和 `run(calls: Sequence[ToolCall], *, allowed_tools, run_id, iteration, mode, cancel_event=None) -> AsyncIterator[AgentEvent]`，结束后提供按原始调用顺序排列的 `results: tuple[ToolResult, ...]`。连续只读调用分组并发，非只读调用逐个形成边界；在 `tests/test_tool_scheduler.py` 通过可控握手验证实际同时启动、活动数量最多 4，以及读取→修改→读取和 shell 的串行边界。
- [x] 5.2 调度前发布完整调用事件，只有有效且实际启动的调用发布开始事件；未知、禁止和非法参数直接产生结果，不启动进程。通过 `tests/test_tool_scheduler.py` 验证这些错误与普通工具失败/超时不阻断同批其他调用，也不在调度器中自动重试，完整写入参数不作为终端摘要输出。
- [x] 5.3 通过有界结果通道按完成顺序发布结果，由单一消费者维护顺序映射；在 `tests/test_tool_scheduler.py` 让后一个读调用先完成，断言事件按完成顺序到达、`results` 按模型顺序排列、每个 ID 恰好一个结果，并验证慢消费者不会造成无限队列。
- [x] 5.4 实现批次取消：停止新调度，收集已完成结果，清理全部活动工具，为未启动调用补齐 `cancelled` 与 `details.not_started=true`。通过 `tests/test_tool_scheduler.py` 验证并发取消、串行后续调用不启动、满队列背压期间取消后恢复消费能完成收尾且无重复结果；运行调度器和执行器测试验证通过。

## 6. 自主 Agent Loop 与停止条件

- [x] 6.1 新增 `src/mewcode/agent.py`，提供 `Agent(provider, executor, *, max_iterations=20)` 和 `run(question: str, *, history: list[Message], mode: AgentMode, cancel_event: asyncio.Event | None = None) -> AsyncIterator[AgentEvent]`；组合收集器与调度器，每次请求使用当前允许工具及独立模式提示，工具结果后继续使用自动工具选择。通过 `tests/test_agent_loop.py` 验证读取→编辑→命令检查→纯文本收尾，以及错误后模型修正参数，事件消费者不依赖终端。
- [x] 6.2 每个 `run()` 创建独立请求预算，在调用 Provider 前计数；所有失败尝试、普通答复和恢复请求使用同一计数，一批多工具仍只算一次。通过 `tests/test_agent_loop.py` 验证默认 20、自定义小预算、最后一次返回工具时处理并保存整批后停止、最后一次纯文本以 `model_done` 停止，以及无额外总结请求；新任务计数重新开始。
- [x] 6.3 按完整工具响应维护连续未知计数，全未知响应累计一次，包含已注册名称则清零，第三轮结果保存后停止；参数错误或模式禁止不算未知名称。通过 `tests/test_agent_loop.py` 验证三个未知调用只算一轮、混合调用清零、已注册但参数错误/模式禁止清零，以及未知阈值和预算同时命中时只报告 `unknown_tool_limit`。
- [x] 6.4 统一五种 `finished.reason` 和取消传播，活动模型流失败/空普通答复以 `stream_error` 停止，用户取消关闭流及调度，不增加除既有上下文恢复外的自动重试；每次请求发布一次用量，缺失任一统计时总量标为不完整。通过 `tests/test_agent_loop.py` 验证五种原因、网络等待取消、工具后流错误、取消与上限重叠时报告取消，以及清理后仅发布一次终止事件。

## 7. 会话历史与现有上下文恢复

- [x] 7.1 改造 `src/mewcode/session.py`，提供 `ask(question: str, *, cancel_event: asyncio.Event | None = None) -> AsyncIterator[AgentEvent]`，由会话持有历史并调用任务 6.1 的 Agent；将原先单工具两阶段控制替换为完整助手调用与全批有序结果的提交边界，首响应完成前用户消息仍是候选。迁移 `tests/test_session.py`、`tests/test_tool_session.py`，验证下一次请求看到全部已提交阶段，无孤立调用及重复执行。
- [x] 7.2 在 `agent.py` 中迁移既有按完整用户轮次、1/2/4 递增裁剪的上下文恢复，保持当前多阶段任务整体及调用/结果配对；每次恢复先检查并消耗任务预算，仅在未输出模型文本/思考且存在较早历史时恢复。通过会话测试验证裁剪提示、无旧历史停止、已经输出后不重试、恢复不会重放工具或改变未知计数，以及恢复耗尽预算的边界。
- [x] 7.3 将历史配对提交纳入受控取消收尾：首响应失败不提交候选历史，后续流失败只丢弃该次部分响应，工具取消保留实际结果并补齐其他结果。通过 `tests/test_tool_session.py` 验证写入后答复中断仍能在下一轮看到真实修改、取消后未启动调用有明确结果，历史提交期间再次取消也不会留下缺失结果；运行两份会话测试验证通过。

## 8. Plan Mode 状态与直接执行计划

- [x] 8.1 在 `session.py` 保存默认 `mode="execute"` 和内存中的待执行计划快照，提供 `enter_plan() -> None`，进入时清除旧待执行标记；规划提示要求成功答复包含完整目标、步骤及验证方式，规划请求仅提供三个只读工具。新增 `tests/test_plan_mode.py`，验证模式切换保留历史、后续消息仍只读、恢复请求仍筛选工具，以及绕过模型列表的写/编辑/shell 调用得到 `tool_not_allowed` 且无实际修改。
- [x] 8.2 在规划任务正常 `model_done` 后保存任务上下文及最终非空答复，新规划或修订开始时使旧待执行计划失效；中间文本、实际思考、工具结果、取消和其他停止原因不生成计划。通过 `tests/test_plan_mode.py` 验证成功修订替换旧计划、修订失败/取消/达到上限后不能执行旧计划，退出重启没有历史或待执行计划。
- [x] 8.3 提供 `execute_plan(*, cancel_event: asyncio.Event | None = None) -> AsyncIterator[AgentEvent]`：原子取出有效快照并清除待执行标记，切回执行模式，将计划作为新的用户执行任务传给同一个 Agent。通过 `tests/test_plan_mode.py` 验证 `/do` 对应的模型请求包含最新计划及已有上下文、允许六个工具、使用新预算，且无需再次描述任务。
- [x] 8.4 在 `session.py` 定义 `PlanStateError`，没有有效计划时 `execute_plan()` 报此中文且可安全展示的状态错误，由 CLI 提示，不请求模型或切换模式；已启动计划不能通过重复 `/do` 重放。通过 `tests/test_plan_mode.py` 验证首次无计划、失败修订后的 `/do`、执行开始后的重复 `/do`，以及执行结束后普通消息继续使用全部工具；运行该测试文件验证通过。

## 9. 终端接入与使用文档

- [x] 9.1 在 `src/mewcode/app.py` 保留同步 `run()` 启动入口，内部通过异步运行函数消费 `session.ask()` / `execute_plan()`，解析 `/plan`、`/plan <任务>` 和 `/do`，仅切换模式时不发模型请求；统一把活动期 Ctrl+C 传给本任务的取消事件，等待收尾后返回提示符，空闲期 EOF、Ctrl+C、`/exit` 保持退出行为，并在退出时异步关闭 Provider。通过 `tests/test_app.py` 验证指令、连续输入、取消后的继续输入和客户端关闭，`cli.py` 的 `--config` 用法保持兼容。
- [x] 9.2 让终端仅根据统一事件展示文本、实际思考、已接收/开始/结果、模式/请求次数/阶段、用量和停止原因，关联并发调用 ID，保留摘要长度、凭据脱敏和控制字符转义。通过 `tests/test_app.py` 验证实时 flush、无原始参数碎片/完整写入正文泄漏、未知用量不显示为零、非正常停止不冒充完成，模型阶段之间不提前出现输入提示符。
- [x] 9.3 更新 `README.md`、`.env.openai.example`、`.env.anthropic.example`、`.env.deepseek.example` 和工具描述，说明 `max_iterations`、多步执行、并发边界、Plan Mode、取消及 Token 统计边界；删除过时的每轮单工具描述，保留拒绝覆盖、唯一匹配、POSIX、搜索依赖和 shell 原有边界。验证示例能按现有配置加载、说明与批准规范一致，文档不包含实际凭据。

## 10. 整体回归与 tmux 端到端验收

- [x] 10.1 运行 `.venv/bin/python -m pytest -q` 完整回归，检查原六工具及两种协议的兼容性和全部新行为；重点复核“结束后用量块”“worker ready 前取消”“背压期间取消”“修订失败后旧计划”“预算与其他停止原因同时触发”五类边界均由对应组件测试证明，不用纯耗时推断并发或仅异常名称断言进程已停止。只有完整回归无失败时进入真实验收。
- [x] 10.2 使用本地 OpenAI 兼容配置在 tmux 启动 MewCode，从临时工作目录提出需要搜索/读取、修改和命令验证的真实任务，观察它自主完成多个阶段；检查实际文件内容、检查命令结果、流式事件、请求次数及下一轮上下文，并在 `checklist.md` 记录真实配置类别、通过/失败及证据位置，不打印密钥。
- [x] 10.3 使用本地 DeepSeek Anthropic 兼容配置在 tmux 验证带思考的连续工具与结果续接、`/plan` 和普通修订始终无写入、`/do` 直接执行最新计划，以及命令执行中 Ctrl+C 后无受控子进程泄漏且能继续提问；检查实际文件和进程状态，分别记录协议结果。官方 Claude 有可用配置时单独验收，否则明确记录未执行及原因，不将 DeepSeek 结果视为 Claude 通过。
- [x] 10.4 对照 `checklist.md` 的新增 Agent Loop 验收项逐项整理自动化和真实 tmux 结果，区分通过、失败、未执行；运行 `openspec validate add-agent-loop --strict --json` 并检查变更与规范一致、全部实际完成任务才勾选。交付测试输出、真实操作证据和剩余限制，归档和主规范同步留待后续明确指令。
