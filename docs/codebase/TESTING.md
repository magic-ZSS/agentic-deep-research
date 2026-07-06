# 测试与验证

## 1) 测试栈与命令

- Primary test framework: `pytest`，依赖声明在 `pyproject.toml`。
- Evaluation stack: LangSmith `Client.aevaluate`、Pydantic structured evaluators、OpenAI/Anthropic evaluator models。
- 断言与 mock 工具: pytest `assert`、LangGraph `MemorySaver`；未发现通用 mock 框架配置。[TODO]
- Intended commands:

```bash
bash ./init.sh
conda run --no-capture-output -n open-deep-research python -m compileall -q src
conda run --no-capture-output -n open-deep-research python -m pytest --collect-only -q src/legacy/tests
python tests/run_evaluate.py
python tests/extract_langsmith_data.py --project-name "YOUR_EXPERIMENT_NAME" --model-name "your-model-name" --dataset-name "deep_research_bench"
```

`tests/run_evaluate.py` 会调用外部模型、Tavily 和 LangSmith；README 明确提示完整评估可能产生成本。

## 2) 本次会话观察到的验证结果

| 命令 | 结果 | 备注 | 证据 |
|---------|--------|-------|----------|
| `bash ./init.sh` inside sandbox | failed | Bash/WSL `E_ACCESSDENIED`。 | terminal output, `progress.md` |
| `bash ./init.sh` escalated | returned 0 with bad output | 仍混入 CRLF/WSL 与 `python: command not found`；不能作为干净通过证据。 | terminal output, `progress.md` |
| `conda run --no-capture-output -n open-deep-research python -m compileall -q src` | passed | 未调用外部模型/搜索。 | terminal output |
| `conda run --no-capture-output -n open-deep-research python -m pytest --collect-only -q src/legacy/tests` | passed | 收集 1 个测试；输出 LangGraph/langchain-community deprecation warnings。 | terminal output |
| `conda run --no-capture-output -n open-deep-research python -m mypy src` | failed | 当前 conda 环境无 `mypy` 模块。 | terminal output |
| `conda run --no-capture-output -n open-deep-research python -m ruff check src tests` | failed | 当前 conda 环境无 `ruff` 模块。 | terminal output |

## 3) 测试布局

- `src/legacy/tests/conftest.py`: pytest CLI options，包括 agent、search API、eval model 和 legacy 模型配置。
- `src/legacy/tests/test_report_quality.py`: 生成 legacy graph/multi-agent 报告，并用 LLM-as-judge 评估报告质量。
- `src/legacy/tests/run_test.py`: rich CLI wrapper，最终调用 pytest。
- `tests/run_evaluate.py`: 当前主图的 Deep Research Bench LangSmith 批量评估。
- `tests/evaluators.py`: relevance、structure、correctness、groundedness、completeness、overall quality 等 evaluator。
- `tests/supervisor_parallel_evaluation.py`: 检查 supervisor 首轮并行 tool call 数是否符合 reference。
- `tests/pairwise_evaluation.py`: head-to-head/free-for-all 对比评估。
- `tests/extract_langsmith_data.py`: 从 LangSmith project 导出 Deep Research Bench JSONL。

## 4) 测试覆盖矩阵

| 范围 | 是否覆盖 | 典型目标 | 备注 |
|-------|----------|----------------|-------|
| Syntax/compile | yes | `src` | `compileall -q src` 本次通过。 |
| Lint | configured, env gap | repo / `src tests` | Ruff 配置存在，但本地 `open-deep-research` env 没有 `ruff` 模块。 |
| Type check | configured, env gap | `src` | `mypy` 在 optional dev dependency 和 `init.sh` 中声明，但本地 env 没有模块。 |
| 单元测试 | partial/unclear | [TODO] | 未发现针对主图小函数的常规 unit test 文件。 |
| Legacy pytest | yes | legacy graph/multi-agent quality test | 当前收集 1 个测试，真实执行会调用模型/搜索。 |
| Current graph evaluation | yes, external | Deep Research Bench via LangSmith | `tests/run_evaluate.py` 使用 dataset `Deep Research Bench`。 |
| E2E/manual | yes, external | `langgraph dev`, `run.py` | 会依赖 `.env` 和外部 provider。 |
| 覆盖率 | no evidence | [TODO] | 未发现 coverage 配置或阈值。 |

## 5) Mock 与隔离策略

- Main evaluation uses `deep_researcher_builder.compile(checkpointer=MemorySaver())` and random `thread_id` for isolation.
- `tests/run_evaluate.py` 明确设置 `mcp_config = None`，保持 benchmark 时不使用 MCP tools。
- legacy pytest 使用真实 graph、真实模型配置和搜索 API；不是隔离的纯 unit test。
- 外部依赖 mock: 未发现对 Tavily、LangSmith、LLM provider 的 mock/stub。[TODO]
- Common failure modes: API key 缺失、外部服务成本/限流、模型 structured output/tool calling 不兼容、当前 `init.sh` 在 Windows/WSL 链路输出不可靠。

## 6) 证据

- `init.sh`
- `pyproject.toml`
- `README.md`
- `progress.md`
- `src/legacy/tests/conftest.py`
- `src/legacy/tests/test_report_quality.py`
- `src/legacy/tests/run_test.py`
- `tests/run_evaluate.py`
- `tests/evaluators.py`
- `tests/supervisor_parallel_evaluation.py`
- `tests/pairwise_evaluation.py`
- `tests/extract_langsmith_data.py`
