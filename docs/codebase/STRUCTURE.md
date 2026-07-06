# 代码库结构

## 1) 顶层结构

| 路径 | 用途 | 证据 |
|------|---------|----------|
| `README.md` | 项目说明、快速启动、配置、评估和部署入口说明。 | `README.md` |
| `pyproject.toml` | Python 包元数据、依赖、setuptools 映射和 Ruff 配置。 | `pyproject.toml` |
| `uv.lock` | 锁定依赖版本；本地规范仍要求优先 conda/LangGraph 命令。 | `uv.lock`, `AGENTS.md` |
| `langgraph.json` | LangGraph 图入口、Python 版本、`.env`、本地依赖和 auth 入口。 | `langgraph.json` |
| `init.sh` | 统一验证入口：compileall、Ruff、mypy、legacy 测试收集。 | `init.sh` |
| `feature_list.json` | 功能状态、依赖和 evidence 的状态源。 | `feature_list.json`, `AGENTS.md` |
| `progress.md` | 当前状态、验证证据、决策、风险和下一步。 | `progress.md`, `AGENTS.md` |
| `session-handoff.md` | 跨会话恢复入口。 | `session-handoff.md`, `AGENTS.md` |
| `.github/` | Claude Code workflows 和 Dependabot 配置。 | `.github/workflows/claude.yml`, `.github/workflows/claude-code-review.yml`, `.github/dependabot.yml` |
| `src/open_deep_research/` | 当前主实现：LangGraph 深度研究 agent。 | `src/open_deep_research/deep_researcher.py` |
| `src/security/` | LangGraph 部署鉴权处理。 | `src/security/auth.py`, `langgraph.json` |
| `src/legacy/` | 早期 workflow 和 multi-agent 实现、legacy 测试与说明。 | `src/legacy/legacy.md`, `src/legacy/graph.py`, `src/legacy/multi_agent.py` |
| `tests/` | 当前 Deep Research Bench / LangSmith 评估脚本与评估器。 | `tests/run_evaluate.py`, `tests/evaluators.py` |
| `examples/` | 生成报告示例。 | `examples/arxiv.md`, `examples/pubmed.md`, `examples/inference-market.md` |
| `docs/codebase/` | 本次生成的 codebase onboarding 文档。 | `docs/codebase/STACK.md` |
| `author_notes/` | 作者临时长期笔记区；本地规范说明正常忽略。 | `AGENTS.md`, `.gitignore` |

## 2) 入口点

- Main runtime entry: `src/open_deep_research/deep_researcher.py:deep_researcher`，由 `langgraph.json` 的 `"Deep Researcher"` 图配置选择。
- IDE/CLI secondary entry: `src/open_deep_research/run.py`，从命令行参数或 `QUESTION` 常量构造 `HumanMessage`，调用已编译的 `deep_researcher.ainvoke(...)`。
- Auth entry: `src/security/auth.py:auth`，由 `langgraph.json` 的 `auth.path` 选择。
- Evaluation entry: `tests/run_evaluate.py`，编译 `deep_researcher_builder` 并在 LangSmith dataset 上运行。
- Legacy entries: `src/legacy/graph.py:builder` 和 `src/legacy/multi_agent.py:supervisor_builder`，由 legacy 测试使用。

## 3) 模块边界

| 边界 | 这里放什么 | 不应放什么 |
|----------|-------------------|------------------------|
| `src/open_deep_research/deep_researcher.py` | LangGraph 节点、子图、主图编排和运行时流转。 | 包管理、部署鉴权、评估脚本。 |
| `src/open_deep_research/configuration.py` | `Configuration`、`SearchAPI`、`MCPConfig` 和 UI metadata。 | 具体外部 API 调用逻辑。 |
| `src/open_deep_research/state.py` | Pydantic structured outputs、TypedDict state 和 reducer。 | Prompt 文案和网络请求。 |
| `src/open_deep_research/prompts.py` | 研究、压缩、最终报告、澄清等 prompt 模板。 | 图节点控制流。 |
| `src/open_deep_research/utils.py` | 搜索工具、MCP 工具、token、API key、token-limit 辅助。 | 顶层 LangGraph graph 构造。 |
| `src/security/auth.py` | Supabase-backed LangGraph auth hooks。 | 研究逻辑和搜索工具。 |
| `tests/` | 当前评估、LangSmith extraction、pairwise/supervisor 评估。 | 生产运行时逻辑。 |
| `src/legacy/` | 历史 workflow/multi-agent 实现和 legacy 测试。 | 新主图的默认逻辑。 |

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
