# 阶段 3：Agentic RAG 与知识生命周期

## 1. 阶段目标

把 Researcher 的搜索入口升级为受治理的本地优先检索：先查知识库，以直接性、覆盖、权威、时效和冲突判断证据是否充分；只有存在明确缺口时才调用现有 Web 搜索。Web 结果先进入 `candidate`，验证后才能 `active`，不合格内容进入 `quarantined`，旧版本进入 `stale/superseded/archived`；所有状态变化有审计，Agent 不能硬删除。

## 2. 为什么此阶段现在做

阶段 2 已提供可定位的 KnowledgeRetriever 和四类导入，阶段 1 已提供状态/Repository。本阶段才能在不依赖自由文本 prompt 的情况下实施 local-first 和知识写回。其治理服务将被阶段 4 Knowledge MCP、阶段 5 Semantic Memory 和阶段 6 Citation Validator复用；若先开放 MCP/Memory，会形成绕过验证的写入通道。

## 3. 范围

- 定义 `RetrievalRequest/Plan`、Requirement coverage、Evidence sufficiency 与缺口模型；
- 在 brief生成后用feature-gated `RequirementExtractor/Normalizer` 把自由文本 `research_brief` 固化为稳定RequirementSet/ResearchPlan；抽取失败至少保留一个“完整brief”Requirement，不能得到空计划；
- 在Supervisor结束前运行`ResearchCompletionGate`：必需Requirement未覆盖且预算未耗尽时拒绝`ResearchComplete`并继续缺口研究；预算耗尽/blocked时允许结束但必须输出未覆盖清单；
- 实现 `GovernedRetrievalOrchestrator`，每次搜索必须先调本地 Retriever；
- 本地阶段顺序固定为：先查active/validated；若仍有缺口，由orchestrator用内部inspection权限召回相关local candidate并送Gate，promotion/quarantine后重新计算coverage；只有仍不足才可Web。未验证candidate绝不直接暴露给模型；
- 在 `enable_agentic_rag=True` 时不向模型直接暴露可绕过gate的Web工具。第一版只允许实现`WebSearchProvider`且能被orchestrator调用、返回结构化结果的adapter；当前OpenAI/Anthropic provider-native server-side search无法可靠包装，配置必须fail closed，要求改用受治理adapter或关闭Agentic RAG；
- 本地充分时 `web_call_count=0`；不足时只为缺口生成受预算限制的 Web query；
- Web结果总是先规范化为candidate envelope，不直接激活；先写入按`run_id`隔离的`RunEvidenceStore`。writeback开启时再持久化canonical candidate DocumentVersion并走promotion；关闭时只保留可由ID解析、至少存活到报告结束的run-scoped EvidenceBundle，不进入跨run知识检索；
- 验证来源可解析性、直接性、时效、权威、重复、冲突、最低内容质量和敏感性；
- 阶段2本地导入candidate与Web persisted candidate走同一Validation/Lifecycle service；“来自本地”或“index ready”都不能自动active；
- 实现状态转换 service、proposal、append-only AuditEvent、soft delete 和替代关系；
- 默认检索只返回 `active` 且在 `as_of` 有效的知识；candidate/stale/quarantined/soft-deleted 不作为当前事实；
- 相同/相似查询复用已激活证据，能量化减少 Web 调用；
- 将结构化证据转换为兼容 `notes` 的受控视图，同时保留 evidence IDs 给后续阶段；
- 结构化Evidence只接收检索/验证产物；`think_tool` reflection、错误/超限ToolMessage和普通诊断文本不得进入Evidence或后续引用输入；
- 修复会破坏本阶段可靠性的既有恢复缺陷：Supervisor不得用`or True`吞掉任意batch异常；保留成功的并行部分结果；compression token裁剪必须按正确compression model真实重试；`ResearchComplete`与待执行研究同轮时不得提前丢弃研究任务；
- 对现有图只做最小 tool registry/路由接入，不全面重写 Supervisor/Researcher。
- 为阶段7消融提供非Agentic `knowledge_augmented_legacy`模式：只把active+validated的`knowledge_search/read`绑定到legacy Researcher并保留原Web工具，不执行local-first/writeback；默认关闭。Agentic模式则改为唯一governed retrieval入口。

## 4. 非目标

- 不实现 Filesystem/Knowledge MCP server；
- 不实现五层 Memory 或 Checkpoint；历史查询复用仍属于知识 Repository；
- 不实现最终 Claim extraction、citation entailment、report repair 或程序化来源表；
- 不引入新的Web provider或付费重排序服务；Tavily作为第一版governed adapter，OpenAI/Anthropic native只在feature flag关闭的legacy路径保留；
- 不让 LLM 单独决定 promotion、hard delete 或覆盖版本；最终 transition 由程序规则/Gate执行；
- 不自动修改系统 prompt 或研究策略；
- 不物理删除任何知识，不让 archived/quarantined 状态丢失审计；
- 不修复无关 Supervisor/Writer 缺陷，除非它们直接阻止本阶段验收且得到用户确认。

## 5. 当前项目修改点

预计新增：

- `src/open_deep_research/knowledge/retrieval/orchestrator.py`、`coverage.py`、`query_planner.py`；
- `src/open_deep_research/evidence/run_store.py`：按run隔离的transient Source/Version/Chunk/Evidence resolver，不属于长期知识库；
- `src/open_deep_research/research/requirements.py`、`completion_gate.py`（若新增独立`research/`包比塞进图文件更清晰）；
- `src/open_deep_research/knowledge/lifecycle/models.py`、`policy.py`、`service.py`、`audit.py`；
- `src/open_deep_research/knowledge/validation/{source,temporal,conflict,quality}.py`；
- `src/open_deep_research/tools/governed_retrieval.py`；
- `tests/unit/knowledge/lifecycle/`、`tests/unit/knowledge/retrieval/test_coverage.py`；
- `tests/unit/research/test_requirements.py`、`test_completion_gate.py`；
- `tests/integration/agentic_rag/` 和 deterministic fake Web/KB fixtures；
- `scripts/review_knowledge_proposals.py` 或只读诊断 CLI。

预计修改：

- `configuration.py`：`enable_knowledge_tools=False`、`enable_agentic_rag=False`、`enable_knowledge_writeback=False`、coverage/authority/freshness/conflict/budget阈值；
- `utils.py::get_all_tools` 或阶段 2 tool registry：开启时用一个 governed retrieval façade 代替直接 Web search；特别处理 provider-native search，禁止旁路；
- `state.py`：新增轻量 retrieval decision/audit IDs 和 Requirement coverage refs；
- `deep_researcher.py`：只允许增加brief后Requirement materialization、Supervisor completion guard、tool binding、恢复缺陷修复和兼容evidence handoff等最小调整，不改变Supervisor—Researcher主架构；
- `prompts.py`：只说明新工具契约和 evidence gap，不能把硬规则仅写在 prompt；
- 阶段 1 Repository schema migration、phase validator 和状态文件。

## 6. 参考仓库

- **PaperQA2**：使用阶段 2 Adapter 的 `retrieve_texts/aget_evidence` 结果、score/contextual summary；借鉴 MMR 和 Evidence Context。默认 `texts_index_mmr_lambda=1.0` 实为纯相似度，不能据名字假定多样性；本项目 Coverage 还需 authority/freshness/conflict。禁止 Agent/aquery；Apache-2.0，依赖 API。
- **LangGraph**：借鉴条件路由、并发 reducer 和 Runtime context。硬门禁放 orchestrator/service，不只依赖 tool description。MIT，使用公共 API。
- **当前 Tavily 实现**：复用 `tavily_search` 的查询/结果限制和 URL 去重，但将自由文本格式化前的结构化结果适配成 candidate；若现有函数无法提供结构化边界，可最小抽取 fetch/normalize 函数并保持旧 formatter 测试。
- **DeepEval**：阶段 0 telemetry 用于证明 Web call 减少；本阶段不运行 Judge。
- 不直接复制 STORM/OpenFactVerification/FIRE；它们只可能在阶段 6 按需参考。

复用/许可规则：PaperQA2仅经Apache-2.0公共API Adapter，LangGraph仅经MIT公共API；当前Tavily代码是本项目内最小重构。默认不复制参考仓库源码；若确需复制，先记录文件、commit、许可证和修改说明。

## 7. 数据结构和接口

```text
RetrievalRequest
  run_id, researcher_id, requirement_ids, query,
  filters, as_of, local_limit, web_budget

RequirementSet
  plan_id, research_brief_hash, requirement_ids,
  extractor_version, created_at

ResearchCompletionDecision
  decision=continue|complete|complete_with_gaps|blocked,
  covered_requirement_ids, missing_requirement_ids,
  remaining_budget, reasons, audit_id

WebSearchProvider
  search(queries, budget, runtime_context) -> list[StructuredWebResult]

CoverageAssessment
  requirement_id, evidence_ids, direct_evidence_count,
  authority_score, freshness_status, conflicts,
  coverage_score, decision=sufficient|insufficient|blocked,
  missing_aspects, policy_version, reasons

KnowledgeCandidate
  candidate_id, source/version/chunk/evidence payload refs,
  persistence=transient|persisted, discovered_by, query, validation_status,
  proposed_at, expires_at?

RunEvidenceBundle
  run_id, transient_source_id, transient_version_id,
  chunk/evidence payloads, validation_status,
  created_at, expires_at, canonical_version_id?

EvidenceResolver
  resolve(run_id, evidence_id)
    -> canonical Repository Evidence | RunEvidenceStore Evidence

ValidationDecision
  decision=promote|quarantine|stale|supersede|archive|reject,
  reasons, rule_results, reviewer, policy_version

LifecycleProposal
  proposal_id, target_id, proposed_action, reason,
  actor, status=pending|approved|rejected|applied

GovernedRetrievalResult
  local_hits, web_hits, canonical_active_evidence_ids,
  run_validated_evidence_ids, usable_evidence_refs,
  coverage_before/after, web_queries, audit_ids,
  compact_notes_view
```

允许转换至少包括：

```text
candidate → active | quarantined | archived
active → stale | superseded | quarantined | archived
stale → active（重新验证） | superseded | archived
quarantined → candidate（修正后重新审查） | archived
superseded → archived
```

Canonical DocumentVersion是上述lifecycle transition的唯一主体；KnowledgeCandidate只是指向candidate Version或RunEvidenceBundle的提议/envelope，Evidence只使用阶段1 validation status。RunEvidenceBundle不是长期DocumentVersion状态机，不能被跨run搜索或称为active；它只记录“validated_for_run”及完整Source/locator/hash链。默认长期可引用资格沿用阶段1规则，当前报告可额外通过EvidenceResolver读取同run已验证Bundle。任何transition都需append AuditEvent；Agent只能创建proposal。自动规则可应用低风险状态变化，但必须记录actor=`policy`、policy version和reasons。没有`hard_delete` transition。

## 8. 执行步骤

1. 定义RequirementSet、coverage/completion/lifecycle contract和DocumentVersion transition matrix，先写拒绝非法转换、append audit、默认过滤测试。
2. 实现brief→RequirementExtractor/Normalizer和稳定ID/trace；把可选Requirement抽取节点挂在brief后，并定义抽取失败的单Requirement降级。
3. 实现确定性CoveragePolicy与ResearchCompletionGate：按每个Requirement检查active、直接性、权威、时间、冲突与最低证据数；LLM只能提供辅助特征，不能绕过硬门槛。
4. 实现GovernedRetrievalOrchestrator的local-only路径：active检索→内部candidate inspection→Gate→coverage重算，记录每一步decision和telemetry；candidate验证后已满足时不得进入Web。
5. 定义`WebSearchProvider`并适配Tavily结构化返回；保持旧字符串formatter给关闭开关路径。Agentic RAG模式显式拒绝当前provider-native配置。
6. 实现insufficient分支、缺口query和统一run budget；对并行Researcher使用共享semaphore/counter，不能只靠prompt限制。
7. 实现RunEvidenceStore/EvidenceResolver；Web结果先写run-scoped Bundle并验证。writeback开启时复制/upsert为canonical candidate再promotion/quarantine；关闭时只标validated_for_run。阶段2本地candidate走同一validation service。
8. 实现stale/supersede/revalidate、soft delete和审计查询；所有读取默认按`as_of`过滤。
9. 实现两个互斥tool binding：`enable_knowledge_tools=True, enable_agentic_rag=False`时是active-only legacy augmentation；Agentic RAG时orchestrator是唯一Web出口、Tavily走adapter、provider-native配置fail closed。completion与ConductResearch同轮时先执行研究，之后重算coverage。
10. 修复`or True`/partial batch与compression真实retry；输出结构化EvidenceBundle和兼容notes view，显式过滤think/error ToolMessage。
11. 用Requirement提前完成、重复query、冲突/旧版本、失败batch和并行Researcher fixtures完成验收，更新状态并停止。

## 9. 配置和回退

- `enable_knowledge_tools=False`且`enable_agentic_rag=False`：精确恢复阶段0当前Web/MCP工具，不绑定知识工具。
- `enable_knowledge_tools=True`且`enable_agentic_rag=False`：`knowledge_augmented_legacy`，只绑定active+validated knowledge_search/read并保留当前Web；不做local-first、candidate inspection或writeback，用于独立验证PaperQA和阶段7消融。
- `enable_agentic_rag=True`：隐含启用知识读取并切换到唯一governed retrieval；不能与legacy augmentation tool列表同时暴露。
- `enable_knowledge_writeback=False`：可运行 local-first/Web fallback，但 Web 结果只用于当前运行，不持久化 candidate；仍保留 run telemetry。
- `run_evidence_store_backend=memory|sqlite`与`run_evidence_ttl`：默认隔离于knowledge DB；无Checkpoint时至少活到final report，阶段5恢复模式必须使用可重开的sqlite store并在checkpoint保存store ref。过期清理是受控维护，不由Agent调用。
- `requirement_extraction_model`可选；失败时使用完整brief单Requirement。`requirement_completion_policy_version`和必需/可选规则写入trace。
- `agentic_web_provider`第一版只接受通过`WebSearchProvider`contract的实现；OpenAI/Anthropic native值与Agentic RAG同时启用时配置校验失败，而feature flag关闭时仍保留旧行为。
- `knowledge_coverage_threshold`、`min_direct_evidence`、`min_source_authority`、`max_evidence_age_days` 按 domain policy 配置；未知领域不得使用一刀切的法律时效规则。
- `max_web_queries_per_run`、`max_web_results_per_query`、全局并发和 token budget 有程序化上限。
- `knowledge_lifecycle_policy_version` 固定到审计；变更 policy 不回写历史 decision。
- 失败时返回已有 active evidence + 明确缺口/错误；关闭开关回到旧 Web 流程，候选记录保留而不激活。

## 10. 单元测试

- coverage 对充分、不足、过时、低权威、非直接、冲突、无结果的确定性判定；
- Requirement 逐项覆盖，不能用一个主题相关 Evidence 自动覆盖多个无关 Requirement；
- transition matrix 的允许/拒绝、幂等和审计 before/after；
- candidate hash/URL 去重、过期、重复 promotion；
- active/stale/superseded/quarantined/archived/soft-deleted 的默认查询过滤和 `as_of`；
- authority policy 区分官方、论文、企业自述、聚合/未知来源；
- temporal policy 对 `published_at/valid_from/valid_to/retrieved_at`；
- Web budget/semaphore 对并行 Researcher 的全局限制；
- writeback 关闭不写 Repository；
- old config path 的工具清单快照。
- baseline、knowledge_augmented_legacy和agentic三种工具清单；candidate在前两种生产模式均不可见；
- Requirement抽取稳定ID、空/失败降级、必需/可选和completion decision；
- Supervisor partial batch异常保留成功结果，token/非token错误不被无条件吞掉；
- compression使用compression model限制并在裁剪后实际再次调用；
- think/error/overflow ToolMessage不能被提升为EvidenceBundle。
- RunEvidenceStore按run隔离、ID解析、TTL、同run citation链和跨run不可见；writeback关闭不产生canonical行。

## 11. 集成测试

- fake KB 充分：Researcher 调 governed tool，`web_call_count=0`，返回 active evidence；
- fake KB 不足：只为missing aspects调fake Web；writeback开启时candidate验证后active，关闭时仅transient且当前run可受控使用；
- 第二次相似查询命中 active evidence，不再调 Web或调用数严格下降；
- 低质量/错误来源进入 quarantined，不出现在默认检索；
- 阶段2导入candidate在验证前不被Agent召回；orchestrator内部inspection发现相关candidate后先验证，验证通过则active并重新计算coverage，若已充分则`web_call_count=0`；
- 旧版本在新版本激活时变为 superseded/stale，`as_of=过去` 仍可审计读取；
- 两个 Researcher 同时发现同一 URL/内容，只生成一个 candidate/version和确定性 refs；
- provider-native search配置在Agentic RAG模式fail closed，关闭模式保持旧绑定；
- Supervisor提前`ResearchComplete`但仍有必需缺口时继续研究；同轮还含ConductResearch时不丢任务。Researcher的ResearchComplete与governed retrieval/普通安全tool同轮时也先执行工具，再决定compress/结束；预算耗尽后以明确gaps结束；
- 一个并行Researcher失败时保留其他成功Evidence；compression token超限fixture触发真实第二次调用；
- legacy notes含think/error时，结构化evidence handoff不包含这些消息；
- writeback关闭后同run Claim/report仍能经EvidenceResolver回溯transient Evidence→Chunk→Version snapshot→Source；新run无法检索该Bundle；
- 开关关闭运行阶段 0 baseline/research limit 回归。
- knowledge_augmented_legacy能在不启用Agentic RAG/writeback时读取active PaperQA evidence，同时保留legacy Web；全部开关关闭时无知识工具。

## 12. 阶段验收测试

- **T3-1**：本地 active Evidence 满足全部 Requirement 时，整个请求的 Web/search provider 调用数为 0。
- **T3-2**：任一 Requirement 缺少直接、有效证据时，只对记录的 missing aspect 发起 Web 查询，且不超过全局预算。
- **T3-3**：所有新Web结果先成为candidate；writeback开启时先持久化candidate Version，关闭时只产生run-scoped transient candidate，均不存在Web结果→直接active旁路。
- **T3-4**：只有 validation 全部满足 policy 的候选可转 `active`；每次转换有 policy version、reason 和 actor 审计。
- **T3-5**：错误、低质量或无法验证来源进入 `quarantined`，默认检索和 notes view 都不返回。
- **T3-6**：过时/被替代内容不作为当前事实返回，但按历史 `as_of` 和审计接口仍可追溯。
- **T3-7**：`enable_knowledge_writeback=True`时，同一/相似query首轮candidate已promotion为active；第二个独立run命中canonical active Evidence，Web调用由fixture中的1降为0。writeback关闭的RunEvidenceStore不得用于跨run满足本项。
- **T3-8**：两个并行 Researcher 的同源结果去重，来源/版本/候选各只有一个，引用 ID 一致。
- **T3-9**：Agent 能提出 stale/quarantine/supersede/soft-delete proposal，但没有 hard delete API，底层行仍存在。
- **T3-10**：Agentic RAG开启时Tavily只能经governed adapter；当前OpenAI/Anthropic provider-native配置必须fail closed且无绑定旁路，未来adapter也须通过同一contract。
- **T3-11**：writeback 关闭时不持久化 candidate，失败 Web 结果不伪装成 active evidence；旧流程可回退。
- **T3-12**：`scripts/validate_phase.py --phase 3` 校验状态分布、审计链、Web 计数和全部 T3 evidence。
- **T3-13**：`research_brief`在研究前生成稳定非空RequirementSet；必需Requirement未覆盖且预算未耗尽时Supervisor不能完成，blocked/耗尽时输出明确缺口。
- **T3-14**：Supervisor的`ResearchComplete + ConductResearch`同轮不会丢任务；Researcher的`ResearchComplete + governed retrieval/普通安全tool`同轮也不会丢工具结果。两层均在执行后重算coverage/状态再决定结束或压缩。
- **T3-15**：一个并行Researcher失败不吞掉其他成功结果；异常类型可见，既有`or True`不再存在。
- **T3-16**：compression在token裁剪后用正确compression model真正重试；配置次数与实际调用次数有测试。
- **T3-17**：think/reflection、错误和超限ToolMessage不会进入结构化EvidenceBundle或引用输入，只保留在诊断/legacy trace。
- **T3-18**：阶段2本地candidate在验证前不可引用；orchestrator先内部召回并经同一Gate处理，通过后active并重算coverage。该candidate足以覆盖Requirement时Web调用为0，失败则quarantined后才允许按缺口Web。
- **T3-19**：writeback关闭时canonical Knowledge Repository零新增，但同run validated Evidence IDs可经RunEvidenceStore完整回溯并供报告；另一run不可见，TTL/清理有审计。
- **T3-20**：非Agentic `knowledge_augmented_legacy`只暴露active+validated knowledge_search/read与legacy Web，不召回candidate、不执行local-first/writeback；所有知识开关关闭时工具清单与Baseline完全一致。

## 13. 验收命令

```powershell
conda run --no-capture-output -n open-deep-research python -m pytest tests/unit/knowledge/lifecycle tests/unit/knowledge/retrieval/test_coverage.py -q
conda run --no-capture-output -n open-deep-research python -m pytest tests/unit/research -q
conda run --no-capture-output -n open-deep-research python -m pytest tests/integration/agentic_rag -m "not live" -q
conda run --no-capture-output -n open-deep-research python -m pytest tests/test_research_limits.py tests/integration/knowledge -q
conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 3
conda run --no-capture-output -n open-deep-research python -m ruff check src/open_deep_research/knowledge src/open_deep_research/evidence/run_store.py src/open_deep_research/research src/open_deep_research/tools src/open_deep_research/state.py src/open_deep_research/configuration.py src/open_deep_research/deep_researcher.py src/open_deep_research/prompts.py tests/unit/knowledge tests/unit/research tests/integration/agentic_rag
conda run --no-capture-output -n open-deep-research python -m mypy src/open_deep_research/knowledge src/open_deep_research/evidence/run_store.py src/open_deep_research/research src/open_deep_research/tools
git diff --check
```

默认全部使用 fake Web/provider，不运行 Tavily 或模型。若需真实 Web 验证，必须单独获得费用和数据写回授权，并使用隔离测试库。

## 14. 完成定义

T3-1至T3-20全部通过；brief→Requirement与Supervisor completion受程序门禁；Baseline/knowledge-augmented legacy/Agentic三种工具模式可机械区分；local-first是程序化唯一Web出口而非prompt建议；本地/Web candidate使用同一Gate；canonical DocumentVersion六态与RunEvidenceStore边界清晰；合法transition、soft delete、candidate validation和append-only audit有测试；重复查询减少Web调用；并行去重/部分失败与compression恢复有效；诊断ToolMessage不污染Evidence；旧路径可通过默认关闭开关恢复；无阶段4+实现；状态evidence完整。

## 15. 风险与降级方案

- **API兼容**：现有provider-native search是模型侧server tool，不能假装成普通tool包装；第一版在Agentic RAG模式显式拒绝，用户改用Tavily governed adapter或关闭新模式。
- **Token成本**：Coverage/validation 若全用 LLM 会放大成本；第一版硬规则 + 可选辅助模型，缓存评估并限制 token。
- **并发**：当前限制多为每节点；新增 run-scoped semaphore/counter，Repository transaction 去重。
- **误激活**：来源看似权威但不直接支持；directness 和 conflict 必须单独门禁，灰区保留 candidate。
- **时效**：不同领域期限不同；policy version/领域配置，未知时标 `not_checkable` 或要求 Web，不伪造当前性。
- **写回失败**：报告可使用当前已验证 EvidenceHit，但标记 persistence failure，重试幂等；不能声称已缓存。
- **Windows/SQLite**：高并发写锁；短事务、busy timeout、队列化 promotion，DB 与索引分开。
- **回退**：关闭 Agentic RAG/writeback，保留候选与审计，恢复当前 Web 工具和阶段 2 本地工具。

## 16. 本阶段 Codex 执行指令

```text
你现在只执行 doc/development_plan/phase_3_agentic_rag_lifecycle.md；先验证阶段 2 completed 且 T2 全部有 evidence，否则停止，不得进入阶段 4。

先读取 AGENTS.md、三个状态文件、本目录总览/架构/参考/协议/本阶段文档、阶段 1–2 knowledge/evidence/repository/parser/retriever/adapter 全部实现与测试、configuration.py、state.py、deep_researcher.py 的 Supervisor/Researcher/tool routing、utils.py 的 Tavily/native/MCP/get_all_tools、prompts.py 和阶段 0 telemetry。定点参考 PaperQA retrieve/evidence 与 LangGraph conditional/reducer 代码。先 git status --short 并保留用户改动。

允许范围：Requirement/completion gate、RunEvidenceStore、active-only knowledge_augmented_legacy工具模式、GovernedRetrievalOrchestrator、coverage/gap、结构化Web adapter、candidate validation、六态transition、proposal/soft delete/audit、run-scoped budget、默认关闭配置、最小工具路由/state/prompt与恢复缺陷修复、相关tests/scripts/状态文件。禁止实现Filesystem/Knowledge MCP、Memory、Checkpoint、Claim/Citation Validator、报告修复、新Web provider或数据库技术；禁止全面重写Supervisor/Researcher、hard delete和prompt-only gate。

必须实现brief→稳定RequirementSet和Supervisor completion gate；必需缺口未覆盖且预算仍有时不能结束，同轮ResearchComplete不能丢ConductResearch。实现active-only knowledge_augmented_legacy供PaperQA消融；Agentic RAG开启时不得有Web旁路：Tavily只走governed adapter，当前OpenAI/Anthropic provider-native配置fail closed；local evidence充分时Web调用严格为0。本地/Web candidate走同一Gate；Web结果先进入按run隔离且可由ID解析的RunEvidenceStore，writeback开启才进入canonical candidate。修复`or True`部分失败和compression真实retry，过滤think/error ToolMessage。使用fake完成第10、11节测试并逐项执行T3-1至T3-20；未经授权不得调用真实搜索或模型。所有知识开关关闭必须回到baseline。

完成后更新 feature_list.json、progress.md、session-handoff.md，报告修改、transition/coverage policy、每项验收、命令/退出码、并发/成本/回退和最终 git status。完成后立即停止，不得自动开始阶段 4。
```
