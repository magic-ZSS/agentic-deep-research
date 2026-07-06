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
| MCP server | Tool/API | 将外部工具加入 researcher 工具集，支持 streamable HTTP。 | 可无 auth；或通过 Supabase token exchange 获取 MCP access token。 | high | `src/open_deep_research/configuration.py`, `src/open_deep_research/utils.py` |
| Supabase | Auth service | 验证 LangGraph request 的 Bearer token，并提供用户 identity。 | `SUPABASE_URL`, `SUPABASE_KEY`。 | high for hosted auth | `src/security/auth.py`, `langgraph.json` |
| LangSmith | Evaluation/observability | Deep Research Bench 评估、experiment tracking、结果导出。 | SDK env vars 或 `LANGSMITH_API_KEY`。 | medium | `tests/run_evaluate.py`, `tests/evaluators.py`, `tests/extract_langsmith_data.py` |
| Legacy search providers | Search APIs | Perplexity、Exa、ArXiv、PubMed、Linkup、DuckDuckGo、Google Search、Azure AI Search。 | 各 provider env vars 或 API wrappers。 | low for current main graph, medium for legacy | `src/legacy/utils.py`, `src/legacy/legacy.md` |
| GitHub Actions / Claude Code | CI/automation | issue/PR/comment 触发 Claude Code 或 review。 | GitHub secrets `ANTHROPIC_API_KEY`。 | medium | `.github/workflows/claude.yml`, `.github/workflows/claude-code-review.yml` |

## 2) 数据存储

| 存储 | 角色 | 访问层 | 主要风险 | 证据 |
|-------|------|--------------|----------|----------|
| LangGraph store | 保存 MCP token，namespace 为 `(user_id, "tokens")`。 | `utils.py` 中的 `get_store()` | token 生命周期依赖 `expires_in` 和 store metadata；无集中审计说明。[TODO] | `src/open_deep_research/utils.py` |
| LangGraph `MemorySaver` | 评估时的内存 checkpointer。 | `tests/run_evaluate.py`, `tests/supervisor_parallel_evaluation.py` | 仅内存态，不适合作为持久生产存储。 | `tests/run_evaluate.py` |
| Supabase Auth | 用户 token 校验。 | `src/security/auth.py` | `SUPABASE_URL`/`SUPABASE_KEY` 缺失会让 auth 初始化失败。 | `src/security/auth.py` |
| `tests/expt_results/*.jsonl` | Deep Research Bench 提交格式结果。 | `tests/extract_langsmith_data.py` | 生成报告可能包含外部内容和成本敏感实验结果；AGENTS 禁止提交敏感报告。 | `tests/extract_langsmith_data.py`, `AGENTS.md` |
| 数据库/队列/缓存 | 未发现生产数据库、queue 或 cache 客户端。 | [TODO] | 如果部署依赖外部 LangGraph 平台存储，需要从部署配置补证据。 | `pyproject.toml`, `src/open_deep_research/` |

## 3) Secret 与凭证处理

- Credential sources: `.env`、环境变量、`RunnableConfig["configurable"]["apiKeys"]`、`x-supabase-access-token`。
- `.env` 被 `.gitignore` 忽略，`langgraph.json` 指向 `./.env`。
- `GET_API_KEYS_FROM_CONFIG=true` 时，OpenAI/Anthropic/Google/Tavily key 从 config 的 `apiKeys` 读取；否则从环境变量读取。
- README 和 AGENTS 提到 `.env.example`，本次文件搜索未发现该文件；需要在 `CONCERNS.md` 中确认文档还是文件应调整。
- 轮换与生命周期: MCP token 根据 `expires_in` 清理；其他 provider key rotation 未在代码中定义。[TODO]

## 4) 可靠性与失败行为

- Retry/backoff behavior:
  - structured output 调用使用 `.with_retry(stop_after_attempt=max_structured_output_retries)`。
  - Tavily 多 query 和网页摘要用 `asyncio.gather` 并行执行。
  - `summarize_webpage` 使用 60 秒 timeout，超时或异常时返回原始网页内容。
  - final report 生成遇到 token-limit 时按 `MODEL_TOKEN_LIMITS` 截断 findings 并重试。
- Timeout 策略: 只在 `summarize_webpage` 明确看到 60 秒 timeout；其他模型调用和 Tavily 搜索 timeout 未在当前代码中显式配置。[TODO]
- Circuit-breaker/fallback:
  - MCP 连接失败返回空工具列表。
  - token-limit fallback 依赖 model token map。
  - `supervisor_tools` 当前异常分支会因 `or True` 捕获所有异常并结束 research phase。

## 5) 集成可观测性

- Logging around external calls: MCP token exchange 和网页摘要失败会写 `logging.error`/`logging.warning`。
- Metrics/tracing coverage: 评估脚本使用 LangSmith；模型配置中多处使用 `tags=["langsmith:nostream"]`。
- Missing visibility gaps:
  - 未发现统一 request id、结构化日志、metrics 或 tracing 配置。[TODO]
  - MCP 连接失败直接返回空列表，缺少日志。
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
