# Phase 7 本地完整评测

本地 `JSON/JSONL/Markdown/manifest` 是唯一权威结果。LangSmith 只是在显式选择后上传去敏摘要的可选镜像；上传失败会写入本地 `tracking-errors.jsonl`，不会重新运行已经付费完成的研究或 Judge 步骤。

## 最快启动

默认直接使用现有 `open-deep-research` 环境。如果 `pip check` 报告版本冲突，只需执行一次锁定修复：

```powershell
conda run --no-capture-output -n open-deep-research python -m pip install -c constraints/evaluation-py311.txt deepeval==4.1.1 click==8.3.1 huggingface-hub==1.4.1
```

此后在仓库根目录运行一条命令：

```powershell
.\scripts\run_phase7_full.cmd -ConfirmCost
```

脚本会依次完成环境与源码检查、无网络 smoke、新 6-run calibration、只读 full 投影、54-run 完整消融、报告生成和 Phase 7 验收。在 full 派发前，它会显示实测 Token 投影；输入 `FULL` 才继续，直接回车则只保留已完成的 calibration。

恢复不是默认行为。只有确认目录是健康中断、而不是 terminal failure 后才运行：

```powershell
.\scripts\run_phase7_full.cmd -ConfirmCost -ResumeCalibration
```

脚本会在任何新付费调用前检查 journal、Token ledger、未结算调用与完整实验 identity。`stopped`、`fail_closed`、unknown usage 或 identity 不一致时会保留原目录并拒绝恢复；此时必须审阅诊断记录并指定一个尚不存在的新 `-CalibrationOutput`，不得删除或改写旧账本。

只有在已经审阅投影并明确愿意跳过交互确认时，才使用：

```powershell
.\scripts\run_phase7_full.cmd -ConfirmCost -ApproveFull
```

可选 LangSmith 镜像仍需先在当前 shell 设置 `LANGSMITH_API_KEY`，再增加 `-Tracking langsmith -LangSmithProject phase7-local-full`。默认 `local` 不上传任何内容。

下面各节保留手动命令，供排错、审计或单独恢复某一步使用；正常运行不需要逐条复制。

## 0. 先冻结评测源码

付费入口只接受干净且固定的评测相关源码。先审阅并提交当前 Phase 7 实现，再确认：

```powershell
git status --short
git rev-parse HEAD
```

生成的 smoke artifact 会同时记录生成时的 `HEAD` 和按路径/bytes 计算的评测源码快照 SHA-256；validator 会将内容 hash 与当前工作树重新比对。同一源码内容在提交前后保持相同 hash，文档、状态文件和生成 artifact 不参与快照，因此写出或提交展示结果不会改变自己的内容身份；dirty-source smoke 在源码提交后仍须重建一次，以绑定可追溯 commit。Calibration/full 更严格，相关源码有任何 staged、unstaged 或 untracked 改动都会在外部调用前拒绝。

## 1. 验证当前评测环境

真实 full 使用现有 `open-deep-research` conda 环境，并要求 Python 3.11 与 DeepEval、Click、Hugging Face Hub 的锁定版本完全一致。

```powershell
conda run --no-capture-output -n open-deep-research python -m pip check
conda run --no-capture-output -n open-deep-research python -c "import deepeval; import open_deep_research.evaluation.full_runner"
```

`constraints/evaluation-py311.txt` 固定 `deepeval==4.1.1`、`click==8.3.1` 和 `huggingface-hub==1.4.1`。CLI 会再次检查 Python、上述版本、`pip check` 和实际 import smoke；任一失败都会在模型、搜索或 LangSmith 调用前停止。

2026-07-23 已在当前 `open-deep-research` 环境验证 Python 3.11.15、`deepeval==4.1.1`、`click==8.3.1`、`huggingface-hub==1.4.1`，且 `pip check` 与真实 import smoke 均通过。

在新环境内先重建无网络 smoke，并确认只有尚缺 live artifact 的门禁失败：

```powershell
python scripts/run_eval.py `
  --mode smoke `
  --variants all `
  --dataset-version v1 `
  --output artifacts/evaluation/smoke
python scripts/validate_phase.py --phase 7
```

在 full 尚未运行时，validator 预期以退出码 `1` 报告 T7-3、T7-4、T7-6、T7-9 缺少 live evidence；其他项目必须通过。这个预期失败不是 calibration/full 授权。

## 2. 先生成新的完整 calibration

仓库现有 `artifacts/evaluation/calibration/` 和 `artifacts/evaluation/calibration-current/` 都是已停止的诊断记录，不能恢复，也不能授权 full。后者因旧 v3 claim scorer 在 2,048 output-token 上限内一次回传 36 个 Claim 而触发 `LengthFinishReasonError`；其 973,999 Token 账本和 terminal journal 必须保持原样。v4 会把这 36 个 Claim 固定拆成 6 次小批 Judge，并在任何 DeepEval Judge 前执行报告结构与 132-candidate 硬上限检查。真实 full 必须由修复后的 v4 scorer 使用当前源码、计划、消融配置、数据集和模型重新完成 6-run calibration，并通过 manifest、journal、Token ledger 与保守投影校验。

Calibration 本身仍需要单独费用授权。授权后使用：

```powershell
$env:ODR_EVAL_MODE="full"
$env:RUN_FULL_EVAL="1"
python scripts/run_eval.py `
  --mode calibration `
  --variants baseline,citation_validator `
  --dataset-version v1 `
  --repeats 1 `
  --max-total-tokens 3000000 `
  --confirm-cost `
  --output artifacts/evaluation/calibration-v4
```

只有 `status=completed`、6 个 journal terminal run、每个 run 的 research + 7 个 DeepEval Judge + 独立 claim/citation scorer 共 9 个付费步骤均终态、无 in-flight/unknown usage，且“已消耗 calibration Token + p95 × 54 × 1.25”的保守投影不超过 full 上限时，full 才会放行。

## 3. 无费用读取 full preflight 与投影

Calibration 完成后，先运行只读 preflight。这个模式不接受费用授权，不构造 tracking sink，也不可能调用研究图、模型、搜索或 full executor：

```powershell
python scripts/run_eval.py `
  --mode full `
  --preflight-only `
  --variants all `
  --dataset-version v1 `
  --repeats 3 `
  --max-total-tokens 42000000 `
  --calibration-output artifacts/evaluation/calibration-v4 `
  --output artifacts/evaluation/full
```

退出码 `0` 且 `status=ready_for_separate_full_authorization` 时，stdout 会给出模型 ID、54-run 矩阵、实测保守 Token 投影、预估模型/Tavily调用区间和固定预算；未知美元成本保持 `null`。先审阅这些值，再另行授权 full。Calibration 到 full 之间不要修改、提交或生成新的仓库文件；projection 会在 full artifact 中自动持久化，不要把 preflight stdout 重定向回仓库。

## 4. 启动固定 54-run full

完整矩阵固定为 45 个主配对（5 variants × 3 cases × 3 repeats）和 9 个 cold/warm run。不能缩小 variants、改变 repeats 或降低/提高固定 Token policy：36,000,000 停止派发，42,000,000 硬上限，单 run 800,000 上限。

```powershell
$env:ODR_EVAL_MODE="full"
$env:RUN_FULL_EVAL="1"
python scripts/run_eval.py `
  --mode full `
  --variants all `
  --dataset-version v1 `
  --repeats 3 `
  --max-total-tokens 42000000 `
  --confirm-cost `
  --calibration-output artifacts/evaluation/calibration-v4 `
  --tracking local `
  --output artifacts/evaluation/full
```

首次运行与恢复都要求评测相关源码处于 clean 状态，并保持相同 commit、plan、ablation、模型和数据集；恢复时增加 `--resume`。稳定 run/step ID 会跳过已完成步骤；manifest、报告或 LangSmith 镜像失败不会导致付费步骤重跑。发现未知 usage、异常 reservation、重复错误、失败率、失败 Token 或预算越界时，runner 会停止后续派发。

## 5. 可选 LangSmith 镜像

只有确实需要远程查看时才开启：

```powershell
$env:LANGSMITH_API_KEY="<set-in-shell-only>"
python scripts/run_eval.py `
  --mode full `
  --variants all `
  --dataset-version v1 `
  --repeats 3 `
  --max-total-tokens 42000000 `
  --confirm-cost `
  --calibration-output artifacts/evaluation/calibration-v4 `
  --tracking langsmith `
  --langsmith-project phase7-local-full `
  --output artifacts/evaluation/full
```

镜像只允许公开 ID、状态、哈希、数值 telemetry 和指标值。报告正文、trace、配置、reason、state artifact、路径、endpoint 和任何 secret 都不会上传。`LANGSMITH_API_KEY` 只从进程环境读取，不写入 artifact。

## 6. 结果与验收

成功或安全停止后，本地目录包含：

- `experiment.json`
- `runs.jsonl`
- `report.json`
- `report.md`
- `budget.json`
- `journal.json`
- `manifest.json`
- `tracking-errors.jsonl`（仅发生镜像错误时）

`report.json` 统计七项 DeepEval 指标、自定义 Citation/Source/Memory/Cost 指标、paired delta、mean/std/95% CI 和 cold/warm Token/Web 变化，并程序化给出 T7-3、T7-4、T7-6、T7-9。`fake`、`smoke`、`calibration`、skip、error 或未知成本均不能被计为 live pass。

```powershell
python scripts/validate_phase.py --phase 7
```

完整 runner 已直接生成权威 `report.json` 和 `report.md`，不要再对 full 目录运行 `scripts/compare_ablations.py`，否则会把丰富统计降级成 smoke 聚合。若验收通过，可从同一机器报告刷新 README 展示块；重新渲染的 Markdown 必须与 manifest 中已有文件逐字节一致：

```powershell
python scripts/render_eval_report.py `
  --input artifacts/evaluation/full/report.json `
  --output artifacts/evaluation/full/report.md `
  --readme README.md
python scripts/validate_phase.py --phase 7
```

Phase 7 只有在真实 full artifact 完整且 T7-1 至 T7-17 全部通过后才能标记为 `completed`。
