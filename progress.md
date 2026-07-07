# 会话进度记录

## 当前状态（Current State）

**最后更新（Last Updated）：** 2026-07-07
**当前功能：** `researcher-tool-fanout-001`
**状态：** completed

## 已完成（What's Done）

- 在 `Configuration` 中新增 `max_concurrent_researcher_tool_calls` 和 `max_queries_per_search_call`，默认值均为 `3`，约束为 `1..10`，并补充 OAP UI slider metadata。
- 在 `researcher_tools` 中按 `max_concurrent_researcher_tool_calls` 截断单轮 tool calls，只并行执行 allowed 部分；overflow 部分返回对应 `ToolMessage`，保持每个原始 tool call 都有结果。
- 在 `tavily_search` 中按 `max_queries_per_search_call` 截断 `queries`，并在输出中提示 skipped queries。
- 在 `tavily_search` 的 raw content 摘要阶段复用 `max_queries_per_search_call` 创建 `asyncio.Semaphore`，限制摘要并发但不丢弃已返回的 unique URL。
- 更新 `research_system_prompt`，明确单轮工具并发上限、单次 `tavily_search` query 上限，并保留 `think_tool` 不与其他工具并行的现有约束。
- 新增 `tests/test_research_limits.py`，用 fake tool / monkeypatch 验证 researcher tool overflow、Tavily query 截断和摘要 semaphore。
- 局部更新 `docs/codebase/ARCHITECTURE.md` 与 `docs/codebase/CONCERNS.md`，记录新的 fan-out 控制结论与后续调优风险。

## 设计决定（Decisions）

- `max_queries_per_search_call` 同时作为 Tavily 单次 query 上限和摘要并发上限；后续如需更细粒度控制，再单独增加摘要并发配置。
- overflow tool call 返回错误型文本 `ToolMessage`，不静默丢弃，以避免破坏 tool-calling 消息协议。
- 不新增依赖，不修改 `src/legacy/`，不运行真实 Tavily/OpenAI/Anthropic/MCP 调用或 Deep Research Bench 评估。
- 新增默认值 `3` 仅作为保守起点，仍需按实际 API rate limit、模型 RPM/TPM、Tavily key 类型和部署并发量调优。

## 本次修改文件（Files Modified This Session）

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

## 验证证据（Verification Evidence）

- `bash ./init.sh`：sandbox 内失败，显示 WSL/Bash `E_ACCESSDENIED`；按策略升级后返回 0，但输出包含 CRLF/乱码、`set: -\r invalid option` 和 `python: command not found`，因此判定为当前 Windows/WSL 链路不可靠，不能作为通过证据。
- `conda run --no-capture-output -n open-deep-research python -m compileall -q src tests/test_research_limits.py`：通过。
- `conda run --no-capture-output -n open-deep-research python -m pytest -q tests/test_research_limits.py`：通过，`2 passed`；仅有既有 Pydantic/LangGraph deprecation warnings 和 `.pytest_cache` WinError 5 warning。

## 阻塞项与风险（Blockers / Risks）

- `init.sh` 仍存在 Windows/WSL/conda 链路输出不可靠问题，本次没有修复脚本。
- `max_concurrent_researcher_tool_calls=3` 和 `max_queries_per_search_call=3` 需要人工结合真实 API rate limit、模型 RPM/TPM、Tavily key 类型和部署并发量继续调优。
- Pydantic `Field(..., metadata=...)` 与 LangGraph `config_schema/input/output` deprecation warnings 是现有风格问题，本次未重构。

## 下次会话说明

1. 先阅读 `AGENTS.md`、`feature_list.json`、`progress.md`、`session-handoff.md` 和相关 `docs/codebase/` 文档。
2. 若继续优化 fan-out 控制，优先评估是否需要拆分 `max_concurrent_search_summarization_tasks`、provider-aware throttling/backoff、成本预算或 cache。
3. 不要主动运行 `tests/run_evaluate.py` 或真实外部搜索/模型调用，除非用户明确确认成本与外部服务调用。
