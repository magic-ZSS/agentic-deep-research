# 阶段 7 本地 Demo

此 Demo 不需要 API key，也不会运行模型或搜索。

```powershell
conda activate open-deep-research
python scripts/run_eval.py --mode smoke --variants all --dataset-version v1 --output artifacts/evaluation/smoke
python scripts/validate_phase.py --phase 7
```

第一条命令应生成 45 条 smoke 记录。第二条命令在尚未授权 full evaluation 时应返回非零：T7-3、T7-4、T7-6 和 T7-9 会明确报告缺少 live/full evidence；这不是 smoke 失败，也不能改写为通过。

查看 `artifacts/evaluation/smoke/report.md` 获取离线结果，查看 `manifest.json` 核对 SHA-256。完整消融只有在用户批准预算后才运行；不得用 smoke/fake 数据宣称线上质量或成本收益。
