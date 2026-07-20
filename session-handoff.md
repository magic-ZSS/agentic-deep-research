# 会话交接

## 当前目标（Current Objective）

- `phase-0-baseline-references-001`、`phase-1-knowledge-evidence-models-001`、`phase-2-document-ingestion-paperqa-001`、`phase-3-agentic-rag-lifecycle-001` 均已完成。
- 阶段 3 于 2026-07-21 收口；T3-1 至 T3-20 全部有确定性离线测试 evidence。
- 阶段 4 尚未开始；下一会话必须先重新核验阶段 3 状态和 T3 evidence，不能直接绕过门禁。

## 阶段 3 交付物

- Requirement/coverage/completion：`src/open_deep_research/research/`。
- run-scoped evidence：`src/open_deep_research/evidence/run_store.py`。
- 共享预算、Web adapter、orchestrator/runtime：`src/open_deep_research/knowledge/retrieval/`。
- 统一候选 Gate：`src/open_deep_research/knowledge/validation/`。
- 六态生命周期/proposal：`src/open_deep_research/knowledge/lifecycle/`。
- schema v3：`src/open_deep_research/storage/migrations/v3.py` 与 InMemory/SQLite Repository 扩展。
- 唯一 Agentic 工具入口：`src/open_deep_research/tools/governed_retrieval.py`。
- 最小路由/状态/prompt/恢复修复：`configuration.py`、`state.py`、`utils.py`、`prompts.py`、`deep_researcher.py`。
- 验收：`scripts/validate_phase.py --phase 3`、`tests/unit/{research,evidence,knowledge,tools}` 与 `tests/integration/agentic_rag/`。

## 阶段 3 已验证状态

- 开始前 `scripts/validate_phase.py --phase 2`：退出码 0，内部 `83 passed`，T2-1 至 T2-15 全部 PASS。
- 最终阶段 3 映射 suite：退出码 0，`103 passed, 0 skipped, 30 warnings`；T3-1 至 T3-20 的直接测试均通过。
- knowledge/legacy 回归：退出码 0，`19 passed`；图治理+validator 自测：退出码 0，`18 passed`；missing-only orchestrator：退出码 0，`11 passed`。
- `python -m compileall -q src scripts tests`、`git diff --check`：退出码 0。
- 较早聚合 `scripts/validate_phase.py --phase 3` 运行退出码 0（内部 `101 passed`，T3-1 至 T3-20 PASS）。最终两项测试/映射修订后的首次重跑暴露并已修复 validator 中误置的 `basetemp` 定义；修复后 pytest 完成功能测试，但 Windows 工具沙箱在 basetemp session-finish 遇到 `WinError 5`。沙箱外重跑申请因执行额度被系统拒绝，因此未获得新的干净 wrapper 退出码；最终 103 项映射 suite 和 validator 自测均另行退出码 0。不得把 NameError/ACL 失败描述为 wrapper 通过或伪造结果。
- `ruff`、`mypy` 未安装；未调用真实模型、Web、MCP、LangSmith 或 LLM Judge。

## 阶段 3 关键契约

- brief 先生成稳定 `RequirementSet`；active Evidence 对当前 Requirement 重跑 Gate。必需 gap 未覆盖且预算未耗尽时不能完成，blocked/预算耗尽时必须输出明确 gap。
- 同轮 `ConductResearch + ResearchComplete` 和 Researcher tool+complete 都先执行工具，再重算 coverage；一个并行任务失败不得吞掉成功结果。
- Agentic 开启后没有 Web 旁路：Tavily 只经 governed adapter，MCP 不绑定，当前 OpenAI/Anthropic provider-native 搜索 fail closed。本地足够时 Web 调用严格为 0，不足时只查 missing aspects。
- 本地 candidate 与 Web candidate 经过同一 ValidationGate。Web evidence 先进入 scope+run 隔离的 `RunEvidenceStore`；writeback 关闭时 canonical Repository 零新增，另一 run 不可见。
- canonical 生命周期只有六态和明确允许边；所有失效/隔离/替代/删除均为 proposal+soft transition，并追加 audit；无 hard delete API。
- legacy augmentation 仅返回 active+validated knowledge 并保留旧 Web；所有知识开关关闭时工具清单和图路径回到 baseline。
- compression token-limit 分支使用配置的 compression model 真正重试；think/error/limit ToolMessage 只进诊断 trace，不进结构化证据或 Writer 引用输入。

## 阶段 2 交付物（历史）

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
- Agentic run budget 由 scope+run 共享、原子扣减且失败也计数；调整默认预算必须同时复验并发和成本门禁。
- 当前 deterministic Gate 是保守的第一版；新增 provider 或更复杂的权威/时效策略必须继续走同一 contract，不能增加旁路或 prompt-only 判断。
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
9. 用户明确指定的阶段文档；若执行下一阶段，则为 `doc/development_plan/phase_4_mcp_integration.md`

## 下一步

等待用户明确下达阶段 4 指令。收到后先确认 `phase-3-agentic-rag-lifecycle-001=completed`，并运行/核验阶段 3 验收及其已记录的 Windows ACL 证据；门禁未通过必须停止。不得自动实现阶段 4 或更后阶段。
