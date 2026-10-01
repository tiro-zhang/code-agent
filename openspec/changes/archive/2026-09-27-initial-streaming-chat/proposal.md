## Why

MewCode 目前还没有可运行的对话入口。先建立终端中的流式多轮对话能力，才能验证模型接入、交互体验与会话状态，并为后续编程助手功能提供基础。

## What Changes

- 提供提示符式终端对话界面；用户提交问题后，随着 SSE 流式数据到达实时显示回复，并可继续提问。
- 在本次运行期间保留已完成的对话历史，让后续请求包含先前上下文；退出后不恢复会话。
- 支持 Anthropic Claude、DeepSeek 的 Anthropic 兼容地址和第三方 OpenAI 兼容接口。OpenAI 协议采用 Chat Completions 格式，两种协议通过统一的 Provider 接口向界面提供流式内容。
- 从启动参数 `--config` 指定的 `.env` 文件读取一组供应商配置：`name`、`protocol`、`model`、`base_url`、`api_key`，以及可选的 `thinking`。不同配置使用不同文件。
- 当 Anthropic 协议的 `thinking` 开启且 API 返回思考片段时，在终端中与最终回答分开实时显示；Claude 请求可读摘要，DeepSeek 兼容地址请求其思考内容。请求失败或中断时给出明确反馈，不把未完成的回答加入会话历史。
- 本阶段只支持纯文本对话，不包含工具调用、文件操作或代码编辑。

## Capabilities

### New Capabilities

- `interactive-chat`: 终端交互、流式展示和本次运行内的多轮会话。
- `provider-configuration`: 单组 `.env` 配置的选择、读取与校验。
- `llm-providers`: Claude、DeepSeek Anthropic 兼容接口与 OpenAI 兼容接口的流式对话及思考内容处理。

### Modified Capabilities

无。

## Impact

- 新增 Python 命令行入口、终端交互、会话管理、配置加载和 Provider 实现；目前没有需迁移的现有应用代码。
- 运行时需要相应模型服务的 API 凭据，并引入 Anthropic 与 OpenAI Python SDK 作为 SSE 流式接入依赖。
- 实现阶段需要增加自动化测试，并按项目约定用 tmux 和 `checklist.md` 验收真实多轮对话、两种协议与思考流。DeepSeek Anthropic 兼容地址可用于协议验收，但不等同于运行官方 Claude 模型。
