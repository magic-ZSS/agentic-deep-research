# 编码约定

## 1) 命名规则

| 项目 | 规则 | 示例 | 证据 |
|------|------|---------|----------|
| Files | Python 文件使用 lowercase/snake_case。 | `deep_researcher.py`, `run_evaluate.py` | `src/open_deep_research/`, `tests/` |
| Functions/methods | snake_case。 | `clarify_with_user`, `get_api_key_for_model` | `src/open_deep_research/deep_researcher.py`, `src/open_deep_research/utils.py` |
| Types/classes | PascalCase。 | `Configuration`, `ResearchQuestion`, `SupervisorState` | `src/open_deep_research/configuration.py`, `src/open_deep_research/state.py` |
| Constants/env vars | Python constants 和环境变量用 uppercase。 | `MODEL_TOKEN_LIMITS`, `OPENAI_API_KEY` | `src/open_deep_research/utils.py` |
| Config fields | snake_case，并可用 uppercase env var 覆盖。 | `max_researcher_iterations` -> `MAX_RESEARCHER_ITERATIONS` | `src/open_deep_research/configuration.py` |

## 2) 格式化与 Lint

- 格式化工具: 未发现独立 formatter 配置。[TODO]
- Linter: Ruff，配置在 `pyproject.toml`。
- Enforced rules: `E`, `F`, `I`, `D`, `D401`, `T201`, `UP`；忽略 `UP006`, `UP007`, `UP035`, `D417`, `E501`；`tests/*` 忽略 `D`, `UP`。
- Docstring convention: Google pydocstyle。
- Run commands:

```bash
python -m ruff check .
python -m mypy src
python -m pytest --collect-only -q src/legacy/tests
```

当前 `open-deep-research` conda 环境中，`python -m ruff` 和 `python -m mypy` 均报告模块不存在；这是环境与 `init.sh` 预期不一致，不是配置文件缺失。

## 3) Import 与模块约定

- Import grouping/order: Ruff 选择了 `I`，即 isort 规则由 Ruff 执行。
- Alias vs relative import policy: 主代码使用绝对包导入，例如 `from open_deep_research.configuration import Configuration`。
- `run.py` 是例外：为支持 IDE 直接运行，先把 `PROJECT_ROOT / "src"` 插入 `sys.path`，再导入 `open_deep_research.deep_researcher`，该导入行用 `# noqa: E402` 标注。
- 公共导出策略: 未发现 `__all__` 或 barrel-style 聚合导出。[TODO]

## 4) 错误处理与日志约定

- Graph 节点错误策略：structured output 调用使用 `.with_retry(...)`；token-limit 错误在压缩和最终报告阶段通过裁剪消息或 findings 重试。
- Tool 执行错误策略：`execute_tool_safely` 将异常转成 `"Error executing tool: ..."` 字符串；MCP 认证交互错误转成 `ToolException`。
- Auth 错误策略：`src/security/auth.py` 用 `Auth.exceptions.HTTPException` 返回 401/500。
- 日志: `utils.py` 使用标准库 `logging.warning`/`logging.error`；未发现集中 logging 配置、结构化字段或 redaction 规则。[TODO]
- Sensitive-data redaction: 代码未发现显式敏感数据脱敏策略；`.env` 被 `.gitignore` 忽略，AGENTS 明确禁止提交 API key 和私有 MCP 配置。

## 5) 测试约定

- Test file location: 当前评估脚本在 `tests/`；legacy pytest 在 `src/legacy/tests/`。
- Test naming: pytest 文件使用 `test_*.py`，测试函数使用 `test_*`。
- Mock 与隔离: 当前可见测试主要用 LangGraph `MemorySaver` 和唯一 `thread_id`；未发现系统性 network mocking。[TODO]
- 覆盖率预期: 未发现 coverage 工具或阈值配置。[TODO]

## 6) 证据

- `pyproject.toml`
- `init.sh`
- `src/open_deep_research/deep_researcher.py`
- `src/open_deep_research/utils.py`
- `src/open_deep_research/run.py`
- `src/security/auth.py`
- `src/legacy/tests/conftest.py`
- `src/legacy/tests/test_report_quality.py`
- `.gitignore`
- `AGENTS.md`
