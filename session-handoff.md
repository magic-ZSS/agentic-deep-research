# 会话交接

## 当前目标

- `phase-0-baseline-references-001` 至 `phase-6-citation-validation-001` 均为 `completed`。
- `phase-7-evaluation-showcase-001` 为 `in-progress`：离线 smoke/报告已完成，等待 full 费用授权。
- 不得自动安装 DeepEval、运行模型/Tavily/LLM Judge、push 或发布。

## 恢复入口

1. 读取 `AGENTS.md`、`feature_list.json`、`progress.md` 和本文件。
2. 读取 `doc/development_plan/{README,architecture_target,reference_repositories,execution_protocol}.md`。
3. 读取 `phase_7_evaluation_and_showcase.md` 和 `tests/evaluation/full_plan.v1.json`。
4. 先运行 `git status --short` 并保留用户改动。
5. 阶段 6 门禁已复核通过；不要重复执行付费任务。

## 阶段 7 恢复点

- 离线 evaluation suite：57 passed、1 deselected；生命周期/Memory 回归：25 passed。
- smoke artifact：45 records，manifest 校验通过，README 由机器 JSON 驱动。
- 当前 T7：T7-1/2/5/7/8/10-17 PASS；T7-3/4/6/9 等待 live/full，validator 退出码 1 是预期状态。
- full 计划：3 cases、5 variants、3 repeats，加 9 次 warm，共 54 research runs；GPT-4.1/GPT-4.1 mini；估算 USD 30–100。
- 用户若明确授权费用上限，再申请安装 `deepeval==4.1.1` 所需网络权限并实现/运行授权 full 路径；否则保持 `in-progress`。

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

等待用户对 `tests/evaluation/full_plan.v1.json` 的 case、模型、调用量和费用上限作出明确授权。未授权不得继续 full。
