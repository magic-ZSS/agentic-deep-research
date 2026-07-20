# 会话进度记录

## 当前状态（Current State）

**最后更新（Last Updated）：** 2026-07-21

**当前功能：** `phase-4-mcp-integration-001`

**状态：** completed（阶段 4 已收口；阶段 5 未开始）

## 阶段门禁

- `phase-0-baseline-references-001` 为 `completed`；最终回归 T0-1 至 T0-12 全部 PASS。
- `phase-1-knowledge-evidence-models-001` 为 `completed`；执行阶段 2 前及最终回归 T1-1 至 T1-16 全部 PASS。
- `phase-2-document-ingestion-paperqa-001` 为 `completed`；`scripts/validate_phase.py --phase 2` 退出码 0，T2-1 至 T2-15 全部 PASS。
- `phase-3-agentic-rag-lifecycle-001` 为 `completed`；最终离线映射 suite 103 passed、0 skipped，T3-1 至 T3-20 均有确定性 evidence。
- `phase-4-mcp-integration-001` 为 `completed`；`scripts/validate_phase.py --phase 4` 退出码 0，T4-1 至 T4-16 全部 PASS。
- 阶段 5 仍为 `not-started`；本轮未实现 Memory、Checkpoint 或后续 Citation Validator/报告修复。

## 阶段 4 交付物

- `src/open_deep_research/mcp/`：向后兼容的多 server schema/client/registry、显式诊断、Allowed Roots、去敏审计、只读 filesystem wrapper 和原子 exclusive-create staging。
- `src/open_deep_research/mcp_servers/`：可信 `KnowledgeScope` 上下文、Knowledge MCP service/FastMCP server/LangChain tools；读操作复用 canonical Retriever/Repository，写操作只创建 pending lifecycle proposal。
- `configuration.py`/`utils.py`：新增默认关闭的 `enable_filesystem_mcp`、`enable_knowledge_mcp`，保留旧 `mcp_config={url,tools,auth_required}`，命名 server 逐个隔离加载；Agentic RAG 路径仍不绑定未分类 MCP 旁路。
- `config/examples/mcp.windows.example.json`、`scripts/validate_mcp_config.py`、`docs/mcp_windows.md`：固定 `@modelcontextprotocol/server-filesystem@2026.1.14`、无真实路径/secret 的 Windows 配置和 ACL/威胁模型说明。
- `tests/{unit,security,integration}/mcp/`：路径、symlink/junction、root replacement、read-only、staging type/quota/race、scope/proposal/redaction、multi-server、unknown tool、annotation 和 Windows stdio 覆盖。

## 阶段 4 威胁模型与决策

- 模型只能提交 `root_id + relative_locator`；绝对路径、drive/UNC/WSL、null、`..`、sibling-prefix、symlink/junction 和 root identity replacement 均 fail closed。返回值仅含 `root://<alias>/<relative>`。
- 只读 root 与 import staging 是分离 capability。上游 filesystem 原始 overwrite/edit/move/delete 工具从不注册；staging 仅用 `O_EXCL` 等效原子创建并做 suffix/media/单文件/每 run count/bytes 限制。
- annotations 不是授权。可信 runtime context、registry 白名单、path policy 和生产 Windows ACL/独立进程是分层防线；示例文档记录 ACL 要求，自动测试验证前三层及真实 stdio 工具集合。
- Knowledge MCP 的 tenant/project/user 不出现在工具参数中；跨 scope 查询和存在性探测返回同类授权错误。`kb_propose_ingest/stale/quarantine` 不改变 version 状态，不提供 hard delete、force 或虚假 Memory 工具。

## 阶段 4 验证

- 前置 Phase 3 门禁：退出码 0，`103 passed`。
- Phase 4 单元/安全：退出码 0，`21 passed, 0 skipped`；Windows symlink 不可用时以临时 junction fallback 完成真实绕过拒绝测试。
- Phase 4 离线集成：退出码 0，`9 passed`（含 SQLite proposal 重开持久化）。
- Windows stdio：显式授权后退出码 0，`1 passed`；固定包完成 handshake、tools/list 和临时 Markdown read。
- `scripts/validate_mcp_config.py --no-start`：退出码 0。
- `scripts/validate_phase.py --phase 4`：退出码 0；T4-1 至 T4-16 全部 PASS，内部 `30 passed`。
- Phase 3 回归首次为 `102 passed, 1 failed`，失败是 Phase 4 合法新增 `propose_ingest` 后旧枚举精确集合断言未更新；契约测试更新后最终回归退出码 0，`103 passed`。
- `python -m compileall -q src scripts tests`、JSON parse 与 `git diff --check`：退出码 0。
- `ruff`/`mypy`：目标 conda 环境缺少模块，命令退出码 1，未安装、未伪报。未调用模型、Web、LangSmith、Deep Research Bench 或 LLM Judge。

## 阶段 3 交付物

- `src/open_deep_research/research/`：从 brief 生成稳定 `RequirementSet`、确定性 coverage/gap 与程序化 Supervisor completion gate。
- `src/open_deep_research/evidence/run_store.py`：InMemory/SQLite 的 scope+run 隔离 `RunEvidenceStore`，支持完整 ID 回溯、CAS、TTL 清理与审计。
- `src/open_deep_research/knowledge/retrieval/`：共享原子预算、结构化 Web adapter、`GovernedRetrievalOrchestrator`、active-only 查询、missing-only Web 和受控 writeback。
- `src/open_deep_research/knowledge/validation/`：本地 candidate 与 Web candidate 共用的确定性 Gate，覆盖可解析性、直接性、支持度、来源权威、时效、冲突和敏感性规则。
- `src/open_deep_research/knowledge/lifecycle/` 与 migration v3：六态 transition、proposal、soft delete 与 append-only audit；没有 hard-delete capability。
- `src/open_deep_research/tools/governed_retrieval.py` 及最小图集成：Agentic 模式单一受治理工具入口；legacy augmentation 仅检索 active+validated 知识；所有开关关闭时保持 baseline 路由。
- `deep_researcher.py` 的限定恢复修复：同轮 tool+complete 保序、并行部分失败保留成功、真实 compression retry，以及 think/error/limit 诊断消息不进入结构化证据输入。

## 阶段 3 transition 与 coverage 决策

- 唯一允许的状态边为：`candidate→active|quarantined|archived`、`active→stale|superseded|quarantined|archived`、`stale→active|superseded|archived`、`quarantined→candidate|archived`、`superseded→archived`；`archived` 无出边。同态请求幂等且不追加重复审计。
- stale/quarantine/supersede/soft-delete 只能经 proposal/Repository 原子转换；Agent 不持有 hard-delete API。
- active Evidence 仍须针对当前 `Requirement` 重跑 Gate，不能仅凭词法命中覆盖需求；必需 gap 存在且预算尚余时 Supervisor 不得结束。
- 本地 active 足够时 Web 严格为 0；不足时只查询 missing aspects。Agentic 模式禁止 Tavily、MCP 和当前 OpenAI/Anthropic provider-native 搜索旁路，无法治理的 provider 配置 fail closed。
- Web 结果总是先写 run-scoped `RunEvidenceStore`；只有 `enable_knowledge_writeback=True` 且 Gate 通过时，才写 canonical `candidate` 并按策略 promotion。writeback 关闭时不会跨 run 复用 transient evidence。

## 阶段 3 验证命令与结果

- 前置门禁 `conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 2`：退出码 0；内部 `83 passed, 0 skipped`，T2-1 至 T2-15 全部 PASS。
- 最终阶段 3 映射 suite（validator、lifecycle、retrieval、research、RunEvidenceStore、tools、agentic_rag、legacy limits）：退出码 0，`103 passed, 0 skipped, 30 warnings`；T3-1 至 T3-20 的直接测试全部通过。
- `tests/test_research_limits.py tests/integration/knowledge` 回归：退出码 0，`19 passed`。
- 图治理与 validator 自测最终复验：退出码 0，`18 passed`；missing-only orchestrator 定向复验：退出码 0，`11 passed`。
- `python -m compileall -q src scripts tests` 与 `git diff --check`：退出码 0。
- 较早阶段 3 聚合 validator 运行曾退出码 0（内部 `101 passed`，T3-1 至 T3-20 PASS）。最终补齐两项测试并修正 acceptance 映射后，首次重跑发现 `_check_phase3_test_suite` 的 `basetemp` 定义误置，20 项均报告 `NameError`；已将定义移回 Phase 3 suite 并复验进入 pytest。修复后 pytest 功能测试完成，但工具沙箱在 session-finish 清理 basetemp 时触发 Windows `WinError 5`；沙箱外同命令申请因当前执行额度被系统拒绝，故不将这两次 wrapper 记为通过。最终 103 项映射 suite 与 validator 自测均单独退出码 0。
- 曾误写不存在的 `tests/unit/knowledge/validation` 路径，pytest 退出码 1/未收集；更正为真实目录后组合 suite 退出码 0，`84 passed`。这是命令路径错误，不是产品测试失败。
- `ruff`、`mypy`：目标 conda 环境未安装对应模块，命令退出码 1；未安装新工具，未伪报通过。
- 未调用真实模型、Tavily/Web、MCP、LangSmith、Deep Research Bench、DeepEval LLM Judge 或任何付费路径。

## 阶段 3 兼容、回退与风险

- `enable_agentic_rag`、`enable_knowledge_tools`、`enable_knowledge_writeback` 等新增开关默认均为 `False`；全部关闭时 `get_all_tools` 和旧图行为保持 baseline。回退只需关闭开关，不删除 SQLite/Blob/audit 数据。
- run budget 以 scope+run 共享并原子扣减；失败调用也消耗预算，防止并行 Researcher 绕过成本上限。SQLite UNIQUE/事务与稳定 ID 负责跨 worker 去重，不依赖先查内存。
- 当前 Gate 是确定性第一版；来源权威/时效策略需要后续以领域 fixture 扩展，但不得由 prompt 绕过。
- Windows pytest basetemp ACL 仍可能导致 session-finish 假性失败；已有受限 gitignored 临时目录未擅自删除。正式 CI 应使用独立可控 temp root。
- 既有 Pydantic/LangGraph deprecation warnings 共 30 条；本阶段未扩大范围修复。`ruff`/`mypy` 仍是环境缺口。

## 阶段 2 交付物（历史）

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

阶段 4 已满足完成定义并停止。只有用户明确下达阶段 5 指令、且重新核验本页 T4 evidence 后，才可读取并执行 `doc/development_plan/phase_5_memory_system.md`；不得自动开始阶段 5。
