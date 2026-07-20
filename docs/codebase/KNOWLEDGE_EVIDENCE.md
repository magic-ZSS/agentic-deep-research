# Knowledge / Evidence Schema v1

## 1. 当前能力边界

阶段 1 已建立独立于 LangGraph 消息的结构化知识与证据基础：

```text
KnowledgeScope
  -> Source
  -> Document
  -> DocumentVersion -> ContentBlob
  -> Chunk
  -> Evidence -> Requirement (optional)
```

`state.py` 只新增 `source_ids`、`evidence_ids`、`requirement_ids` 轻量引用；原有
`notes`、`raw_notes`、`compressed_research` 继续保留。当前没有节点读写 Repository，
也没有接入解析、检索、PaperQA2 或知识生命周期策略。证据链默认关闭，证据为
`src/open_deep_research/configuration.py::Configuration.enable_structured_evidence=False`。

## 2. 身份与不可变性

- 原始内容身份是未经换行、Unicode 或 HTML 归一化的精确 bytes 的 SHA-256。
- 文本、URL、Windows 路径仅在逻辑身份层 canonicalize，不改变原始快照。
- 所有领域 ID 使用完整 SHA-256，并显式包含 `scope_id`；不同 scope 不共享 ID 或去重命中。
- 相同 `Document + content_sha256` 返回同一个 `DocumentVersion`；内容变化创建单调递增的新版本。
- `ContentBlob` 在单一 scope 内按内容去重；不同 Source 可以共享 blob，但保留独立来源链。
- `DocumentVersion`、`Chunk` 和原始 blob 不覆盖；领域模型为 frozen Pydantic v2 模型，schema version 为 `1.0`。

证据：`src/open_deep_research/knowledge/ids.py`、`knowledge/models.py`、
`evidence/models.py`。

## 3. Scope 与访问控制

每个 Repository 调用必须同时传入：

- `KnowledgeScope(tenant_id, project_id, owner_user_id?, visibility)`；
- 由调用边界提供的 `KnowledgeAccessContext`。

Repository 对 tenant、project、visibility 和 private owner fail closed。跨 scope ID
读取返回 `RepositoryAccessError`，不会通过 `not found`/去重结果泄露另一 scope 的存在。
当前没有全局默认 scope；server/MCP 也尚未接入这些接口。

证据：`src/open_deep_research/knowledge/repositories.py`、
`tests/integration/storage/test_repository_contract.py`。

## 4. Repository 与 SQLite v1

异步 Protocol 包括 `BlobRepository`、`DocumentRepository`、`EvidenceRepository`、
`RequirementRepository` 和 `AuditRepository`。metadata 有两个等价实现：

- `InMemoryRepository`：测试、fake 和参考语义；
- `SQLiteRepository`：标准库 `sqlite3`、foreign keys、WAL、busy timeout、短事务。

blob 有两个实现：

- `InMemoryBlobRepository`；
- `LocalBlobRepository`：`<root>/<scope_id>/<blob_id>.blob`，临时文件、`fsync`、
  `os.replace` 和读取后 SHA-256 验证。

SQLite schema version 固定为 `1`，表为 `knowledge_scopes`、`sources`、`documents`、
`content_blobs`、`document_versions`、`chunks`、`requirements`、`evidence`、
`audit_events`。关键唯一约束均包含 `scope_id`；并发版本写在
`BEGIN IMMEDIATE` 内依赖 UNIQUE 约束收敛，不使用进程内 check-then-write。

证据：`src/open_deep_research/storage/migrations/v1.py`、`storage/sqlite.py`、
`storage/blob_repository.py`、`knowledge/sqlite_repository.py`。

## 5. 删除、状态与审计

- Repository 没有 hard-delete API；Source、Document、DocumentVersion、Chunk、
  Requirement、Evidence 只写 `soft_deleted_at`。
- 默认 get/list 隐藏 soft-deleted 实体；`include_deleted=True` 仅供审计读取。
- mutation 与 `AuditEvent` 在同一 metadata 事务内提交；审计按 entity 或 correlation 查询。
- `candidate/active/stale/superseded/quarantined/archived` 只属于 DocumentVersion；
  Evidence 只使用 `pending/validated/rejected`。
- 可引用资格要求完整同 scope 链一致、Source/Document/Version/Chunk/Evidence 未删除、
  Version 为 active、Evidence 为 validated，并通过 Version 时间窗口。

自动 promotion、stale、supersede、quarantine policy 属于阶段 3，本阶段不实现。

## 6. 配置、回退与已知限制

新增配置均为声明式且未接入主图：`enable_structured_evidence`、
`knowledge_repository_backend`、`knowledge_db_path`、`knowledge_blob_dir`、
`sqlite_busy_timeout_ms` 以及可选本地 scope 字段。开关关闭时不会创建数据库或 blob 目录。

回退只需保持开关为 `False` 并不实例化 Repository；SQLite/blob 数据可以原地保留，
不得为了回退物理删除。SQLite 与本地 blob 是两个持久化边界，尚无跨介质事务；调用方必须先
成功写 blob，再提交引用它的 metadata。SQLite 适合第一版本地单机，不代表 PostgreSQL 并发能力。

## 7. 验证入口

```powershell
conda run --no-capture-output -n open-deep-research python -m pytest tests/unit/knowledge tests/unit/evidence -q
conda run --no-capture-output -n open-deep-research python -m pytest tests/integration/storage -q
conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 1
```

验收映射与命令结果记录在 `progress.md`。不得用这些离线测试替代阶段 2 的真实解析/检索验收。
