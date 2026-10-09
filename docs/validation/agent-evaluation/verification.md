# 本次验证命令

最终工作区：当前项目；隔离实施源：/private/tmp/mewcode-agent-eval-20261008。实际源码指纹保存在各运行 manifest.source；最终源码与主工作区内容一致。虚拟环境仅重新安装本地项目入口，没有更新运行依赖。

```sh
UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv sync --offline
UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv run --no-sync pytest -q
UV_CACHE_DIR=/private/tmp/mewcode-uv-cache uv build --offline --out-dir /private/tmp/mewcode-agent-eval-20261008/.implementation/dist-final
openspec validate add-agent-evaluation --strict
git diff --check
uv run mewcode-eval --help
uv run mewcode-eval run --help
uv run mewcode-eval compare --help
```

定向测评器测试为六个 tests/test_eval_*.py 模块，44 项通过；完整回归含它们共 2053 项通过。全量在获准本机监听端口环境中执行。检查最终 wheel 的 entry_points.txt 和新增模块、sdist 的 evals/core-v1/suite.yaml 均通过。

真实运行均在本轮创建的 tmux 中执行，配置文件路径显式指定，配置内容不保存。普通终端执行 uv run mewcode --config 已授权的 Anthropic 兼容配置，在临时目录读取 README.md，处理本次授权，观察真实工具与流式结果后退出。终端的 120 秒包含等待本次批准时间，不能当作模型延迟。

```sh
uv run mewcode-eval run --config /absolute/path/.env.claude --suite evals/core-v1 --output /private/tmp/mewcode-eval-20261008/final-baseline --label final-baseline
uv run mewcode-eval run --config /absolute/path/.env.claude --suite evals/core-v1 --output /private/tmp/mewcode-eval-20261008/candidate --label candidate
uv run mewcode-eval run --config /absolute/path/.env --suite evals/core-v1 --output /private/tmp/mewcode-eval-20261008/openai-final --case repair-verify --case plan-revise --case failing-check
uv run mewcode-eval compare --baseline docs/validation/agent-evaluation/runs/final-baseline --candidate docs/validation/agent-evaluation/runs/candidate --kind code --output /private/tmp/mewcode-eval-20261008/reviewed-comparison
```

取消夹具的真实命令先写 started，等待 90 秒再写 late；监测实际 started 后立即给 tmux 发送 Ctrl-C。当前 cancelled、剩余 not_run、退出 130，保存的最终文件无 late。早期取消发生在命令完成后，另存且不当作回收证明。

离线读取复制后的每份完整 manifest/result 和证据引用哈希均通过；人工记录校验通过，未评不会计人工通过。真实密钥扫描覆盖全部验收证据普通文件，两个配置的密钥均零命中。无配置全文、请求头或思考正文；测试中的秘密哨兵仅是虚构字符串。
