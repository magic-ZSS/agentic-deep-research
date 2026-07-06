# 架构

## 1) 架构风格

- 主要风格: LangGraph state-machine + supervisor/researcher 子图 + 工具适配层。
- 分类依据: 主图、supervisor 子图和 researcher 子图都用 `StateGraph` 构造；节点返回 `Command(goto=..., update=...)`；搜索/MCP/模型通过配置和工具列表注入。
- 主要约束:
  - 研究过程依赖支持 structured output 和 tool calling 的模型。
  - 搜索和 MCP 可能调用外部服务，API key 来自环境变量或 `RunnableConfig`。
  - 并行 researcher 数由 `max_concurrent_research_units` 限制，README 和配置都提示更高并发可能触发 rate limit。

## 2) 系统流程

```text
messages
  -> clarify_with_user
  -> write_research_brief
  -> research_supervisor subgraph
  -> parallel researcher subgraphs
  -> compress_research
  -> final_report_generation
  -> final_report/messages
```

1. `langgraph.json` 选择 `src/open_deep_research/deep_researcher.py:deep_researcher` 作为主图入口。
2. `clarify_with_user` 根据 `Configuration.allow_clarification` 决定直接继续或向用户提出澄清问题。
3. `write_research_brief` 用 structured output 生成 `ResearchQuestion.research_brief`，并初始化 supervisor messages。
4. `supervisor` 绑定 `ConductResearch`、`ResearchComplete` 和 `think_tool`，决定是否继续委派研究。
5. `supervisor_tools` 将多个 `ConductResearch` tool call 截断到 `max_concurrent_research_units`，用 `asyncio.gather` 并行调用 `researcher_subgraph`。
6. `researcher` 通过 `get_all_tools` 获取 `ResearchComplete`、`think_tool`、搜索工具和 MCP 工具；`researcher_tools` 并行执行 tool calls；`compress_research` 将结果压缩成 `compressed_research`。
7. `final_report_generation` 汇总 `notes`、`research_brief` 和原始 messages，生成 `final_report`，并在 token-limit 异常时按模型 token map 截断 findings 后重试。

## 3) 层与模块职责

| 层或模块 | 负责 | 不应负责 | 证据 |
|-----------------|------|--------------|----------|
| Main graph | 澄清、brief、supervisor 子图、最终报告生成的顺序编排。 | 搜索 API 具体实现。 | `src/open_deep_research/deep_researcher.py` |
| Supervisor subgraph | 研究任务拆分、并发 researcher 派发、研究结束判断。 | 单个网页抓取和摘要。 | `src/open_deep_research/deep_researcher.py` |
| Researcher subgraph | 工具调用循环、搜索/MCP 使用、研究压缩。 | 最终全局报告写作。 | `src/open_deep_research/deep_researcher.py` |
| Configuration | 默认模型、搜索 API、重试次数、并发数、MCP 配置和 OAP UI metadata。 | 业务执行逻辑。 | `src/open_deep_research/configuration.py` |
| State | graph state、structured output schema、override reducer。 | Prompt 文案。 | `src/open_deep_research/state.py` |
| Tool/integration utils | Tavily、native web search、MCP、token store、API key、token-limit helper。 | 顶层 graph 节点定义。 | `src/open_deep_research/utils.py` |
| Auth | LangGraph thread/assistant/store 的用户隔离。 | 搜索和报告生成。 | `src/security/auth.py` |
| Evaluation | Deep Research Bench、LangSmith evaluators、结果导出。 | 生产 graph 默认路径。 | `tests/run_evaluate.py`, `tests/evaluators.py` |

## 4) 复用模式

| 模式 | 出现位置 | 存在原因 |
|---------|-------------|---------------|
| `StateGraph` + compiled subgraph | `deep_researcher.py`, `src/legacy/graph.py`, `src/legacy/multi_agent.py` | 将研究流程拆成可组合的状态机。 |
| `Command(goto, update)` routing | `deep_researcher.py` | 节点动态选择下一步并更新 state。 |
| Pydantic structured output | `state.py`, `deep_researcher.py`, `tests/evaluators.py` | 约束模型输出为可验证结构。 |
| Config from env + `RunnableConfig` | `configuration.py` | 支持 LangGraph Studio/OAP 配置和环境变量覆盖。 |
| Tool adapter list | `utils.py` | 根据 `search_api` 和 `mcp_config` 拼装 researcher 可用工具。 |
| Async fan-out/fan-in | `deep_researcher.py`, `utils.py` | 并行 researcher 和并行搜索/摘要，提高吞吐。 |
| Retry/truncation on structured output/token limit | `deep_researcher.py`, `utils.py` | 处理模型 structured output 和上下文长度失败。 |

## 5) 已知架构风险

- `supervisor_tools` 的异常分支使用 `if is_token_limit_exceeded(...) or True`，所有异常都会结束 research phase；这会掩盖非 token-limit 失败。
- 当前主实现的 MCP 配置只构造 `"server_1"`，代码 TODO 明确等待 OAP multi-MCP server 支持；作者已确认多 MCP server 支持是明确后续 feature。
- `load_mcp_tools` 在 MCP 连接失败时返回空列表；如果同时没有搜索工具，`researcher` 才会抛出 "No tools found"。
- `MODEL_TOKEN_LIMITS` 注释说明 token limit map 可能过时或不适用于用户模型，需要随模型更新维护。
- `langgraph.json` 配置了 auth path；本地 `langgraph dev` 时若启用该 auth，需要 `SUPABASE_URL` 和 `SUPABASE_KEY`。

## 6) 证据

- `langgraph.json`
- `src/open_deep_research/deep_researcher.py`
- `src/open_deep_research/configuration.py`
- `src/open_deep_research/state.py`
- `src/open_deep_research/utils.py`
- `src/security/auth.py`
- `tests/run_evaluate.py`
