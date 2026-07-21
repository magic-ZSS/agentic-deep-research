# 会话交接

## 当前目标

- `phase-0-baseline-references-001` 至 `phase-5-memory-system-001` 均为 `completed`。
- 阶段 5 于 2026-07-21 收口；T5-1 至 T5-16 均有确定性离线 evidence。
- 阶段 6 尚未开始，不得自动实现 Citation Validator 或报告修复。

## 恢复入口

1. 读取 `AGENTS.md`、`feature_list.json`、`progress.md`、本文件。
2. 读取 `doc/development_plan/{README,architecture_target,reference_repositories,execution_protocol}.md`。
3. 若用户明确要求阶段 6，再读取 `phase_6_citation_validation.md`。
4. 先运行 `git status --short` 并保留用户改动。
5. 进入阶段 6 前必须运行 `conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 5`，要求 T5-1～T5-16 全部 PASS。

## 阶段 5 核心契约

- `open_deep_research.runtime.persistence.persistence_lifespan` 拥有 saver/store；禁止返回离开 context 的可用裸连接。
- `open_deep_research.runtime.graph_factory.open_deep_research_graph` 在 lifespan 内编译 root builder；旧 `deep_researcher` export 继续兼容默认关闭路径。
- Namespace 只由 `RuntimeIdentity` 生成；Memory tool 不接受 tenant/user/project/namespace 参数。
- Working Memory 为 checkpoint state；Episodic/Semantic/Procedural/Preference 为长期 Memory。
- 长期写入一律 proposal → 七项 Gate → decision/audit；Agent 没有 raw put/delete/force 工具。
- Semantic recall 必须重新验证 Evidence；Procedural 最少三次成功且需 regression/approval；Preference 仅接受明确 statement ref。
- `memory_search` 只读且仅在能力 ready、配置启用时注册。
- checkpoint、checkpoint store、knowledge、run evidence、memory 使用不同 SQLite 文件；serializer 禁用 pickle fallback。

## 已验证命令

- `scripts/validate_phase.py --phase 4`：退出码 0，T4 全 PASS。
- 阶段 5 unit/security/integration：21 passed。
- `scripts/resume_research.py --self-test`：退出码 0。
- `scripts/validate_phase.py --phase 5`：退出码 0，T5-1～T5-16 全 PASS。
- 阶段 3/baseline 回归：30 passed。
- Phase 4 MCP 回归：30 passed、1 skipped（显式 Windows stdio marker）。
- Ruff 阶段范围、mypy 隔离阶段范围、compileall、`git diff --check`：通过。
- 未调用远程模型、Web、LangSmith、LLM Judge。

## 下一步

等待用户明确下达阶段 6 指令。收到后先复核阶段 5 门禁；未通过必须停止。不得自动开始阶段 6。
