# 会话进度记录

## 当前状态

**最后更新：** 2026-07-23

**当前功能：** `phase-7-evaluation-showcase-001`

**状态：** in-progress（本地 full 执行与验收框架已完成；真实 calibration/full 尚未重新授权执行）

## 阶段门禁

- 阶段 0–5 均保持 `completed`。
- 开始阶段 6 前运行 `scripts/validate_phase.py --phase 5`，退出码 0；T5-1 至 T5-16 全部 PASS。
- 阶段 6 最终运行 `scripts/validate_phase.py --phase 6`，退出码 0；T6-1 至 T6-18 全部 PASS。
- 未下载 STORM、OpenFactVerification 或 FIRE；未调用真实模型、Web、LangSmith、Deep Research Bench 或 LLM Judge。

## Phase 7 本地完整评测实现（2026-07-22）

### 当前环境依赖修复（2026-07-23）

- 已复现终端首个错误：`huggingface-hub 1.24.0` 要求 `click>=8.4.2`，而环境中的 `click 8.3.3` 与 `deepeval 4.1.1` 的 `click<8.4.0` 约束不可同时满足。
- 已在现有 `open-deep-research` conda 环境按仓库约束安装 `click==8.3.1` 与 `huggingface-hub==1.4.1`；`deepeval==4.1.1` 保持不变。Python 3.11.15、`pip check` 和 `import deepeval; import open_deep_research.evaluation.full_runner` 均通过。
- 一键入口默认环境改为 `open-deep-research`，因此正常命令缩短为 `.\scripts\run_phase7_full.cmd -ConfirmCost`。新增本地 Git 预检，在 import/付费调用前列出未提交评测文件并以退出码 4 停止，不再输出路径乱码 traceback。
- 入口与 Source Gate 聚焦回归 `13 passed`，Ruff、compileall 和 PowerShell parser 通过；重新生成 45-record smoke，当前源码快照为 `8d7d2e2be7c9...`。
- 当前唯一启动阻塞是评测相关源码尚未提交；这是设计中的 clean-source 可复现性门禁，不能通过放宽检查绕过。未调用 Qwen、Tavily、Judge 或 LangSmith。

### Windows 一键启动收口

- 新增 `scripts/run_phase7_full.cmd` 与 ASCII-safe `scripts/run_phase7_full.ps1`。首次创建独立环境后，用户只需运行 `.\scripts\run_phase7_full.cmd -ConfirmCost`；同一命令也用于中断恢复。
- 入口固定串联 `pip check`/import/clean-source 门禁、无网络 smoke、新 6-run calibration（最多 300 万 Token）、只读 full 投影、`FULL` 二次确认、固定 54-run full、报告渲染与 Phase 7 validator。`-ApproveFull` 仅供已审阅投影后显式跳过交互；tracking 默认 `local`。
- 已存在 `calibration-current` 或 `full` 时只在相同目录增加 `--resume`；脚本没有循环重试或删除逻辑，子命令失败会原样停止，报告/validator 失败不会触发付费研究重跑。旧 `artifacts/evaluation/calibration/` 不会被恢复。
- 聚焦离线验证 `30 passed`；PowerShell parser 通过；缺少 `-ConfirmCost` 时在 conda/外部调用前以退出码 2 拒绝；缺失评测环境时不创建 calibration/full 输出；精确 Ruff 与 compileall 通过。
- 重新生成 45-record smoke；最新源码快照见上方 2026-07-23 记录。直接 Phase 7 validator 仍按设计仅 T7-3/4/6/9 因缺少真实 full artifact FAIL，其余 PASS。

- `scripts/run_eval.py --mode full` 已接入真实调度内核，固定生成 45 个 main 与 9 个 warm 定义，共 54 个稳定 run ID；不允许缩减五组 variant 或改变 repeats。
- 每个 variant/output 使用隔离的知识、Memory、checkpoint、writeback runtime；cold/warm 同时校验初始只读快照与 cold 后运行态快照。
- 每个 run 将 research、7 个 DeepEval metric、独立 `evaluation-claim-scorer-v3` 和终态 record 分别持久化；恢复按 terminal step 跳过，报告、manifest 或 tracking 失败不会重放已完成付费步骤。
- Token ledger 固定为 3600 万停止派发、4200 万硬上限、单 run 80 万；unknown usage、异常 reservation、重复错误、失败率/失败 Token/retry Token 越界均 fail closed。Calibration/full 均有 output-scoped 跨进程 lease和逐 run journal/ledger/record 对账。
- 本地 `experiment.json`、`runs.jsonl`、`report.json`、`report.md`、`budget.json`、`journal.json`、step/run-record hashes 与 `manifest.json` 是验收权威；LangSmith 是默认关闭、失败不重跑的去敏镜像。
- 新增 `environment.phase7.yml` 与 `constraints/evaluation-py311.txt`；真实付费入口要求 Python 3.11、锁定 DeepEval/Click/Hugging Face Hub、`pip check`、import smoke、clean evaluation source、双环境门禁、`--confirm-cost` 和显式 Token 上限全部先通过。
- 严格 validator 会读取 54 条 run、全部 step artifact、journal、ledger、claim report hash、source attestation、Markdown 与 manifest，重算指标和 T7-3/T7-4/T7-6/T7-9；fake/skip/error/unknown cost 均不能计 pass。
- 离线验证：core suite `131 passed`；54-run/resume/failure full runner suite `14 passed`；strict validator suite `4 passed`；其余 Phase 7 contract suite `20 passed`，合计 169 passed。`compileall` 通过，Phase 7 新增精确范围 Ruff 通过。
- `scripts/validate_phase.py --phase 7` 恢复为预期语义：T7-1/2/5/7/8/10-17 PASS，T7-3/4/6/9 因没有真实 full artifact FAIL，命令退出码 1；不会把 fake 或 stopped calibration 计为通过。
- 已离线重生成 `artifacts/evaluation/smoke/` 的 45 条记录、报告与 manifest，全部绑定 `evaluation-claim-scorer-v3`；validator 现在会拒绝旧 scorer、重复/缺失 case×variant 或非唯一 run ID。
- smoke 现同时记录生成时 `HEAD`，并将限定评测文件的路径/bytes 快照 SHA-256、scorer version 与矩阵参数绑定到实验 ID；当前快照前缀为 `8d7d2e2be7c9`。内容 hash 在同一源码提交前后稳定，排除文档/状态/artifact，并在写入前二次采样；dirty-source smoke 在源码 commit 后必须重建，clean-source smoke 可随展示 artifact 一同后续提交而不自引用。
- 通用 Markdown/README renderer 已识别 rich full report；真实 full 完成后可从同一 `report.json` 生成 README 表格。`scripts/compare_ablations.py` 仅用于 smoke，不得覆盖 full runner 已生成的统计报告。
- 新增 `--mode full --preflight-only` 只读入口：在不读取费用授权、不构造 tracking sink、不运行图/模型/搜索/full executor的前提下，验证 calibration、环境、clean source和保守投影，并输出模型、矩阵、Token/调用区间和 `estimated_cost_usd=null`，供 full 二次授权决策。
- 本次收口聚焦回归 `53 passed`（source gate、smoke/report、full reporting、full entry/preflight、strict full validator、phase validator），compileall、3 个 source file 的隔离 Mypy与精确 Ruff 通过。直接 Phase 7 validator 退出码 `1`，仅 T7-3/4/6/9 因缺少真实 full artifact FAIL，其余 PASS；这是当前正确语义。
- 未带 `--confirm-cost` 的真实 full CLI 在创建 output 或加载外部执行器前返回 `not_run_no_authorization`（退出码 3，output 不存在）。新增 scorer/full metric 的隔离 Mypy 检查通过；更宽的 13-file 检查只剩既有 `evaluation/models.py` 4 个 Literal typing 错误。
- 全目录收集仍会在当前 Windows 开发环境被系统应用控制阻止 `uuid_utils._uuid_utils` DLL；两项旧 Phase 0 测试因此无法在该环境形成全目录通过证据。Phase 7 测试使用 test-local UUID shim，真实付费环境的 import smoke 仍严格 fail closed。
- `artifacts/evaluation/calibration/` 仍是 3/6、632,627 Token 的 stopped diagnostic；本轮没有修改、恢复或冒充它，也没有调用 Qwen、Tavily、DeepEval Judge 或 LangSmith。

## 阶段 7 离线交付（2026-07-21）

- `tests/baseline/cases.jsonl` 继续作为唯一 prompt/Requirement 来源；`goldens.v1.jsonl` 仅保存 supplemental full-eval 字段。
- 固定五组公平消融 manifest，并用实际 `get_all_tools` registry 校验 variant-specific expected tools。
- 实现版本化 Experiment/Metric/Telemetry/Artifact schema、trace adapter、DeepEval lazy full metrics、自定义 Citation/Source/Memory/Cost 指标和只读统一 scorer。
- smoke runner 生成 45 条结构记录及 `runs.jsonl`、`report.json`、`report.md`、`experiment.json`、`manifest.json`；README 表格由机器 report 生成。
- 无网络 evaluation suite：66 passed、1 deselected；生命周期/Memory 回归：25 passed；新增范围 Ruff 和增量 Mypy 9 source files 通过。
- `validate_phase --phase 7`：T7-1/2/5/7/8/10-17 PASS；T7-3/4/6/9 缺少用户授权 live/full evidence，按设计 FAIL，退出码 1。
- 完整 `mypy src/open_deep_research/evaluation` 仍被 Phase 0 既有 typing 与跨模块历史错误阻塞（138 errors）；未以新增阶段结果冒充通过。

## Full 授权点

- 模型沿用 `.env` 中此前已验证的 `qwen3.7-plus`；模型标识只读取四个既有 MODEL 环境变量，Judge 可由 `EVALUATION_JUDGE_MODEL` 覆盖，否则回退 Research model；不读取或记录 secret。
- 新付费运行必须先提交或隔离评测相关改动，使 clean-source gate 通过，再在独立 Python 3.11 conda 环境运行 6-run calibration；校准 token 上限仍为 300 万。只有“已消耗 calibration + p95 × 54 × 1.25”投影通过后才能进入主矩阵。
- 固定子集仍为 `simple-001`、`medium-001`、`complex-001`；45 次主配对加 9 次 warm，共 54 次研究运行。
- 主计划预计 2200–3800 万 tokens；3600 万停止派发、4200 万硬停止、单 run 80 万上限，为用户估计 5000–6000 万额度至少保留 800 万。
- 每个付费步骤 checkpoint并用稳定 run/step ID恢复去重；连续 2 次失败、同一错误 2 次、失败率超过 25%、失败 run消耗超过 400 万、retry超过总预算10%或 usage未知会立即停止。未经新的明确授权不得启动 calibration/full。

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
- `environment.phase7.yml` 与版本门禁已有离线测试，但尚未在一个全新独立 conda 环境实际完成创建、`pip check` 和真实 import smoke；在这三项成功前不得运行新的付费 calibration。

## 下一步

阶段 7 保持 `in-progress`。现有 `open-deep-research` 环境已修复；审阅并提交终端列出的评测相关文件后，只需运行 `.\scripts\run_phase7_full.cmd -ConfirmCost`。脚本会完成新的最多 300 万 Token calibration并展示保守投影，只有用户输入 `FULL` 才开始 4200 万硬上限的 full；此前不得把 T7-3/4/6/9 标为通过。
