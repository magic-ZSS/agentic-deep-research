# 架构

## 1) 架构风格

- 主要风格: LangGraph state-machine + supervisor/researcher 子图 + 工具适配层。
- 分类依据: 主图、supervisor 子图和 researcher 子图都用 `StateGraph` 构造；节点返回 `Command(goto=..., update=...)`；搜索/MCP/模型通过配置和工具列表注入。
- 主要约束:
  - 研究过程依赖支持 structured output 和 tool calling 的模型。
  - 搜索和 MCP 可能调用外部服务，API key 来自环境变量或 `RunnableConfig`。
  - 并行 researcher 数由 `max_concurrent_research_units` 限制，README 和配置都提示更高并发可能触发 rate limit。
  - 单个 researcher 每轮并行工具调用由 `max_concurrent_researcher_tool_calls` 限制；Tavily 单次搜索 query fan-out 和摘要并发由 `max_queries_per_search_call` 限制。

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
6. `researcher` 通过 `get_all_tools` 获取 `ResearchComplete`、`think_tool`、搜索工具和 MCP 工具；`researcher_tools` 将 tool calls 截断到 `max_concurrent_researcher_tool_calls` 后并行执行，溢出调用返回错误型 `ToolMessage`；`compress_research` 将结果压缩成 `compressed_research`。
7. `final_report_generation` 汇总 `notes`、`research_brief` 和原始 messages，生成 `final_report`，并在 token-limit 异常时按模型 token map 截断 findings 后重试。
8. 可选 `print_process_info` trace 默认关闭；开启后由 `utils.py:process_print` 统一输出 brief、supervisor/researcher 轮次、Tavily search id、summary id、compression 和 final report 生成前事件，正文内容不进入 trace。

## 3) 层与模块职责

| 层或模块 | 负责 | 不应负责 | 证据 |
|-----------------|------|--------------|----------|
| Main graph | 澄清、brief、supervisor 子图、最终报告生成的顺序编排。 | 搜索 API 具体实现。 | `src/open_deep_research/deep_researcher.py` |
| Supervisor subgraph | 研究任务拆分、并发 researcher 派发、研究结束判断。 | 单个网页抓取和摘要。 | `src/open_deep_research/deep_researcher.py` |
| Researcher subgraph | 工具调用循环、搜索/MCP 使用、研究压缩。 | 最终全局报告写作。 | `src/open_deep_research/deep_researcher.py` |
| Configuration | 默认模型、搜索 API、重试次数、并发数、MCP 配置和 OAP UI metadata。 | 业务执行逻辑。 | `src/open_deep_research/configuration.py` |
| State | graph state、structured output schema、override reducer。 | Prompt 文案。 | `src/open_deep_research/state.py` |
| Tool/integration utils | Tavily、native web search、MCP、token store、API key、token-limit helper、可选运行流程 trace helper。 | 顶层 graph 节点定义。 | `src/open_deep_research/utils.py` |
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
| Bounded tool/search fan-out | `deep_researcher.py`, `configuration.py`, `utils.py`, `prompts.py` | 用配置限制 researcher 单轮工具并发、Tavily 单次 query 数和摘要并发，降低 rate limit 与成本风险。 |
| Optional process trace | `configuration.py`, `deep_researcher.py`, `utils.py` | 用 `print_process_info` 开关和 `RunnableConfig` 私有 context 输出短流程 trace，避免污染 graph state 或打印正文。 |
| Retry/truncation on structured output/token limit | `deep_researcher.py`, `utils.py` | 处理模型 structured output 和上下文长度失败。 |

## 5) 已知架构风险

- MCP 已支持命名多 server 和逐 server 故障隔离；Filesystem/Knowledge 能力需要 trusted runtime 服务注入，配置开关默认关闭。
- 生产 Windows ACL 是 Allowed Roots/registry 之外的第二层防线，代码无法替代部署权限配置；部署变更后必须重跑 stdio/security 验收。
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

## 7) 阶段 1：结构化知识与证据边界

- 新增的 `knowledge/`、`evidence/`、`storage/` 是独立领域/持久化层，不在当前 Supervisor—Researcher 主路径中实例化。
- 权威链为 `KnowledgeScope -> Source -> Document -> DocumentVersion -> Chunk -> Evidence`，原始 bytes 由 scope-local `ContentBlob` 保存，`Requirement` 可与 Evidence 关联。
- InMemory 与 SQLite metadata 实现同一 async Protocol；InMemory 与 Local Blob 实现同一不可变 blob Protocol。
- `state.py` 仅增加由确定性 reducer 合并的 ID 引用；`notes/raw_notes/compressed_research` 和 Writer 输入语义未改变。
- 完整 schema、回退和限制见 `docs/codebase/KNOWLEDGE_EVIDENCE.md`。

证据：`src/open_deep_research/knowledge/`、`src/open_deep_research/evidence/`、`src/open_deep_research/storage/`、`tests/integration/storage/`。

## 8) 阶段 4：MCP 能力边界

- `mcp/` 负责连接配置、tool registry、显式诊断、路径 policy、去敏审计和 staging；`mcp_servers/` 只把已有 Repository/Retriever/Lifecycle service 暴露为工具，不直接访问 SQLite/blob/index。
- Knowledge MCP 的读路径与内部 Retriever 共用同一实例和 stable ID；proposal 路径不会执行 promotion、soft delete 或 Memory write。
- 非 Agentic Researcher 可按开关获得受限 MCP tools；Agentic 路径仍只有 `governed_retrieval`，不因阶段 4 重新开放旁路。

证据：`src/open_deep_research/mcp/`、`src/open_deep_research/mcp_servers/`、`src/open_deep_research/utils.py`、`tests/integration/mcp/`。

## 9) 阶段 5：Checkpoint 与 Memory 边界

- 模块级 `deep_researcher` 继续作为默认关闭的兼容导出；`runtime.graph_factory.open_deep_research_graph` 仅在 managed async lifespan 内为 root builder 注入 checkpointer/store。
- `AsyncSqliteSaver` 与 `AsyncSqliteStore` 在同一 `AsyncExitStack` 内完成 setup/关闭；Checkpoint、Store、Knowledge、Run Evidence 与长期 Memory 使用不同 SQLite 文件。
- Working Memory 保存在 checkpoint state；长期 Episodic/Semantic/Procedural/Preference 通过 `MemoryRepository`，不得经 LangGraph raw Store 或 Agent 工具直接写入。
- Namespace 由可信 `RuntimeIdentity` 生成；长期写入必须经过 proposal 和七项 Gate，Semantic recall 会再次核验 Evidence。
- `memory_search` 是只读能力，不接受 Namespace 参数；LangMem adapter 只输出 proposal，不持有 Store。

证据：`src/open_deep_research/runtime/`、`src/open_deep_research/memory/`、`src/open_deep_research/tools/memory.py`、`tests/integration/checkpoint/`、`tests/integration/memory/`。
