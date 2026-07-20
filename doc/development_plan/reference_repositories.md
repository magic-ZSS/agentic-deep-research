# 参考仓库与复用边界

## 1. 固定版本

本轮按用户授权将五个仓库浅克隆到 `doc/reference/` 并定点阅读。正式实施阶段 0 必须生成机器可读 lock/manifest；在此之前以下 SHA 是规划证据，不代表依赖已安装。

| 仓库 | 本地目录 | 分支 / 提交 | 许可证 | 主要阶段 |
|---|---|---|---|---|
| Future-House/paper-qa | `doc/reference/paper-qa/` | `main` / `d7675d7b7eddeb3535e8c260399c5bbeeb818c50`（浅克隆无 tag，动态版本不可据此可靠解析） | Apache-2.0 | 1、2、3、6 |
| confident-ai/deepeval | `doc/reference/deepeval/` | `main` / `58c9ef78a4634ba119c7d2cc145f5cf9aeb24524`（v4.1.1） | Apache-2.0 | 0、7 |
| langchain-ai/langmem | `doc/reference/langmem/` | `main` / `a2d580946465137c89162e67dc0b18108bd4850c`（v0.0.30） | MIT | 5、7 |
| langchain-ai/langgraph | `doc/reference/langgraph/` | `main` / `49ae27c2ae983cfb92091b0dea9f7bc37a716479`（LangGraph 1.2.9；checkpoint 4.1.1；checkpoint-sqlite 3.1.0） | MIT | 1、3、5 |
| modelcontextprotocol/servers | `doc/reference/mcp-servers/` | `main` / `d31124c982401739917fd817c2a59db344529c16`（filesystem v0.6.3） | MIT/Apache-2.0 过渡；文档 CC-BY-4.0 | 4 |

当前项目规划基准为 `8c2b26ea1e582590d9653188a286c4fc14f6480d`。参考仓库的 `main` API 可能继续变化；实现时必须选定兼容发布版、记录 package version 与 SHA，不得只依赖“最新”。

## 2. PaperQA2

### 重点代码

- `src/paperqa/docs.py`：`Docs.aadd`、`aadd_texts`、`retrieve_texts`、`aget_evidence`、`aquery`；
- `src/paperqa/types.py`：`Doc`、`DocDetails`、`Text`、`Context`、`ParsedText/ParsedMetadata/ChunkMetadata`、`PQASession`；
- `src/paperqa/readers.py`：PDF/文本读取、页码范围命名；
- `src/paperqa/agents/tools.py`：`PaperSearch`、`GatherEvidence`、`GenerateAnswer` 和 Agent 环境状态；
- `src/paperqa/settings.py` 及索引配置代码：模型、embedding、索引缓存和并发设置；
- `tests/`：文档加入、检索、索引和 evidence 行为。

### 借鉴与适配

- 借鉴 `Doc → Text → Context` 的科学文档证据流、PDF 页码定位、MMR/候选检索、contextual summarization 和索引缓存。
- Adapter 只使用文档加入/索引/`aget_evidence` 类能力，把 `Context` 映射为本项目的 `Evidence` 与 `Chunk`。
- PaperQA2 的默认 content hash/dockey 实际基于 MD5，可用于索引缓存关联，但本项目使用自己的 SHA-256、Source/DocumentVersion 身份和版本历史作为权威模型。
- Markdown 标题层级、HTML 快照定位和历史查询记录由本项目 parser 补足，不能假设 PaperQA 的纯文本 reader 自动保存这些元数据。

### 不借鉴

- 不调用 `Docs.aquery` 生成最终回答；
- 不嵌入 PaperQA Agent、`PaperSearch/GatherEvidence/GenerateAnswer` 循环；
- 不让 PaperQA 管理 Web 搜索、Supervisor 规划、知识生命周期或最终来源编号；
- 不直接暴露 PaperQA 类型给主图，以免升级泄漏。

### 代码复用与许可证

优先通过受支持的包 API 依赖和 adapter 调用，不复制源码。若必须复制小段 Apache-2.0 代码，实施者需记录源文件、提交、修改和 NOTICE/版权要求，并先确认该文件确属该许可证。PaperQA2 要求 Python 3.11+，与目标一致，但须单独做依赖解析和 Windows 安装验证。

## 3. DeepEval

### 重点代码

- `deepeval/integrations/langchain/callback.py::CallbackHandler`；LangGraph 实际复用该 LangChain callback；
- `deepeval/tracing/types.py`：`Trace`、`LlmSpan`、`RetrieverSpan`、`ToolSpan`；
- `deepeval/dataset/golden.py::Golden`、`dataset.py::EvaluationDataset`；
- `deepeval/test_case/llm_test_case.py`：`LLMTestCase`、`ToolCall`、`RetrievedContextData`；
- `deepeval/metrics/`：Task Completion、Tool Correctness、Step Efficiency、Plan Adherence、Faithfulness、Contextual Precision/Recall；
- `deepeval/metrics/base_metric.py::BaseMetric` 与 pytest/CLI 集成；
- `tests/test_integrations/test_langgraph/`。

### 借鉴与适配

- 阶段 0 使用 Golden/TestCase 思路设计数据集和统一结果 schema；评测 callback 必须为可选且不改变图行为。
- smoke 只采用确定性自定义 metric、fixture/fake trace，不调用 Judge。
- 阶段 7 在显式 full marker/环境开关下使用 Agent 与 RAG Judge 指标。
- 项目自己记录 `input_tokens`、`output_tokens`、`total_tokens`、`estimated_cost`、`wall_time`、各类 tool call；不沿用可能混淆 token 与货币成本的单一字段。

### 不借鉴

- 不把 DeepEval trace 当生产审计或 Evidence 数据库；
- 不把平台上传、登录或联网缓存作为本地验收前提；
- 不以 Faithfulness 替代 Claim—Citation 精确绑定、时间与权威验证；
- 不单独用 Plan Adherence 作硬门槛，因为未抽取到计划时上游可能给满分。

### 代码复用与许可证

优先依赖公共 API；自定义指标在本项目实现。Apache-2.0 允许复用但仍需保留许可证/NOTICE 和变更说明。版本升级要通过 adapter contract tests。

## 4. LangMem

### 重点代码

- `src/langmem/knowledge/tools.py`：`create_search_memory_tool`、`create_manage_memory_tool`；
- `src/langmem/knowledge/extraction.py`：`create_memory_manager`、`MemoryManager`、`MemoryStoreManager`、`create_memory_searcher`；
- `src/langmem/utils.py::NamespaceTemplate`；
- `src/langmem/prompts/optimization.py::create_prompt_optimizer`；
- `src/langmem/reflection.py::ReflectionExecutor`；
- `src/langmem/short_term/summarization.py`：`RunningSummary`；
- `docs/docs/concepts/conceptual_guide.md` 和 memory guides。

### 借鉴与适配

- 借鉴 typed memory schema、LangGraph Store、Namespace template、search content/artifact 分离、functional extraction 和后台 reflection 模式。
- `create_memory_manager` 仅可作为 `MemoryWriteProposal` 生成器；项目 Gate 再执行 evidence、importance、dedupe、freshness、sensitivity、quality 和 promotion。
- procedural optimizer 仅生成候选策略，必须经多次任务证据和回归验证后激活。

### 不借鉴

- 不把 `create_manage_memory_tool` 或默认可删除的 `MemoryStoreManager` 直接暴露给 Agent；
- 不允许直接 `put/delete` 绕过 Gate；
- 不把 LangMem 当 Working Memory checkpoint；
- 不因单次成功自动修改系统提示词；
- 不把相似度当唯一召回或激活标准。

### 代码复用与许可证

优先依赖公共 API并在 adapter 后使用。MIT 代码复制仍需保留版权与许可证文本；实现自有治理模型可避免绑定其内部 API。

## 5. LangGraph

### 重点代码

- 当前项目使用的 `StateGraph`、subgraph、reducer、`RunnableConfig` 和 Store 模式；
- `libs/checkpoint-sqlite/`：`SqliteSaver`、`AsyncSqliteSaver`；
- SQLite Store 的 `SqliteStore/AsyncSqliteStore` 实现与序列化/迁移相关测试；
- checkpoint、interrupt、time travel、subgraph 和多用户 Store 文档/测试。

### 借鉴与适配

- Working Memory 使用 checkpointer，而长期知识/记忆使用独立 Repository/Store；
- 用 `thread_id` 恢复运行，用受信 `tenant/user/project/type` 生成长期 Namespace；
- reducer 合并稳定 ID 集合，避免并行 Researcher 来源重复；
- SQLite checkpointer 与知识/记忆数据库分文件，未来用 factory 切换 PostgreSQL。
- 阶段 5 异步图优先 `AsyncSqliteSaver`；`SqliteSaver` 的同步锁模型不作为并发部署方案。Checkpoint 与长期 Store 严格分工。
- `thread_id`、`checkpoint_ns` 和子图继承策略必须显式测试；`interrupt` 恢复会从节点开头重跑，外部副作用需幂等。

### 不借鉴

- 不把整个图 state 持久化为长期 Semantic Memory；
- 不把 checkpoint 当知识版本或审计日志；
- 不在阶段 1 提前引入 PostgreSQL；
- 不直接依赖参考仓库 `main` 私有实现。
- 不把 Store tuple namespace 当作授权；原生 `delete` 是物理删除，必须由项目 Gate 包装。

### 代码复用与许可证

通过官方 Python 包 API 使用。参考仓库为 MIT；如复制示例，保留声明并改造成当前 LangGraph 版本的 contract test。`langgraph-checkpoint-sqlite` 当前环境未安装，阶段 5 才允许按计划加入。

## 6. MCP Servers Filesystem

### 重点代码

- `src/filesystem/path-validation.ts`：allowed directory 边界；
- `src/filesystem/lib.ts`：`validatePath`、读取/写入和搜索；
- `src/filesystem/roots-utils.ts`：Roots 列表更新；
- `src/filesystem/path-utils.ts`：Windows drive、UNC、WSL 路径；
- `src/filesystem/index.ts`：工具注册、annotations 和启动；
- `src/filesystem/__tests__/`：path、roots、startup、symlink/TOCTOU 测试。

### 借鉴与适配

- 借鉴 canonical path/realpath、`allowedDir + separator` 边界、null-byte、symlink、真实 parent 和 Windows 路径测试场景。
- 使用官方 Windows 启动模式作为模板，但配置由项目生成并验证。
- Knowledge MCP 借鉴 tool schema、annotations、Roots/能力发现；实际实现调用 Python Repository/Service。
- Roots 为空或更新后全无效时，本项目必须 fail closed；不能保留旧权限。

### 不借鉴

- 不把 `readOnlyHint` 当授权；
- 不对只读知识源暴露上游的 write/edit/move 工具；
- 不依赖单一 server 同时保护只读与可写根目录；
- 不开放数据库文件、hard delete、force promotion 或 force memory write。

### 代码复用与许可证

优先以已发布 MCP server 依赖运行，Python Knowledge MCP 独立实现。该仓库正在 MIT → Apache-2.0 过渡，文档为 CC-BY-4.0，README 与源码声明存在差异；未来复制任何文件前必须检查该文件历史和适用许可证，并保留 notices。仅“借鉴测试场景/安全模式”最稳妥。

## 7. 非核心可选参考

`stanford-oval/storm`、`Libr-AI/OpenFactVerification`、`mbzuai-nlp/fire` 不在阶段 0–5 的依赖或必读范围。阶段 6 只有在现有 PaperQA/DeepEval 模式不足以设计 claim verification 时，才允许按需浅克隆、固定提交并记录许可证；不得因此引入第二套研究 Agent、完整事实核查平台或新阶段。

## 8. 参考仓库到本项目模块映射

| 参考能力 | 本项目目标边界 | 首次实现阶段 | 后续复用/评测阶段 |
|---|---|---|---|
| PaperQA `Doc/Text/Context`、`aget_evidence` | 自有模型借鉴；`knowledge.paperqa_adapter` + `KnowledgeRetriever` 映射到 Source/Version/Chunk/Evidence | 1、2 | 3 的coverage、6 的claim evidence |
| DeepEval Golden/Trace/Metric | `evaluation`数据集、遥测adapter、smoke/full指标 | 0 | 6借鉴schema、7完整评测 |
| LangGraph reducer/checkpoint/store | `state.py` additive refs、条件门禁、managed checkpointer、reporting子图 | 1 | 3、5、6 |
| LangMem extraction/search/namespace | Memory proposal、recall adapter、Write Gate后端 | 5 | 7记忆评测 |
| MCP Filesystem roots/path validation | 受限filesystem配置、安全验证器和集成测试 | 4 | 5的可信identity/`memory_search`扩展 |
| MCP tool registration | proposal-only `mcp_servers.knowledge_server` | 4 | 5注册只读`memory_search` |

## 9. 版本与许可证执行规则

阶段 0 必须新增：

- `doc/reference/README.md`：获取/更新命令与“只供参考、不参与包发现/测试”；
- `doc/reference/refs.lock.json`：仓库 URL、commit、branch/tag、获取日期；
- `THIRD_PARTY_NOTICES.md` 或等效清单：真正作为依赖或复制时的许可证说明；
- `.gitignore` 规则或子模块/下载脚本策略，由用户确认是否提交浅克隆本体。默认不把五个嵌套 `.git` 工作树作为项目提交内容。

任何阶段若改变参考提交，都必须先更新 lock、重新核对 API/许可证并记录在 `progress.md`，不得静默漂移。
