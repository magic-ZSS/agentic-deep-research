# 会话交接

## 当前目标（Current Objective）

- `phase-0-baseline-references-001` 已完成，T0-1 至 T0-12 已在阶段 1 启动前重新验证通过。
- `phase-1-knowledge-evidence-models-001` 正在执行；只允许完成阶段 1，阶段 2 尚未开始。

## 阶段 0 交付物

- 参考仓库：`.gitmodules`、`doc/reference/refs.lock.json`、`doc/reference/README.md`、`THIRD_PARTY_NOTICES.md`。
- Baseline：`tests/baseline/cases.jsonl`、`tests/baseline/baseline_manifest.json`、`tests/baseline/fixtures/simple-001.replay.json`。
- 评测骨架：`src/open_deep_research/evaluation/`，包括 schema、存储、指标、telemetry、DeepEval adapter、费用门禁和 manifest。
- 执行入口：`scripts/run_baseline.py`、`scripts/capture_baseline_manifest.py`、`scripts/validate_phase.py`。
- 测试门禁：`tests/conftest.py`、`tests/evaluation/`、`tests/baseline/`，以及受保护的既有外部评测脚本。
- 配置：Python 3.11+、显式 evaluation 子包、可选 `eval` extra、pytest 默认禁用 DeepEval plugin/cache，并排除参考仓库和产物目录。

## 恢复时必读

1. `AGENTS.md`
2. `feature_list.json`
3. `progress.md`
4. `session-handoff.md`
5. `doc/development_plan/README.md`
6. `doc/development_plan/architecture_target.md`
7. `doc/development_plan/reference_repositories.md`
8. 用户明确指定的下一阶段文档

## 已验证状态

- 离线全套 pytest：`51 passed, 1 skipped`，退出码 0。
- Phase 0 定向 smoke：`44 passed, 1 deselected`，退出码 0。
- 既有 `tests/test_research_limits.py`：`7 passed`，退出码 0。
- `scripts/validate_phase.py --phase 0`：T0-1 至 T0-12 全部 PASS，退出码 0。
- replay：退出码 0，结果保存在忽略目录 `artifacts/baseline/smoke.jsonl`。
- live：未授权，在任何外部调用前返回 `not_run_no_authorization`，退出码 3，未生成伪结果。
- 未运行真实模型、搜索、LangSmith、Deep Research Bench 或 LLM Judge。

## 关键边界与风险

- 所有 Phase 0 能力默认关闭；核心 Supervisor—Researcher、搜索、Writer、`notes/raw_notes/compressed_research` 行为未修改。
- `doc/reference/` 是固定只读参考源码，不得 lint、格式化或编辑；commit 与许可证以 `refs.lock.json` 为准。
- DeepEval 是可选依赖，当前环境未安装；真实 adapter/full-eval 验证需后续明确授权。
- 真实 live 仍没有费用授权，不能把 replay 当作 live。
- `ruff` 和 `mypy` 在 `open-deep-research` conda 环境缺失；不要伪报通过或未经许可安装。
- telemetry 对不可证明的数据使用 `null`/完整性标志；不要把未知 token、成本、搜索或 Researcher 次数改写成 0。
- JSONL 跨进程写入按单 writer 约定；如未来需要多进程并发，应先设计文件锁或集中 writer。

## 下一步

等待用户明确下达阶段 1 指令。未收到该指令前，不实现知识模型、Repository、SQLite 或任何后续阶段功能。
