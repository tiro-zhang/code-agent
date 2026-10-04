# Repository Guidelines

## 项目结构与模块职责

MewCode 是使用 Python 3.11+ 实现的终端 AI 编程助手，支持 Anthropic 与 OpenAI 兼容协议。

- `src/mewcode/`：主包；`cli.py` 是命令行入口，`app.py` 管理终端交互，`agent.py` 执行代理循环，`session.py` 管理会话。
- `providers/`、`tools/`、`permissions/`：主包下的协议适配、工具执行与权限模块。新增逻辑放入对应模块，避免集中到入口文件。
- `tests/`：pytest 测试；`conftest.py` 提供异步测试辅助与供应商替身。
- `openspec/specs/` 保存主规格，`openspec/changes/` 保存变更方案；`docs/validation/` 保存验收材料，`checklist.md` 是端到端验收清单。

## 安装、运行与构建

使用 macOS 或 Linux；搜索工具依赖 `rg`，端到端测试依赖 `tmux`。在仓库根目录执行：

| 命令 | 用途 |
| --- | --- |
| `uv sync` | 安装项目和开发依赖 |
| `uv run mewcode --config .env` | 使用指定配置启动；`--config` 必填 |
| `uv run pytest -q` | 运行完整自动化测试 |
| `uv run pytest tests/test_agent_loop.py -q` | 运行指定模块测试 |
| `uv build` | 使用 setuptools 构建源码包和 wheel |

<!-- CODEGRAPH_START -->
## CodeGraph

本项目已建立 `.codegraph/` 索引。定位代码、理解模块职责、追踪调用链或分析修改影响时，主动使用 CodeGraph，无需用户重复指定。

- MCP 工具可用时，优先调用 `codegraph_explore`，在查询中包含相关符号名、文件名或具体问题；工具延迟加载时先按名称查找。
- 当前会话尚未加载 MCP 工具时，使用 `codegraph explore "<符号名或问题>"`；需要进一步定位时使用 `codegraph node`、`callers`、`callees` 或 `impact`。
- 代码发生较多变化后，在依赖分析前检查 `codegraph status`，有待同步变更时运行 `codegraph sync`；不反复全量重建索引。
- 精确文本搜索及非源码文件使用 `rg`。CodeGraph 不可用、未覆盖或结果不足时，明确说明并通过源码补充核对，不把空查询结果视为代码不存在。
<!-- CODEGRAPH_END -->

## 编码风格与命名

中文回答，中文注释和文档字符串。使用四空格缩进；模块、函数和变量采用 `snake_case`，类采用 `PascalCase`，常量采用 `UPPER_SNAKE_CASE`。沿用现有类型标注和异步接口约定。当前未配置格式化或 lint 工具，遵循邻近代码风格，避免无关格式调整。

## 测试与验收

测试文件命名为 `test_*.py`，测试函数以 `test_` 开头。按变更覆盖正常、错误、取消及权限边界；优先复用 `tmp_path` 和供应商替身。当前未设置覆盖率百分比门槛。

开发完功能后，必须用 tmux 做端到端测试：

1. 配置真实且已授权的模型服务，执行 `tmux new-session -s mewcode-e2e 'uv run mewcode --config .env'`。
2. 输入真实请求，例如“读取 README.md，概括启动步骤”，并处理工具授权提示。
3. 观察实际工具调用、流式回复及结果；涉及修改或命令执行时，在临时工作目录验证产物。
4. 对照 `checklist.md` 逐项验收，记录通过、失败或未执行及原因；不能用历史记录代替本次验证。

## 提交与 Pull Request

沿用历史中的 `feat:`、`fix:`、`docs:` 前缀及简短中文说明，例如 `fix: 修复取消后的流关闭`。每次提交聚焦一个改动。PR 应说明问题、行为变化和验证结果，关联适用的 issue 或 OpenSpec 变更；终端交互变更附脱敏的 tmux 输出。涉及用户可见行为时同步 README 和验收清单。

## 配置与敏感信息

从 `.env.*.example` 复制本地配置，勿将真实密钥写入模板、日志或提交记录。项目共享规则位于 `.mewcode/permissions.yaml`，本地永久批准位于被忽略的 `.mewcode/permissions.local.yaml`。修改权限逻辑时保留明确拒绝优先及规划模式只读约束。
