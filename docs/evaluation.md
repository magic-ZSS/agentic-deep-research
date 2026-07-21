# 本地评测与消融协议

阶段 7 只把 `tests/baseline/cases.jsonl` 作为 case ID、prompt、difficulty、Requirement 与预算的权威来源。`tests/evaluation/goldens.v1.jsonl` 只能补充参考答案、来源、时间上下文、Memory setup 与 variant-specific tool policy；加载器会拒绝重复 prompt/Requirement、未知 case 和版本漂移。

默认 smoke 完全离线：

```powershell
conda run --no-capture-output -n open-deep-research python scripts/run_eval.py --mode smoke --variants all --dataset-version v1 --output artifacts/evaluation/smoke
conda run --no-capture-output -n open-deep-research python scripts/compare_ablations.py --input artifacts/evaluation/smoke --output artifacts/evaluation/smoke/report.json
conda run --no-capture-output -n open-deep-research python scripts/render_eval_report.py --input artifacts/evaluation/smoke/report.json --output artifacts/evaluation/smoke/report.md --readme README.md
```

Smoke 的 45 条记录仅证明 9 个 canonical case、5 个 variant、配置公平性、工具 registry、schema、自定义硬规则和 artifact 链路可执行。它不证明 Task Completion、引用质量提升、Memory 收益或 Web/token 降低。

Full evaluation 必须同时满足 `ODR_EVAL_MODE=full`、`RUN_FULL_EVAL=1`、`--confirm-cost` 和 `--repeats 3`，并且只能在用户确认 case、模型、调用量和预算后运行。DeepEval/Confident 上传默认关闭；LangSmith `tests/run_evaluate.py` 是另一条有成本的历史路径，不属于本地默认验收。

五组 variant 使用同一数据、模型、Tavily 限制和运行预算；每组从相同只读知识快照开始，允许写回或 Memory 的组使用独立 clone。所有输出由相同 `evaluation-claim-scorer-v1` 只读评分，Tool Correctness 使用该 variant 的实际工具 registry。

机器产物包括 `runs.jsonl`、`report.json`、`report.md`、`experiment.json` 和 `manifest.json`。未知 token 或成本写 `null`，skip/error 原样保留且不计 pass。
