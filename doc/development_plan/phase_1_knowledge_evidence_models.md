# 阶段 1：知识、来源与证据数据模型

## 1. 阶段目标

建立与 LangGraph 运行消息解耦的 `KnowledgeScope → Source → Document → DocumentVersion → Chunk → Evidence`、`ContentBlob` 以及 `Requirement` 领域模型，提供 InMemory/SQLite metadata Repository和 InMemory/本地 content-addressed BlobRepository、SHA-256 去重、不可变版本、稳定 ID、并行 reducer、软删除与基础审计。完成后系统可可靠保存原始快照并回溯结构化证据，同时继续兼容现有自由文本 `notes/raw_notes/compressed_research`。

## 2. 为什么此阶段现在做

阶段 0 提供可测基线和版本矩阵。本阶段先固定领域身份和存储契约，阶段 2 的导入/PaperQA Adapter、阶段 3 的生命周期、阶段 4 的 Knowledge MCP、阶段 5 的 Semantic Memory 和阶段 6 的 Claim 引用都依赖这些稳定接口。若先接检索工具，第三方 hash、页码文本或 ToolMessage 会错误地成为长期事实模型。

## 3. 范围

- 定义 KnowledgeScope、Source、Document、DocumentVersion、ContentBlob、Chunk、Evidence、Requirement、locator、provenance 和审计基础模型；
- KnowledgeScope 至少含 tenant/project、可选 owner user和 visibility；所有 Repository读写显式接收 scope/access context，禁止靠任意 metadata过滤；
- 将 `candidate/active/stale/superseded/quarantined/archived` 明确定义为 **DocumentVersion 的 lifecycle status**；Evidence 只持有 `pending/validated/rejected` validation status。默认可引用资格由 Version active + Evidence validated + Source/时间/soft-delete条件派生，避免多套生命周期冲突；复杂 transition policy 留给阶段 3；
- 使用 canonical bytes + SHA-256 做内容身份；`ContentBlob` 在同一 scope内 content-addressed去重，同源同内容幂等、内容变化新建 Version，旧 Version/原始 bytes 不覆盖；
- canonical URL/internal storage ref/public display URI 与内容 hash 分离。相同内容来自不同 Source时保留两条 Source→Document→Version链，但共享同一 scope内的 ContentBlob；不跨 scope暴露或复用 dedupe结果；
- 定义 async `BlobRepository`、`DocumentRepository`、`EvidenceRepository`、`RequirementRepository`、`AuditRepository` Protocol；
- 实现 InMemory 与 SQLite，含 schema version、迁移入口、事务、唯一约束和审计记录；
- 建立跨并行 Researcher 的稳定 ID 去重 reducer，输出确定性排序；
- 对 `AgentState`/子图输出仅做 additive 引用字段扩展，并保留旧自由文本字段；
- 修正 Python 3.11/package discovery 以保证新增子包可安装（若阶段 0 未包含该最小变更，则本阶段补齐）。

## 4. 非目标

- 不安装或调用 PaperQA2，不解析 PDF/Markdown/HTML，不建立 embedding/index；
- 不改变 Researcher 先 Web 搜索的行为，不实现 evidence coverage 或知识写回；
- 不实现阶段 3 的自动 stale/supersede/quarantine 判定；
- 不实现 MCP、Memory、Claim extraction、citation validation 或 report repair；
- 不把完整领域对象塞入 LangGraph messages/checkpoint；主图只传稳定引用 ID 或轻量摘要；
- 不引入 PostgreSQL、向量数据库或 ORM；第一版使用标准 SQLite 和 Protocol；
- 不删除 `notes/raw_notes/compressed_research`，不修改现有 Writer 输入语义。

## 5. 当前项目修改点

预计新增：

- `src/open_deep_research/knowledge/models.py`：KnowledgeScope/Source/Document/Version/ContentBlob/Chunk、locator、status；
- `src/open_deep_research/evidence/models.py`：Evidence、Requirement、引用/支持类型；
- `src/open_deep_research/knowledge/ids.py`：canonicalization、SHA-256 和稳定 ID；
- `src/open_deep_research/knowledge/repositories.py`、`evidence/repositories.py`：Blob/Document/Evidence/Requirement/Audit Protocol；
- `src/open_deep_research/knowledge/in_memory_repository.py`、`sqlite_repository.py`；
- `src/open_deep_research/evidence/in_memory_repository.py`、`sqlite_repository.py`；
- `src/open_deep_research/evidence/reducers.py`；
- `src/open_deep_research/storage/sqlite.py`、`blob_repository.py`、`migrations/`：本阶段必建的共享 SQLite连接/迁移和原子 content-addressed blob边界；
- `tests/unit/knowledge/`、`tests/unit/evidence/`、`tests/integration/storage/`。

预计修改：

- `src/open_deep_research/state.py`：新增可选 `evidence_ids/source_ids/requirement_ids` 等轻量字段与 reducer，旧字段原样保留；
- `pyproject.toml`：确保 `open_deep_research.*` 子包被包含，Python >=3.11；不增加数据库/向量依赖；
- `configuration.py`：`enable_structured_evidence=False` 和可配置 SQLite 路径/超时，仅声明不接入图主路径；
- `scripts/validate_phase.py`、状态文件和必要的 schema 文档。

本阶段不得修改 `deep_researcher.py` 主图边或 `prompts.py`。

## 6. 参考仓库

- **PaperQA2**：参考 `types.py::Doc/DocDetails/Text/Context/ParsedMetadata` 的文档、chunk、context 关系。借鉴 metadata/定位概念；不使用其 MD5、question-derived Context ID、隐式 `Text.name` 页码或 `Docs.delete` 作为本项目身份/删除语义。其无 DocumentVersion/治理审计。只借鉴模式，本阶段不依赖或复制代码；Apache-2.0。
- **LangGraph**：参考 `Annotated` reducer、`add_messages`、Store/Checkpoint 分界。自定义 evidence/source reducer必须按 canonical ID 去重并确定性排序，不能用简单 `operator.add`。本阶段不引入 checkpointer；MIT，优先公共 API。
- **当前项目**：保留 `state.py::override_reducer` 和既有消息 reducer 的协议；结构化引用用新 reducer，避免改变现有测试。
- **DeepEval/LangMem/MCP Servers**：不是本阶段运行依赖，仅确保模型未来能被 adapter/Memory/MCP 使用。

允许直接复用的范围仅限少量通用思想或符合许可证的接口形状；领域模型、ID、Repository 与迁移必须由本项目实现。复制上游实现须单独 attribution 审核，默认不复制。

## 7. 数据结构和接口

核心字段至少包括：

```text
Source
  source_id, scope_id, kind, canonical_uri/internal_storage_ref,
  public_display_uri, display_name, publisher,
  authority_class, created_at, soft_deleted_at

KnowledgeScope
  scope_id, tenant_id, project_id, owner_user_id?,
  visibility=project|private, created_at

KnowledgeAccessContext
  trusted_tenant_id, trusted_user_id, trusted_project_id,
  allowed_visibilities, auth_source, request_id

Document
  document_id, source_id, logical_key, title, media_type, created_at

DocumentVersion
  version_id, document_id, blob_id, content_sha256, version_number,
  retrieved_at, published_at, valid_from, valid_to,
  supersedes_version_id, metadata, lifecycle_status, created_at

ContentBlob
  blob_id, scope_id, content_sha256, byte_size, media_type,
  storage_ref, created_at

Chunk
  chunk_id, version_id, ordinal, text_sha256, text,
  locator_type, page_start, page_end, heading_path,
  anchor, token_count, metadata

Evidence
  evidence_id, chunk_id, requirement_id?, excerpt,
  relation, directness, confidence, valid_at,
  retrieval_method, created_at,
  validation_status=pending|validated|rejected, soft_deleted_at

Requirement
  requirement_id, run_id/template_id, text, acceptance_hint,
  priority, parent_id?, status

AuditEvent
  event_id, entity_type, entity_id, action, actor_type,
  reason, before_status, after_status, created_at, correlation_id
```

状态与枚举必须序列化为稳定字符串。`Chunk.text` 可在 SQLite 中保存，但 graph state 只传 ID/短摘要。

Protocol 至少提供：

```text
DocumentRepository
  upsert_source(access_context, scope, ...)
  add_version(access_context, scope, ...)
  add_chunks(access_context, scope, ...)
  get_source/get_document/get_version/get_chunk(access_context, id)
  find_by_content_hash(access_context, scope, ...)
  list_versions(access_context, document_id)
  soft_delete(access_context, target_id, ...)

BlobRepository
  put(access_context, scope, bytes, media_type) -> ContentBlob
  get(access_context, blob_id) -> bytes/stream
  verify(access_context, blob_id, sha256)

EvidenceRepository
  add_evidence(access_context, ...)
  get_evidence(access_context, evidence_id)
  list_for_requirement/chunk/source(access_context, ...)

RequirementRepository
  add/list/get/update_status(access_context, ...)

AuditRepository
  append(access_context, event)
  list_for_entity(access_context, entity_type, entity_id)
  list_for_correlation(access_context, correlation_id)
```

SQLite 写入必须使用 scope-aware UNIQUE约束 + 事务/upsert实现并发幂等，不能依赖“先检查、await、再写”的内存流程。Local BlobRepository使用 scope目录 + SHA-256路径、临时文件和原子替换；公开报告/日志只能使用 `public_display_uri` 或 root-relative locator，不能泄漏 Windows绝对路径。

## 8. 执行步骤

1. 根据 baseline compatibility matrix 固定 Python/Pydantic/serialization约束，先写模型和 ID contract tests。
2. 实现 KnowledgeScope、内部/公开 source locator、URI/path/text canonicalization 与 SHA-256；明确 CRLF/LF、Unicode 和 HTML 原始快照的 hash 规则，原始字节 hash 不因展示格式改变。
3. 实现 Pydantic/dataclass 领域模型、不变量和稳定序列化；模型含 `schema_version`。
4. 定义 async Repository Protocol 和领域错误类型（not found、conflict、invalid transition、corrupt schema）。
5. 实现 InMemory metadata/Blob Repository，作为后续 service/fake 的参考语义；完成 scope、版本、blob去重、soft delete测试。
6. 设计 SQLite schema/migration v1和本地 content-addressed BlobRepository，建立外键、scope-aware唯一约束、索引、事务、原子文件写入和 busy timeout。
7. 添加并发写入/重复 upsert 测试，证明只生成一个版本/证据身份且不会覆盖旧版本。
8. 实现稳定 ID reducer：合并并行 source/evidence refs，按 ID 或显式 rank 稳定排序，避免重复。
9. 对 `state.py` 作 additive 扩展；运行既有 graph/state 测试，证明旧自由文本路径不变。
10. 验证 editable install 包含新子包，执行 phase validator、状态更新并停止。

## 9. 配置和回退

- `enable_structured_evidence: bool = False`；本阶段即使为 True 也只允许 state/repository 测试，不改变 Researcher 行为。
- `knowledge_repository_backend: Literal["memory", "sqlite"] = "sqlite"`，测试显式选择 memory。
- `knowledge_tenant_id/project_id`只用于本地CLI显式构造KnowledgeScope；server/MCP不得从模型参数取值。缺scope时持久Repository fail closed，不使用全局默认库。
- `knowledge_db_path`、`knowledge_blob_dir` 使用项目数据目录的相对/可配置路径，不含个人路径；`sqlite_busy_timeout_ms` 有保守默认。
- 配置关闭时不创建数据库、不增加必需 state 字段、不改变 Writer。
- SQLite schema v1 只增表；回退旧代码时忽略新 DB。不得为了回退删除数据，测试 DB 可在 `tmp_path` 回收。

## 10. 单元测试

- Source URI/local path canonicalization、Unicode、CRLF、SHA-256 和稳定 ID；
- 同 Source 同内容幂等；同 Source 内容变化创建 version_number+1；旧 Version 内容与引用保持不变；
- 相同内容来自不同 Source时只有一个 scope-local ContentBlob，但保留两条独立来源链；跨 scope不泄漏 dedupe命中；
- Chunk locator 对 page/heading/anchor 的约束与 invalid combination；
- Evidence 必须引用存在的 Chunk，Requirement parent 不得成环；
- 状态字符串和 schema round-trip；未知 schema/version 拒绝；
- soft delete 后默认查询过滤、include_deleted 可审计读取；
- DocumentVersion lifecycle与 Evidence validation status组合的可引用资格，不允许 Evidence自行覆盖 Version状态；
- internal storage ref/Windows绝对路径不进入 public view、日志或序列化报告；
- reducer 对不同排列、重复输入和并行批次产生同一确定结果；
- Repository Protocol 的 InMemory contract suite。

## 11. 集成测试

- 同一 contract suite 同时跑 InMemory 和 SQLite；
- SQLite 关闭重开后 Source/Version/Chunk/Evidence/Requirement 可完整回溯；
- 删除或改写原始导入路径后，仍可从 ContentBlob读取旧 Version原始 bytes并验证 hash；
- 两个 async writer 并发写相同内容，只生成一个逻辑版本且 audit 可解释；
- 内容变化生成新 Version，旧 Chunk/Evidence 仍指向旧 Version；
- `AgentState` 合并两个模拟 Researcher 的重复来源/证据 ID 后无重复且顺序稳定；
- `pip install -e .` 或等效 build/import smoke 能导入所有新子包；
- `enable_structured_evidence=False` 时现有 `tests/test_research_limits.py` 通过。

## 12. 阶段验收测试

- **T1-1**：相同 Source 和相同原始内容导入两次，DocumentVersion 数量保持 1，返回同一 version ID。
- **T1-2**：同一 Source 内容改变后生成新 Version，version number 单调增加，旧 Version/Chunk 不被覆盖。
- **T1-3**：任一 Evidence 可通过外键逐级回溯到 Chunk、DocumentVersion、Document 和 Source，缺失引用写入失败。
- **T1-4**：PDF page locator 与 Markdown heading locator 可结构化序列化；本阶段不要求真实解析。
- **T1-5**：两个并行 Researcher 合并相同 source/evidence refs 后各只保留一次，输入顺序变化不改变结果。
- **T1-6**：SQLite 仓库关闭并重开后数据、版本顺序、soft-delete 和审计记录保持一致。
- **T1-7**：并发重复写依靠 UNIQUE/事务保证幂等，无重复版本且无未处理 integrity error。
- **T1-8**：soft delete 不物理删除 Source/Version/Chunk/Evidence，默认检索不返回，审计查询仍可访问。
- **T1-9**：新增子包在 editable install 后可导入，Python 约束为 3.11+，不新增 ORM/向量数据库。
- **T1-10**：开关关闭时不创建业务 DB，旧 `notes/raw_notes/compressed_research` 类型和现有测试保持不变。
- **T1-11**：Repository contract tests 对 InMemory 与 SQLite 产生一致的可观察结果。
- **T1-12**：`scripts/validate_phase.py --phase 1` 能验证 schema version、迁移状态、T1 evidence 并返回正确退出码。
- **T1-13**：相同 bytes来自两个 Source时同 scope只有一个 ContentBlob、两条可追溯来源链；不同 scope查询不能观察另一 scope的blob/dedupe结果。
- **T1-14**：原始路径删除/改写后，旧 Version仍能通过 BlobRepository读取原始 bytes且SHA-256匹配。
- **T1-15**：`candidate/active/stale/superseded/quarantined/archived` 只属于 DocumentVersion；Evidence validation status独立，默认可引用资格派生规则有自动测试。
- **T1-16**：tenant/project/private scope的Repository过滤生效，跨 scope ID读取返回授权错误而非泄漏存在性。

## 13. 验收命令

```powershell
conda run --no-capture-output -n open-deep-research python -m pytest tests/unit/knowledge tests/unit/evidence -q
conda run --no-capture-output -n open-deep-research python -m pytest tests/integration/storage -q
conda run --no-capture-output -n open-deep-research python -m pytest tests/test_research_limits.py -q
conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 1
conda run --no-capture-output -n open-deep-research python -m ruff check src/open_deep_research/knowledge src/open_deep_research/evidence src/open_deep_research/storage src/open_deep_research/state.py tests/unit/knowledge tests/unit/evidence tests/integration/storage
conda run --no-capture-output -n open-deep-research python -m mypy src/open_deep_research/knowledge src/open_deep_research/evidence src/open_deep_research/storage src/open_deep_research/state.py
conda run --no-capture-output -n open-deep-research python -m pip install -e . --no-deps
Push-Location $env:TEMP
conda run --no-capture-output -n open-deep-research python -c "from open_deep_research.knowledge.models import Source; from open_deep_research.evidence.models import Evidence"
Pop-Location
git diff --check
```

如 `ruff`/`mypy` 未安装，按 `AGENTS.md` 记录环境缺口；不能伪造通过。不得运行外部模型或评测。

## 14. 完成定义

T1-1 至 T1-16 全部通过；领域模型和两个 metadata/Blob Repository后端契约一致；scope隔离、并发去重、不可变版本/原始快照、回溯、soft delete、审计和 reducer有自动测试；新子包从仓库外可导入；旧自由文本流程和现有低成本测试通过；没有实现阶段2+能力；状态文件含每项evidence。

## 15. 风险与降级方案

- **API兼容**：Pydantic/LangGraph 版本跨度可能影响 reducer/serialization；领域模型不继承第三方类型，adapter 后置。
- **Token成本**：本阶段无模型调用；Chunk 文本不得进入 graph state，避免未来 checkpoint 膨胀。
- **并发**：SQLite 写锁和 check-then-write 竞态；使用唯一约束、短事务、busy timeout 与幂等 key。
- **数据迁移**：schema v1 仍可能变化；保存 schema_version、迁移事务和 fixture，禁止覆盖旧 Version。
- **Windows**：路径 canonicalization、盘符大小写、反斜杠和 SQLite file lock 需专门测试；测试只用 `tmp_path`。
- **测试波动**：全部确定性，不依赖时间排序；冻结 clock 或断言相对关系。
- **规模**：SQLite 文本表不做向量检索；未来 PaperQA 索引承担检索，Repository 保持权威。
- **回退**：关闭开关并保留 DB；旧代码忽略新 state 可选字段，不执行破坏性 downgrade。

## 16. 本阶段 Codex 执行指令

```text
你现在只执行 doc/development_plan/phase_1_knowledge_evidence_models.md；确认阶段 0 已 completed 且 T0 全部有 evidence，否则停止汇报，不得绕过门禁或进入阶段 2。

先完整读取：AGENTS.md、feature_list.json、progress.md、session-handoff.md、doc/development_plan/{README,architecture_target,reference_repositories,execution_protocol,phase_1_knowledge_evidence_models}.md、pyproject.toml、src/open_deep_research/{state,configuration,deep_researcher,prompts,utils}.py、阶段 0 新增的 evaluation/validator 代码和相关测试；重点读取 doc/reference/paper-qa/src/paperqa/types.py、utils.py 中 hash、docs.py 的 add/delete，以及 doc/reference/langgraph 的 reducer/store/checkpoint contract 文件。先执行 git status --short 并保留用户改动。

允许范围：knowledge/evidence/storage 领域模型、Protocol、InMemory/SQLite Repository、迁移 v1、SHA-256/稳定 ID、确定性 reducer、state.py additive 引用字段、默认关闭配置、package discovery、对应测试/文档/状态文件。禁止安装或接入 PaperQA2，禁止解析文档、改变主图/Researcher/Web 搜索/Writer，禁止实现 lifecycle policy、MCP、Memory、Citation Validator，禁止 PostgreSQL/向量数据库，禁止修改 src/legacy/。

按第8节逐步实施；必须让同一Repository contract suite覆盖InMemory与SQLite/Local Blob，实现scope隔离、原始快照和AuditRepository，完成第10、11节测试并逐项执行T1-1至T1-16。所有删除必须soft delete，所有并行去重必须由稳定ID + scope-aware SQLite UNIQUE/事务保证，不能依赖内存先检查。保留notes/raw_notes/compressed_research兼容。

完成后更新 feature_list.json、progress.md、session-handoff.md，报告修改、模型/迁移决策、每项验收、命令/退出码、兼容回退、风险和最终 git status。完成后立即停止，不得自动开始阶段 2。
```
