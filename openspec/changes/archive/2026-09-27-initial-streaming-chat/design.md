## Context

仓库目前只有项目约定与 OpenSpec 文件，没有 Python 应用、测试或 `checklist.md`。本设计以 [proposal.md](proposal.md) 和三个新增能力规格为依据。首版运行于单个终端进程，用户通过 `--config` 选择一份 `.env` 配置；两种模型协议的流都由各自的 Python SDK 通过 SSE 消费。

## Goals / Non-Goals

**Goals:**

- 让终端交互、会话状态和供应商协议各有清晰边界，新增后端时无需改动对话循环。
- 保证已显示的流式片段不因终端缓冲而延迟，同时只把完整回答提交为下一轮上下文。
- 把配置错误、API 错误和中断转成不暴露密钥的用户可读反馈。

**Non-Goals:**

- 全屏界面、持久化会话、上下文压缩、运行中切换配置或自动选择模型。
- 工具调用、多模态消息、文件访问及代码编辑。

## Decisions

### 1. 采用提示符式同步对话循环

建立 `mewcode` 命令入口，先解析 `--config` 并校验，再进入逐轮读取输入的循环。标准输入负责行编辑，标准输出负责分段写入并即时刷新；`/exit` 与输入处的 EOF 结束会话。空输入重新显示提示符。选择这种形式是因为用户已确定首版采用提示符式交互；全屏 UI 框架会增加状态与重绘复杂度，但不改善本阶段的核心流式能力。

### 2. 使用最小的会话模型与统一流式事件

会话只保存按顺序排列的 `{role, content}` 纯文本消息。每轮先构造“已提交历史 + 当前问题”的候选请求，消费 Provider 的 `thinking_delta`、`text_delta` 和 `completed` 事件；界面即时输出前两者，并只累积 `text_delta` 作为助手最终回答。收到完整结束事件且回答非空后，才把用户问题及最终回答一起加入历史。SDK 异常、流错误、Ctrl+C 或缺少正常结束事件均丢弃本轮候选历史，已显示的片段会标注为未完成。

Provider 抽象接收配置与纯文本历史，输出上述事件或抛出分类错误。对话循环不读取两家 SDK 的原生事件结构。Claude 思考摘要和 DeepSeek 思考片段只供显示，不加入后续请求；在没有工具调用的纯对话场景，这分别符合 [Claude 关于多轮思考块的说明](https://platform.claude.com/docs/en/build-with-claude/thinking)与 [DeepSeek 思考模式说明](https://api-docs.deepseek.com/guides/thinking_mode/)。相比直接把 SDK 调用写进终端循环，这能独立测试协议转换并为新增后端保留接入点。

### 3. 每个配置文件只加载一组值

使用 `.env` 解析库读取指定文件，不把配置写入进程全局环境。五个必填键为 `name`、`protocol`、`model`、`base_url`、`api_key`；`thinking` 只接受 `true`/`false`，缺省为 `false`。`base_url` 定义为 SDK 要使用的 API 基础地址，而非完整请求端点；OpenAI 兼容服务的配置示例需包含服务要求的路径前缀，例如 `/v1`。启动时校验文件、字段、协议、布尔值和地址形式，显示 `name`，不显示密钥。`protocol=openai` 与 `thinking=true` 的组合直接报配置错误。

相比在一个 `.env` 中编码多组带前缀的变量，一文件一配置与 `--config` 完全对应，字段语义固定。仓库提供不含真实凭据的示例配置和忽略规则，避免误提交密钥。

### 4. 两个 SDK 适配器消费 SSE 并转换事件

Anthropic 适配器使用 Messages 的流式接口，区分 `thinking_delta` 和 `text_delta`，在服务端正常结束后发送 `completed`。OpenAI 适配器使用 Chat Completions 的流式接口，读取 `delta.content`；通过 SDK 的 `base_url` 接入遵循该协议的第三方服务。两者忽略与纯文本对话无关的非错误事件，并将协议级错误转为不含原始凭据的分类错误。选择官方 SDK 是为了利用其 SSE 解析与连接管理，同时保持业务层的事件契约一致；自行解析 SSE 会增加协议维护面。[Claude Streaming 文档](https://platform.claude.com/docs/en/build-with-claude/streaming)、[OpenAI Chat Completions 参考](https://developers.openai.com/api/reference/resources/chat)

### 5. `thinking` 控制 Anthropic 协议的思考请求与展示

Claude 模型配置下，`thinking=true` 时 Anthropic 适配器按已知模型能力选择 adaptive 或旧式手动 extended thinking，并请求 `display: summarized`；旧式模式使用内部固定的合法 `max_tokens` 与思考预算，不增加第七个配置字段。未知或不兼容模型返回明确错误，不静默降级成普通回答。`thinking=false` 时不主动请求可读思考，界面也不显示思考事件；它不承诺关闭某些模型默认且不可关闭的内部思考。Claude API 返回的是可读摘要，简单请求可能不生成思考块，因此界面不能自行补写思考内容。[Claude Thinking 文档](https://platform.claude.com/docs/en/build-with-claude/thinking)

DeepSeek 官方 Anthropic 兼容地址 `https://api.deepseek.com/anthropic` 使用同一 Anthropic SDK 与 SSE 事件适配，但不按 Claude 模型名选择思考参数。该地址下 `thinking=true` 发送 `type=enabled`，并按到达顺序显示真实思考与回答片段；`thinking=false` 显式发送 `type=disabled`，因为 DeepSeek 默认启用思考。只对该精确官方地址使用此配置，避免给其他 Anthropic 兼容服务发送不受支持的参数。此验证覆盖 Anthropic 兼容协议的思考流，不等同于运行官方 Claude 模型。[DeepSeek Anthropic API 文档](https://api-docs.deepseek.com/guides/anthropic_api/)

### 6. 分层验证并准备真实终端验收

测试分别覆盖配置边界、两种 SDK 事件到统一事件的转换、Claude 与 DeepSeek 兼容思考参数、成功轮次的提交，以及失败或中断时的历史回滚。使用可控的假流验证片段在流完成前就已输出；再按项目约定在 tmux 中用真实配置启动 MewCode，检查两轮上下文、两种协议、DeepSeek Anthropic 兼容思考流与中断恢复。真实 OpenAI 兼容服务必须符合 Chat Completions 流式协议；DeepSeek 验收不作为官方 Claude 模型效果的证据。

## Risks / Trade-offs

- [不同 Claude 模型的思考参数与默认值不同] → 将模式选择留在 Anthropic 适配器，覆盖已知模型族；不兼容时给出可操作错误并在实现时核对当前官方文档。
- [第三方“OpenAI 兼容”实现可能只支持协议子集] → 请求限定为纯文本 Chat Completions 的常用字段，并以符合标准流式响应作为兼容边界。
- [长会话可能超过模型上下文窗口] → 首版不自动裁剪历史；保留已完成轮次并提示模型返回的上下文错误，后续变更再设计压缩策略。
- [流中断时用户可能已经看见部分回答] → 在终端明确标注本轮未完成，且不把部分内容写进会话历史。
- [真实端到端测试依赖可用的 API 凭据和兼容服务] → 自动化测试使用假流，tmux 验收使用用户提供的本地配置；无法连接的后端在验收结果中明确记录。

## Migration Plan

这是全新命令行应用，无现有数据或 API 需要迁移。发布时提供示例配置与启动说明；回退只需停止使用新命令，不涉及会话数据迁移。
