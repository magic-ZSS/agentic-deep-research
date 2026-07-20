# 会话交接

## 当前目标（Current Objective）

- `phase-0-baseline-references-001`、`phase-1-knowledge-evidence-models-001`、`phase-2-document-ingestion-paperqa-001` 均已完成。
- 阶段 2 于 2026-07-21 收口；T2-1 至 T2-15 全部有机器可执行 evidence。
- 阶段 3 尚未开始；下一会话必须先重新核验阶段 2 状态和 T2 evidence，不能直接绕过门禁。

## 阶段 2 交付物

- 导入模型/服务：`src/open_deep_research/knowledge/ingestion/`。
- 四类 parser：`src/open_deep_research/knowledge/ingestion/parsers/`。
- ImportJob migration/Repository：`src/open_deep_research/storage/migrations/v2.py` 及 knowledge repositories。
- 检索边界：`src/open_deep_research/knowledge/retrieval/`。
- PaperQA 隔离层：`src/open_deep_research/knowledge/paperqa_adapter.py`。
- 管理 inspection contract：`src/open_deep_research/tools/knowledge.py`。
- CLI：`scripts/ingest_knowledge.py`、`scripts/search_knowledge.py`。
- 依赖门禁：`scripts/check_phase2_dependencies.py`、`doc/development_plan/phase_2_dependency_matrix.md`。
- 阶段验收：`scripts/validate_phase.py --phase 2`。
- fixtures/tests：`tests/fixtures/knowledge/`、`tests/unit/knowledge/`、`tests/unit/tools/test_knowledge_tools.py`、`tests/integration/knowledge/`、`tests/integration/storage/test_phase2_repository_contract.py`。

## 已验证状态

- `scripts/validate_phase.py --phase 2`：退出码 0；内部 `83 passed, 0 skipped`，T2-1 至 T2-15 全部 PASS。
- 四格式本地 CLI 在 `artifacts/phase2/final-cli-20260721-c/` 完成 dry-run、candidate 导入、Repository/PaperQA inspection 与 active-only 空结果验证；不匹配 scope 的历史查询导入会以 `missing_scope` 拒绝。
- `scripts/validate_phase.py --phase 1`：退出码 0，T1-1 至 T1-16 全部 PASS。
- `scripts/validate_phase.py --phase 0`：退出码 0，T0-1 至 T0-12 全部 PASS。
- 全量离线 pytest：退出码 0，`147 passed, 1 skipped, 30 warnings`；唯一 skip 为未安装的可选 DeepEval adapter。
- 既有 Researcher 限制测试：退出码 0，`7 passed`。
- dependency smoke、`pip check`、compileall、`git diff --check`、`git diff --check HEAD^`：退出码均为 0；当前工作树和阶段整体差异均无 whitespace error。
- legacy 只做 collect：退出码 0，收集 1 项；未修改或执行 legacy 测试正文。
- `ruff`、`mypy` 在目标 conda 缺失，退出码 1；没有伪报通过。
- 未调用真实模型、远程 embedding、Web、LangSmith、Deep Research Bench 或 LLM Judge。

## 固定依赖与参考

- Python：`3.11.15`，Windows AMD64。
- `paper-qa==2026.3.18`
- `paper-qa-pypdf==2026.3.18`
- `tantivy==0.26.0`
- `fhaviary==0.34.0`
- `fhlmi==0.45.0`
- `litellm==1.82.4`
- PaperQA 参考提交：`d7675d7b7eddeb3535e8c260399c5bbeeb818c50`。

## 关键契约

- Repository/ContentBlob 是唯一权威存储；PaperQA 是从 scope-aware records 重建的派生检索状态。
- 导入只接受调用方提供的本地 bytes，不打开 `input_ref`、不抓取 URL；CLI 只遍历显式 root，并拒绝 symlink/path escape。
- 新 Version 固定为 `candidate`，新 Evidence 固定为 `pending`；index ready 不等于 active，不可引用。
- Candidate 只能由可信 inspection capability 显式查看；生产 Researcher 没有绑定 `knowledge_search/read`。
- scope/filter/as_of/lifecycle 在 backend 前后程序化执行；PaperQA 返回 ID 不能成为 canonical ID。
- `paperqa.ask`、`Docs.aquery`、answer API 和 Agent loop 禁止；只允许 raw text retrieval。contextual provider 必须注入并受并发/timeout/token 限制。
- 关闭 `enable_knowledge_base`、`enable_paperqa_retrieval` 和 contextual 开关后，不导入 PaperQA、不创建存储或索引，旧图保持不变。
- `notes`、`raw_notes`、`compressed_research` 兼容路径未改变。

## 环境缺口与风险

- Windows pytest 沙箱临时目录可能在 session-finish 触发 `WinError 5`；使用唯一 `--basetemp=.phase-validation-tmp/<run-id>`，必要时按审批在沙箱外运行。不要擅自删除旧受限目录。
- `ruff`/`mypy` 未安装；后续若需要补齐静态检查，应先取得用户依赖安装授权。
- 当前 PaperQA inspection 每次从 Repository rehydrate，安全但可能较慢；未来可信 manifest 不能替代权威 Repository。
- deterministic query embedding 当前会计算两次；无外部成本，但未来换模型时需控制计费。
- `packaging==25.0`、`click==8.4.2` 是安装 knowledge extra 后的解析结果；当前 `pip check`/全量回归通过。
- 许可证边界见 `doc/development_plan/phase_2_dependency_matrix.md`，尤其既有 PyMuPDF 的 AGPL/商业双许可证风险。

## 恢复时必读

1. `AGENTS.md`
2. `feature_list.json`
3. `progress.md`
4. `session-handoff.md`
5. `doc/development_plan/README.md`
6. `doc/development_plan/architecture_target.md`
7. `doc/development_plan/reference_repositories.md`
8. `doc/development_plan/execution_protocol.md`
9. 用户明确指定的阶段文档

## 下一步

等待用户明确下达阶段 3 指令。收到后先确认 `phase-2-document-ingestion-paperqa-001=completed`，并运行 `scripts/validate_phase.py --phase 2`；门禁未通过必须停止。不得自动实现阶段 3 或更后阶段。
