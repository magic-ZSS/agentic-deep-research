# 测试与验证

## 1) 测试栈与命令

- Primary test framework: `pytest`，依赖声明在 `pyproject.toml`。
- Evaluation stack: LangSmith `Client.aevaluate`、Pydantic structured evaluators、OpenAI/Anthropic evaluator models。
- 断言与 mock 工具: pytest `assert`、LangGraph `MemorySaver`；未发现通用 mock 框架配置。[TODO]
- Intended commands:

```bash
bash ./init.sh
conda run --no-capture-output -n open-deep-research python -m compileall -q src
conda run --no-capture-output -n open-deep-research python -m compileall -q src tests/test_research_limits.py
conda run --no-capture-output -n open-deep-research python -m pytest -q tests/test_research_limits.py
conda run --no-capture-output -n open-deep-research python -m pytest tests/evaluation tests/baseline -m "not live and not full_eval" -q
conda run --no-capture-output -n open-deep-research python scripts/run_baseline.py --mode replay --case simple-001 --output artifacts/baseline/smoke.jsonl
conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 0
conda run --no-capture-output -n open-deep-research python -m pytest --collect-only -q src/legacy/tests
python tests/run_evaluate.py
python tests/extract_langsmith_data.py --project-name "YOUR_EXPERIMENT_NAME" --model-name "your-model-name" --dataset-name "deep_research_bench"
```

`tests/run_evaluate.py` 会调用外部模型、Tavily 和 LangSmith，现由 `ODR_EVAL_MODE=full` 与 `RUN_FULL_EVAL=1` 保护；live baseline 还要求 `ODR_EVAL_MODE=live`、`RUN_LIVE_RESEARCH=1` 和 `--confirm-cost`。后续文档中的安装和运行示例统一使用 conda/pip 与 LangGraph 原生命令，不推荐 uv。

## 2) 本次会话观察到的验证结果

| 命令 | 结果 | 备注 | 证据 |
|---------|--------|-------|----------|
| `bash ./init.sh` inside sandbox | failed | Bash/WSL `E_ACCESSDENIED`。 | terminal output, `progress.md` |
| `bash ./init.sh` escalated | returned 0 with bad output | 仍混入 CRLF/WSL 与 `python: command not found`；不能作为干净通过证据。 | terminal output, `progress.md` |
| `conda run --no-capture-output -n open-deep-research python -m compileall -q src` | passed | 未调用外部模型/搜索。 | terminal output |
| `conda run --no-capture-output -n open-deep-research python -m compileall -q src tests/test_research_limits.py` | passed | 未调用外部模型/搜索；覆盖当前主实现与 targeted tests。 | terminal output, `progress.md` |
| `conda run --no-capture-output -n open-deep-research python -m pytest -q tests/test_research_limits.py` | passed | `7 passed`；使用 fake Tavily、fake summary 和 fake researcher model。 | terminal output, `progress.md` |
| `conda run --no-capture-output -n open-deep-research python -m pytest --collect-only -q src/legacy/tests` | passed | 收集 1 个测试；输出 LangGraph/langchain-community deprecation warnings。 | terminal output |
| `conda run --no-capture-output -n open-deep-research python -m mypy src` | failed | 当前 conda 环境无 `mypy` 模块。 | terminal output |
| `conda run --no-capture-output -n open-deep-research python -m ruff check src tests` | failed | 当前 conda 环境无 `ruff` 模块。 | terminal output |
| `conda run --no-capture-output -n open-deep-research python -m pytest tests/evaluation tests/baseline -m "not live and not full_eval" -q` | passed | Phase 0 离线 schema、storage、telemetry、runner、DeepEval adapter、门禁与 validator 测试通过；不调用外部服务。 | terminal output, `progress.md` |

## 3) 测试布局

- `src/legacy/tests/conftest.py`: pytest CLI options，包括 agent、search API、eval model 和 legacy 模型配置。
- `src/legacy/tests/test_report_quality.py`: 生成 legacy graph/multi-agent 报告，并用 LLM-as-judge 评估报告质量。
- `src/legacy/tests/run_test.py`: rich CLI wrapper，最终调用 pytest。
- `tests/run_evaluate.py`: 当前主图的 Deep Research Bench LangSmith 批量评估。
- `tests/baseline/`: 三档 case、去敏 replay fixture、machine-readable manifest 与 replay/live runner 集成测试。
- `tests/evaluation/`: schema、原子存储、callback、确定性 metric、DeepEval 可选边界、成本门禁与 T0 validator 测试。
- `tests/unit/mcp/`、`tests/security/mcp/`、`tests/integration/mcp/`：多 server contract、Allowed Roots/staging、Knowledge scope/proposal、redaction、unknown tool 和固定 Windows stdio smoke。
- `tests/conftest.py`: 默认关闭 `live` / `full_eval` marker；仅 CLI 与环境门禁同时打开时放行。
- `tests/test_research_limits.py`: 当前主图 targeted unit tests，覆盖 researcher 工具并发限制、Tavily query/result 限制、summary `key_excerpts` 兼容、`print_process_info` trace helper 和 fake search/summary/researcher trace。
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
| 单元测试 | partial | `tests/test_research_limits.py` | 覆盖主图并发限制、Tavily 限制、summary 结构兼容和可选流程 trace；不是完整主图 E2E。 |
| Phase 0 offline evaluation | yes | `tests/evaluation`, `tests/baseline` | 覆盖三档 case、replay、无侵入 callback、失败/取消、成本门禁、DeepEval 缺失和 T0-1 至 T0-12。 |
| Phase 4 MCP | yes | `tests/{unit,security,integration}/mcp` | 离线 30 项通过；固定 filesystem package Windows stdio 需显式环境门禁。 |
| Legacy pytest | yes | legacy graph/multi-agent quality test | 当前收集 1 个测试，真实执行会调用模型/搜索。 |
| Current graph evaluation | yes, external | Deep Research Bench via LangSmith | `tests/run_evaluate.py` 使用 dataset `Deep Research Bench`。 |
| E2E/manual | yes, external | `langgraph dev`, `run.py` | 会依赖 `.env` 和外部 provider。 |
| 覆盖率 | no evidence | [TODO] | 未发现 coverage 配置或阈值。 |

## 5) Mock 与隔离策略

- Main evaluation uses `deep_researcher_builder.compile(checkpointer=MemorySaver())` and random `thread_id` for isolation.
- `tests/run_evaluate.py` 明确设置 `mcp_config = None`，保持 benchmark 时不使用 MCP tools。
- `tests/test_research_limits.py` 使用 fake Tavily、fake summary、fake researcher model 和 monkeypatch，不调用真实外部模型或搜索 API。
- Phase 0 replay fixture 明确标记为 `synthetic_fake`；fake LangGraph 并行 StructuredTool 验证 callback 合并，未知 cost 保持 `null`。
- `pyproject.toml` 用 `-p no:deepeval` 禁用可选插件自动加载，并把 `doc/reference/` 排除在 pytest 收集路径外。
- legacy pytest 使用真实 graph、真实模型配置和搜索 API；不是隔离的纯 unit test。
- 外部依赖 mock: targeted unit tests 已覆盖 Tavily/search summary/researcher model 的局部 fake；LangSmith evaluator 和完整 E2E 仍依赖真实外部服务。
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
- `tests/test_research_limits.py`
- `tests/evaluators.py`
- `tests/supervisor_parallel_evaluation.py`
- `tests/pairwise_evaluation.py`
- `tests/extract_langsmith_data.py`
- `tests/security/mcp/`
- `tests/integration/mcp/`
- `scripts/validate_phase.py`
