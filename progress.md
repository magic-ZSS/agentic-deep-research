# 会话进度记录

## 当前状态

**最后更新：** 2026-07-21

**当前功能：** `phase-7-evaluation-showcase-001`

**状态：** in-progress（离线 smoke 已完成；等待 full evaluation 费用授权）

## 阶段门禁

- 阶段 0–5 均保持 `completed`。
- 开始阶段 6 前运行 `scripts/validate_phase.py --phase 5`，退出码 0；T5-1 至 T5-16 全部 PASS。
- 阶段 6 最终运行 `scripts/validate_phase.py --phase 6`，退出码 0；T6-1 至 T6-18 全部 PASS。
- 未下载 STORM、OpenFactVerification 或 FIRE；未调用真实模型、Web、LangSmith、Deep Research Bench 或 LLM Judge。

## 阶段 7 离线交付（2026-07-21）

- `tests/baseline/cases.jsonl` 继续作为唯一 prompt/Requirement 来源；`goldens.v1.jsonl` 仅保存 supplemental full-eval 字段。
- 固定五组公平消融 manifest，并用实际 `get_all_tools` registry 校验 variant-specific expected tools。
- 实现版本化 Experiment/Metric/Telemetry/Artifact schema、trace adapter、DeepEval lazy full metrics、自定义 Citation/Source/Memory/Cost 指标和只读统一 scorer。
- smoke runner 生成 45 条结构记录及 `runs.jsonl`、`report.json`、`report.md`、`experiment.json`、`manifest.json`；README 表格由机器 report 生成。
- 无网络 evaluation suite：57 passed、1 deselected；生命周期/Memory 回归：25 passed；新增范围 Ruff 和 8-file 增量 Mypy 通过。
- `validate_phase --phase 7`：T7-1/2/5/7/8/10-17 PASS；T7-3/4/6/9 缺少用户授权 live/full evidence，按设计 FAIL，退出码 1。
- 完整 `mypy src/open_deep_research/evaluation` 仍被 Phase 0 既有 typing 与跨模块历史错误阻塞（138 errors）；未以新增阶段结果冒充通过。

## Full 授权点

- 固定子集：`simple-001`、`medium-001`、`complex-001`。
- 五 variants × 3 repeats = 45 主研究运行；`complex-001` 的 baseline/agentic/full 各增加 3 次 warm，共 54 次研究运行。
- 模型：研究链 GPT-4.1（summarization/judge 为 GPT-4.1 mini）；7 个 DeepEval metric；预计 756–972 次 Judge 模型调用、540–2700 次研究链模型调用、54–810 Tavily basic credits。
- 估算总费用 USD 30–100。该区间依赖实际 token/工具循环，不是保证价格；用户授权前不得安装 DeepEval 或启动 full。

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

阶段 7 保持 `in-progress`。等待用户明确批准上述 full 配置与费用上限；未批准不得运行、不得把 T7-3/4/6/9 标为通过。
