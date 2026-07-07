# 会话交接

## 当前目标（Current Objective）

- 目标：实现 researcher 工具并发与 Tavily 搜索内部 fan-out 控制。
- 当前状态：completed。
- 分支 / 提交：当前工作树，未创建提交。

## 本次已完成

- 新增配置：
  - `max_concurrent_researcher_tool_calls`：限制单个 researcher 每轮并行工具调用数量。
  - `max_queries_per_search_call`：限制一次 `tavily_search` 的 query 数，并复用为网页摘要并发上限。
- 更新 `researcher_tools`，只执行 allowed tool calls；overflow tool calls 返回对应错误型 `ToolMessage`，保证每个原始 tool call 都有结果。
- 更新 `tavily_search`，截断超量 `queries`、提示 skipped queries，并用 `asyncio.Semaphore` 限制 raw content 摘要并发。
- 更新 `research_system_prompt`，把工具并发和搜索 query 上限暴露给 researcher。
- 新增轻量测试 `tests/test_research_limits.py`，覆盖 tool overflow、query 截断和摘要并发限制。
- 局部更新 `docs/codebase/ARCHITECTURE.md` 与 `docs/codebase/CONCERNS.md`。
- 更新 `feature_list.json` 和 `progress.md`。

## 验证证据

| 检查 | 命令 | 结果 | 备注 |
|---|---|---|---|
| 初始化脚本 | `bash ./init.sh` | 不可靠 | sandbox 内 `E_ACCESSDENIED`；升级后返回 0 但包含 CRLF/乱码、`set: -\r invalid option`、`python: command not found` |
| 编译 | `conda run --no-capture-output -n open-deep-research python -m compileall -q src tests/test_research_limits.py` | 通过 | 未调用外部服务 |
| 针对性测试 | `conda run --no-capture-output -n open-deep-research python -m pytest -q tests/test_research_limits.py` | 通过 | `2 passed`；仅有既有 deprecation warnings 和 `.pytest_cache` 权限 warning |

## 修改文件（Files Changed）

- `src/open_deep_research/configuration.py`
- `src/open_deep_research/deep_researcher.py`
- `src/open_deep_research/utils.py`
- `src/open_deep_research/prompts.py`
- `tests/test_research_limits.py`
- `docs/codebase/ARCHITECTURE.md`
- `docs/codebase/CONCERNS.md`
- `feature_list.json`
- `progress.md`
- `session-handoff.md`

## 阻塞项与风险（Blockers / Risks）

- `init.sh` 在当前 Windows/WSL/conda 链路仍不能作为可靠验证入口。
- 两个新增默认值均为 `3`，后续需要人工按真实 API rate limit、模型 RPM/TPM、Tavily key 类型和部署并发量调优。
- 未运行 `tests/run_evaluate.py`、真实 Tavily/OpenAI/Anthropic/MCP 调用或任何可能产生成本的 E2E。

## 下次会话启动（Next Session）

1. 阅读 `AGENTS.md`、`feature_list.json`、`progress.md`、`session-handoff.md`。
2. 执行 `git status --short`，保留已有改动。
3. 若继续做 fan-out 能力，优先判断是否需要拆分摘要并发配置、增加 provider-aware throttling/backoff、成本预算或 cache。
