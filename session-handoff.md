# 会话交接

## 当前目标

- `phase-0-baseline-references-001` 至 `phase-6-citation-validation-001` 均为 `completed`。
- `phase-7-evaluation-showcase-001` 为 `in-progress`：本地 54-run full 执行、恢复、预算、报告与 validator 已完成；真实 calibration/full 尚未重新授权执行。
- 不得自动安装 DeepEval、运行模型/Tavily/LLM Judge、push 或发布。

## 恢复入口

1. 读取 `AGENTS.md`、`feature_list.json`、`progress.md` 和本文件。
2. 读取 `doc/development_plan/{README,architecture_target,reference_repositories,execution_protocol}.md`。
3. 读取 `phase_7_evaluation_and_showcase.md` 和 `tests/evaluation/full_plan.v1.json`。
4. 先运行 `git status --short` 并保留用户改动。
5. 阶段 6 门禁已复核通过；不要重复执行付费任务。

## 阶段 7 恢复点

- 2026-07-22 离线精确套件共 169 passed：core 131、full runner 14、strict validator 4、其余 Phase 7 contracts 20；新增范围 Ruff 与 compileall 通过。
- `validate_phase --phase 7` 当前按设计仅 T7-3/4/6/9 FAIL（缺少真实 full artifact），其余验收 PASS；Phase 7 因此保持 `in-progress`。
- smoke artifact：45 records，manifest 校验通过，README 由机器 JSON 驱动。
- smoke artifact 已于 2026-07-22 离线刷新为统一 `evaluation-claim-scorer-v3`，并记录生成时 `HEAD`、绑定按限定文件路径/bytes 计算的评测源码快照 `67446f46bef1...`；validator 会拒绝旧 scorer、不完整矩阵或源码内容漂移。
- source snapshot 的内容 hash 在相同源码提交前后稳定，且排除 docs/status/artifacts，因而结果提交不会造成自引用；dirty-source smoke 在源码提交后须重建，paid calibration/full 始终要求相关源码 clean。新增收口聚焦回归 53 passed，精确 Ruff、隔离 Mypy 与 compileall 通过。
- 新增 read-only `--preflight-only`：完成新 calibration 后先输出实测 projection和调用区间，再等待用户单独授权 full；该路径不会调用图、模型、搜索、Judge或LangSmith，也不会写 output。
- `scripts/render_eval_report.py` 现在可直接渲染 full rich report 和机器驱动 README 表格；不要对 full 目录运行仅适用于 smoke 的 `scripts/compare_ablations.py`。
- full runner 固定 45 main + 9 warm；每 run 依次持久化 research、7 个 DeepEval metric、独立 v3 claim scorer与终态，恢复不会重放 terminal step。
- 反浪费门禁：3600 万停派、4200 万硬限、单 run 80 万；跨进程 lease、逐 run 对账、unknown usage、异常 reservation、连续失败、重复错误签名、失败率、失败/retry Token与校准投影均可熔断。
- 本地 artifact 是权威；`--tracking langsmith` 只镜像去敏元数据和指标，失败记本地 tracking error，不重跑研究。
- 真实入口要求 clean evaluation source 与 `open-deep-research-eval` Python 3.11 独立环境；当前 Windows 开发环境另有系统策略阻止 `uuid_utils` 原生 DLL 的已知缺口。
- 独立评测环境目前只有配置与 fake/offline 门禁证据，尚未实际新建并运行真实 `pip check`/import smoke；这是下一次付费授权前必须消除的环境风险。
- 既有 calibration 是 3/6、632,627 Token 的 stopped diagnostic，保持原样且不得 `--resume`；新的 calibration 必须使用新 output 和新的明确费用授权。

## 阶段 6 核心契约

- citation identity 固定为 `(source_id, version_id)`；locator 不参与身份，但在同一 registry entry 内合并。
- Claim 各自拥有 evidence links/results，不继承相邻句引用。
- EvidenceResolver 只解析授权 scope 的 canonical Evidence 或同 run transient Evidence；跨 run/scope fail closed。
- directness、temporal、authority 和数字一致性是独立硬门禁，不能用综合分数覆盖。
- 显式错误引用保留 `explicit_draft_citation` 失败记录；supplemental evidence 不能洗白。
- repair 以 section original hash 防护，只修改失败 Claim 所在局部；未受影响 section hash 不变。
- 最终编号和来源表只由程序生成；legacy `SOURCE n/[n]` 与诊断消息先剥离。
- `off` 为 legacy no-op，`audit` 不改 Writer 报告，`enforce` 对失败 Claim 和依赖缺失 fail closed。
- 本地路径/internal storage ref 不得进入正文、来源表或 validation artifact。

## 已验证命令

- Phase 5 gate：退出码 0，T5 全 PASS。
- Phase 6 unit：20 passed。
- Phase 6 integration：9 passed。
- Phase 6 validator：退出码 0，T6-1～T6-18 全 PASS，内部 29 tests。
- Phase 3/5 regression：35 passed。
- `validate_report.py`、Ruff、Mypy（18 files）、`git diff --check`：均退出码 0。
- 未调用远程模型、Web、LangSmith、Deep Research Bench 或 LLM Judge。

## 下一步

先审阅并提交本轮 Phase 7 评测实现，使 clean-source gate 可验证固定 commit；随后如用户重新明确授权，再在独立评测环境运行新的最多 300 万 Token calibration。主矩阵仍需根据新 calibration 投影单独授权，完成前 Phase 7 保持 `in-progress`。
