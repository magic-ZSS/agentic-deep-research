# 本地评测与消融协议

## Phase 7 一键入口

完整本地评测直接使用现有 `open-deep-research` 环境，统一从以下命令启动：

```powershell
.\scripts\run_phase7_full.cmd -ConfirmCost
```

该命令是首次启动命令。脚本会自动串联 smoke、最多 300 万 Token 的 calibration、只读投影、人工 `FULL` 确认、固定 54-run 消融、报告和验收；默认只写本地 artifact。只有经过本地检查确认健康的中断状态才可增加 `-ResumeCalibration`，`stopped`、`fail_closed`、未知 usage 或 identity 不一致的目录必须保留并改用新的 `-CalibrationOutput`。完整参数、依赖锁定和手动排错路径见 [`phase7-local-full-evaluation.md`](phase7-local-full-evaluation.md)。

阶段 7 只把 `tests/baseline/cases.jsonl` 作为 case ID、prompt、difficulty、Requirement 与预算的权威来源。`tests/evaluation/goldens.v1.jsonl` 只能补充参考答案、来源、时间上下文、Memory setup 与 variant-specific tool policy；加载器会拒绝重复 prompt/Requirement、未知 case 和版本漂移。

默认 smoke 完全离线：

```powershell
conda run --no-capture-output -n open-deep-research python scripts/run_eval.py --mode smoke --variants all --dataset-version v1 --output artifacts/evaluation/smoke
conda run --no-capture-output -n open-deep-research python scripts/compare_ablations.py --input artifacts/evaluation/smoke --output artifacts/evaluation/smoke/report.json
conda run --no-capture-output -n open-deep-research python scripts/render_eval_report.py --input artifacts/evaluation/smoke/report.json --output artifacts/evaluation/smoke/report.md --readme README.md
```

Smoke 的 45 条记录仅证明 9 个 canonical case、5 个 variant、配置公平性、工具 registry、schema、自定义硬规则和 artifact 链路可执行。它不证明 Task Completion、引用质量提升、Memory 收益或 Web/token 降低。

付费评测沿用 `.env` 中已经验证过的 `qwen3.7-plus` 配置。模型标识分别从 `SUMMARIZATION_MODEL`、`RESEARCH_MODEL`、`COMPRESSION_MODEL`、`FINAL_REPORT_MODEL` 读取；Judge 优先使用 `EVALUATION_JUDGE_MODEL`，未设置时回退 `RESEARCH_MODEL`。评测代码不会打印 API key 或 base URL。

先运行 2 个 canary run，再补齐剩余 4 个，总计 6 个 calibration run（3 个难度 × baseline/full 两组），校准硬上限 300 万 tokens。校准结束后用“已消耗 calibration Token + 实测 p95 × 54 × 1.25”重新投影；超出总硬上限就不进入主矩阵。

主计划预计 2200–3800 万 tokens，3600 万停止派发新 run，4200 万硬停止，至少为用户估计的 5000–6000 万额度保留 800 万 tokens。单次研究上限 80 万 tokens。每个 run 立即 checkpoint，恢复时按稳定 run ID 跳过已完成项。

连续 2 个失败、同一错误签名出现 2 次、至少 4 次运行后失败率超过 25%、失败 run累计超过 400 万 tokens、retry累计超过总预算 10%，或任何 token usage未知，都会停止后续派发。每个可重试调用最多重试 1 次。

Calibration/full 必须同时满足 `ODR_EVAL_MODE=full`、`RUN_FULL_EVAL=1`、`--confirm-cost` 和显式 `--max-total-tokens`。Full 还要求 `--repeats 3`，且必须先有通过熔断与投影门禁的 calibration artifact。DeepEval/Confident 上传默认关闭；LangSmith `tests/run_evaluate.py` 是另一条有成本的历史路径，不属于本地默认验收。

五组 variant 使用同一数据、模型、Tavily 限制和运行预算；每组从相同只读知识快照开始，允许写回或 Memory 的组使用独立 clone。所有输出由相同 `evaluation-claim-scorer-v4` 只读评分；它把项目确定性拆出的 Claim 按 6 条分批，模型只返回全局 ordinal 与分类，不能回写 Claim 文本或 citation IDs。它仍作为第 8 个 Judge/scorer journal 步骤统一计量、落盘和恢复，Tool Correctness 使用该 variant 的实际工具 registry。

机器产物包括 `runs.jsonl`、`report.json`、`report.md`、`experiment.json`、`budget.json`、`journal.json` 和 `manifest.json`。本地产物是验收权威；LangSmith 只是默认关闭的去敏镜像。未知 token 或成本写 `null`，skip/error 原样保留且不计 pass。

当前 smoke artifact 还绑定评测相关工作树的 source snapshot SHA-256，而不只记录 `git rev-parse HEAD`。快照按路径与当前 bytes 覆盖限定源码、测试、计划、约束与脚本中的 tracked/untracked 文件，排除文档、状态文件与生成 artifact；同一源码内容在提交前后保持相同 hash，提交展示 artifact 也不会产生自引用。`scripts/validate_phase.py --phase 7` 会与当前源码重新比对。真实 calibration/full 进一步要求这些路径完全 clean，避免旧 commit 标识掩盖未提交实现。

## 2026-07-21 校准结果

用户仅授权了累计上限 300 万 Token 的 calibration，没有授权完整消融矩阵。实验 `cal-34e1ee27efd02bab2423df3b516472d2` 在完成 3/6 个 run record 后安全停止：共结算 632,627 Token（561,343 input、71,284 output），其中 106 次 research model call、32 次 judge call、8 次 Web search。第三个 run 的供应商 usage 比声明的输出上限多 2 Token，账本因此按 `actual_usage_exceeded_reservation` fail closed；后续 3 个 run 没有派发。

最终账本已通过单调 revision 补写为 revision 276，active reservation 为 0，`budget.json` 与 `report.json.token_budget` 一致，manifest 重算无错误。评测端为供应商 usage 增加了独立安全余量，但不会扩大传给模型的生成上限；结算异常也会先持久化最终 fail-closed 快照。

这次结果是 stopped diagnostic，不是 full evidence：simple 的两个已完成 variant 都未达到任务完成要求，medium baseline 在 Judge 前停止，complex 未运行；不能据此判断质量提升、cold/warm 收益或 T7-3/T7-4/T7-6/T7-9。simple case 要求读取本项目文件，而当前生产 Researcher registry 没有 Filesystem 工具，这是后续应回到数据集/工具资格边界处理的业务缺口，不能在 Phase 7 中偷偷修改研究逻辑。

Qwen 的美元价格未配置，因此 `estimated_cost_usd` 保持 `null`，不会猜测价格或写成 0。已修正后续 `cost_field_integrity` 契约，使“价格表未知且 cost 为 null”与“价格已知但漏算”明确区分；既有 calibration 原始结果不回写。当前 artifact 位于 `artifacts/evaluation/calibration/`，已经 fail closed，且源码身份随后发生变化，不得自动 `--resume`。任何后续付费运行都需要新的明确授权；完整五组矩阵仍未授权。

## 2026-07-23 `calibration-current` 失败与修复

后续实验 `artifacts/evaluation/calibration-current/` 在完成 2 个 run、并把第 3 个 run 写成 terminal error 后停止。账本共结算 973,999 Token，其中实际 input 857,144、实际 output 86,003，另因旧适配器未读取异常所带 usage 而保守计入 unknown 30,852；`fail_closed_reason=model_call_error:LengthFinishReasonError`。失败发生在 `medium-001/baseline` 的 `claim_citation_scorer`：36 个候选 Claim 被一次性要求在 2,048 output-token 上限内连同原文和 citation IDs 完整回传，结构化输出被截断。剩余 3 个 calibration run 没有派发。

修复后的 `evaluation-claim-scorer-v4` 不再让模型回显项目已经确定的文本和引用，按 6 条确定性分批且每批只携带相关 source registry；Tavily 与 governed retrieval 只按显式 canonical URL/Evidence ID 绑定，歧义来源保持未绑定。单 run 最多允许 22 个 scorer provider call（即 132 个候选 Claim），报告结构和该上限都在 7 个 DeepEval Judge 之前做无模型 preflight。若供应商异常的 completion 中存在完整 usage，账本会精确结算但仍保持错误熔断；usage 确实缺失时才按 unknown fail closed。旧 `calibration-current` 的 terminal journal、账本与实验 identity 均不可改写或恢复；一键入口默认改用新的 `artifacts/evaluation/calibration-v4`，已有目录必须显式请求并通过安全检查才允许 resume。本次修复只运行离线测试，没有再次调用 Qwen、Tavily、DeepEval Judge 或 LangSmith。
