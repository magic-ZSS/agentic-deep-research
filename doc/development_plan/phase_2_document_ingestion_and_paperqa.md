# 阶段 2：文档导入与 PaperQA2 本地检索

## 1. 阶段目标

在阶段1的领域模型和Repository上实现PDF、Markdown、HTML/Wikipedia快照及历史高质量查询结果的受控导入；通过本项目`KnowledgeRetriever`/Adapter隔离PaperQA2，提供供管理CLI/内部service调用的`knowledge_search`和`knowledge_read`契约，并让每个检索结果可稳定定位到PDF页码、Markdown标题层级或HTML快照位置。阶段2只验证candidate的diagnostic/inspection检索，不把未验证本地内容绑定给Researcher；阶段3验证并激活后才接入生产Researcher。

## 2. 为什么此阶段现在做

阶段 1 已确立 Source/Version/Chunk/Evidence 和持久化语义，本阶段才能安全把解析/索引结果映射到稳定身份，而不是让 PaperQA 的 MD5、内存字典或 `Text.name` 成为权威。阶段 3 的本地优先 Coverage Gate 需要本阶段的可定位检索；阶段 4 Knowledge MCP、阶段 5 Semantic Memory 和阶段 6 Citation Validator也依赖相同 Retriever 接口。

## 3. 范围

- 为本地允许路径中的 `.pdf`、`.md/.markdown`、静态 `.html/.htm` 和规范化历史查询 JSON/JSONL 实现导入；
- 使用原始字节 SHA-256 去重、原子保存阶段1 ContentBlob、创建 DocumentVersion/Chunk，并保存 parser、chunk参数和导入job元数据；所有新导入Version先为 `candidate`，索引ready不等于知识active；
- PDF locator 保存 `page_start/page_end`，Markdown 保存 `heading_path`，HTML 保存 snapshot URL/标题/DOM 或语义 anchor；
- 历史查询只导入经过显式 `verified=true`、包含 Source/Evidence 和 KnowledgeScope归属的记录，不把模型输出本身当无来源事实；
- 建立 `DocumentParser`、`IngestionService`、`KnowledgeRetriever`、`PaperQAKnowledgeRetriever` 等 Protocol/adapter；
- PaperQA2 仅用于解析/索引、`retrieve_texts` 和可选 `aget_evidence` contextual summarization；
- 提供返回结构化 `EvidenceHit` 的 `knowledge_search` 与按稳定ID读取的 `knowledge_read`；Agent路径默认只返回阶段1派生规则判定可引用的active/validated内容，candidate只允许管理/导入检查模式查询；
- 搜索无结果返回空列表和明确 metadata，不生成答案或占位内容；
- 实现LangChain/MCP可适配的工具schema和直接调用contract，但本阶段不绑定到生产Researcher tool registry；旧Researcher搜索/MCP工具始终不变；
- 完成 PaperQA2 与当前依赖的 Windows/Python 3.11 安装及 adapter contract smoke。

## 4. 非目标

- 不调用 `paperqa.ask`、`Docs.aquery`、PaperQA Agent、`PaperSearch/GatherEvidence/GenerateAnswer/Complete`；
- 不让 PaperQA2 拆任务、联网搜索、写最终回答或决定知识生命周期；
- 不实现“本地充分则禁止 Web”的 Agentic RAG gate，该逻辑属于阶段 3；
- 不自动把 Web 搜索结果写回知识库；
- 不在本阶段自动 promotion新导入candidate；阶段3负责validation/lifecycle。测试需要active资料时使用显式seed fixture，不伪造生产promotion；
- 不让Researcher在阶段2消费candidate或inspection结果；生产tool binding属于阶段3 governed orchestrator；
- 不实现 Filesystem MCP；导入仅接受应用层已校验的本地路径/字节流；
- 不实现 Memory、Claim validation 或报告修复；
- 不依赖 PaperQA pickle/Tantivy 目录作为 canonical Repository，不加载不可信 cache；
- 不实现 OCR 全覆盖、复杂表格/公式还原或远程 URL 抓取；解析失败需显式报告。

## 5. 当前项目修改点

预计新增：

- `src/open_deep_research/knowledge/ingestion/models.py`、`service.py`、`parsers/{pdf,markdown,html,past_query}.py`；
- `src/open_deep_research/knowledge/retrieval/models.py`、`protocols.py`、`repository_retriever.py`；
- `src/open_deep_research/knowledge/paperqa_adapter.py`；
- `src/open_deep_research/tools/knowledge.py`；
- `scripts/ingest_knowledge.py`、`scripts/search_knowledge.py`；
- `tests/fixtures/knowledge/`：自制/许可清晰的小 PDF、Markdown、HTML 和历史查询；
- `tests/unit/knowledge/ingestion/`、`tests/unit/knowledge/retrieval/`、`tests/integration/knowledge/`。

预计修改：

- `pyproject.toml`：新增可选 `knowledge` 依赖并固定兼容 PaperQA2/解析器版本；不得变成默认生产依赖；
- `configuration.py`：`enable_knowledge_base=False`、`enable_paperqa_retrieval=False`，索引/导入目录、chunk、evidence_k、contextual summarization 等配置；
- `state.py`：如阶段 1 的轻量 Evidence refs 不足，仅作 additive 扩展；
- `scripts/validate_phase.py` 和状态文件。

`deep_researcher.py`、`utils.py::get_all_tools`和生产tool binding本阶段不改；`tools/knowledge.py`只提供可直接测试、供阶段3注册的contract。

## 6. 参考仓库

- **PaperQA2 `types.py`**：借鉴 `Doc/Text/Context/ParsedMetadata/ChunkMetadata` 关系和 metadata；将 `Context` 映射为自有 `EvidenceHit`，不暴露 `PQASession`。其 Context ID 与问题/摘要绑定，不作全局 ID。
- **PaperQA2 `readers.py`**：参考 PDF page text 和 chunk 流；上游把页范围编码在 `Text.name`，Markdown 走普通行/代码 chunk且不保留标题，因此本项目必须输出显式 locator。
- **PaperQA2 `docs.py`**：允许 adapter 使用 `aadd_texts`、`retrieve_texts`、`aget_evidence`；空库会返回空 session。禁止 `aquery`。`aadd` 默认可能调用 metadata/LLM/multimodal，离线导入必须显式 metadata、关闭 enrichment/multimodal 或走受控 `aadd_texts`。
- **PaperQA2 `agents/search.py`/settings**：借鉴 index naming/cache manifest；不使用目录同步删除、不把 pickle cache 当可信输入、不依赖 filename check 识别内容变化。
- **PaperQA tests**：参考 docs lifecycle、evidence、duplicate、location、index manifest 和 parser tests；不得复制测试论文/网页内容，使用自制 fixture。
- Apache-2.0：优先公共 API + adapter；若必须访问非顶层 reader API，固定 SHA/包版本并加 contract test和 attribution。
- **LangGraph**：只参考 Tool 返回结构/异步调用；本阶段不改变图规划。

## 7. 数据结构和接口

新增契约至少包括：

```text
DocumentInput
  knowledge_scope, source_kind, logical_uri/path, media_type, bytes/stream,
  title?, published_at?, retrieved_at, metadata, trust_label

ImportJob
  job_id, input_ref, content_sha256, parser_name/version,
  chunk_config, status, document_id?, version_id?,
  blob_id?, index_status, error, created_at, finished_at

ChunkLocator
  type = page | heading | html_anchor | query_record
  page_start/page_end | heading_path | anchor/css_path | record_id

EvidenceHit
  evidence_id, chunk_id, version_id, source_id,
  text, contextual_summary?, score, rank, locator,
  published_at, retrieved_at, lifecycle_status,
  validation_status, retrieval_method

DocumentParser
  supports(media_type, suffix) -> bool
  parse(DocumentInput) -> ParsedDocument

KnowledgeRetriever
  search(query, filters, limit, as_of?) -> list[EvidenceHit]
  read(chunk_or_evidence_id) -> EvidenceHit/ChunkView

PaperQAAdapter
  index(version, chunks) -> IndexReceipt
  retrieve(query, filters, limit, contextualize) -> list[EvidenceHit]
  remove/rebuild only by internal maintenance service, never by Agent
```

IngestionService先把原始bytes原子写入ContentBlob，再在同一scope记录`ImportJob=pending`和immutable candidate Version，然后生成PaperQA派生索引；索引成功只标记`index_status=ready`，不得把lifecycle改为active。失败保留Blob/Version/Job，可重试或重建，不能出现“索引存在但来源/原始快照无法回溯”。

PaperQA `Docs`/默认Numpy索引是进程内派生状态，不视为持久化API。启动时Adapter必须从权威Repository按scope/partition rehydrate，或加载本项目创建且manifest包含scope、Version/chunk hash、配置hash和可信路径的缓存；不加载任意pickle。PaperQA原生检索不保证本项目`filters/as_of`，Adapter需先按scope/eligible Version分区，必要时overfetch后程序化post-filter，过滤不足时返回更少结果而非补造内容。

## 8. 执行步骤

1. 在独立 conda/临时环境解析 `knowledge` extra，验证 Python 3.11、PaperQA2、Tantivy/PDF parser 的 Windows wheel；记录兼容矩阵，不升级无关依赖。
2. 先实现 parser Protocol、ImportJob 和四类小 fixture；所有 parser 产生阶段 1 Chunk/locator，不接 PaperQA。
3. 实现 Markdown heading stack、HTML 静态 snapshot 和历史查询验证；PDF parser 保存显式页码并覆盖跨页 chunk。
4. 实现 IngestionService 的scope、ContentBlob原子保存、hash、幂等、candidate Version、事务和失败恢复；路径由调用者或受控root policy验证，导入器不接受任意URL。
5. 定义 KnowledgeRetriever/EvidenceHit，并实现基于 Repository 的确定性关键词/fake retriever，供无模型测试和回退。
6. 实现 PaperQA Adapter：显式构造metadata，关闭默认联网/enrichment；从权威Repository rehydrate或验证可信cache manifest；索引canonical chunks；对scope/filter/as_of做分区/overfetch/post-filter；映射公开返回类型并稳定排序`(-score, chunk_id)`。
7. 分离 raw retrieval 和 opt-in contextual evidence；contextual 路径使用注入模型/fake，设置 evidence_k、并发、timeout 和 token 上限。
8. 实现`knowledge_search/read`可调用工具contract，返回结构化artifact + 紧凑文本；candidate仅在可信inspection context可见，空结果为`[]`，无答案生成。
9. 保持生产Researcher工具集合不变；为阶段3预留明确注册接口，并证明任何阶段2配置都不会让未验证candidate进入Researcher。
10. 运行四类导入、重开存储、重建索引和工具集成验收；更新状态并停止。

## 9. 配置和回退

- `enable_knowledge_base=False`、`enable_paperqa_retrieval=False`；两者独立，Repository/fake retrieval 可在不启用 PaperQA 时测试。
- `knowledge_import_roots`、`knowledge_import_staging`、`paperqa_index_dir` 必须可配置且无个人绝对路径默认。
- `paperqa_contextual_summarization=False` 默认可先走 raw retrieval；开启时显式配置模型、并发、timeout、token。
- `paperqa_evidence_k`、`knowledge_search_limit`、chunk size/overlap 有上限校验。
- `knowledge_search_visibility=active_only` 是Agent默认；`include_candidate` 只能由管理CLI/测试inspection context显式开启，不能由模型参数开启。
- PaperQA 导入或检索失败时可回退 Repository retriever 或返回结构化错误/空结果，绝不自动生成内容。
- 关闭两个开关后不加载PaperQA、不创建索引；无论开关如何，本阶段都不把知识工具绑定到生产Researcher，当前Web搜索路径不变。

## 10. 单元测试

- PDF 页码、跨页 chunk 与无页码解析失败；
- Markdown H1–H6 heading stack、重复标题、code fence、CRLF；
- HTML title/heading/anchor、script/style 去除、canonical snapshot metadata；
- 历史查询必须 verified 且每个事实有 Source/Evidence，否则拒绝或只存非事实 run artifact；
- 历史查询缺 tenant/project scope时拒绝，private记录不得跨owner读取；
- 导入 hash 幂等、同路径内容变化新 Version、失败 job 可重试；
- Adapter 类型映射、PaperQA MD5/Context ID 不泄漏为 canonical ID；
- `retrieve_texts`/`aget_evidence` fake 返回稳定排序、score/locator 映射、timeout/exception；
- empty index 返回空列表，禁止生成 synthetic Evidence；
- knowledge_read 只接受稳定 ID，不接受任意路径；
- rehydrate/cache manifest、scope partition、filter/as_of post-filter和不足limit行为；
- 配置限值、默认开关和工具 schema。

## 11. 集成测试

- 四类 fixture 各导入一次，重开 SQLite/PaperQA index 后可检索；
- 删除/改写fixture源文件后，旧Version仍从ContentBlob恢复原始快照并保持页码/anchor可审计；
- 同一文件重复导入不重复建 Version，内容变化后新旧 Version 均可按 ID 读取；
- PDF query 返回真实 `page_start/page_end`，Markdown query 返回完整 `heading_path`，HTML 返回 snapshot/anchor；
- 用 fake/local embedding + fake contextual model 跑 PaperQA Adapter contract，不调用外部网络；
- 索引构建中断后 ImportJob 留下可诊断状态，重试成功且不重复数据；
- `knowledge_search/read`直接调用contract可运行；管理inspection可检索candidate并显示“不可引用”，生产Researcher工具集合仍与阶段0 snapshot相同；
- `knowledge_search` 无结果通过 ToolMessage 表达明确空集而不是模型补写事实。

## 12. 阶段验收测试

- **T2-1**：PDF、Markdown、HTML/Wikipedia 快照和 verified 历史查询各有一个 fixture 可成功导入并持久化。
- **T2-2**：PDF 检索结果包含结构化 `page_start/page_end`，可读取到对应 Version/Source。
- **T2-3**：Markdown 检索结果包含稳定标题层级路径，而不是只依赖文本行号。
- **T2-4**：HTML 结果绑定导入时快照 Version、canonical URL 和 anchor，后续网页变化不覆盖该 Version。
- **T2-5**：空库、无匹配或 PaperQA 返回空时，工具返回空列表且不生成答案/证据文本。
- **T2-6**：代码和 trace 证明未调用 `paperqa.ask`、`Docs.aquery` 或 `paperqa.agents` 完整 Agent loop。
- **T2-7**：相同内容重复导入不重复，内容变化创建新 Version，并能重建派生索引。
- **T2-8**：PaperQA Context/Text 映射到本项目稳定 Source/Version/Chunk/Evidence ID，结果按 score+ID 确定性排序。
- **T2-9**：本阶段不把知识工具绑定到生产Researcher；其工具集合和旧Web流程始终与baseline一致，关闭开关时PaperQA也不导入/初始化。
- **T2-10**：PaperQA/解析失败产生结构化错误和可重试 ImportJob，不留下 active 的孤立索引记录。
- **T2-11**：`knowledge_read` 只能按 Repository ID 读取，无法用任意本地路径绕过导入边界。
- **T2-12**：Windows Python 3.11 的 `knowledge` extra 安装/import smoke 与 adapter contract 通过，版本矩阵有 evidence。
- **T2-13**：所有导入Version初始为candidate；index ready不改变lifecycle，生产Researcher不接收这些结果，可信inspection模式可见且标记不可引用。
- **T2-14**：源PDF/Markdown/HTML被删除或改写后，旧Version仍可从ContentBlob读取原始bytes并验证hash/locator。
- **T2-15**：PaperQA Adapter重启后从权威Repository rehydrate或验证可信manifest；scope/filter/as_of均由程序化分区/后过滤保证，不假设PaperQA原生支持。

## 13. 验收命令

```powershell
conda run --no-capture-output -n open-deep-research python -m pytest tests/unit/knowledge tests/unit/tools/test_knowledge_tools.py -q
conda run --no-capture-output -n open-deep-research python -m pytest tests/integration/knowledge tests/integration/storage/test_phase2_repository_contract.py -m "not live" -q
conda run --no-capture-output -n open-deep-research python scripts/check_phase2_dependencies.py --json
conda run --no-capture-output -n open-deep-research python scripts/ingest_knowledge.py --source tests/fixtures/knowledge --tenant tenant-a --scope project-a --dry-run --json
conda run --no-capture-output -n open-deep-research python scripts/ingest_knowledge.py --source tests/fixtures/knowledge --tenant tenant-a --scope project-a --db artifacts/phase2/test-kb.sqlite --blob-dir artifacts/phase2/blobs --index-dir artifacts/phase2/paperqa-index --json
conda run --no-capture-output -n open-deep-research python scripts/search_knowledge.py --db artifacts/phase2/test-kb.sqlite --index-dir artifacts/phase2/paperqa-index --tenant tenant-a --scope project-a --include-candidate --query "storage evidence" --json
conda run --no-capture-output -n open-deep-research python scripts/search_knowledge.py --db artifacts/phase2/test-kb.sqlite --index-dir artifacts/phase2/paperqa-index --tenant tenant-a --scope project-a --include-candidate --paperqa --query "storage evidence" --json
conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 2
conda run --no-capture-output -n open-deep-research python -m ruff check src/open_deep_research/knowledge src/open_deep_research/tools tests/unit/knowledge tests/integration/knowledge scripts/ingest_knowledge.py scripts/search_knowledge.py
conda run --no-capture-output -n open-deep-research python -m mypy src/open_deep_research/knowledge src/open_deep_research/tools
conda run --no-capture-output -n open-deep-research python -m pytest tests/test_research_limits.py -q
git diff --check
```

安装验证在阶段开始经用户确认后执行：

```powershell
conda run --no-capture-output -n open-deep-research python -m pip install -e ".[knowledge]"
```

测试不得使用真实 API key 或远程 URL。

## 14. 完成定义

T2-1至T2-15全部通过；四类scope-aware candidate导入、原始Blob、定位、版本、失败恢复、rehydrate/可信缓存和索引重建有确定性测试；PaperQA只在Adapter后且无第二套Agent；inspection工具空结果不生成内容，生产Researcher未绑定candidate；Windows依赖矩阵通过；旧流程保持不变；状态文件包含evidence。未能安装兼容PaperQA2时阶段保持`blocked/in-progress`，不得以自制替代品冒充完成。

## 15. 风险与降级方案

- **API兼容**：PaperQA 浅克隆无可解析版本号且 API 可能漂移；固定兼容 release/SHA、adapter contract，失败退 Repository retriever。
- **Token成本**：`aget_evidence` 默认会调用 summary LLM；默认关闭 contextualization，测试 fake，生产设 evidence_k/并发/token 上限。
- **并发**：PaperQA `aadd_texts` 的 check-await-write 非原子；权威去重由阶段 1 SQLite UNIQUE/事务完成，索引写入串行/有 job lock。
- **数据迁移**：parser/chunk 参数改变应创建新 index generation，不覆盖旧 Version；索引可删重建，领域库不可硬删。
- **Windows**：Tantivy/PDF parser wheel、长路径和文件占用可能失败；先做安装 smoke，允许选兼容 parser，不改用 uv。
- **不可信 cache**：PaperQA Docs 可 pickle；只加载本项目创建且带 manifest/hash 的 cache，默认重建。
- **解析质量**：扫描 PDF/OCR 不支持时明确 `unsupported`，保留原文件 hash，不生成幻觉文本。
- **回退**：关闭 PaperQA 或整个 KB；旧 Web tool 保持可用，索引目录可重建。

## 16. 本阶段 Codex 执行指令

```text
你现在只执行 doc/development_plan/phase_2_document_ingestion_and_paperqa.md；先验证阶段 1 已 completed 且所有 T1 有 evidence，否则停止，不得进入阶段 3。

先完整读取 AGENTS.md、三个状态文件、本目录 README/architecture/reference/execution protocol/本阶段文档、pyproject.toml、configuration.py、state.py、utils.py 中 get_all_tools/Tavily、deep_researcher.py 的 Researcher 工具调用、阶段 1 全部 knowledge/evidence/repository 代码及测试。必须定点阅读 doc/reference/paper-qa 的 types.py、readers.py、docs.py、settings.py、agents/search.py、agents/tools.py 与相关 tests；先 git status --short，保留用户改动。

允许范围：四类本地candidate导入、parser/locator、ContentBlob/ImportJob、KnowledgeRetriever、PaperQA Adapter、仅供管理inspection/内部service的knowledge_search/read contract、可选knowledge依赖/配置、fixtures/tests/scripts/状态文件。禁止把知识工具绑定给生产Researcher，禁止调用或嵌入PaperQA ask/aquery/agents，禁止联网抓取、Agentic RAG/writeback、MCP、Memory、Citation Validator、重写主图、把PaperQA hash/index当权威库或修改src/legacy/。

先完成Windows/Python3.11依赖smoke；导入和测试显式关闭PaperQA默认联网metadata、multimodal/enrichment，使用自制fixture和fake/local模型。新导入只创建candidate，原始bytes进入ContentBlob，PaperQA进程状态必须从权威Repository rehydrate或验证可信manifest。完成第10、11节测试并逐项执行T2-1至T2-15；证明scope/filter/as_of、空结果、页码/标题、源文件删除后的快照和开关回退。若PaperQA兼容性阻塞，保留adapter/fake evidence并如实标记阶段未完成，不得换成第二套Agent。

完成后更新 feature_list.json、progress.md、session-handoff.md，报告修改、固定版本、每项验收、命令/退出码、成本/Windows 风险、回退和最终 git status。完成后立即停止，不得自动开始阶段 3。
```
