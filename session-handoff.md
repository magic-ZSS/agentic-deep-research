# 会话交接

## 当前目标（Current Objective）

- 目标：新增 IDE 友好的 Deep Research 普通运行入口。
- 当前状态：completed。
- 分支 / 提交：当前工作树，未创建提交。

## 本次已完成

- 新增 `src/open_deep_research/run.py`。
- 入口默认适合 IDE：修改 `QUESTION = "..."` 后直接运行文件。
- 入口也兼容轻量命令行：`python src/open_deep_research/run.py "你的研究问题"`。
- 保留原本 LangGraph 图和默认配置：直接导入 `deep_researcher`，不修改 `deep_researcher.py`，不传入 clarification/model/search 等配置覆盖。
- 更新 `feature_list.json` 和 `progress.md`，记录功能状态、设计决定、验证证据和风险。

## 验证证据

| 检查 | 命令 | 结果 | 备注 |
|---|---|---|---|
| 统一入口基线 | `bash ./init.sh` | 异常输出 | 沙盒内 WSL `E_ACCESSDENIED`；提权后返回 0，但混入 CRLF/WSL 与 `python: command not found` 报错 |
| 单文件编译 | `conda run --no-capture-output -n open-deep-research python -m compileall -q src/open_deep_research/run.py` | 通过 | 未触发模型调用 |
| 单文件 Ruff | `conda run --no-capture-output -n hf-agent python -m ruff check src/open_deep_research/run.py` | 通过 | 仅目标文件 |
| 空问题路径 | `conda run --no-capture-output -n open-deep-research python src/open_deep_research/run.py` | 通过预期 | 未设置 `QUESTION` 时给出本地错误提示，不调用图 |

## 修改文件（Files Changed）

- `src/open_deep_research/run.py`
- `feature_list.json`
- `progress.md`
- `session-handoff.md`

## 阻塞项与风险（Blockers / Risks）

- `init.sh` 当前在 Windows/WSL 调用链中输出不可靠，需要单独修复 CRLF/WSL/Python 环境问题。
- `open-deep-research` conda 环境未发现可用 `mypy` 模块。
- 未执行真实研究调用，以避免调用外部模型/搜索服务和产生费用。

## 下次会话启动（Next Session）

1. 阅读 `AGENTS.md`、`feature_list.json`、`progress.md` 和本文件。
2. 执行 `git status --short`，保留用户已有改动。
3. 如需真实运行，先配置 `.env`，再编辑 `src/open_deep_research/run.py` 顶部 `QUESTION`。
