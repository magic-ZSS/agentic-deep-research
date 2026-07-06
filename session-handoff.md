# 会话交接

## 当前目标（Current Objective）

- 目标：固化作者对 onboarding 问题的决策。
- 当前状态：completed。
- 分支 / 提交：当前工作树，未创建提交。

## 本次已完成

- 更新 `README.md`，Quickstart 改为 conda/pip + `langgraph dev`，不再推荐 uv 或单独环境模板文件。
- 更新 `AGENTS.md`，加入作者已确认规则：
  - `.env` 是正式本地环境配置文件。
  - 后续文档统一 conda/pip，不推荐 uv。
  - `init.sh` 修复路径由后续执行者按最小化改动原则自行决定。
  - 只保证主实现 `src/open_deep_research/`，legacy 仅历史参考。
  - 多 MCP server 支持是明确后续 feature。
- 更新 `docs/codebase/` 中相关 onboarding 文档，并把 `CONCERNS.md` 的未决问题列表替换成“作者已决策规则”。
- 更新 `feature_list.json` 和 `progress.md`。
- 未修改业务代码。

## 验证证据

| 检查 | 命令 | 结果 | 备注 |
|---|---|---|---|
| JSON 格式 | `conda run --no-capture-output -n open-deep-research python -m json.tool feature_list.json` | 通过 | 状态文件仍为合法 JSON |
| 规则残留 | README/AGENTS/onboarding 文档规则残留检查 | 通过 | 无匹配；不再有推荐命令、旧模板文件名和未决问题标记 |
| whitespace | `git diff --check` | 通过 | 仅有 LF/CRLF 提示，无 whitespace 错误 |

## 修改文件（Files Changed）

- `README.md`
- `AGENTS.md`
- `docs/codebase/STACK.md`
- `docs/codebase/STRUCTURE.md`
- `docs/codebase/ARCHITECTURE.md`
- `docs/codebase/INTEGRATIONS.md`
- `docs/codebase/TESTING.md`
- `docs/codebase/CONCERNS.md`
- `feature_list.json`
- `progress.md`
- `session-handoff.md`

## 阻塞项与风险（Blockers / Risks）

- `init.sh` 仍未修复；当前只是记录后续最小化修复策略。
- 当前环境之前已确认缺少 `ruff` 和 `mypy`。

## 下次会话启动（Next Session）

1. 阅读 `AGENTS.md`、`feature_list.json`、`progress.md`、`session-handoff.md`。
2. 阅读 `docs/codebase/CONCERNS.md` 中的作者已决策规则。
3. 执行 `git status --short`，保留已有改动。
4. 若开始多 MCP server 支持，先在 `feature_list.json` 建独立功能项。
