# 独立代码审查与修复

独立审查代理审阅本次上下文功能及接入，对照实施前源码快照排除工作区原有修改。报告3项Important、无Critical；未改文件。

| 发现 | 复现 | 修复与验证 |
| --- | --- | --- |
| 任意用户项目的缓存可被git add纳入 | 新建Git仓库后落盘，check-ignore返回1 | ResultCache在专用context目录创建私有`*`忽略文件；test_cache_is_git_ignored_in_arbitrary_user_project先失败后通过 |
| 100个完整路径超过2K摘要上限 | 最小六节摘要加100条路径已超过2000 token | 超过15项时原子维护可分页results-index.jsonl；摘要引用单一索引，底层文件全部保留检查；test_many_old_results_use_bounded_index_instead_of_overflowing_summary先失败后通过 |
| 缓存失效误计模型失败并重试三次 | 落盘后删文件，三份合法摘要均在事后读取时失败 | 请求前和提交前检查；I/O障碍受控停止且不计模型失败；test_missing_cache_blocks_before_llm_without_failure_count先失败后通过，另测请求期间消失 |

最终全量752项通过，构建与OpenSpec严格校验通过。没有延期处理的审查发现。另补充维护阶段标签及真实终端取消测试，并稳定两个既有终端画面断言的等待条件。
