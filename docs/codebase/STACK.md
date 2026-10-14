# 技术栈

## 1) 运行时摘要

| 领域 | 值 | 证据 |
|------|-------|----------|
| Primary language | Python | `pyproject.toml`, `src/open_deep_research/deep_researcher.py` |
| Runtime + version | 包元数据要求 `>=3.10`；LangGraph 配置指定 `3.11`；README 的本地 conda 示例也创建 Python 3.11 环境。 | `pyproject.toml`, `langgraph.json`, `README.md` |
| Package manager | 项目使用 `pyproject.toml` + pip editable install；作者已确认后续文档和操作路径统一使用 conda/pip 与 LangGraph 原生命令，不再推荐 uv 命令。 | `pyproject.toml`, `README.md`, `AGENTS.md` |
| Module/build system | `setuptools.build_meta`，包从 `src/` 映射到 `open_deep_research`、`legacy`、`tests`。 | `pyproject.toml` |

## 2) 生产依赖与框架

| 依赖 | 版本 | 系统角色 | 证据 |
|------------|---------|----------------|----------|
| `langgraph` | `>=0.5.4` | 图编排、`StateGraph`、运行入口和状态流转。 | `pyproject.toml`, `src/open_deep_research/deep_researcher.py` |
| `langchain-community` | `>=0.3.9` | legacy 检索器和社区集成。 | `pyproject.toml`, `src/legacy/utils.py` |
| `langchain-openai` | `>=0.3.28` | OpenAI 模型与评估器。 | `pyproject.toml`, `tests/evaluators.py` |
| `langchain-anthropic` | `>=0.3.15` | Anthropic 模型与评估器。 | `pyproject.toml`, `tests/evaluators.py` |
| `langchain-google-vertexai`, `langchain-google-genai` | `>=2.0.25`, `>=2.1.5` | Google/Gemini 模型提供商依赖。 | `pyproject.toml`, `src/open_deep_research/utils.py` |
| `langchain-groq`, `langchain-deepseek`, `langchain-aws` | `>=0.2.4`, `>=0.1.2`, `>=0.2.28` | Groq、DeepSeek、AWS Bedrock 模型集成依赖。 | `pyproject.toml`, `AGENTS.md` |
| `langchain-mcp-adapters`, `mcp` | `>=0.1.6`, `>=1.9.4` | MCP server 工具加载与 streamable HTTP 客户端。 | `pyproject.toml`, `src/open_deep_research/utils.py` |
| `langchain-tavily`, `tavily-python` | 未固定, `>=0.5.0` | 默认 Tavily 搜索工具和异步搜索客户端。 | `pyproject.toml`, `src/open_deep_research/configuration.py`, `src/open_deep_research/utils.py` |
| `openai` | `>=1.99.2` | OpenAI 模型 SDK，默认模型为 `openai:gpt-4.1*`。 | `pyproject.toml`, `src/open_deep_research/configuration.py` |
| `requests`, `httpx`, `aiohttp` | `>=2.32.3`, `>=0.24.0`, transitive/lock | HTTP 调用；MCP token exchange 使用 `aiohttp`。 | `pyproject.toml`, `src/open_deep_research/utils.py` |
| `beautifulsoup4`, `markdownify`, `pymupdf`, `xmltodict` | `4.14.3`, `>=0.11.6`, `>=1.25.3`, `>=0.14.2` | legacy 搜索结果抓取、内容转换和文档处理辅助。 | `pyproject.toml`, `src/legacy/utils.py` |
| `arxiv`, `duckduckgo-search`, `exa-py`, `linkup-sdk` | `>=2.1.3`, `>=3.0.0`, `>=1.8.8`, `>=0.2.3` | legacy 搜索后端。 | `pyproject.toml`, `src/legacy/utils.py` |
| `azure-identity`, `azure-search`, `azure-search-documents` | `>=1.21.0`, `>=1.0.0b2`, `>=11.5.2` | legacy Azure AI Search 集成。 | `pyproject.toml`, `src/legacy/utils.py` |
| `supabase` | `>=2.15.3` | LangGraph 部署鉴权，验证 Bearer token。 | `pyproject.toml`, `src/security/auth.py` |
| `langsmith` | `>=0.3.37` | Deep Research Bench 和 LangSmith 评估。 | `pyproject.toml`, `tests/run_evaluate.py` |
| `pytest`, `pandas`, `rich`, `ipykernel` | 未固定, `>=2.3.1`, `>=13.0.0`, `>=6.29.5` | 测试、结果处理、CLI 输出和 notebook 支持。 | `pyproject.toml`, `src/legacy/tests/run_test.py` |

## 3) 开发工具链

| 工具 | 用途 | 证据 |
|------|---------|----------|
| `ruff` | lint/import order/pydocstyle；配置选择 `E`, `F`, `I`, `D`, `D401`, `T201`, `UP`。 | `pyproject.toml` |
| `mypy` | 类型检查；声明在 `dev` optional dependencies，`init.sh` 会调用。 | `pyproject.toml`, `init.sh` |
| `pytest` | legacy 测试收集与测试执行。 | `pyproject.toml`, `init.sh`, `src/legacy/tests/conftest.py` |
| `compileall` | 源码语法/编译检查。 | `init.sh` |
| `langgraph-cli[inmem]` | 本地 LangGraph server / Studio 启动。 | `pyproject.toml`, `README.md` |
| GitHub Actions Claude workflows | PR/issue/comment 触发 Claude Code 与 Claude Code Review。 | `.github/workflows/claude.yml`, `.github/workflows/claude-code-review.yml` |
| Dependabot | 版本更新配置；当前 YAML 有重复 `updates` key。 | `.github/dependabot.yml` |

## 4) 关键命令

```bash
conda activate open-deep-research
pip install -e .
langgraph dev
python src/open_deep_research/run.py "你的研究问题"
bash ./init.sh
conda run --no-capture-output -n open-deep-research python -m compileall -q src
conda run --no-capture-output -n open-deep-research python -m pytest --collect-only -q src/legacy/tests
python tests/run_evaluate.py
```

注意：`python tests/run_evaluate.py` 会调用 LangSmith、模型和搜索服务；README 明确提示完整评估可能产生成本。

## 5) 环境变量与配置

- Config sources: `langgraph.json`, `src/open_deep_research/configuration.py`, `.env`, `RunnableConfig["configurable"]`。
- Core env vars observed in code: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `TAVILY_API_KEY`, `GET_API_KEYS_FROM_CONFIG`, `PRINT_PROCESS_INFO`, `SUPABASE_URL`, `SUPABASE_KEY`。
- Evaluation env vars observed in code: `LANGSMITH_API_KEY` and LangSmith client environment variables.
- Legacy/search env vars observed in code: `AZURE_AI_SEARCH_ENDPOINT`, `AZURE_AI_SEARCH_INDEX_NAME`, `AZURE_AI_SEARCH_API_KEY`, `PERPLEXITY_API_KEY`, `EXA_API_KEY`, `GOOGLE_CX`, `EVAL_MODEL`, `RESEARCH_AGENT`, `SEARCH_API`, `SUPERVISOR_MODEL`, `RESEARCHER_MODEL`, `PLANNER_PROVIDER`, `PLANNER_MODEL`, `WRITER_PROVIDER`, `WRITER_MODEL`, `MAX_SEARCH_DEPTH`。
- 作者已确认 `.env` 是本项目正式本地环境配置文件；不再要求维护或引用单独的环境模板文件。
- Runtime constraints: 默认搜索 API 为 Tavily；默认模型为 OpenAI `gpt-4.1*`；本地规范要求优先 conda/LangGraph 命令。

## 6) 证据

- `pyproject.toml`
- `langgraph.json`
- `README.md`
- `AGENTS.md`
- `init.sh`
- `src/open_deep_research/configuration.py`
- `src/open_deep_research/utils.py`
- `src/security/auth.py`
- `tests/run_evaluate.py`
- `.github/dependabot.yml`
