# 会话进度记录

## 当前状态

**最后更新：** 2026-07-21

**当前功能：** `phase-6-citation-validation-001`

**状态：** completed（阶段 6 已收口；阶段 7 未开始）

## 阶段门禁

- 阶段 0–5 均保持 `completed`。
- 开始阶段 6 前运行 `scripts/validate_phase.py --phase 5`，退出码 0；T5-1 至 T5-16 全部 PASS。
- 阶段 6 最终运行 `scripts/validate_phase.py --phase 6`，退出码 0；T6-1 至 T6-18 全部 PASS。
- 未下载 STORM、OpenFactVerification 或 FIRE；未调用真实模型、Web、LangSmith、Deep Research Bench 或 LLM Judge。

## 阶段 6 交付

- `src/open_deep_research/reporting/`：checkpoint-safe Draft/Section/AtomicClaim/link/result/patch/registry/artifact 模型，确定性 claim extraction、hash-guarded section-local repair、按 `(source_id, version_id)` 构建的 registry 与程序化 renderer。
- `src/open_deep_research/evidence/validation/`：canonical 与同 run transient EvidenceResolver、claim-local retriever、可注入 entailment evaluator，以及 directness/temporal/authority/numeric 硬门禁。
- `configuration.py`：新增默认关闭的 `citation_validation_mode=off|audit|enforce` 及版本化 policy 配置。
- `state.py`：仅新增轻量 citation artifact/ID/registry 引用字段，保留 `notes/raw_notes/compressed_research`。
- `deep_researcher.py`：legacy Writer 后最小挂接独立 `citation_validation` 节点；`off` 为 no-op，`audit` 保留原报告，`enforce` 依赖缺失时 fail closed。
- `scripts/validate_report.py` 与 `scripts/validate_phase.py --phase 6`：验证 registry 双向一致、时间有效性、失败 claim、路径去敏及 T6 evidence。
- `tests/unit/evidence/validation/`、`tests/unit/reporting/`、`tests/integration/citation_pipeline/`：全部使用 InMemory/fake/deterministic fixtures。

## 处置与安全决策

- `fully_supported` 保留；`partially_supported` 限定表达；`unsupported`、`contradicted`、`not_checkable` 在默认 enforce policy 下移除，或按显式配置标记证据不足。
- 显式 Draft citation 必须独立通过；supplemental Evidence 不能覆盖或洗白显式错误，只能触发后续 repair/rebind。
- corporate self-report 只可支持明确归因的企业宣称，不可提升为行业事实。
- registry 只接收 accepted links，编号在全部验证完成后确定性生成；同 source/version 合并 locator，不同 version 不合并。
- 本地 Source 只渲染 public alias/root-relative locator；Windows 绝对路径、blob/internal storage ref 不进入报告或 artifact。
- 所有新功能默认关闭；关闭后 Writer 输出不被 citation node 修改。

## 最终验证证据

- Phase 5 前置门禁：退出码 0，T5-1～T5-16 PASS。
- Phase 6 unit：20 passed，退出码 0。
- Phase 6 integration：9 passed，退出码 0。
- `scripts/validate_report.py --input tests/fixtures/citations/valid_report.json`：VALID，退出码 0。
- Phase 6 validator：T6-1～T6-18 PASS，内部 29 tests，退出码 0。
- 阶段 3/5 回归：35 passed，退出码 0。
- Ruff：All checks passed，退出码 0。
- Mypy：18 source files 无问题，退出码 0；对既有未类型化 configuration/knowledge/run-store 使用显式增量 overrides，新 Phase 6 模块仍严格检查。
- `git diff --check`：退出码 0。
- 测试仅出现既有 Pydantic/LangGraph deprecation warnings，无测试失败。

## 风险与回退

- 当前默认 entailment fallback 是保守的确定性 token overlap；真实模型 adapter 和质量评测留待后续明确授权/阶段 7。
- `citation_validation_mode=off` 可立即恢复 legacy Writer；`audit` 可在不改报告的情况下观察 artifact。
- `enforce` 在 pipeline 未注入时 fail closed，不会静默退回未经验证的确定性结论。
- 当前 claim extraction 是确定性句子/分号拆分；复杂 AST、跨句指代和真实模型质量不在本阶段承诺内。

## 下一步

阶段 6 已满足完成定义并停止。只有用户明确要求阶段 7，并重新核验本页 T6 evidence 后，才可执行 `doc/development_plan/phase_7_evaluation_and_showcase.md`。
