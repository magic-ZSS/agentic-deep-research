# 外部集成

## 1) 集成清单

| 系统 | 类型 | 用途 | 鉴权方式 | 重要性 | 证据 |
|--------|------|---------|------------|-------------|----------|
| LangGraph | Runtime/API | 图编排、本地 Studio、部署入口、auth hooks。 | `langgraph.json` + LangGraph SDK auth。 | high | `langgraph.json`, `src/open_deep_research/deep_researcher.py`, `src/security/auth.py` |
| LangChain `init_chat_model` | Model abstraction | 统一初始化 OpenAI/Anthropic/Google/Groq/DeepSeek/AWS 等模型。 | Provider env vars 或 SDK 默认凭证。 | high | `pyproject.toml`, `src/open_deep_research/deep_researcher.py`, `src/open_deep_research/utils.py` |
| OpenAI | LLM/API + native web search | 默认研究、压缩、最终报告模型；可选 native web search。 | `OPENAI_API_KEY` 或 config `apiKeys`。 | high | `src/open_deep_research/configuration.py`, `src/open_deep_research/utils.py` |
| Anthropic | LLM/API + native web search | 可选研究模型和 native web search。 | `ANTHROPIC_API_KEY` 或 config `apiKeys`。 | high | `src/open_deep_research/configuration.py`, `src/open_deep_research/utils.py` |
| Google/Gemini | LLM/API | 可选模型提供商。 | `GOOGLE_API_KEY` 或 config `apiKeys`。 | medium | `pyproject.toml`, `src/open_deep_research/utils.py` |
| Tavily | Search API | 默认搜索工具，返回 raw content 后进行摘要。 | `TAVILY_API_KEY` 或 config `apiKeys`。 | high | `src/open_deep_research/configuration.py`, `src/open_deep_research/utils.py` |
| MCP server | Tool/API | 命名多 server、stdio/streamable HTTP、受限 Filesystem MCP 与 Knowledge MCP。 | 可无 auth；HTTP 可通过 Supabase token exchange；本地能力由 trusted runtime/Allowed Roots 授权。 | high | `src/open_deep_research/mcp/`, `src/open_deep_research/mcp_servers/`, `src/open_deep_research/configuration.py`, `src/open_deep_research/utils.py` |
| Supabase | Auth service | 验证 LangGraph request 的 Bearer token，并提供用户 identity。 | `SUPABASE_URL`, `SUPABASE_KEY`。 | high for hosted auth | `src/security/auth.py`, `langgraph.json` |
| LangSmith | Evaluation/observability | Deep Research Bench 评估、experiment tracking、结果导出。 | SDK env vars 或 `LANGSMITH_API_KEY`。 | medium | `tests/run_evaluate.py`, `tests/evaluators.py`, `tests/extract_langsmith_data.py` |
| DeepEval | Optional evaluation adapter | 可选转换 `BaselineRunRecord` 为 `LLMTestCase`；Phase 0 的确定性 metric 不依赖 DeepEval，也不默认上传。 | 无默认平台鉴权；安全懒导入会隐藏 Confident key 并禁用 dotenv/telemetry/tracing。 | low in Phase 0 | `pyproject.toml`, `src/open_deep_research/evaluation/deepeval_adapter.py` |
| Legacy search providers | Search APIs | Perplexity、Exa、ArXiv、PubMed、Linkup、DuckDuckGo、Google Search、Azure AI Search。 | 各 provider env vars 或 API wrappers。 | low for current main graph, medium for legacy | `src/legacy/utils.py`, `src/legacy/legacy.md` |
| GitHub Actions / Claude Code | CI/automation | issue/PR/comment 触发 Claude Code 或 review。 | GitHub secrets `ANTHROPIC_API_KEY`。 | medium | `.github/workflows/claude.yml`, `.github/workflows/claude-code-review.yml` |

## 2) 数据存储

| 存储 | 角色 | 访问层 | 主要风险 | 证据 |
|-------|------|--------------|----------|----------|
| LangGraph store | 保存 MCP token，namespace 为 `(user_id, "tokens")`。 | `utils.py` 中的 `get_store()` | token 生命周期依赖 `expires_in` 和 store metadata；无集中审计说明。[TODO] | `src/open_deep_research/utils.py` |
| LangGraph `MemorySaver` | 评估时的内存 checkpointer。 | `tests/run_evaluate.py`, `tests/supervisor_parallel_evaluation.py` | 仅内存态，不适合作为持久生产存储。 | `tests/run_evaluate.py` |
| Supabase Auth | 用户 token 校验。 | `src/security/auth.py` | `SUPABASE_URL`/`SUPABASE_KEY` 缺失会让 auth 初始化失败。 | `src/security/auth.py` |
| `tests/expt_results/*.jsonl` | Deep Research Bench 提交格式结果。 | `tests/extract_langsmith_data.py` | 生成报告可能包含外部内容和成本敏感实验结果；AGENTS 禁止提交敏感报告。 | `tests/extract_langsmith_data.py`, `AGENTS.md` |
| `tests/baseline/*.json*`, `artifacts/baseline/*.jsonl` | 已提交的去敏 case/fixture/manifest 与本地忽略的 run record。 | `evaluation/storage.py`, `scripts/run_baseline.py` | JSONL writer 当前约定单进程写；live 输出可能敏感，artifact 默认不提交。 | `tests/baseline/`, `.gitignore`, `src/open_deep_research/evaluation/storage.py` |
| 数据库/队列/缓存 | 未发现生产数据库、queue 或 cache 客户端。 | [TODO] | 如果部署依赖外部 LangGraph 平台存储，需要从部署配置补证据。 | `pyproject.toml`, `src/open_deep_research/` |

## 3) Secret 与凭证处理

- Credential sources: `.env`、环境变量、`RunnableConfig["configurable"]["apiKeys"]`、`x-supabase-access-token`。
- `.env` 被 `.gitignore` 忽略，`langgraph.json` 指向 `./.env`。
- `GET_API_KEYS_FROM_CONFIG=true` 时，OpenAI/Anthropic/Google/Tavily key 从 config 的 `apiKeys` 读取；否则从环境变量读取。
- 作者已确认 `.env` 是正式本地环境配置文件；不再要求维护或引用单独的环境模板文件。
- 轮换与生命周期: MCP token 根据 `expires_in` 清理；其他 provider key rotation 未在代码中定义。[TODO]

## 4) 可靠性与失败行为

- Retry/backoff behavior:
  - structured output 调用使用 `.with_retry(stop_after_attempt=max_structured_output_retries)`。
  - Tavily 多 query 和网页摘要用 `asyncio.gather` 并行执行。
  - `summarize_webpage` 使用 60 秒 timeout，超时或异常时返回原始网页内容。
  - final report 生成遇到 token-limit 时按 `MODEL_TOKEN_LIMITS` 截断 findings 并重试。
- Timeout 策略: 只在 `summarize_webpage` 明确看到 60 秒 timeout；其他模型调用和 Tavily 搜索 timeout 未在当前代码中显式配置。[TODO]
- Circuit-breaker/fallback:
  - MCP 按 server 独立加载；失败形成显式 diagnostic，不移除其他健康 server 工具。
  - token-limit fallback 依赖 model token map。
  - `supervisor_tools` 当前异常分支会因 `or True` 捕获所有异常并结束 research phase。

## 5) 集成可观测性

- Logging around external calls: MCP token exchange 和网页摘要失败会写 `logging.error`/`logging.warning`。
- Metrics/tracing coverage: 评估脚本使用 LangSmith；Phase 0 另有默认关闭的本地 callback，保存 token 覆盖、耗时、工具执行和失败状态，不修改图 state。
- Missing visibility gaps:
  - 未发现统一 request id、结构化日志、metrics 或 tracing 配置。[TODO]
  - MCP 连接失败通过 warning/diagnostic 暴露；仍需部署侧统一结构化日志汇聚。[TODO]
  - Tavily 搜索失败路径未见局部重试/日志包装。

## 6) 证据

- `langgraph.json`
- `pyproject.toml`
- `src/open_deep_research/configuration.py`
- `src/open_deep_research/deep_researcher.py`
- `src/open_deep_research/utils.py`
- `src/security/auth.py`
- `tests/run_evaluate.py`
- `tests/evaluators.py`
- `tests/extract_langsmith_data.py`
- `src/legacy/utils.py`
- `.github/workflows/claude.yml`

## 7) 阶段 4 MCP 安全边界

- `MCPConfig` 保留旧单 HTTP 形态并支持命名 `mcp_servers`；两个新能力开关默认关闭。Agentic RAG 的提前返回路径不注册未分类 MCP，避免 Web 旁路。
- Filesystem 能力只接收 `root_id + relative_locator`；空/无效 roots、绝对路径、drive/UNC/WSL、traversal、symlink/junction 和 root identity replacement fail closed。模型只看到 `root://` locator。
- 只读上游进程只注册 read/list/search/info；staging 为项目自有 `O_EXCL` 等效创建，没有 overwrite/edit/move/delete。
- Knowledge MCP 复用 scope-aware Repository/Retriever；scope 来自可信 runtime，所有写工具只创建 pending proposal/audit。
- Windows 示例固定 `@modelcontextprotocol/server-filesystem@2026.1.14`；许可证证据与参考 commit 见 `doc/reference/refs.lock.json`，部署/ACL 说明见 `docs/mcp_windows.md`。

证据：`src/open_deep_research/mcp/`、`src/open_deep_research/mcp_servers/`、`tests/security/mcp/`、`tests/integration/mcp/`、`config/examples/mcp.windows.example.json`。

## 8) 阶段 5 LangGraph / LangMem 集成

- 固定 `langgraph-checkpoint-sqlite==3.1.0`；只通过 managed `from_conn_string` context 使用异步 Saver/Store，SQLite Store 不启用 vector index。
- 固定可选 `langmem==0.0.30`；不暴露 manage/store side effect，adapter 仅把 extraction 结果转为 `MemoryWriteProposal`。
- Checkpoint serializer 禁止 pickle fallback，并使用 msgpack 安全 allowlist。
- Knowledge MCP 在真实 `MemoryRecall` 与可信 `RuntimeIdentity` 都存在时才注册只读 `memory_search`。

证据：`pyproject.toml`、`src/open_deep_research/runtime/persistence.py`、`src/open_deep_research/memory/langmem_adapter.py`、`src/open_deep_research/mcp_servers/knowledge_server.py`。
