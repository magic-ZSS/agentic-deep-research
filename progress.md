# 会话进度记录

## 当前状态（Current State）

**最后更新（Last Updated）：** 2026-07-21

**当前功能：** `phase-2-document-ingestion-paperqa-001`

**状态：** completed（阶段 2 已收口；阶段 3 未开始）

## 阶段门禁

- `phase-0-baseline-references-001` 为 `completed`；最终回归 T0-1 至 T0-12 全部 PASS。
- `phase-1-knowledge-evidence-models-001` 为 `completed`；执行阶段 2 前及最终回归 T1-1 至 T1-16 全部 PASS。
- `phase-2-document-ingestion-paperqa-001` 为 `completed`；`scripts/validate_phase.py --phase 2` 退出码 0，T2-1 至 T2-15 全部 PASS。
- 阶段 3 仍为 `not-started`；本轮未实现 Agentic RAG、知识 writeback 或生产 Researcher 工具绑定。

## 阶段 2 交付物

- `src/open_deep_research/knowledge/ingestion/`：bytes-only `DocumentInput`、`ImportJob`、PDF/Markdown/HTML/verified past-query parser、结构化 locator 和可恢复 `IngestionService`。
- `src/open_deep_research/storage/migrations/v2.py` 与 Repository 扩展：scope-aware ImportJob、CAS 状态转换、审计、SQLite 重开和并发 claim。
- `src/open_deep_research/knowledge/retrieval/`：项目自有 `KnowledgeRetriever`、`EvidenceHit`、Repository keyword retriever、filter/as_of/candidate policy 和 stable-ID read。
- `src/open_deep_research/knowledge/paperqa_adapter.py`：懒加载的 PaperQA Adapter、Repository rehydrate、离线 Settings、确定性本地 embedding、raw retrieval、有界 contextual seam、稳定排序和安全回退。
- `src/open_deep_research/tools/knowledge.py`：仅供可信管理/内部 inspection 直接调用的 `knowledge_search/read` contract；没有注册到生产 `get_all_tools`。
- `scripts/ingest_knowledge.py`、`scripts/search_knowledge.py`：受显式本地 root 约束的导入 CLI，以及 Repository/PaperQA inspection CLI。
- `scripts/check_phase2_dependencies.py`、`doc/development_plan/phase_2_dependency_matrix.md`：Windows/Python 3.11 固定依赖、离线配置和禁止 Agent API 门禁。
- `scripts/validate_phase.py --phase 2`：T2-1 至 T2-15 的确定性、零付费阶段验收器。
- `tests/fixtures/knowledge/` 与 Phase 2 unit/integration tests：自制小 PDF、Markdown、HTML 和 verified past-query fixture；真实 PaperQA 使用本地 embedding 且 socket 被测试显式禁止。

## 核心设计与迁移决策

- SQLite Repository 和 ContentBlob 始终是权威数据；PaperQA `Docs`/索引仅是可丢弃派生状态，每次从已授权的 scope records rehydrate，不接受任意 pickle/cache。
- 原始 bytes 在解析前写入 scope-local ContentBlob；相同 bytes/配置幂等，内容变化生成不可变新 Version，所有新 Version 固定为 `candidate`。
- ImportJob 与知识生命周期分离；`index_status=ready` 不会把 Version 提升为 `active`，解析/索引失败保留结构化错误并可重试。
- PDF 使用一基页码范围，Markdown 保存完整 heading path，HTML 保存 canonical snapshot URI 与 anchor，历史查询只接受显式 verified、scope 完整且 Source/Evidence-bound 的事实。
- Repository 与 PaperQA 检索在 backend 前后都执行 scope、lifecycle、filter 和 as_of 检查；未知 backend ID、跨 scope record、零相似度和 NaN/Inf score 均 fail closed。
- PaperQA 仅调用 `Docs.aadd_texts` 与 `retrieve_texts`；不调用 `paperqa.ask`、`Docs.aquery`、answer API 或 Agent loop。上游包导入可能加载 `paperqa.agents` 模块，但本项目不调用其 API。
- contextualization 默认关闭；可选 wrapper 只接受注入 provider，并强制 evidence_k、并发、timeout 和 token 参数，不构造远程模型。
- `enable_knowledge_base=False`、`enable_paperqa_retrieval=False`、`paperqa_contextual_summarization=False`；关闭时不导入 PaperQA、不创建 `data/` 或索引，旧图和 Web 工具路径保持不变。

## T2 验收证据

| 验收项 | 结果 | 自动化证据 |
|---|---|---|
| T2-1 | PASS | 四类自制 fixture 均以 candidate 持久化；SQLite 重开后 Source/Version/Chunk/Evidence/ImportJob 可恢复。 |
| T2-2 | PASS | PDF search/read 返回 `page_start/page_end` 及对应 Source/Version 稳定 ID。 |
| T2-3 | PASS | Markdown search 返回完整、可重复的 `heading_path`，含重复标题、H6、code fence 与 CRLF 测试。 |
| T2-4 | PASS | HTML search 返回 canonical snapshot URI/anchor；同 URI 新内容创建 Version 2，旧 Version/Blob 未覆盖。 |
| T2-5 | PASS | 空库、无词项命中、PaperQA 零相似度均返回空 hits 和明确 empty reason，不生成答案或证据。 |
| T2-6 | PASS | Adapter AST 与运行探针证明未调用 ask/aquery/Agent loop；metadata、multimodal、enrichment 和网络均关闭。 |
| T2-7 | PASS | 相同内容幂等，变化内容创建新 Version；派生索引失败可在同一 job 上重试且不重复数据。 |
| T2-8 | PASS | PaperQA 只返回项目 Source/Version/Chunk/Evidence ID；重复/未知 backend hit 被过滤，同分按 chunk ID 稳定排序。 |
| T2-9 | PASS | `deep_researcher.py`/`utils.py` 与阶段 0 hash 一致；生产工具集未绑定 knowledge contract；默认关闭无副作用。 |
| T2-10 | PASS | parser/index/contextual/PaperQA 失败均结构化或显式回退，不创建 active 孤立记录。 |
| T2-11 | PASS | `knowledge_read` 只接受完整 `chk_`/`evd_` SHA-256 ID，Windows 路径和短 ID 被拒绝。 |
| T2-12 | PASS | Windows AMD64/Python 3.11.15 的精确依赖矩阵、关键 import、离线 Settings、包发现和仓库外导入全部通过。 |
| T2-13 | PASS | 新 Version 均为 candidate、Evidence 为 pending；可信 inspection 可见且 `inspection_only=true/citable=false`。 |
| T2-14 | PASS | PDF/Markdown/HTML 源文件改写并删除后，重开 Repository/Blob 仍能读取原始 bytes、核验 hash 并恢复 locator。 |
| T2-15 | PASS | fresh PaperQA backend 从权威 Repository rehydrate；scope/filter/as_of/candidate/future 均由 Adapter 程序化过滤。 |

## 固定依赖

| Distribution | 版本 |
|---|---:|
| `paper-qa` | `2026.3.18` |
| `paper-qa-pypdf` | `2026.3.18` |
| `tantivy` | `0.26.0` |
| `fhaviary` | `0.34.0` |
| `fhlmi` | `0.45.0` |
| `litellm` | `1.82.4` |

PaperQA 参考源码仍固定为 `d7675d7b7eddeb3535e8c260399c5bbeeb818c50`；发布包版本和参考提交是两条独立证据，不声称该 SHA 对应 PyPI tag。

## 验证命令与结果

- `conda run --no-capture-output -n open-deep-research python -m pip install -e ".[knowledge]"`：退出码 0；精确 knowledge extra 已安装到目标 conda 环境。
- `conda run --no-capture-output -n open-deep-research python scripts/check_phase2_dependencies.py --json`：退出码 0，`status=compatible`，Windows/Python/版本/import/offline Settings/参考 SHA/禁止 API 全部通过。
- `conda run --no-capture-output -n open-deep-research python -m pip check`：退出码 0，`No broken requirements found`。
- 阶段 2 范围测试：退出码 0，`83 passed, 25 warnings`；真实 PaperQA 两项没有 skip。
- PaperQA Adapter/CLI 定向复验：退出码 0，`23 passed`；覆盖离线 rehydrate、失败恢复和 `--paperqa` inspection。
- `conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 2`：退出码 0；内部 `83 passed, 0 skipped`，T2-1 至 T2-15 全部 PASS。
- 四格式 dry-run、实际导入、Repository inspection、PaperQA inspection 与 active-only empty CLI：各退出码 0；本地验证数据位于 gitignored `artifacts/phase2/final-cli-20260721-c/`。另用不匹配的 tenant/scope 运行实际导入时得到结构化 `missing_scope` 拒绝，未产生伪造成功结果。
- `conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 1`：退出码 0，T1-1 至 T1-16 全部 PASS。
- `conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 0`：退出码 0，T0-1 至 T0-12 全部 PASS。
- `conda run --no-capture-output -n open-deep-research python -m pytest tests/test_research_limits.py -q ...`：退出码 0，`7 passed, 30 warnings`。
- 全量离线 `pytest`：退出码 0，`147 passed, 1 skipped, 30 warnings`；唯一 skip 是未安装的可选 DeepEval adapter，不是 Phase 2/PaperQA 测试。
- `python -m compileall -q src scripts tests`、`git diff --check`、`git diff --check HEAD^`：退出码均为 0；阶段整体差异与当前工作树均无 whitespace error，仅有 Git 的 LF/CRLF 工作树提示。
- `pytest --collect-only -q src/legacy/tests`：退出码 0，收集 1 项；未执行或修改 legacy。
- `ruff`、`mypy`：目标 conda 环境未安装对应模块，命令退出码均为 1；未擅自安装，未伪报通过。
- 首次在工具沙箱内使用 pytest basetemp 时曾于 session-finish 遇到 Windows `WinError 5`；使用唯一目录并按批准在沙箱外重跑后均退出码 0。该问题是工具 ACL，不是功能失败。
- 未运行真实模型、远程 embedding、Web 搜索、LangSmith、Deep Research Bench、DeepEval LLM Judge 或其他付费路径。

## 兼容、回退与风险

- 回退时保持 `enable_knowledge_base=False` 与 `enable_paperqa_retrieval=False`；主图不会加载 PaperQA 或知识工具，现有 Web/MCP 流程不变。已导入 SQLite/Blob 数据可保留，不做破坏性 downgrade。
- PaperQA 当前每次 inspection 从 Repository rehydrate；这是安全而可重建的第一版，较持久索引慢。可信 manifest/index generation 可在后续阶段单独设计。
- 本地 deterministic embedding 会被 PaperQA 内部和 Adapter 为可验证 score 各计算一次 query；无网络/Token 成本，但未来模型 embedding 需避免重复计费。
- 安装 knowledge extra 将项目 conda 中 `packaging` 调整为 `25.0`、`click` 调整为 `8.4.2`；`pip check` 和完整回归通过，仍需在未来依赖升级时重跑矩阵。
- Tantivy wheel、长路径、文件锁和 pytest ACL 是主要 Windows 风险；Python 小版本/架构变化后必须重跑 T2-12。
- SQLite metadata 与 Blob 文件不是单一跨资源事务；ImportJob 的幂等/CAS 可恢复流程是当前降级边界。
- PyMuPDF 是项目既有 AGPL-3.0/商业双许可证风险；PaperQA core 与 `paper-qa-pypdf` 为 Apache-2.0，不能相互覆盖许可证义务。
- 既有 Pydantic/LangGraph deprecation warnings 共 30 条；本阶段没有改写主图处理这些警告。

## 下一步

阶段 2 已满足完成定义并停止。只有用户明确下达阶段 3 指令、且重新核验本页 T2 evidence 后，才可读取并执行 `doc/development_plan/phase_3_agentic_rag_lifecycle.md`；不得自动开始阶段 3。
