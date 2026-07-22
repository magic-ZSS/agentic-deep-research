# 阶段 7 本地 Demo

此 Demo 不需要 API key，也不会运行模型或搜索。

```powershell
conda activate open-deep-research
python scripts/run_eval.py --mode smoke --variants all --dataset-version v1 --output artifacts/evaluation/smoke
python scripts/validate_phase.py --phase 7
```

第一条命令应生成 45 条 smoke 记录。第二条命令在尚未授权 full evaluation 时应返回非零：T7-3、T7-4、T7-6 和 T7-9 会明确报告缺少 live/full evidence；这不是 smoke 失败，也不能改写为通过。

查看 `artifacts/evaluation/smoke/report.md` 获取离线结果，查看 `manifest.json` 核对 SHA-256。完整消融只有在用户批准预算后才运行；不得用 smoke/fake 数据宣称线上质量或成本收益。

以下命令是 2026-07-21 已获授权并执行过的 calibration 记录，仅用于复现命令形状，不要直接重跑：

```powershell
$env:ODR_EVAL_MODE="full"
$env:RUN_FULL_EVAL="1"
python scripts/run_eval.py --mode calibration --variants 'baseline,citation_validator' --dataset-version v1 --repeats 1 --max-total-tokens 3000000 --confirm-cost --output artifacts/evaluation/calibration
```

该实验在 632,627 Token、3/6 个 run record 时因 usage 超 reservation 2 Token 自动 fail closed；`artifacts/evaluation/calibration/` 保留完整 stopped diagnostic。当前源码已增加 provider output accounting margin 和异常后持久化，尚未重新付费验证。完整消融矩阵没有授权，旧 calibration 也不得自动恢复；任何进一步模型调用都必须重新取得明确费用授权。
