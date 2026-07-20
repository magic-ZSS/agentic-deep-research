# 会话进度记录

## 当前状态（Current State）

**最后更新（Last Updated）：** 2026-07-20
**当前功能：** `development-plan-001`
**状态：** completed（仅规划完成；阶段 0–7 均为 `not-started`）

## 已完成（What's Done）

- 按 `AGENTS.md` 恢复仓库上下文，读取 README、pyproject、LangGraph 主实现、运行入口、全部当前测试、三个动态状态文件和 `docs/codebase/`。
- 以当前项目提交 `8c2b26ea1e582590d9653188a286c4fc14f6480d` 为规划基线，确认主图、Supervisor/Researcher 子图、`notes/raw_notes/ToolMessage` 传递、搜索/MCP配置、并发/token限制、错误恢复与现有评测边界。
- 在用户授权范围内，将五个参考仓库浅克隆到 `doc/reference/` 并定点读取实际代码：PaperQA2、DeepEval、LangMem、LangGraph、MCP Servers Filesystem。
- 在 `doc/development_plan/` 创建 12 份规划文档：总入口、目标架构、参考仓库映射、执行协议，以及阶段 0–7。
- 每个阶段严格包含 16 个固定章节、稳定 `Tn-*` 验收项、实际文件修改范围、配置回退、测试/命令和可直接复制的 Codex 指令。
- 明确所有新能力默认关闭；保留 Supervisor—Researcher、旧自由文本字段和 legacy Writer 路径；禁止 PaperQA2 第二套 Agent、Agent hard delete、Memory bypass 和 MCP 路径旁路。
- 明确 `memory_search` 的阶段顺序：阶段 4 只预留真实扩展点，阶段 5 在 MemoryRepository 与 Namespace/Gate 完成后才注册，不提供虚假 stub。

## 关键设计决定（Decisions）

- 使用当前请求指定的 `doc/development_plan/` 与 `doc/reference/`。实际总体计划文件为用户已有的 `doc/overview.md`，与请求文字中的 `docs/development_plan/overview.md` 不一致；本次保留原文件并在新 README 中说明。
- 阶段顺序保持 0–7，不合并或新增阶段：Baseline → 领域模型 → 导入/PaperQA2 → Agentic RAG → MCP → Memory → Citation → Evaluation。
- PaperQA2 仅作为 parser/index/evidence retrieval adapter；本项目 SHA-256、DocumentVersion、Repository、生命周期和审计为权威。
- 阶段 1 只定义状态枚举与存储不变量；自动 promotion/stale/quarantine policy 留阶段 3，避免跨阶段实现。
- LangGraph Checkpoint 只负责 Working Memory；知识、长期 Memory、Checkpoint 使用独立 SQLite 文件并经各自 Repository/factory。
- DeepEval smoke 默认确定性、无网络；full Judge、真实搜索和 live baseline必须有明确费用授权。
- Filesystem MCP 的只读源与可写 staging 采用分 server/最小工具白名单 + realpath policy + Windows ACL，不把 annotations 当授权。

## 规划证据（Planning Evidence）

- 12 份文档文件和大小已列出，8 份 phase 文档均检测到恰好 16 个连续章节。
- 验收编号连续无缺号：T0 12项、T1 16项、T2 15项、T3 20项、T4 16项、T5 16项、T6 18项、T7 17项。
- 所有相对 Markdown 文件链接目标存在。
- 参考提交：PaperQA `d7675d7...`、DeepEval `58c9ef7...`、LangMem `a2d5809...`、LangGraph `49ae27c...`、MCP Servers `d31124c...`；许可证边界写入 `reference_repositories.md`。
- 未安装 PaperQA2/DeepEval/LangMem，未修改 `pyproject.toml`、LangGraph 主图或功能源码，未运行真实模型、搜索、LangSmith或高成本评测。

## 已发现但未在本轮修复的事实（Baseline Risks）

- `Configuration.print_process_info` 当前 Python default 为 `True`，而 UI metadata和旧状态文档写 `False`；`allow_clarification` 的 Python default 为 `False`，UI metadata写 `True`。
- 模型字段的 Python default来自 `os.getenv(...)`，UI metadata中的模型名不是运行时 fallback。
- Supervisor Researcher batch异常分支存在 `or True`，compression token裁剪后没有真实第二次尝试，同轮 `ResearchComplete` 与研究工具调用存在任务丢失风险；阶段 0 只固定 baseline，阶段 3 负责修复与回归。未知工具路径可能抛出 `KeyError`，由阶段 4 在 MCP 路由边界修复。
- `pyproject.toml` 要求 Python >=3.10、`langgraph.json` 使用3.11，而 PaperQA2/目标架构要求3.11+；阶段 0/1需固定兼容矩阵。
- 当前 `pyproject.toml` 显式列包，新子包需在实施阶段调整 package discovery。
- `init.sh` 和 `.pytest_cache` 在 Windows/WSL/conda 链路仍有既有可靠性/权限问题。

## 验证证据（Verification Evidence）

- PowerShell 结构检查：8 份 `phase_*.md` 均为连续 `## 1.` 至 `## 16.`，退出码 0。
- PowerShell验收编号检查：所有阶段 `Tn-1` 至最大编号连续，退出码 0。
- Markdown相对文件链接检查：`All relative Markdown file links resolve.`，退出码 0。
- 本轮未运行功能测试：没有修改 Python/配置/依赖，且用户明确禁止开始阶段 0 或运行高成本 DeepResearch测试。

## 本次修改文件（Files Modified This Session）

- `doc/development_plan/README.md`
- `doc/development_plan/architecture_target.md`
- `doc/development_plan/reference_repositories.md`
- `doc/development_plan/execution_protocol.md`
- `doc/development_plan/phase_0_baseline_and_references.md`
- `doc/development_plan/phase_1_knowledge_evidence_models.md`
- `doc/development_plan/phase_2_document_ingestion_and_paperqa.md`
- `doc/development_plan/phase_3_agentic_rag_lifecycle.md`
- `doc/development_plan/phase_4_mcp_integration.md`
- `doc/development_plan/phase_5_memory_system.md`
- `doc/development_plan/phase_6_citation_validation.md`
- `doc/development_plan/phase_7_evaluation_and_showcase.md`
- `feature_list.json`
- `progress.md`
- `session-handoff.md`

参考浅克隆位于 `doc/reference/`，属于用户本轮授权的只读研究材料；是否提交这些嵌套仓库本体留待阶段 0 决定。用户原有 `doc/overview.md` 未修改。

## 阻塞项与风险（Blockers / Risks）

- 当前规划无阻塞；所有实施阶段尚未开始。
- 阶段0的真实simple live baseline是可选发布证据；阶段7 full eval是完成门禁，两者都需用户明确费用授权。
- PaperQA2发布版本/embedding策略、领域时效与权威阈值、可信 identity来源、Windows Node/ACL和敏感数据保留政策仍需在对应阶段确认。

## 下次会话说明

1. 先按 `AGENTS.md` 恢复上下文并确认工作树。
2. 从 `doc/development_plan/phase_0_baseline_and_references.md` 第 16 节复制完整 Codex执行指令。
3. 本次只执行阶段 0；未通过 T0 全部验收前不得进入阶段 1。
4. 不要默认运行live/付费测试；阶段0可用replay/smoke完成，若要追加live证据先报告预算并获取授权。
