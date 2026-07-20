# 阶段 5：分层记忆系统

## 1. 阶段目标

实现可恢复的 Working Memory，以及按 Namespace 隔离并经 Memory Write Gate 管理的 Episodic、Semantic、Procedural 和 User Preference Memory。完成后同一 Thread 可 checkpoint/interrupt 恢复；跨 Thread 可安全召回高质量经验、带证据事实和明确偏好；无证据、低质量、过时、重复或敏感内容不会被自动激活。

## 2. 为什么此阶段现在做

阶段 1–3 已提供 Evidence、生命周期和审计，阶段 4 已建立可信 MCP/Namespace 边界，Memory 才能不绕过证据治理或跨用户泄漏。阶段 6 将依赖 Semantic Memory 的 Evidence 绑定和 Working Memory 的可恢复验证状态；阶段 7 需要量化记忆复用、隔离和陈旧率。

## 3. 范围

- 使用 LangGraph checkpointer 保存单 Thread 的消息、brief、计划/Requirement 覆盖、工具结果引用、Agent 状态与中断位置；
- Checkpoint保存阶段3 `RunEvidenceStore`引用而非把大块原文塞入state；恢复时先重开同run store并验证hash/namespace，再继续Claim/Writer，完成后按retention policy归档/过期；
- 提供managed async checkpointer lifespan：`off/in_memory/sqlite`，本地持久化优先`AsyncSqliteSaver`，使用独立checkpoint DB；`from_conn_string`资源必须在async context内存活并在CLI/server关闭时显式释放；
- 明确 root graph 和 Researcher 子图的 checkpoint 继承/namespace 策略，防止子图跨调用意外累积；
- 实现 `MemoryRepository`、InMemory/SQLite 或 LangGraph Store adapter，使用独立 memory DB；
- Namespace 固定为可信 `(tenant_id, user_id, project_id, memory_type, ...)`，service 层执行授权；
- 定义五类 memory model、status、有效时间、confidence、quality、evidence refs、origin run 和审计；
- 所有长期写入先形成 `MemoryWriteProposal`，经 importance、source/evidence、dedupe、freshness、sensitivity、quality 和 policy gate 后 promote/reject；
- Semantic Memory 强制 Evidence；Episodic 有最终质量/得分阈值；Procedural 要至少多次独立成功及回归/人工审批；Preference 只接受用户明确表达；
- 召回默认过滤 stale/quarantined/rejected/soft-deleted，按 token budget 注入主图/Researcher；
- 阶段 4 Knowledge MCP 在真实实现存在后注册只读 `memory_search`，不提供直接 memory write；
- checkpoint 恢复与外部副作用幂等测试。

## 4. 非目标

- 不把 LangMem `create_manage_memory_tool` 或 `MemoryStoreManager` 直接暴露给 Agent；
- 不允许 raw Store `delete`/`put` 绕过 Gate，不实现强制写入；
- 不把 Working Memory、Checkpoint、长期 Memory 和知识库混为一个数据库/namespace；
- 不因单次成功自动更新系统 prompt；Procedural 只形成 candidate，达到门槛后才激活；
- 不把模型推断的隐含偏好当用户偏好；
- 不实现阶段 6 的 Claim/Citation Validator；
- 不引入 Postgres、Redis、Kafka、durable worker 或外部向量数据库；
- 不在 checkpoint 存 secret、完整大型原文或无界 ToolMessage；
- 不全面重写 Supervisor/Researcher。

## 5. 当前项目修改点

预计新增：

- `src/open_deep_research/runtime/checkpointer.py`、`graph_factory.py`、`context.py`；
- `src/open_deep_research/memory/models.py`、`repositories.py`、`in_memory_repository.py`、`sqlite_repository.py` 或 `store_adapter.py`；
- `src/open_deep_research/memory/write_gate.py`、`policies.py`、`recall.py`、`namespace.py`、`proposals.py`；
- `src/open_deep_research/memory/langmem_adapter.py`（可选 proposal/search adapter）；
- `src/open_deep_research/tools/memory.py`；
- `scripts/inspect_memory.py`、`scripts/resume_research.py`；
- `tests/unit/memory/`、`tests/integration/memory/`、`tests/integration/checkpoint/`、`tests/security/test_memory_namespace.py`。

预计修改：

- `deep_researcher.py`：导出 builder 保持不变，增加通过 factory 编译的路径和少量 recall/write-gate 节点或 hooks；旧 `deep_researcher` export 兼容；
- `langgraph.json`、`run.py`：支持可信 `thread_id/user_id/project_id` 与恢复命令，具体方式按当前 LangGraph contract 最小调整；
- `state.py`：Working Memory 所需的 plan/Requirement coverage/memory refs 和 reducer，避免保存大块 Memory 对象；
- `configuration.py`：memory/checkpoint 开关、DB 路径、namespace、召回/token/gate 阈值；
- `mcp_servers/knowledge_server.py`：仅增加经 service 授权的 `memory_search`；
- `pyproject.toml`：固定兼容的 `langgraph-checkpoint-sqlite` 和可选 LangMem；
- `scripts/validate_phase.py` 和状态文件。

## 6. 参考仓库

- **LangGraph**：重点参考 `BaseCheckpointSaver`、`AsyncSqliteSaver`、`BaseStore/AsyncSqliteStore`、`StateGraph.compile(checkpointer, store)`、`interrupt/Command(resume=...)` 和 subgraph persistence tests。Checkpoint 主键含 `thread_id/checkpoint_ns/checkpoint_id`；子图 checkpointer继承须显式选择。SQLite 同步 Saver 不适合异步扩展，优先 Async。
- LangGraph Store 的 namespace 不是授权，raw delete 是物理删除；必须用项目 service/Gate 包装。启用 `LANGGRAPH_STRICT_MSGPACK=true` 或等效 allowlist，防不可信 checkpoint 反序列化。
- **LangMem**：参考 `create_memory_manager` 的 functional extraction、`create_search_memory_tool`、`NamespaceTemplate`、RunningSummary、ReflectionExecutor 和 prompt optimizer。只把 extraction/optimizer 输出当 proposal；不直接用默认可 delete 的 manager/store side effect。MIT，优先 API。
- **当前图**：`deep_researcher` 当前无 checkpointer；`tests/run_evaluate.py` 只用 `MemorySaver` 临时编译。需保留无 checkpoint 旧路径。
- **阶段 1–4**：Semantic 绑定 Evidence，知识 status/soft delete复用生命周期语义；MCP 只读 memory search。

复用/许可规则：LangGraph与LangMem均优先使用固定版本的公共API/adapter，不复制内部实现；两者当前参考提交为MIT，若复制示例或小段代码仍须保留版权/许可证并记录commit。PaperQA/MCP/DeepEval不是本阶段代码复用来源。

## 7. 数据结构和接口

```text
RuntimeIdentity
  tenant_id, user_id, project_id, thread_id, auth_source

MemoryRecord
  memory_id, memory_type, namespace, status,
  content, content_hash, created_at, updated_at,
  valid_from, valid_to, confidence, sensitivity,
  origin_run_ids, evidence_ids, supersedes_id?, soft_deleted_at

EpisodicMemory
  task_type, brief_fingerprint, plan_summary,
  useful_tools, failure_causes, outcome_score, reusable_lessons

SemanticMemory
  fact, evidence_ids(non-empty), source_ids,
  observed_at, valid_at, confidence

ProceduralMemory
  strategy, applicable_when, supporting_run_ids,
  success_count, regression_result, approval

UserPreferenceMemory
  preference, scope, explicit_statement_ref, confirmed_at

MemoryWriteProposal
  proposal_id, type, candidate_content, provenance,
  importance, status, gate_checks, decision_reason

MemoryGateDecision
  importance/source/dedupe/freshness/sensitivity/quality checks,
  decision=promote|reject|quarantine|needs_review,
  policy_version, audit_id
```

接口：

```text
CheckpointerLifespan.open(config)
  -> AsyncContextManager[BaseCheckpointSaver | None]
MemoryRepository.search/get/propose/apply_decision/mark_stale/soft_delete
MemoryWriteGate.evaluate(proposal, runtime_identity) -> MemoryGateDecision
MemoryRecall.search(query, identity, types, as_of, token_budget)
```

Graph node 只接收 `RuntimeIdentity` 的可信 context和 memory refs/compact summaries；Namespace 参数不由模型工具调用传入。

进入context时完成必要`setup()`/migration（Store后端同样setup），退出时`aclose`/关闭SQLite连接；graph、CLI和LangGraph server factory不得持有已离开context的Saver。

## 8. 执行步骤

1. 固定 LangGraph/checkpoint-sqlite/LangMem 兼容版本，先做 Windows SQLite import/open/close smoke；不启用 sqlite-vec 时记录原因。
2. 实现 RuntimeIdentity/Namespace policy 和授权测试；CLI local identity 需显式参数/配置，Supabase 环境从可信 auth提取。
3. 实现managed async checkpointer/store lifespan和root graph compile factory；在context内setup/compile/run，shutdown关闭连接；确定Researcher子图继承策略，添加thread resume/namespace/连接释放测试。
4. 测试 interrupt 后节点从头重跑语义；所有 proposal/write tool 使用幂等 key，副作用放在 interrupt 后或可安全重试。
5. 定义五类 Memory 和 Repository contract，完成 InMemory/SQLite 实现、status/soft delete/audit。
6. 实现 deterministic Memory Write Gate 七项检查和 proposal workflow；LangMem extractor 仅可选生成 proposal。
7. 实现各类型 promotion policy：Semantic evidence mandatory；Episodic quality；Procedural 多次成功+回归/审批；Preference explicit。
8. 实现 recall 的 Namespace、freshness、status、dedupe、rank和 token budget；以轻量上下文挂到 brief/Researcher，功能默认关闭。
9. 在 Knowledge MCP 注册只读 `memory_search`，服务端从可信 context决定 namespace；不添加 manage/write tool。
10. 完成跨 Thread、跨用户、陈旧/重复、checkpoint中断恢复和兼容回退验收，更新状态并停止。

## 9. 配置和回退

- `enable_memory=False`、`enable_memory_writes=False`；可只读召回而禁止写入。
- `checkpointer_backend=off|memory|sqlite` 默认 `off` 以保持旧行为；本地可选择 sqlite。
- `checkpoint_db_path`、`memory_db_path` 与 `knowledge_db_path` 分开；未来 factory 预留 postgres 字符串但本阶段不实现。
- `memory_namespace_template` 只由应用配置生成，字段来自 trusted context；缺 identity 时长期 Memory fail closed。
- `memory_recall_limit/token_budget`、敏感级别、freshness、episodic score、procedural minimum successes（不得小于 3）可配置且有边界。
- `memory_search` 只在 `enable_memory` 且 server capability ready 时注册。
- 关闭开关后无 recall/write；`checkpointer_backend=off` 使用当前预编译图行为。已有 DB 保留，不自动删。

## 10. 单元测试

- Namespace 构造、非法/空 ID、模型伪造 user_id、跨 tenant/project拒绝；
- 五类 Memory schema、hash、status、validity、soft delete与审计；
- Gate 的 importance、Evidence/source、dedupe、freshness、sensitivity、quality、promotion 每项 pass/fail；
- Semantic 无 Evidence 必拒绝；Evidence stale/quarantined 时不得 active；
- Episodic 低分/失败任务不激活，高分且可复用才允许；
- Procedural 1/2 次成功仍 candidate，达到最少 3 次且回归/审批后才 active；
- Preference 必须有 explicit statement ref，推断偏好拒绝；
- dedupe 合并 origin runs而不无限新增；
- recall 过滤过时/隔离/删除并遵守 token budget；
- checkpointer/config factory 的 off/memory/sqlite 和错误处理；
- managed lifespan在异常/取消/正常退出时关闭连接，Store setup只执行幂等迁移；离开context后不复用Saver；
- LangMem adapter 不调用 raw delete/put，输出仅 proposal。

## 11. 集成测试

- 运行到人工 interrupt/测试断点，进程关闭/重建 AsyncSqliteSaver 后用同 `thread_id` 恢复，状态/ToolMessage配对正确；
- 不同 `thread_id` 状态隔离，Researcher 子图不意外共享历史；
- 同 user/project 的显式 preference 可跨 Thread 召回，不同 user/project 查不到；
- Semantic proposal 从 active Evidence 创建可召回，删除/过时 Evidence 后不再作为当前事实；
- 重复 proposal 多次应用只保留一个 memory，origin/usage 计数有界更新；
- 外部 tool/write 在 interrupt resume 后幂等，不重复创建 proposal/知识；
- writeback关闭的运行在interrupt后仍可解析原transient Evidence IDs，跨run/跨user不能访问其RunEvidenceStore；
- `memory_search` 与内部 MemoryRecall 结果一致，MCP 不能指定他人 namespace；
- 关闭 Memory/checkpointer 后阶段 3 integration 和 baseline回归通过。

## 12. 阶段验收测试

- **T5-1**：SQLite Checkpoint 在重建进程/graph 后用同 `thread_id` 恢复到正确节点和 state，不重复已提交副作用。
- **T5-2**：不同 tenant/user/project/thread 数据严格隔离，伪造 Namespace 参数被拒绝。
- **T5-3**：用户明确偏好可跨 Thread 召回；无明确 statement ref 的推断偏好不能激活。
- **T5-4**：没有 active Evidence 的事实不能进入 Semantic Memory；Evidence 失效后该记忆不作为当前事实返回。
- **T5-5**：低质量/失败任务不进入 active Episodic Memory，高质量门槛结果有 score/evidence。
- **T5-6**：过时、quarantined、soft-deleted Memory 默认不召回，历史审计仍可读取。
- **T5-7**：相同 Memory 重复写入不会无限增长，只更新受控 origin/usage metadata。
- **T5-8**：单次或两次成功不能激活 Procedural Memory；最少三次独立成功且回归/审批后才可激活。
- **T5-9**：所有长期写入都有 proposal、七项 Gate 结果、policy version 和审计；没有直接 Agent write/delete 工具。
- **T5-10**：`memory_search` 只在实现启用后注册，与内部 recall 一致且不能跨 Namespace。
- **T5-11**：Checkpoint、Knowledge、Memory 使用独立 SQLite 文件，未加载不可信 pickle，strict serialization 配置有 evidence。
- **T5-12**：关闭 Memory/Checkpoint 后旧图输入输出和阶段 3 行为回归通过。
- **T5-13**：recall 遵守条数/token 上限，不把完整数据库或大型原文注入 prompt。
- **T5-14**：`scripts/validate_phase.py --phase 5` 验证恢复、隔离、Gate 和状态 evidence。
- **T5-15**：AsyncSqliteSaver/Store只在managed async lifespan内使用，setup/migration完成，正常、异常和取消退出后连接关闭且进程无挂起线程/句柄。
- **T5-16**：writeback关闭的运行中断后，恢复流程能按checkpoint中的引用重开同一RunEvidenceStore并解析原Evidence IDs；跨run/用户拒绝，完成后retention状态可审计。

## 13. 验收命令

```powershell
conda run --no-capture-output -n open-deep-research python -m pytest tests/unit/memory tests/security/test_memory_namespace.py -q
conda run --no-capture-output -n open-deep-research python -m pytest tests/integration/checkpoint tests/integration/memory -m "not live" -q
conda run --no-capture-output -n open-deep-research python scripts/resume_research.py --self-test --db artifacts/test-checkpoints.sqlite
conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 5
conda run --no-capture-output -n open-deep-research python -m ruff check src/open_deep_research/runtime src/open_deep_research/memory src/open_deep_research/tools/memory.py tests/unit/memory tests/integration/memory tests/integration/checkpoint
conda run --no-capture-output -n open-deep-research python -m mypy src/open_deep_research/runtime src/open_deep_research/memory
conda run --no-capture-output -n open-deep-research python -m pytest tests/integration/agentic_rag tests/test_research_limits.py -q
git diff --check
```

阶段开始时安装/锁定 checkpoint/LangMem 依赖需按仓库规则执行；所有测试使用 fake model，不调用远程服务。

## 14. 完成定义

T5-1至T5-16全部通过；真实SQLite checkpoint和RunEvidenceStore恢复、managed lifespan/连接关闭、子图语义和幂等副作用已验证；五类Memory/Namespace/Gate/soft invalidation有自动测试；Semantic/Episodic/Procedural/Preference硬规则不可被模型绕过；MCP只读search安全；数据库分离；关闭开关回归；状态evidence完整。没有跨进程恢复、资源关闭或Namespace隔离evidence时不得完成。

## 15. 风险与降级方案

- **API兼容**：当前 pyproject 下限 `langgraph>=0.5.4` 与本机/参考 HEAD 跨度大；固定 matrix，factory/contract 隔离，不假设 HEAD API。
- **Windows/sqlite-vec**：checkpoint-sqlite 可能带原生扩展；第一版不启用 SQLite Store vector index，向量检索仍由 PaperQA；Windows smoke失败则只用 InMemory测试并保持阶段未完成。
- **Checkpoint重放**：interrupt resume 会从节点开头重跑；所有写入幂等，避免在 interrupt 前不可逆副作用。
- **数据/锁**：三个独立 DB、短事务、busy timeout；不在 DB 事务内调 LLM。
- **隐私**：Namespace 非授权；service 用可信 identity，敏感 proposal默认 reject/needs_review，日志去敏。
- **Token**：Working/raw notes 和 recall 可能膨胀；存引用/摘要、限额、必要时 RunningSummary，但不丢 ToolMessage protocol。
- **记忆污染**：LangMem extractor可能误判；只生成 proposal，硬 Gate与人工 review可降级。
- **回退**：关闭 writes/recall/checkpointer；保留 DB 和旧图 export，不物理删除 Memory。

## 16. 本阶段 Codex 执行指令

```text
你现在只执行 doc/development_plan/phase_5_memory_system.md；先验证阶段 4 completed 且所有 T4 有 evidence，否则停止，不得进入阶段 6。

先读取 AGENTS.md、状态文件、本目录总览/架构/参考/协议/本阶段文档、deep_researcher.py 的 graph builder/子图、state.py、configuration.py、run.py、langgraph.json、阶段 1–4 Evidence/Repository/Lifecycle/MCP/identity 实现和测试。必须定点阅读 doc/reference/langgraph 的 BaseCheckpointSaver、AsyncSqliteSaver、BaseStore/AsyncSqliteStore、compile/interrupt/runtime 和 subgraph persistence tests，以及 doc/reference/langmem 的 knowledge extraction/tools、NamespaceTemplate、RunningSummary、ReflectionExecutor、prompt optimizer。先 git status --short 并保留用户改动。

允许范围：managed async checkpointer/store lifespan、graph factory和恢复、可信RuntimeIdentity/Namespace、五类Memory模型/Repository、proposal/Write Gate/recall、可选受控LangMem adapter、只读memory_search、默认关闭配置、最小graph/state/CLI/langgraph.json挂接、tests/scripts/状态文件。禁止返回/长期持有离开context的裸AsyncSqliteSaver，禁止直接使用LangMem manage/store side effect、Agent raw put/delete/force write、单次成功修改prompt、阶段6 citation工作、Postgres/Redis/Kafka/外部向量库、全面重写Supervisor/Researcher或修改src/legacy/。

所有长期写入必须先proposal再通过七项Gate；Semantic必须Evidence，Episodic需质量，Procedural至少三次独立成功+回归/审批，Preference必须明确表达。完成第10、11节测试并逐项执行T5-1至T5-16，重点证明跨进程checkpoint/RunEvidenceStore恢复、managed lifespan/setup/关闭、interrupt副作用幂等和跨用户隔离。测试不得调用远程模型。

完成后更新 feature_list.json、progress.md、session-handoff.md，报告修改、版本/namespace/gate决策、每项验收、命令/退出码、安全/回退和最终 git status。完成后立即停止，不得自动开始阶段 6。
```
