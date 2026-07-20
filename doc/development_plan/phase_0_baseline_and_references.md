# 阶段 0：参考仓库与 Baseline

## 1. 阶段目标

在不修改核心研究逻辑的前提下，固定当前系统的可观察行为、依赖解析结果和参考仓库版本；建立简单/中等/复杂三档 baseline 数据、统一运行遥测、确定性 smoke eval 和最小 DeepEval 适配骨架。完成后，后续每个阶段都能与同一 baseline 比较，并能证明“关闭新增功能时原系统行为不变”。

## 2. 为什么此阶段现在做

当前测试只覆盖部分并发和 Tavily 格式，现有 LangSmith 脚本成本高且结果文件没有 token、耗时或工具调用统计。代码与文档还存在配置默认值漂移，依赖范围也未锁定。若先改知识/图逻辑，后续将无法判断变化来自新能力、依赖升级还是既有不稳定。因此阶段 0 是阶段 1–7 的共同测量基线；阶段 7 将直接复用其数据与结果 schema。

## 3. 范围

- 记录项目 HEAD、Python/conda、已解析依赖、配置默认值和已知行为快照；
- 固定五个参考仓库的 URL、commit、许可证与获取方式；
- 建立至少 3 个简单、3 个中等、3 个复杂 baseline case，包含稳定 ID、问题、类别、预期 Requirement、是否允许联网与预算标签；
- 建立 `BaselineRunRecord`，保存输入、输出、状态、token、耗时、工具调用、错误和配置快照；
- 提供默认无网络的 fixture/replay smoke，以及需显式授权的 live runner；
- 接入 DeepEval 的最小可选 adapter/自定义确定性 metric，不启用 LLM Judge；
- 把高成本评测与普通 pytest 隔离，修正任何“模块导入即发起外部评测”的测试入口；
- 建立 `scripts/validate_phase.py --phase 0` 和后续阶段可扩展的验收清单格式；
- 运行现有低成本测试，记录基线告警和已知缺陷，不在本阶段修复核心图缺陷。

## 4. 非目标

- 不修改 Supervisor、Researcher、搜索或 Writer 的业务路径；
- 不修复 `deep_researcher.py` 中 `or True`、compression retry 或未知工具 KeyError 等逻辑；
- 不引入知识库、SQLite 业务库、PaperQA2、MCP、Memory 或 Citation Validator；
- 不运行完整 Deep Research Bench、LangSmith 比较或 DeepEval LLM Judge，除非用户对具体命令和成本明确授权；
- 不把历史 `tests/expt_results/*.jsonl` 误写成包含遥测的新 baseline；
- 不改变现有配置默认值来消除文档漂移，只记录并提出决策项；
- 不对 `doc/reference/` 内外部仓库做 lint、格式化或修改。

## 5. 当前项目修改点

预计新增：

- `tests/baseline/cases.jsonl`：三档 case 与 Requirement；
- `tests/baseline/fixtures/`：去敏后的确定性 replay 输入/输出；
- `tests/evaluation/test_baseline_smoke.py`、`tests/evaluation/test_result_schema.py`；
- `src/open_deep_research/evaluation/models.py`：统一 run/telemetry schema；
- `src/open_deep_research/evaluation/telemetry.py`：不侵入图的 wrapper/callback；
- `src/open_deep_research/evaluation/deepeval_adapter.py`：可选依赖边界；
- `scripts/run_baseline.py`、`scripts/validate_phase.py`；
- `doc/reference/README.md`、`doc/reference/refs.lock.json`、`THIRD_PARTY_NOTICES.md` 或等效清单；
- `tests/pytest.ini` 或根级 pytest 配置中的 `smoke`、`live`、`full_eval` marker（结合当前布局选择一种，不重复配置）。

预计最小修改：

- `pyproject.toml`：统一目标 Python 为 `>=3.11`、让新子包可被发现，并将 DeepEval 放入可选 `eval` 依赖；不得把它变成生产必需依赖；
- `tests/pairwise_evaluation.py`：把模块底部外部调用放进显式 CLI/main gate，确保收集测试不会联网；
- `feature_list.json`、`progress.md`、`session-handoff.md`：阶段状态与 evidence；
- `.gitignore`：仅添加 baseline artifact、缓存、参考浅克隆策略所需规则，不能忽略规划文档。

只读取、不修改核心行为：`src/open_deep_research/deep_researcher.py`、`configuration.py`、`state.py`、`prompts.py`、`utils.py`。

## 6. 参考仓库

- **DeepEval**：参考 `Golden`、`EvaluationDataset`、`LLMTestCase`、`CallbackHandler`、`BaseMetric`、LangGraph integration tests。借鉴 dataset/trace/metric/pytest 边界；不借鉴平台上传作为前提，不在 smoke 使用 Judge，不把其 trace 当生产审计。允许通过 Apache-2.0 公共 API 依赖；复制代码需 attribution。
- **LangGraph**：参考当前项目的 `tests/run_evaluate.py` 和参考仓库 checkpoint/trace 配置方式，仅用于正确调用现有 builder 和构造 fake/replay；本阶段不引入持久 checkpointer。MIT；优先依赖 API。
- **PaperQA2、LangMem、MCP Servers**：本阶段只固定 SHA、版本/动态版本事实和许可证，不调用代码。PaperQA 当前浅克隆版本由 setuptools_scm 动态生成且无 tag，必须以 SHA 记录，不能虚构版本号。
- 五个仓库的精确提交和复用限制以 `reference_repositories.md` 为准。参考 fixture 可能另有内容版权，不允许直接复制论文或网页样本。

## 7. 数据结构和接口

至少定义以下规划内契约：

```text
BaselineCase
  id, difficulty, prompt, expected_requirements,
  network_policy, budget_class, tags, fixture_version

RunTelemetry
  started_at, finished_at, wall_time_ms,
  input_tokens, output_tokens, total_tokens, estimated_cost,
  model_calls, tool_calls_by_name, search_calls, researcher_runs,
  status, error_type

BaselineRunRecord
  schema_version, run_id, case_id, project_commit,
  config_snapshot, output, telemetry, artifact_refs, created_at

EvaluationAdapter
  to_deepeval_case(case, run) -> LLMTestCase-like value
  evaluate_smoke(case, run) -> list[MetricResult]
```

JSONL 写入使用临时文件 + 原子替换或逐条 append 锁，避免中断留下半行。`estimated_cost` 和 token 分开保存；未知成本为 `null`，不得伪造为 0。

## 8. 执行步骤

1. 记录当前 HEAD、conda Python、已安装核心包、`pyproject.toml` 范围和所有配置实际默认值，生成 machine-readable baseline manifest。
2. 建立 `refs.lock.json` 和获取说明；决定浅克隆本体是否提交，默认只提交 lock/脚本并忽略嵌套仓库。
3. 定义 `BaselineCase`、`RunTelemetry`、`BaselineRunRecord` 与 JSON schema/序列化测试。
4. 编写三档数据集；问题不得包含 secret，Requirement 必须可逐项检查，live/replay 标签分离。
5. 实现不修改图节点的 telemetry wrapper；对 fake graph/replay 验证 token、耗时、工具调用和错误保存。
6. 建立 DeepEval 可选 adapter 和至少两个确定性 smoke metric（如输出存在、Requirement 字段/引用格式的结构检查）；无 `eval` extra 时给出清晰 skip/error。
7. 把所有外部评测置于 `live/full_eval` marker 和显式环境开关后，消除 import-time 网络调用。
8. 实现 baseline CLI：默认 `--mode replay`，live 模式要求 case、预算确认开关和输出目录；中断也保存失败记录。
9. 验证live runner的双重费用门禁；用户若明确授权，可额外运行一个simple live case作为发布证据。无授权时记录`not_run_no_authorization`，但不阻止仅以replay/smoke完成阶段0。
10. 执行低成本回归和 phase validator，更新状态文件并停止。

## 9. 配置和回退

- 新增 `enable_evaluation_telemetry: bool = False`，或等效 CLI-only 配置；默认不注册 callback。
- `ODR_EVAL_MODE=smoke|live|full` 默认 `smoke`；`RUN_LIVE_RESEARCH=1` 和 `RUN_FULL_EVAL=1` 必须显式设置。
- baseline 输出目录默认在可配置的本地 artifact 路径，敏感输出不提交。
- 禁用 telemetry 后直接调用当前 `deep_researcher`/builder，输出 state 不增加必需字段。
- 回退时移除 callback/runner 即可；核心图无变更，旧测试仍应完全可运行。

## 10. 单元测试

- case/telemetry/run record 的 schema、JSONL round-trip、版本拒绝和未知 cost；
- 工具调用计数、并行 span 合并、失败/取消状态和 wall time 单调性；
- 输出目录创建、原子写入和损坏尾行诊断；
- DeepEval 未安装时的可预测行为；
- 确定性 metric 对 pass/fail fixture 的断言；
- pytest 收集不会导入执行外部比较；
- live/full marker 默认 skip；
- 配置关闭时 wrapper 不改变输入、输出和异常类型。

## 11. 集成测试

- 用 fake model/tool 或已保存 replay 跑一个完整 baseline case，生成可重新加载的 JSONL record；
- 对现有 builder 做无网络 callback 集成，验证并行 ToolMessage 不重复计数；
- 在无 API key 环境执行普通 pytest，证明不会发起网络；
- 用户授权后仅运行一个简单 live case，验证真实输出和遥测文件；中等/复杂 live 只验证 CLI 可选取，不默认执行。

## 12. 阶段验收测试

- **T0-1**：`refs.lock.json` 能解析，五个仓库的 URL、commit、许可证字段齐全，且本地存在时 HEAD 与 lock 一致。
- **T0-2**：baseline 数据集至少含 3 个 simple、3 个 medium、3 个 complex case，ID 唯一且 schema 校验通过。
- **T0-3**：默认 smoke 在无 API key、断网条件下运行，不调用外部模型、搜索、LangSmith 或上传服务。
- **T0-4**：至少一个 replay/fake case 完整运行并产生可加载结果，包含 wall time、token 字段、工具计数、状态和输出。
- **T0-5**：未设置环境开关或缺少`--confirm-cost`时，live runner在任何外部调用前以可识别非零码拒绝并且不生成伪结果；若用户明确授权，可附加一个simple live同schema记录，但这不是阶段0技术完成的必需条件。
- **T0-6**：telemetry 关闭时，当前图的输入输出键和已有 `tests/test_research_limits.py` 结果不变。
- **T0-7**：普通 pytest 收集和 smoke 不触发 `tests/pairwise_evaluation.py` 的外部调用。
- **T0-8**：DeepEval 未安装时生产运行不受影响；安装 `eval` extra 后确定性 metric 通过。
- **T0-9**：实际配置默认值和文档/UI 漂移被写入 manifest/决策清单，没有在本阶段静默改值。
- **T0-10**：机器结果能区分 token 数、估算成本、耗时和各工具调用，未知值为 `null` 而非伪造数字。
- **T0-11**：`scripts/validate_phase.py --phase 0` 对完整 fixture 返回 0，对缺字段/错误 commit 的 fixture 返回非 0。
- **T0-12**：阶段变更未修改 `deep_researcher.py`、`prompts.py` 或 `utils.py` 的核心研究逻辑。

## 13. 验收命令

未来实施时在 `open-deep-research` conda 环境执行；若环境名不同，记录实际环境：

```powershell
conda run --no-capture-output -n open-deep-research python -m pytest tests/test_research_limits.py -q
conda run --no-capture-output -n open-deep-research python -m pytest tests/evaluation tests/baseline -m "not live and not full_eval" -q
conda run --no-capture-output -n open-deep-research python scripts/run_baseline.py --mode replay --case simple-001 --output artifacts/baseline/smoke.jsonl
conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 0
conda run --no-capture-output -n open-deep-research python -m ruff check src/open_deep_research/evaluation tests/evaluation tests/baseline scripts
conda run --no-capture-output -n open-deep-research python -m mypy src/open_deep_research/evaluation
git diff --check
```

只有用户明确批准费用后才运行：

```powershell
$env:RUN_LIVE_RESEARCH='1'
conda run --no-capture-output -n open-deep-research python scripts/run_baseline.py --mode live --case simple-001 --confirm-cost --output artifacts/baseline/live.jsonl
```

不要在本阶段运行 `python tests/run_evaluate.py`。

## 14. 完成定义

T0-1至T0-12全部通过；baseline schema、数据、replay runner、live费用门禁、最小DeepEval adapter和参考lock均有测试；至少一个replay/fake case完整可运行；普通测试默认无网络；新增观测关闭后旧行为回归通过；状态文件和evidence完整。simple live是可选发布证据，未获授权时记录未运行原因但不阻止阶段0技术完成。

## 15. 风险与降级方案

- **API/版本**：当前 `langgraph>=0.5.4` 与本机 1.2.6、参考 HEAD 1.2.9 跨度大；先记录 resolved matrix，callback 用 adapter，失败则只保留本项目 telemetry。
- **Token/费用**：live case 可能产生费用；默认 replay，限制只跑一个 simple case并保存预算。
- **并发**：异步 span 顺序不稳定；按稳定 call ID 合并，不靠到达顺序。
- **配置漂移**：`print_process_info`、`allow_clarification`、模型 fallback 不一致；列为 `[ASK USER]`，不在 baseline 阶段擅自统一。
- **Windows**：`./init.sh`、路径和 conda 输出可能异常；记录证据后使用 `conda run` 子命令。
- **测试波动**：live 结果只做记录和宽松结构门禁，确定性 smoke 才是日常硬门禁。
- **许可证/仓库体积**：默认提交 lock/说明而不是嵌套 `.git`；复制内容前逐文件确认许可。
- **回退**：关闭 telemetry、移除可选 eval extra 即可，核心图不变。

## 16. 本阶段 Codex 执行指令

```text
你现在只执行 doc/development_plan/phase_0_baseline_and_references.md，不得开始阶段 1。

先完整读取：AGENTS.md、feature_list.json、progress.md、session-handoff.md、doc/development_plan/README.md、doc/development_plan/architecture_target.md、doc/development_plan/reference_repositories.md、本阶段文档、README.md、pyproject.toml、langgraph.json、src/open_deep_research/{configuration,state,deep_researcher,utils,run}.py、tests/ 全部当前 Python 文件，以及五个 doc/reference/ 仓库中本阶段第 6 节列出的文件。先执行 git status --short，保留用户已有改动。

允许范围：baseline/评测 schema、无侵入 telemetry wrapper、replay/live runner、确定性 smoke、可选 DeepEval adapter、参考仓库 lock/许可证说明、pytest 成本门禁、阶段验证脚本、必要的 pyproject package/optional-eval 配置，以及状态文件。禁止修改 Supervisor、Researcher、搜索、Writer 的核心行为；禁止安装/实现 PaperQA2、知识库、数据库、MCP、Memory、Citation Validator；禁止运行完整 Deep Research Bench、LangSmith 或 LLM Judge，除非我在本轮对具体命令明确授权。

按第8节逐步实现；必须编写第10、11节测试并逐项执行T0-1至T0-12。live simple case需要费用授权；未授权时只验证runner在外部调用前拒绝并记录`not_run_no_authorization`，不得伪造live结果，但可凭完整replay/smoke门禁完成阶段0。所有新功能默认关闭，证明关闭后已有行为不变。不得lint/格式化doc/reference/。

完成后更新 feature_list.json、progress.md、session-handoff.md，报告修改、设计决策、每个验收编号、完整命令/退出码、跳过原因、兼容回退和最终 git status。报告后立即停止，不得自动进入阶段 1。
```
