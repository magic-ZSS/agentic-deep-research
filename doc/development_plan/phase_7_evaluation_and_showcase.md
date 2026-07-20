# 阶段 7：完整评测、消融实验与项目展示

## 1. 阶段目标

在阶段 0 统一 baseline/telemetry 基础上建立 DeepEval 回归体系，覆盖 Agent、RAG、Citation、Memory、成本与耗时；对简单/中等/复杂任务运行固定消融矩阵，产出机器可读报告、统计摘要和可复现 Demo/README 展示。完成后能量化证明完整系统不降低任务完成率、提升复杂任务引用忠实度，并在重复查询中减少 Web 调用和 token。

## 2. 为什么此阶段现在做

阶段 0 已固定原始行为和数据结构，阶段 1–6 分别交付可独立测量的能力。只有全部功能稳定后，消融组合、Memory复用、知识失效和 Citation Validator指标才具有可比性。本阶段是发布/求职展示门禁；如果指标失败，应回到能力所属阶段另开修复任务，而不是在评测阶段混入功能重构。

## 3. 范围

- 固化版本化 EvaluationDataset/Golden，覆盖 simple/medium/complex、重复查询、旧知识、冲突证据、工具失败和记忆隔离；
- 统一 DeepEval `LLMTestCase/ToolCall/RetrievedContextData` adapter 与 LangGraph callback/项目 telemetry；
- smoke：默认无网络/无 Judge，覆盖 schema、工具轨迹、引用一致性、状态/Memory硬规则和成本字段；
- full：显式授权后运行 Task Completion、Tool Correctness、Step Efficiency、Plan Adherence、Faithfulness、Contextual Precision/Recall；
- 自定义 Source Quality、Citation Fidelity、Temporal Validity、Requirement Coverage、Memory Reuse/Isolation/Staleness 和 Cost/Tool指标；
- 对所有variant（包括没有阶段6结构化Claim产物的Baseline）运行同一个**evaluation-only** claim/citation scorer；它只解析/评分、不修复输出，确保Citation指标口径一致；
- 消融矩阵：Baseline；Baseline+PaperQA2；+Agentic RAG；+Memory；+Citation Validator；
- 对同一版本、case、模型/搜索配置和预算执行配对比较，至少 3 个重复 seed/run并保存均值、标准差和原始记录；
- 生成 JSON/JSONL 机器报告、Markdown 摘要、图表数据和 README 展示表；
- 提供一条低成本本地 Demo流程和一条需授权的 full eval命令；
- 保留现有 LangSmith评测为独立可选路径，不让 import/普通 pytest触发。

## 4. 非目标

- 不在本阶段新增知识、Memory、MCP或Citation业务功能；指标暴露缺陷时记录并回到相应阶段修复；
- 不把 LLM Judge结果当唯一验收，确定性安全/编号/隔离规则必须独立硬断言；
- 不要求 Confident AI、LangSmith或任何云端上传；本地 artifact 是权威评测证据；
- 不用不同模型、预算或数据集制造不公平消融结果；
- 不把单次随机运行当改进证据；
- 不开发复杂前端或在线 dashboard；
- 不提交 API key、完整敏感报告或成本账户信息；
- 不运行 full eval，除非用户明确批准具体配置、case数量和预算。

## 5. 当前项目修改点

预计新增：

- `src/open_deep_research/evaluation/deepeval_metrics.py`、`custom_metrics.py`、`trace_adapter.py`、`experiment.py`、`reporting.py`；
- `tests/evaluation/golden_overlays/*.jsonl`、`ablations.yaml`或JSON；overlay只按`case_id`补充expected output/reference source等Judge字段，不复制prompt/Requirement；
- `tests/evaluation/metrics/`、`tests/evaluation/test_smoke_eval.py`、`test_ablation_config.py`；
- `tests/evaluation/full/`（统一 `full_eval` marker）；
- `scripts/run_eval.py`、`scripts/compare_ablations.py`、`scripts/render_eval_report.py`、`scripts/run_demo.py`；
- `artifacts/evaluation/.gitkeep` 或 schema 示例；真实运行 artifact按敏感/体积策略处理；
- `docs/evaluation.md`、`docs/demo.md`。

预计修改：

- 阶段 0 `evaluation/models.py`、telemetry/DeepEval adapter和 baseline dataset；
- `pyproject.toml`：固定 `eval` extra 和 pytest markers；
- `README.md`：只写经 evidence支持的架构、运行命令和结果表，不覆盖原项目历史说明；
- `tests/run_evaluate.py`、`evaluators.py`、`pairwise_evaluation.py`：仅做入口/结果 schema互操作和显式成本门禁；保留 LangSmith可选能力；
- `scripts/validate_phase.py` 和状态文件。

原则上不修改 `deep_researcher.py`、Knowledge/Memory/Citation业务逻辑；若 trace缺字段，只允许观测性 hook且需证明输出不变。

## 6. 参考仓库

- **DeepEval**：使用 `Golden/EvaluationDataset/LLMTestCase/ToolCall/RetrievedContextData`、`CallbackHandler`、Trace/Span、`BaseMetric` 和 pytest/evaluate API。LangGraph 集成位于 `integrations/langchain/callback.py`，不是假想的独立 LangGraph模块。
- Agent full metrics：TaskCompletion（优先 trace）、ToolCorrectness、StepEfficiency、PlanAdherence。PlanAdherence 在无 plan时可能得 1，必须同时有“计划已进入 trace”的确定性检查。
- RAG full metrics：Faithfulness、ContextualPrecision/Recall；后两者需要 expected_output。它们不取代本项目 Claim级 Citation metric。
- DeepEval的某些示例把 token数塞入 `token_cost`；本项目保留明确 token/美元字段。Apache-2.0，使用公共 API和自定义 BaseMetric。
- **LangMem**：只用于评测 proposal/recall轨迹和 procedural gate，不让 optimizer改变实验配置。
- **LangGraph**：trace/checkpoint/thread配置对所有消融保持一致；记录版本和 seed。
- **现有 LangSmith tests**：可作指标定义和历史结果参考，不能与 DeepEval smoke混跑；`tests/expt_results` 缺 telemetry，不可冒充新 baseline。

复用/许可规则：DeepEval通过Apache-2.0公共API与自定义`BaseMetric`扩展，LangGraph/LangMem通过MIT公共API；不复制runner/callback内部实现，不复制参考仓库测试数据。若产物展示第三方内容，另行确认数据许可和去敏。

## 7. 数据结构和接口

```text
EvaluationGolden
  case_id, difficulty, input, expected_output?,
  expected_requirements, expected_tools_by_variant?, reference_sources,
  temporal_context, memory_setup, tags, dataset_version

ExperimentVariant
  variant_id, feature_flags, model_config_hash,
  search_config_hash, budget, dataset_version, repeats,
  available_tools_from_registry

MetricResult
  metric_name, metric_version, score, threshold,
  success, reason, deterministic, judge_model?, cost?

ExperimentRun
  experiment_id, variant_id, case_id, repeat,
  project_commit, dependency_lock, output_ref,
  retrieval_context, trace_ref, telemetry, metric_results,
  status/error, started_at/finished_at

AblationReport
  schema_version, variants, aggregate_by_difficulty,
  paired_deltas, mean/std/confidence_interval,
  cost/tool/token deltas, failures, artifact_manifest
```

阶段0的`tests/baseline/cases.jsonl`是case ID、prompt、difficulty、Requirement和预算标签的唯一权威数据集。阶段7 golden overlay只能补充full-eval字段；runner按`case_id + dataset_version`合并，遇到重复prompt/Requirement、未知case或版本漂移即失败。标记`full_rag_metrics=true`的Golden必须有非空`expected_output`，否则Contextual Precision/Recall记为不具备资格且整个T7-9门禁不能完成，不能以skip代替结果。Tool Correctness按variant评分：`available_tools`从该variant实际registry snapshot生成，`expected_tools_by_variant`只允许引用该集合；不能拿Baseline的工具期望惩罚PaperQA/Agentic，反之亦然。

自定义指标必须定义公式和方向：

- Citation Fidelity = fully supported cited atomic claims / cited atomic claims；若存在可核验Claim但无引用，得分为0而非空集满分；
- Citation Completeness（Claim Support Coverage）= 至少绑定一个有效Evidence的可核验Claim / 全部可核验Claim；
- Unsupported Claim Rate = 最终输出中unsupported/contradicted且未正确标注的可核验Claim / 全部可核验Claim；
- Source Numbering Error Rate = (孤儿正文引用 + 无引用来源 + 重复/非连续错误) / 引用项；硬门槛 0；
- Source Quality 根据 authority policy，不把企业自述普遍化；
- Memory Reuse = 有效 Memory命中并改变受控决策的 case比例，同时报告污染/跨 Namespace错误数；
- Web/Token Reduction使用固定cold/warm协议：每个variant从相同独立初始快照开始，cold运行后只允许该variant自己的受治理写回/Memory影响warm运行；先比较同variant cold→warm，再比较warm Agentic/完整variant与warm Baseline，并同时满足相同输出质量门槛。

五个variant的最小flag矩阵固定如下；未列出的新增能力保持相同配置，Filesystem/Knowledge MCP默认关闭并由独立安全suite验收，避免把基础设施差异混入质量消融：

| Variant | `enable_knowledge_base` / `enable_paperqa_retrieval` / `enable_knowledge_tools` | `enable_agentic_rag` / `enable_knowledge_writeback` | `enable_memory` / `enable_memory_writes` | `citation_validation_mode` |
|---|---|---|---|---|
| `baseline` | `false / false / false` | `false / false` | `false / false` | `off` |
| `paperqa` | `true / true / true` | `false / false` | `false / false` | `off` |
| `agentic_rag` | `true / true / true` | `true / true` | `false / false` | `off` |
| `memory` | `true / true / true` | `true / true` | `true / true` | `off` |
| `citation_validator` | `true / true / true` | `true / true` | `true / true` | `enforce` |

`paperqa`使用阶段3的active-only `knowledge_augmented_legacy`绑定，因此与Baseline存在可测差异但不具备local-first/writeback。各variant从同一只读active知识快照开始；需要写回或Memory的variant使用独立clone，避免顺序污染。

## 8. 执行步骤

1. 冻结阶段0 canonical dataset v1、只含补充字段的golden overlay、消融flag矩阵、模型/搜索/预算配置、Judge版本和统计口径；写合并/漂移/公平性检查。
2. 实现LangGraph trace→DeepEval TestCase adapter，保留plan、tool calls、retrieval context、token/耗时；验证并行span归属、full Golden expected_output条件和variant-specific available/expected tools。
3. 实现默认 smoke custom metrics和所有硬安全断言，无 API key可跑。
4. 配置 full Agent/RAG metrics；缺 key/Judge时明确 skip，禁止将 skip汇总为 pass。
5. 实现统一evaluation-only claim/citation scorer，以及Source/Citation Fidelity/Completeness/Unsupported Rate/Temporal/Requirement/Memory/Cost自定义指标和单元fixtures；scorer对所有variant使用相同版本且不修复报告。
6. 实现实验 runner：每 variant隔离 DB/index/checkpoint或使用只读快照；固定 seed、case顺序、重复次数、超时和预算。
7. 先跑全量smoke；它只证明runner/schema/硬规则可执行，不得用replay/fake结果宣称Task Completion、Citation提升或Web/token真实收益。任何schema/编号/隔离失败先停止并回到所属阶段修复。
8. 向用户展示 full eval case数、模型、估算调用和预算；获得明确授权后运行三档/消融。
9. 汇总 paired raw records、mean/std/CI与失败分析；生成 JSON/Markdown和README展示，不能挑选性删除失败 run。
10. 运行本地 Demo/复现命令，更新状态/版本/许可证/evidence并停止。

## 9. 配置和回退

- `ODR_EVAL_MODE=smoke|full` 默认 `smoke`；`RUN_FULL_EVAL=1` 才运行 Judge/live Research。
- `evaluation_dataset_version`、`ablation_manifest`、`repeats>=3`、seed、timeout和预算显式记录。
- 每个 variant只能由 flag manifest构造，禁止手工修改代码形成不可复现实验。
- DeepEval callback/metrics只在 runner配置注入，生产默认无 callback/上传。
- `CONFIDENT_API_KEY`/LangSmith不是本地报告前提；若启用上传，需单独授权并去敏。
- full失败可回退 smoke和历史 artifact；不得把旧结果标为当前 commit。

## 10. 单元测试

- canonical baseline case + golden overlay的合并、重复字段/未知case拒绝，以及experiment/result schema和版本；
- ablation flags必须与上表逐步累加，`paperqa`确实绑定active-only知识工具，模型/预算/数据一致；
- trace adapter 对并行 ToolCall、plan缺失、retrieval context、token和错误；
- variant registry snapshot与`expected_tools_by_variant`一致性；未知/跨variant tool期望拒绝；
- custom metric 的正/反例和分母为零语义；
- Source numbering错误率、统一scorer、Citation fidelity/completeness/unsupported rate五类状态、零分母语义、temporal/source authority；
- Memory复用、跨 Namespace、stale recall、重复增长指标；
- aggregate mean/std/paired delta、失败/skip不被丢弃；
- secret redaction、artifact manifest/hash和重复运行ID；
- full marker/env gate默认不执行 Judge。

## 11. 集成测试

- 无 key环境运行 simple/medium/complex各至少一个 replay smoke并生成统一报告；
- 用 fake trace跑五个消融 variant，验证配置和结果聚合；
- 重复查询 fixture证明 Agentic RAG variant的 Web/tool/token计数可比较；
- stale/quarantined knowledge失效后召回为0，Memory跨用户错误为0；
- 所有variant输出经过同一evaluation-only scorer；Citation Validator on/off fixture产生预期fidelity/completeness/unsupported/numbering delta且原输出未被修改；
- 经授权后 full runner在固定小子集运行 DeepEval Agent/RAG metrics并保存 judge model/cost；
- README表格从机器 JSON生成或有一致性检查，禁止手填与结果不符。

## 12. 阶段验收测试

- **T7-1**：simple、medium、complex数据均可被 runner加载并完成 smoke；每档至少 3 个 case，ID/版本稳定。
- **T7-2**：五个消融 variant使用相同数据、模型、搜索限制和预算；flag矩阵机器校验通过。
- **T7-3**：经用户授权的live/full配对结果中，完整variant的Task Completion均值不低于Baseline；报告展示每档均值、标准差和失败case。replay/fake smoke不能用于判定本项。
- **T7-4**：经授权的full complex结果由同一evaluation-only scorer评分，完整variant的Citation Fidelity和Citation Completeness均值严格高于Baseline，Unsupported Claim Rate低于Baseline，原始Claim级结果可追溯；smoke不能判定本项。
- **T7-5**：完整 variant的 Source Numbering Error Rate在全部 smoke/full输出均为 0。
- **T7-6**：经授权的live/full按固定cold/warm协议运行；在相同任务完成硬门槛下，Agentic/完整variant的warm相对自身cold下降，且其warm Web调用和total tokens低于warm Baseline。报告快照hash、绝对值与百分比；fake计数只验证机制，不判定收益。
- **T7-7**：错误知识转 stale/quarantined/soft-deleted后当前召回数为 0，历史审计仍存在。
- **T7-8**：Memory复用有命中率/避免工具调用或步骤数的量化；跨 Namespace错误、无Evidence Semantic写入和陈旧事实召回均为 0。
- **T7-9**：Task Completion、Tool Correctness、Step Efficiency、Plan Adherence、Faithfulness、Contextual Precision/Recall均有full结果；所有适用Golden含expected_output，无plan trace不能因上游默认分通过，skip不能完成本项。
- **T7-10**：Source Quality、Citation Fidelity/Completeness/Unsupported Rate、Memory和Cost自定义metric均有deterministic unit test、版本、公式、零分母语义和阈值。
- **T7-11**：报告含 input/output/total tokens、估算成本、wall time、各工具调用和Researcher数量；未知值不写0。
- **T7-12**：生成机器可读 JSON/JSONL、Markdown摘要、artifact manifest/hash和README展示，内容一致性校验通过。
- **T7-13**：普通 pytest/smoke无网络；full只有显式开关和用户费用授权才运行，skip不计pass。
- **T7-14**：`scripts/validate_phase.py --phase 7` 对上述门槛和 artifact完整性返回正确退出码。
- **T7-15**：阶段0 canonical cases是prompt/Requirement唯一数据源；golden overlay不复制这些字段，合并漂移测试通过。
- **T7-16**：Baseline到完整variant全部使用相同版本的evaluation-only claim/citation scorer，scorer不修改原输出；有可核验Claim但零引用时Citation Fidelity/Completeness不可能得到虚假满分。
- **T7-17**：Tool Correctness对每个variant使用其实际available tools和对应expected tools；跨variant不存在的工具不会被当作必需或可用，配置漂移会使验收失败。

## 13. 验收命令

默认、无成本：

```powershell
conda run --no-capture-output -n open-deep-research python -m pytest tests/evaluation -m "not full_eval and not live" -q
conda run --no-capture-output -n open-deep-research python scripts/run_eval.py --mode smoke --variants all --dataset-version v1 --output artifacts/evaluation/smoke
conda run --no-capture-output -n open-deep-research python scripts/compare_ablations.py --input artifacts/evaluation/smoke --output artifacts/evaluation/smoke/report.json
conda run --no-capture-output -n open-deep-research python scripts/render_eval_report.py --input artifacts/evaluation/smoke/report.json --output artifacts/evaluation/smoke/report.md
conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 7
conda run --no-capture-output -n open-deep-research python -m ruff check src/open_deep_research/evaluation tests/evaluation scripts/run_eval.py scripts/compare_ablations.py scripts/render_eval_report.py
conda run --no-capture-output -n open-deep-research python -m mypy src/open_deep_research/evaluation
git diff --check
```

仅在用户确认 case、Judge、预算后：

```powershell
$env:RUN_FULL_EVAL='1'
conda run --no-capture-output -n open-deep-research python scripts/run_eval.py --mode full --variants all --dataset-version v1 --repeats 3 --confirm-cost --output artifacts/evaluation/full
conda run --no-capture-output -n open-deep-research python -m pytest tests/evaluation/full -m full_eval -q
```

现有 `python tests/run_evaluate.py` 仍是独立 LangSmith成本命令，不作为默认验收。

## 14. 完成定义

T7-1至T7-17全部通过；用户已明确授权并完成full消融（未授权则阶段保持`in-progress`）；五组variant公平可复现且统一scorer/variant-specific tool policy；Task Completion不低于Baseline、复杂Citation Fidelity/Completeness提升且Unsupported Rate下降、来源编号错误率零、重复查询Web/token下降、错误知识/跨用户记忆召回为零；机器报告与README一致；全部原始失败/skip保留；状态evidence完整。

## 15. 风险与降级方案

- **Judge波动**：固定 model/prompt/version、至少3次配对、展示 mean/std/CI和原始结果；硬规则不用 Judge。
- **Token/费用**：先 smoke和小子集估算，用户批准预算后 full；设置并发、timeout、最大case，失败保存部分记录。
- **API兼容**：DeepEval metrics/trace API变化；adapter contract和版本锁，平台上传关闭。
- **公平性**：Memory/KB可污染 variant；每 variant使用隔离快照和预定义 setup，不让后运行天然获益。
- **PlanAdherence**：无 plan可能得1；自定义 plan-present硬断言，缺 plan视为失败/不可评而非通过。
- **数据泄漏**：Goldens/expected output不得被生产 Agent读取；runner分离输入与judge context。
- **统计解读**：小样本不夸大结论；README写 case数、模型、日期、误差和局限。
- **回退**：任何 full失败保留 smoke和原始 artifact；功能问题回到所属阶段修复后重新跑，不在本阶段补丁业务逻辑。

## 16. 本阶段 Codex 执行指令

```text
你现在只执行 doc/development_plan/phase_7_evaluation_and_showcase.md；先验证阶段 6 completed 且全部 T6 有 evidence，否则停止。本阶段是最后阶段，完成后也不得自行发布、push或扩展功能。

先读取 AGENTS.md、状态文件、本目录全部规划文档、阶段 0 baseline/telemetry/dataset/validator、阶段 1–6 的配置开关与测试、现有 tests/run_evaluate.py、evaluators.py、pairwise_evaluation.py、expt_results schema。必须定点阅读 doc/reference/deepeval 的 CallbackHandler、tracing types、Golden/EvaluationDataset/LLMTestCase、BaseMetric、TaskCompletion/ToolCorrectness/StepEfficiency/PlanAdherence/Faithfulness/ContextualPrecision/Recall及 LangGraph/pytest tests。先 git status --short并保留用户改动。

允许范围：Evaluation/DeepEval adapters、自定义 metrics、goldens/消融manifest、smoke/full runners、统计/机器报告/README展示、成本门禁、观测性hook、测试/脚本/状态文件。禁止新增或修复 Knowledge/Agentic RAG/MCP/Memory/Citation业务逻辑；指标失败时记录并回到所属阶段另开任务。禁止复杂前端、云上传默认开启、不同预算的不公平比较、隐藏失败结果、修改 src/legacy/。

先完成所有无网络smoke和第10、11节deterministic测试；确认阶段0case是唯一prompt/Requirement源，所有variant使用同一evaluation-only claim/citation scorer，Tool Correctness使用各variant真实registry/expected policy。向我报告full eval的case数、模型、预计调用/成本并等待明确授权；未授权时保持阶段in-progress。授权后跑固定五组消融和至少3次配对，逐项执行T7-1至T7-17；T7-3/4/6只能由live/full结果判定。生成JSON/JSONL、Markdown、manifest/hash和机器结果驱动的README表格。skip不得计pass，未知成本不得写0。

完成后更新 feature_list.json、progress.md、session-handoff.md，报告各variant、每项验收、完整命令/退出码、统计结果、成本、局限、回退和最终 git status。然后停止，不得自行push、发布或开始额外阶段。
```
