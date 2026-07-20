# 会话交接

## 当前目标（Current Objective）

- 规划任务 `development-plan-001` 已完成。
- 功能实施阶段 0–7 均未开始，状态均为 `not-started`。
- 下一次建议目标：仅执行阶段 0，指令位于 `doc/development_plan/phase_0_baseline_and_references.md` 第 16 节。

## 已完成

- 完整恢复并核对当前项目源码、测试、配置、状态和 `docs/codebase/`。
- 浅克隆并定点分析五个参考仓库，记录固定 commit、重点 API、不可复用边界与许可证。
- 创建 `doc/development_plan/` 下 12 份规划文档：
  - `README.md`
  - `architecture_target.md`
  - `reference_repositories.md`
  - `execution_protocol.md`
  - `phase_0_baseline_and_references.md`
  - `phase_1_knowledge_evidence_models.md`
  - `phase_2_document_ingestion_and_paperqa.md`
  - `phase_3_agentic_rag_lifecycle.md`
  - `phase_4_mcp_integration.md`
  - `phase_5_memory_system.md`
  - `phase_6_citation_validation.md`
  - `phase_7_evaluation_and_showcase.md`
- 校验每个 phase 均有 16 个固定章节、连续验收编号和明确停止门禁；相对 Markdown文件链接均可解析。
- 未安装依赖、未修改功能源码/主图/`pyproject.toml`、未启动阶段 0、未运行外部模型/搜索/LangSmith/DeepEval Judge。

## 下一会话必读

1. `AGENTS.md`
2. `feature_list.json`
3. `progress.md`
4. `session-handoff.md`
5. `doc/development_plan/README.md`
6. `doc/development_plan/architecture_target.md`
7. `doc/development_plan/reference_repositories.md`
8. `doc/development_plan/execution_protocol.md`
9. `doc/development_plan/phase_0_baseline_and_references.md`
10. 阶段 0 第 5、6 节列出的当前源码、测试和参考文件

## 关键边界

- 当前请求指定的执行目录是 `doc/development_plan/`。用户原有总体计划实际位于 `doc/overview.md`，未修改。
- `doc/reference/` 下有五个浅克隆嵌套仓库；阶段 0 决定 lock/ignore/下载策略前不要修改、格式化或提交其源码。
- 所有新能力默认关闭，必须保留 Supervisor—Researcher 与 `notes/raw_notes/compressed_research` 兼容。
- PaperQA2只能在 Adapter 后提供解析/索引/evidence retrieval，禁止嵌入完整 Agent或调用 `aquery` 生成回答。
- Agent/MCP不能 hard delete、force promote或绕过 Memory Write Gate。
- 阶段 4 不实现虚假 `memory_search`；阶段 5有真实 MemoryRepository和 Namespace授权后才注册。
- 每次只执行一个阶段，完成后必须停止。

## 已知未决事项

- 当前代码的 `print_process_info`、`allow_clarification` 和模型默认值与 UI/旧状态文档有漂移；阶段 0只固定事实并请求决策，不要静默修复。
- Python/LangGraph/PaperQA2/DeepEval/LangMem兼容版本需阶段 0记录、阶段 2/5再安装验证。
- PaperQA2 embedding/provider、领域 authority/freshness阈值、可信 tenant/user/project identity和敏感数据保留政策需在对应阶段确认。
- Windows Filesystem MCP需要固定 Node package、真实 stdio smoke与 ACL evidence。
- 阶段0 live baseline是可选发布证据，阶段7 full eval是完成门禁；任何真实调用都需要明确费用授权。

## 工作树注意

- 用户原有未跟踪文件：`doc/overview.md`，不要移动或覆盖。
- 本次规划新增：`doc/development_plan/`；参考浅克隆新增于 `doc/reference/`。
- 会话扫描产生的临时 `.codebase-plan-scan.txt` 已删除，不是项目产物。
- `git status` 仍可能提示既有 `pytest-cache-files-*` 权限 warning；不要为清理 warning 删除未知目录。

## 建议下一条指令

打开 `doc/development_plan/phase_0_baseline_and_references.md`，复制第 16 节“本阶段 Codex 执行指令”的完整代码块发送给 Codex。阶段 0完成并通过 T0-1 至 T0-12 前，不得开始阶段 1。
