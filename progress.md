# 会话进度记录

## 当前状态（Current State）

**最后更新（Last Updated）：** 2026-07-21

**当前功能：** `phase-1-knowledge-evidence-models-001`

**状态：** completed（阶段 1 已收口；阶段 2 尚未开始）

## 阶段门禁

- `phase-0-baseline-references-001` 为 `completed`。
- 2026-07-21 重新运行 `scripts/validate_phase.py --phase 0`，退出码 0，T0-1 至 T0-12 全部 PASS。
- 2026-07-21 重新运行 `scripts/validate_phase.py --phase 1`，退出码 0，T1-1 至 T1-16 全部 PASS。
- 阶段 2 仍为 `not-started`；本次收口没有读取或实现阶段 2。

## 阶段 1 交付物

- `src/open_deep_research/knowledge/`：scope-aware 稳定 ID、领域模型、async Repository Protocol、InMemory 与 SQLite metadata Repository。
- `src/open_deep_research/evidence/`：Requirement/Evidence 模型、Repository 导出与确定性引用 ID reducer。
- `src/open_deep_research/storage/`：InMemory/Local content-addressed Blob Repository、SQLite 连接管理和 migration v1。
- `src/open_deep_research/state.py`：仅以 optional/additive 方式加入 `source_ids`、`evidence_ids`、`requirement_ids`；保留 `notes`、`raw_notes`、`compressed_research`。
- `src/open_deep_research/configuration.py`：`enable_structured_evidence=False` 及本地 Repository 配置，默认关闭且不创建数据库。
- `tests/unit/knowledge/`、`tests/unit/evidence/`、`tests/integration/storage/`：模型、ID、scope、双后端 contract、并发、持久化、原始快照、soft delete/audit 和 LangGraph reducer 测试。
- `scripts/validate_phase.py --phase 1`：T1-1 至 T1-16 的确定性阶段门禁。

## 模型与迁移决策

- 原始 bytes 使用完整 SHA-256；稳定 ID 包含 `KnowledgeScope`，跨 tenant/project 不共享身份或 dedupe 可见性。
- `DocumentVersion` 是不可变内容版本；同 Source 同 bytes 幂等，内容变化生成单调递增新版本，旧 Chunk/Evidence 不被覆盖。
- 生命周期状态只属于 `DocumentVersion`：`candidate/active/stale/superseded/quarantined/archived`；Evidence 使用独立的 `pending/validated/rejected`。
- 可引用资格由完整的 Evidence → Chunk → DocumentVersion → Document → Source 链、scope、soft-delete、版本状态、验证状态和有效时间共同派生。
- SQLite migration v1 使用复合 scope 主键/外键、scope-aware UNIQUE、`BEGIN IMMEDIATE` 短事务、WAL、busy timeout 和事务内 audit；并行去重不依赖内存先检查。
- 所有领域删除均为 soft delete；Blob Repository 不暴露删除 API。原始内容写入 scope-local content-addressed Blob，可在原路径删除或改写后读取。
- PaperQA2 的类型分层仅作为设计参考；阶段 1 未安装、调用或嵌入 PaperQA2，也未实现解析、检索或第二套 Agent。

## T1 验收证据

| 验收项 | 结果 | 自动化证据 |
|---|---|---|
| T1-1 | PASS | 相同 Source/bytes 两次写入得到同一 Version ID，版本数为 1。 |
| T1-2 | PASS | bytes 变化生成单调新版本，旧 Version/Chunk 保持不变。 |
| T1-3 | PASS | Evidence 可回溯至 Chunk、Version、Document、Source；缺失外键写入失败。 |
| T1-4 | PASS | PDF page 与 Markdown heading locator 可类型化序列化/反序列化。 |
| T1-5 | PASS | reducer 去重、排序且对输入排列和分批方式保持不变。 |
| T1-6 | PASS | SQLite 关闭重开后数据、顺序、soft delete 与 audit 保持一致。 |
| T1-7 | PASS | 两个 SQLite writer 依靠事务和 UNIQUE 收敛到一个逻辑版本。 |
| T1-8 | PASS | Source/Version/Chunk/Evidence 均只 soft delete；默认过滤且审计可查。 |
| T1-9 | PASS | Python 约束为 3.11+；新子包已登记且可从仓库外导入；未新增 ORM/向量库。 |
| T1-10 | PASS | 默认关闭时不创建 DB；旧自由文本 state 和既有测试保持兼容。 |
| T1-11 | PASS | 同一参数化 contract suite 覆盖 InMemory 与 SQLite/Local Blob。 |
| T1-12 | PASS | schema version 1、必需表和互不混用的状态词汇由 validator 检查。 |
| T1-13 | PASS | 同 scope 相同 bytes 复用一个 Blob 并保留不同来源链；跨 scope 不泄漏。 |
| T1-14 | PASS | 原始路径删除/改写后，旧 Version 仍可读取 hash 匹配的原始 bytes。 |
| T1-15 | PASS | 只有 active Version + validated Evidence 的完整有效链可引用。 |
| T1-16 | PASS | tenant/project/private 过滤 fail closed；跨 scope ID 返回授权错误而非存在性信息。 |

## 验证命令与结果

- `conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 1`：退出码 0，T1-1 至 T1-16 全部 PASS。
- `conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 0`：退出码 0，T0-1 至 T0-12 全部 PASS。
- `conda run --no-capture-output -n open-deep-research python -m pytest --basetemp=.phase-validation-tmp/phase1-close-20260721-01 -q`：退出码 0，`81 passed, 1 skipped, 30 warnings`；跳过项为可选 DeepEval/full-eval 路径。
- 阶段实现期间的定向结果：unit `20 passed`、integration/storage `9 passed`、`tests/test_research_limits.py` `7 passed`，退出码均为 0。
- 阶段实现期间的 `compileall`：退出码 0。
- `ruff` 与 `mypy`：目标 conda 环境缺少对应模块，退出码均为 1；未擅自安装，未伪报通过。
- `pip install -e . --no-deps`：沙箱对 conda 环境/临时目录写入拒绝；阶段验证器已从仓库外成功导入新子包，T1-9 仍有自动化证据。
- 未运行真实模型、Web 搜索、LangSmith、Deep Research Bench、DeepEval LLM Judge 或其他付费路径。

## 兼容、回退与风险

- `enable_structured_evidence=False` 时新 Repository 不会被主图构造，不创建数据库，不改变 Supervisor、Researcher、搜索或 Writer。
- 回退只需保持开关关闭并让旧代码忽略 optional state 引用；保留 SQLite/Blob 数据，不执行破坏性 downgrade。
- SQLite metadata 与本地 Blob 是两个存储边界；进程在两者提交之间崩溃时，未来导入服务必须提供幂等重试/修复。
- SQLite 适合第一版本地单机和中等并发，不等同于未来 PostgreSQL 或向量检索方案。
- Windows 文件锁、长路径和 ACL 仍需后续阶段持续使用独立临时目录测试。
- 既有 Pydantic/LangGraph deprecation warnings 共 30 条，本阶段没有改写主图以处理这些警告。

## 下一步

阶段 1 已满足完成定义。只有用户明确下达阶段 2 指令并重新通过本门禁后，才可读取并执行 `doc/development_plan/phase_2_document_ingestion_and_paperqa.md`；不得自动开始阶段 2。
