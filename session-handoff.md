# 会话交接

## 当前目标

- `phase-0-baseline-references-001` 至 `phase-6-citation-validation-001` 均为 `completed`。
- 阶段 6 于 2026-07-21 收口；T6-1 至 T6-18 均有确定性离线 evidence。
- 阶段 7 尚未开始，不得自动执行完整 DeepEval、LLM Judge 或展示改造。

## 恢复入口

1. 读取 `AGENTS.md`、`feature_list.json`、`progress.md` 和本文件。
2. 读取 `doc/development_plan/{README,architecture_target,reference_repositories,execution_protocol}.md`。
3. 若用户明确要求阶段 7，再读取 `phase_7_evaluation_and_showcase.md`。
4. 先运行 `git status --short` 并保留用户改动。
5. 进入阶段 7 前必须运行 `conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 6`，要求 T6-1～T6-18 全部 PASS。

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

等待用户明确下达阶段 7 指令。收到后先复核阶段 6 门禁；未通过必须停止。不得自动开始阶段 7。
