# 阶段 6：引用验证与报告局部修复

## 1. 阶段目标

把当前单步 Writer 升级为受证据治理的报告流水线：先生成 Draft，再抽取原子 Claim，检索并绑定直接 Evidence，验证蕴含、时效和来源权威性，按五类状态处置失败 Claim，只局部修复受影响文本，最后由程序统一生成正文来源编号和来源表。完成后 Unsupported Claim 不进入最终报告，正文引用与来源表可机械验证且跨 Researcher 不冲突。

## 2. 为什么此阶段现在做

阶段 1–3 已建立 Requirement—Evidence—Chunk—Version—Source 和有效性治理，阶段 5 提供可恢复的运行/验证状态。本阶段才能对 Writer 的实际结论做 Claim 级验证，而不是继续依赖 Researcher 自律或自由文本 `notes`。阶段 7 将以此流水线输出计算 Citation Fidelity、Source Quality 和消融对比。

## 3. 范围

- 定义 DraftReport、ReportSection、AtomicClaim、ClaimEvidenceLink、ValidationResult、RepairPatch 和 SourceRegistry；
- 从 Draft 中抽取最小可验证 Claim，并映射到 Requirement/段落/字符或 AST 位置；
- 为每个 Claim 检索 active Evidence，禁止仅因主题相关就判为支持；
- 实现 `fully_supported/partially_supported/unsupported/contradicted/not_checkable`；
- 独立验证 citation entailment、证据直接性、版本/有效时间和 source authority；
- 识别旧版本法律/规范、企业自述泛化、无依据数字和一源多机制误配；
- 处置策略：fully 保留；partial 降低/限定表述；unsupported 删除或显式标为证据不足；contradicted 修正/并列冲突；not_checkable 删除或明确标注不可核验；
- Repair 只编辑失败 Claim 所在局部，保持其他 section/hash 不变；
- 程序按稳定 `(source_id, version_id)` citation key去重、排序、分配`[1]`等编号并渲染来源表；同一Version的多个locator可共号，旧/新Version必须是可区分条目；
- Draft阶段只允许使用稳定Evidence/Source/Version占位符，不接受Researcher自由文本中的局部`SOURCE n`/`[n]`作为最终身份；legacy编号先剥离并通过URL/ID映射，无法映射时视为未绑定；
- 在 LangGraph 中新增 draft→extract→validate→repair→render 节点或等效子图，保留旧单步 Writer 路径；
- 验证过程支持 checkpoint/idempotent resume，保存结构化审计结果。

## 4. 非目标

- 不重新设计 Supervisor/Researcher 研究规划，不添加新搜索 provider；
- 不实现完整通用事实核查平台或知识图谱；
- 不把 DeepEval Faithfulness 当生产 Citation Validator；阶段 7 才运行完整评测；
- 不要求每个主观建议都强行引用；应标 `not_checkable` 并按报告规则处理；
- 不让 Writer 覆盖 Validator 的 unsupported/contradicted 状态；
- 不允许全篇无差别重写作为“局部修复”；
- 不由模型自由生成最终来源编号/来源表；
- 不把本地Source的internal storage ref、Windows绝对路径或真实Allowed Root渲染到报告；
- 不自动修改旧知识状态，发现 stale/conflict 时只走阶段 3 proposal/service；
- 不默认克隆 STORM/OpenFactVerification/FIRE；只有明确设计缺口时按需参考和记录。

## 5. 当前项目修改点

预计新增：

- `src/open_deep_research/evidence/claims.py`、`claim_extractor.py`；
- `src/open_deep_research/evidence/claim_retrieval.py`、`entailment.py`、`temporal.py`、`authority.py`；
- `src/open_deep_research/evidence/citation_validator.py`、`repair.py`、`source_registry.py`；
- `src/open_deep_research/reporting/models.py`、`pipeline.py`、`rendering.py`；
- `tests/fixtures/citations/`：错误映射、旧版本、企业宣称、数字、冲突和局部修复 fixtures；
- `tests/unit/evidence/validation/`、`tests/unit/reporting/`、`tests/integration/citation_pipeline/`；
- `scripts/validate_report.py`。

预计修改：

- `deep_researcher.py`：保留 legacy `final_report_generation`，新增或调用独立 reporting pipeline 节点；不得把实现堆入本文件；
- `state.py`：新增 draft/report sections、claim IDs、validation summary、source registry ref 等轻量/可 checkpoint 字段；
- `prompts.py`：拆分 draft、claim extraction、repair prompt，硬规则仍由代码执行；
- `configuration.py`：`citation_validation_mode=off|audit|enforce`、模型/timeout/token/authority policy；
- 阶段 1/3 Evidence/Audit Repository：新增 Claim/link/validation/proposal 存储或独立 Repository；
- `scripts/validate_phase.py` 和状态文件。

## 6. 参考仓库

- **PaperQA2**：参考 `Context`、source text、score、context serialization/citation格式和 contextual summary；只作为 Evidence 候选，不使用 `aquery` 回答或其局部 Context ID作全局编号。Apache-2.0，使用阶段 2 Adapter。
- **DeepEval**：参考 Faithfulness 和 community citation metric 的输入/结果设计；不用于生产 gate，不替代时间/权威/Claim—Source精确映射。Apache-2.0，阶段 7再作为评测依赖。
- **LangGraph**：使用独立节点、checkpoint 和 reducer 支持局部验证/恢复；不在节点重放时重复写 audit/repair。
- **可选 STORM/OpenFactVerification/FIRE**：只有现有设计不能覆盖 claim decomposition/verification 时，经用户同意浅克隆到 `doc/reference/`、固定 SHA/许可证后定点参考。允许借鉴 schema/评测场景，不嵌入它们的完整 Agent/流水线，不直接复制不明许可代码。
- **当前 prompts**：现有 Researcher/Writer 已包含引用建议，但这些仅作 legacy兼容；程序验证和 registry 是权威。

复用/许可规则：PaperQA2/DeepEval均只借鉴Apache-2.0公共类型/metric模式或经既有Adapter使用，LangGraph经MIT公共API编排；默认不复制上游实现。任何可选事实核查仓库在锁定commit和许可证前只允许记录`[TODO]`，不得直接复用代码或数据集。

## 7. 数据结构和接口

```text
AtomicClaim
  claim_id, requirement_ids, section_id, text,
  span_start/span_end or ast_path, claim_type,
  subject, predicate, object/value, temporal_scope,
  cited_evidence_ids, cited_citation_keys=(source_id,version_id),
  extraction_version

ClaimEvidenceLink
  link_id, claim_id, evidence_id, relation=supports|contradicts|context,
  origin=explicit_draft_citation|supplemental_retrieval,
  entailment_score, directness, temporal_status,
  authority_status, rationale, validator_version

ValidationResult
  claim_id, status=fully_supported|partially_supported|unsupported|
  contradicted|not_checkable, links, failed_checks,
  required_action, confidence, audit_id

RepairPatch
  patch_id, section_id, original_hash, target_claim_ids,
  replacement_text, preserved_claim_ids, reason, applied_at

SourceRegistryEntry
  citation_key=(source_id, version_id), source_id, version_id,
  display_number, title, publisher,
  canonical_uri, version/published/retrieved dates,
  locators_used
```

核心接口：

```text
ClaimExtractor.extract(draft, requirements) -> list[AtomicClaim]
ClaimEvidenceRetriever.retrieve(claim, as_of, run_id) -> list[Evidence]
EvidenceResolver.resolve(run_id, evidence_id) -> canonical | same-run transient Evidence
CitationValidator.validate(claim, evidence) -> ValidationResult
ReportRepairer.repair(section, failed_results) -> RepairPatch
SourceRegistry.build(validated_claims) -> registry
ReportRenderer.render(sections, registry) -> final_report
```

最终编号仅由`SourceRegistry`决定；同一Source的同一Version多locator共用编号，同一Source的不同Version分配不同citation key/条目，正文marker保留locator。`canonical_uri`是公开URI/alias，不得使用internal storage ref。报告parser/renderer应用AST或稳定section/span，不用全局字符串替换。

## 8. 执行步骤

1. 定义 Claim/link/result/patch/registry schema、状态处置表和 deterministic fixtures，先写 validator/renderer contract tests。
2. 把legacy writer包成`generate_draft`，在`off`模式保持字节/语义兼容；enforce/audit模式把结构化EvidenceBundle渲染为稳定`evidence_id/source_id/version_id/chunk locator`占位符，剥离/映射legacy局部数字编号，过滤think/error ToolMessage；新增reporting module而非扩张`deep_researcher.py`。
3. 实现claim extraction adapter：模型structured output + deterministic normalization/ID；拆分并列数字、机制和时间断言，保留span/section以及稳定占位符关联。
4. 实现Claim→Evidence retrieval：通过阶段3EvidenceResolver解析canonical或同run transient Evidence；先原样验证Draft显式引用的Evidence/(Source,Version)，再单独记录supplemental active evidence。补充Evidence不能把错误原始引用伪装成正确，只能触发repair后重新绑定。每个Claim独立，不继承邻句引用。
5. 实现 deterministic prechecks（存在、状态、版本、时间、authority、直接性）和可注入 entailment model；fake 测试覆盖五类状态。
6. 实现处置 policy和 stale/quarantine proposal；Writer不能把 failed status改成 fully。
7. 实现局部 RepairPatch，应用前校验 `original_hash`；只重验证被改 Claim和相邻引用，不重写已通过 section。
8. 实现 SourceRegistry/renderer；从 validated links程序生成编号、正文 markers和来源表，检查零孤儿/零缺项。
9. 把 pipeline 作为 feature-gated子图/节点接到主图；`audit` 只记录不改输出，`enforce` 输出修复报告，`off`走旧 Writer。
10. 完成中断恢复、并行 Researcher source冲突、全部 fixtures和回归验收，更新状态并停止。

## 9. 配置和回退

- `citation_validation_mode: off|audit|enforce = off`；默认旧 Writer。
- `citation_entailment_model`、`claim_extraction_model`、`report_repair_model` 可独立配置，含 retry/timeout/token 上限；测试注入 fake。
- `citation_min_entailment`、`citation_require_temporal_validity`、`source_authority_policy` 和 `unsupported_action` 有版本化配置。
- `audit` 模式生成机器 validation artifact但返回原 draft，便于上线前观察。
- `enforce` 失败时默认 fail closed：保留已验证 sections，对失败部分给明确“证据不足”；不可静默回 legacy确定性结论。
- 完全回退使用 `off`；旧 notes→Writer路径和输出 schema保留。Claim/Audit 数据不删除。

## 10. 单元测试

- 原子 Claim 拆分：并列机制、多个数字、条件/否定、时间范围、主观句；
- Claim stable ID、span/AST定位、重复和 section边界；
- citation 指向主题相关但不直接支持、同源支持多个无关机制、错误 source ID；
- 显式旧Version/错误citation key与supplemental新Version分开记录，不能用补充检索覆盖原始错误；
- fully/partial/unsupported/contradicted/not_checkable 五类判定；
- 旧法律/规范版本、valid_to、未来/未知日期；
- 企业自述只支持“企业声称”，不能提升为行业事实；
- 无依据数字、范围/单位不一致、摘录断章取义；
- authority/directness/entailment分别失败，不能用总分掩盖硬失败；
- RepairPatch hash、最小 span、冲突 patch、幂等应用；
- SourceRegistry按(source,version)稳定编号、跨Researcher去重、同Version locator合并、双Version区分、零孤儿；
- legacy局部SOURCE编号剥离/映射、无法映射拒绝，以及think/error消息过滤；
- 本地Source只渲染public alias/root-relative locator，不泄漏internal path；
- `off/audit/enforce` 配置和错误降级。

## 11. 集成测试

- draft 含错误 Claim—Source 映射，pipeline 标为 unsupported/contradicted并只修复该句；
- 旧版本法规与新要求，temporal validator拒绝旧 Evidence并选择新 Version或标证据不足；
- 企业宣称被修为归因表述，其他已验证段落 hash不变；
- 无依据数字删除/标注，未引入新数字；
- 两个Researcher都使用局部`[1]`时，结构化(source,version) citation keys最终生成唯一连续registry；
- 同一canonical URL的旧/新Version同时出现在draft时生成两个可区分registry条目，并让temporal validator只接受正确Version；
- checkpoint 中断于 validation/repair 后恢复，不重复编号/audit/patch；
- audit 模式输出原 draft + validation artifact；off模式走阶段 0 baseline；
- final renderer正文 markers 与 source table 双向一致。

## 12. 阶段验收测试

- **T6-1**：fixture 中主题相关但不直接支持的 Claim—Source 映射被识别为 partial/unsupported，而非 fully supported。
- **T6-2**：旧版本法律/规范不能支持新时点结论；新旧 Version 和 `as_of` 判断可追溯。
- **T6-3**：企业来源的自述被保留为“该企业声称”或降级，不能渲染为无归因行业事实。
- **T6-4**：无 Evidence 的数字被删除或明确标注证据不足，最终报告不保留确定性数字。
- **T6-5**：`unsupported` Claim 不进入 enforce 最终报告；`contradicted/not_checkable/partial` 按处置表处理。
- **T6-6**：一个 Source 不能自动支持多个无关机制；每个 Atomic Claim 都有独立 link/result。
- **T6-7**：正文每个来源编号恰好对应 registry一项，registry每项至少被正文引用，编号连续稳定，错误率为 0。
- **T6-8**：并行Researcher的局部来源编号不会泄漏到最终编号；相同(source,version)全局去重，不同Version不合并。
- **T6-9**：局部修复只改变失败 Claim 所在 section/span，其他通过 section 的 canonical hash不变。
- **T6-10**：Validator 的五类结果、failed checks、Evidence/Chunk/Version/Source链和 policy version全部持久可审计。
- **T6-11**：`off` 模式与 legacy Writer回归一致；`audit` 不改报告；`enforce` fail closed且可恢复。
- **T6-12**：`scripts/validate_report.py` 对合法报告返回 0，对孤儿引用、缺来源、旧版本和 unsupported Claim返回非 0。
- **T6-13**：`scripts/validate_phase.py --phase 6` 校验全部 T6 evidence。
- **T6-14**：同一Source的两个DocumentVersion同时出现时使用不同citation key/来源条目；同Version多页/标题locator仍可合并，时效判断无歧义。
- **T6-15**：Draft输入的legacy局部`SOURCE n/[n]`被剥离并映射到稳定占位符；无法映射的编号和think/error ToolMessage不能成为引用。
- **T6-16**：本地Source在正文、来源表、validation artifact和日志中只显示public alias/root-relative locator，不泄漏Windows绝对路径或blob storage ref。
- **T6-17**：Draft显式引用的旧/错误citation key保留`explicit_draft_citation`验证结果；补充检索的新Evidence标为supplemental，不能覆盖或洗白原始错误，只能经RepairPatch重新绑定。
- **T6-18**：writeback关闭时，同run transient Evidence可通过EvidenceResolver完成Claim→Chunk→snapshot→Source验证；其他run无法解析，过期/缺失时fail closed为unsupported/not_checkable。

## 13. 验收命令

```powershell
conda run --no-capture-output -n open-deep-research python -m pytest tests/unit/evidence/validation tests/unit/reporting -q
conda run --no-capture-output -n open-deep-research python -m pytest tests/integration/citation_pipeline -m "not live" -q
conda run --no-capture-output -n open-deep-research python scripts/validate_report.py --input tests/fixtures/citations/valid_report.json
conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 6
conda run --no-capture-output -n open-deep-research python -m ruff check src/open_deep_research/evidence src/open_deep_research/reporting tests/unit/evidence/validation tests/unit/reporting tests/integration/citation_pipeline scripts/validate_report.py
conda run --no-capture-output -n open-deep-research python -m mypy src/open_deep_research/evidence src/open_deep_research/reporting
conda run --no-capture-output -n open-deep-research python -m pytest tests/test_research_limits.py tests/integration/agentic_rag tests/integration/memory -q
git diff --check
```

默认 fake extraction/entailment/repair model。真实模型质量测试留阶段 7，未经授权不得调用。

## 14. 完成定义

T6-1至T6-18全部通过；Claim级五类验证、canonical/同run transient解析、显式与supplemental证据区分、时效/权威/直接性、局部修复和按(source,version)程序化registry有确定性测试；legacy编号/诊断消息不能污染引用，本地路径已去敏；Unsupported不进入enforce报告，引用双向一致且错误率零；pipeline可checkpoint恢复；off/audit/enforce回退明确；实现位于独立模块而非堆进`deep_researcher.py`；状态evidence完整。

## 15. 风险与降级方案

- **模型误判**：Claim extraction/entailment非确定；先做硬预检和 structured schema，低置信度 `not_checkable/needs_review`，不强行 fully。
- **Token成本**：按 section/batch检验、只对候选 links调用模型、缓存 claim/evidence hash、严格 token/并发上限。
- **修复漂移**：使用 AST/span + original hash，patch后重验目标 Claim；hash不符拒绝应用，不全篇重写。
- **时效规则**：领域差异；policy版本化，未知时不假定有效，可提 stale proposal。
- **来源权威**：企业/博客并非全无效；允许支持“谁声称什么”，但不能越权泛化。
- **并发/编号**：编号在所有验证完成后按(source_id,version_id)确定性构建，不依赖Researcher到达顺序；不同Version不合并。
- **恢复**：节点重放造成重复 audit/patch；用 claim/policy/original hash构造幂等 key。
- **回退**：先 `audit` 观察，再 `enforce`；紧急关闭为 `off`，保留 validation artifacts。

## 16. 本阶段 Codex 执行指令

```text
你现在只执行 doc/development_plan/phase_6_citation_validation.md；先验证阶段 5 completed 且所有 T5 有 evidence，否则停止，不得进入阶段 7。

先读取 AGENTS.md、状态文件、本目录总览/架构/参考/协议/本阶段文档、deep_researcher.py 的 final_report_generation和图边、prompts.py 的 compress/final report prompt、state.py、configuration.py、阶段 1–5 Requirement/Evidence/Source/Version/Lifecycle/Checkpoint实现和测试。定点参考 PaperQA Context/serializer、DeepEval faithfulness/citation metric schema和 LangGraph节点/恢复模式；只有确认设计缺口并获得许可后才可下载 STORM/OpenFactVerification/FIRE。先 git status --short并保留用户改动。

允许范围：Draft/Section/AtomicClaim/link/result/patch/registry模型，claim extraction、evidence retrieval、entailment/temporal/authority validator、局部 repair、程序化编号/render、off/audit/enforce配置、独立 reporting子图/最小主图挂接、tests/scripts/状态文件。禁止重写 Supervisor/Researcher、添加搜索 provider、完整事实核查平台、DeepEval full eval、模型自由编号、硬删/直接改知识状态或把实现堆入 deep_researcher.py。

先用deterministic fixtures/fake model完成第10、11节测试并逐项执行T6-1至T6-18。硬性证明unsupported不进入enforce报告、canonical/同run transient Evidence均能受scope解析、旧版本/企业宣称/数字被正确处置、每个Claim独立绑定Evidence、显式错误引用不能被supplemental证据洗白、registry按(source,version)编号且双向一致为零错误、legacy局部编号/诊断消息不污染引用、本地路径不泄漏、局部修复不改变其他section hash；off必须回到legacy Writer。未经明确授权不得调用真实模型。

完成后更新 feature_list.json、progress.md、session-handoff.md，报告修改、处置规则、每项验收、命令/退出码、成本/回退和最终 git status。完成后立即停止，不得自动开始阶段 7。
```
