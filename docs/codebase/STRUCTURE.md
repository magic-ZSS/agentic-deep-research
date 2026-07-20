# 代码库结构

## 1) 顶层结构

| 路径 | 用途 | 证据 |
|------|---------|----------|
| `README.md` | 项目说明、快速启动、配置、评估和部署入口说明。 | `README.md` |
| `pyproject.toml` | Python 包元数据、依赖、setuptools 映射和 Ruff 配置。 | `pyproject.toml` |
| `uv.lock` | 历史依赖锁定文件；作者已确认后续文档和操作不再推荐 uv 命令。 | `uv.lock`, `AGENTS.md` |
| `langgraph.json` | LangGraph 图入口、Python 版本、`.env`、本地依赖和 auth 入口。 | `langgraph.json` |
| `init.sh` | 统一验证入口：compileall、Ruff、mypy、legacy 测试收集。 | `init.sh` |
| `feature_list.json` | 功能状态、依赖和 evidence 的状态源。 | `feature_list.json`, `AGENTS.md` |
| `progress.md` | 当前状态、验证证据、决策、风险和下一步。 | `progress.md`, `AGENTS.md` |
| `session-handoff.md` | 跨会话恢复入口。 | `session-handoff.md`, `AGENTS.md` |
| `.github/` | Claude Code workflows 和 Dependabot 配置。 | `.github/workflows/claude.yml`, `.github/workflows/claude-code-review.yml`, `.github/dependabot.yml` |
| `.gitmodules`, `doc/reference/` | 五个只读开发参考仓库的 gitlink 获取配置、版本锁和许可证说明。 | `.gitmodules`, `doc/reference/refs.lock.json`, `THIRD_PARTY_NOTICES.md` |
| `scripts/` | Phase 0 manifest 捕获、replay/live baseline runner 与阶段验收入口。 | `scripts/capture_baseline_manifest.py`, `scripts/run_baseline.py`, `scripts/validate_phase.py` |
| `src/open_deep_research/` | 当前主实现：LangGraph 深度研究 agent。 | `src/open_deep_research/deep_researcher.py` |
| `src/security/` | LangGraph 部署鉴权处理。 | `src/security/auth.py`, `langgraph.json` |
| `src/legacy/` | 早期 workflow 和 multi-agent 实现、legacy 测试与说明；后续仅作历史参考，不在保证范围。 | `src/legacy/legacy.md`, `src/legacy/graph.py`, `src/legacy/multi_agent.py`, `AGENTS.md` |
| `tests/` | 确定性 baseline/evaluation 测试、成本门禁，以及需显式授权的 Deep Research Bench / LangSmith 评估脚本。 | `tests/baseline/`, `tests/evaluation/`, `tests/conftest.py`, `tests/run_evaluate.py` |
| `examples/` | 生成报告示例。 | `examples/arxiv.md`, `examples/pubmed.md`, `examples/inference-market.md` |
| `docs/codebase/` | 本次生成的 codebase onboarding 文档。 | `docs/codebase/STACK.md` |
| `author_notes/` | 作者临时长期笔记区；本地规范说明正常忽略。 | `AGENTS.md`, `.gitignore` |

## 2) 入口点

- Main runtime entry: `src/open_deep_research/deep_researcher.py:deep_researcher`，由 `langgraph.json` 的 `"Deep Researcher"` 图配置选择。
- IDE/CLI secondary entry: `src/open_deep_research/run.py`，从命令行参数或 `QUESTION` 常量构造 `HumanMessage`，调用已编译的 `deep_researcher.ainvoke(...)`。
- Auth entry: `src/security/auth.py:auth`，由 `langgraph.json` 的 `auth.path` 选择。
- Evaluation entry: `tests/run_evaluate.py`，编译 `deep_researcher_builder` 并在 LangSmith dataset 上运行。
- Offline baseline entry: `scripts/run_baseline.py` 默认 replay；`scripts/validate_phase.py --phase 0` 执行 T0-1 至 T0-12。
- Legacy entries: `src/legacy/graph.py:builder` 和 `src/legacy/multi_agent.py:supervisor_builder`，由 legacy 测试使用。

## 3) 模块边界

| 边界 | 这里放什么 | 不应放什么 |
|----------|-------------------|------------------------|
| `src/open_deep_research/deep_researcher.py` | LangGraph 节点、子图、主图编排和运行时流转。 | 包管理、部署鉴权、评估脚本。 |
| `src/open_deep_research/configuration.py` | `Configuration`、`SearchAPI`、`MCPConfig` 和 UI metadata。 | 具体外部 API 调用逻辑。 |
| `src/open_deep_research/state.py` | Pydantic structured outputs、TypedDict state 和 reducer。 | Prompt 文案和网络请求。 |
| `src/open_deep_research/prompts.py` | 研究、压缩、最终报告、澄清等 prompt 模板。 | 图节点控制流。 |
| `src/open_deep_research/utils.py` | 搜索工具、MCP 工具、token、API key、token-limit 辅助。 | 顶层 LangGraph graph 构造。 |
| `src/open_deep_research/evaluation/` | 版本化 baseline schema、原子 JSONL、确定性 metrics、opt-in callback、费用门禁和可选 DeepEval adapter。 | 主图节点、生产图状态、默认外部调用或平台上传。 |
| `src/security/auth.py` | Supabase-backed LangGraph auth hooks。 | 研究逻辑和搜索工具。 |
| `tests/` | 离线单元/集成测试，以及受 `live/full_eval` marker 和环境开关保护的外部评估。 | 生产运行时逻辑。 |
| `src/legacy/` | 历史 workflow/multi-agent 实现和 legacy 测试，仅作参考。 | 新主图的默认逻辑；后续保证范围。 |

## 4) 命名与组织规则

- File naming pattern: Python 源文件使用 lowercase/snake_case，例如 `deep_researcher.py`, `configuration.py`, `run_evaluate.py`。
- Directory organization pattern: 当前主实现按层/职责拆分，不按 feature 目录拆分。
- Types/classes: Pydantic 模型和 state 类型使用 PascalCase，例如 `Configuration`, `ResearchQuestion`, `SupervisorState`。
- Functions/variables: 函数与字段使用 snake_case，例如 `clarify_with_user`, `max_concurrent_research_units`。
- Import conventions: setuptools 将 `src/open_deep_research` 映射为 `open_deep_research` 包；代码使用绝对包导入，例如 `from open_deep_research.configuration import Configuration`。未发现 TypeScript/JS path alias。

## 5) 证据

- `langgraph.json`
- `pyproject.toml`
- `src/open_deep_research/deep_researcher.py`
- `src/open_deep_research/run.py`
- `src/open_deep_research/configuration.py`
- `src/open_deep_research/state.py`
- `src/open_deep_research/utils.py`
- `src/security/auth.py`
- `src/legacy/legacy.md`
- `tests/run_evaluate.py`
- `AGENTS.md`

## 6) 阶段 1 新增目录

| 路径 | 职责 |
|------|------|
| `src/open_deep_research/knowledge/` | scope、来源、文档、版本、chunk、稳定 ID、Repository Protocol 与 metadata 实现 |
| `src/open_deep_research/evidence/` | Requirement、Evidence、AuditEvent、可引用资格与确定性 ID reducer |
| `src/open_deep_research/storage/` | SQLite v1 migration/连接策略，以及 InMemory/Local BlobRepository |
| `tests/unit/knowledge/`, `tests/unit/evidence/` | canonicalization、模型、状态、配置与 reducer 单元契约 |
| `tests/integration/storage/` | 同一 Repository contract suite、SQLite 重开/并发、scope 和原始快照测试 |
| `docs/codebase/KNOWLEDGE_EVIDENCE.md` | schema v1、不变量、配置、回退和已知限制 |
