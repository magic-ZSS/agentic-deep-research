# 会话进度记录

## 当前状态（Current State）

**最后更新（Last Updated）：** 2026-07-06
**当前功能：** `author-decisions-001`
**状态：** completed

## 已完成（What's Done）

- 基于作者对 onboarding 未决问题的回答，固化 5 条项目规则：
  - `.env` 是正式本地环境配置文件；不再要求维护或引用单独环境模板文件；不得提交 `.env`、API key 或私有 MCP 配置。
  - 后续文档、命令示例和操作路径统一使用 conda/pip 与 LangGraph 原生命令，不再推荐 uv。
  - `init.sh` 若在 Windows/WSL 链路不可靠，后续执行者可在最小化改动、最低修复工作量、不影响其他任务封装性的前提下自行选择修复方式。
  - 后续只保证主实现 `src/open_deep_research/`；`src/legacy/` 仅作为历史参考。
  - 多 MCP server 支持是明确后续 feature，需要单独建功能项并更新状态文件。
- 更新 `README.md` Quickstart，移除 uv 命令和旧环境模板复制步骤，改为 conda/pip + `langgraph dev`。
- 更新 `AGENTS.md`，加入作者已确认的项目规则和保证范围。
- 更新 `docs/codebase/` 相关 onboarding 文档，将未决问题改为已决策规则。
- 未修改业务代码。

## 设计决定（Decisions）

- 不在本次直接修复 `init.sh`；作者给的是策略授权，不是当前要求修复脚本。
- 不新增单独环境模板文件；作者确认 `.env` 是正式本地环境配置文件。
- 不删除或重构 `src/legacy/`；只在文档中明确其为历史参考、不在后续保证范围。
- 不在本次开发多 MCP；只把多 MCP server 支持记录为明确后续 feature。

## 本次修改文件（Files Modified This Session）

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

## 验证证据（Verification Evidence）

- `git status --short`：开始时已有 `AGENTS.md` 改动；本次保留并在其上追加规则。
- `conda run --no-capture-output -n open-deep-research python -m json.tool feature_list.json`：通过。
- 规则残留检查：README/AGENTS/onboarding 文档中不再保留旧环境模板文件名、uv 推荐命令或未决问题标记。
- `git diff --check`：通过；仅输出 Git 下次触碰部分文本文件时 LF 会替换为 CRLF 的提示。

## 阻塞项与风险（Blockers / Risks）

- `init.sh` 仍未修复；只是记录了后续最小化修复策略。
- 当前环境之前已确认缺少 `ruff` 和 `mypy`；本次文档规则更新不依赖运行它们。

## 下次会话说明

1. 先阅读 `AGENTS.md` 和 `docs/codebase/CONCERNS.md` 的作者已决策规则。
2. 后续新增文档或命令示例时，只使用 conda/pip 与 LangGraph 原生命令。
3. 多 MCP server 支持是明确后续 feature；开发前需要在 `feature_list.json` 建独立功能项。
