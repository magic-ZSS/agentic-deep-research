# AGENTS.md

本文件是本仓库的智能体工作规范。Codex / coding agent 每次进入本仓库时，必须优先遵守本文件；用户在当前对话中的明确要求优先级更高。

## 1. 项目定位

本项目是一个基于 LangGraph / LangChain 的 Open Deep Research 智能体工程，核心目标是支持可配置的深度研究流程，包括模型配置、搜索工具、MCP 工具接入、研究任务拆分、并行研究、结果压缩和最终报告生成。

主要维护范围：

* `src/open_deep_research/`：当前主实现，默认优先维护。
* `src/security/`：LangGraph 部署相关鉴权代码。
* `tests/`：Deep Research Bench / LangSmith 评估脚本。
* `docs/codebase/`：阶段性项目认知文档。
* `src/legacy/`：历史实现，仅作参考；除非用户明确要求，否则不要主动修改。

## 2. 每次新会话启动流程

开始任何开发、调试、重构或文档任务前，先执行以下上下文恢复流程：

1. 确认当前位于仓库根目录。
2. 读取 `AGENTS.md`。
3. 读取动态状态文件：

   * `feature_list.json`
   * `progress.md`
   * `session-handoff.md`
4. 如果存在 `docs/codebase/`，优先阅读其中与当前任务相关的文档：

   * `STACK.md`
   * `STRUCTURE.md`
   * `ARCHITECTURE.md`
   * `CONVENTIONS.md`
   * `INTEGRATIONS.md`
   * `TESTING.md`
   * `CONCERNS.md`
5. 执行 `git status --short`，识别并保留用户已有改动。
6. 在修改代码前，必须再阅读与当前任务直接相关的源码、测试和配置文件。

不要只根据文件名、README 或历史聊天记录推断实现细节。

## 3. 工作范围控制

默认一次只处理一个明确功能或问题。

不得：

* 静默扩大功能范围；
* 顺手重构无关模块；
* 覆盖、清理或格式化无关工作树改动；
* 主动修改 `src/legacy/`；
* 主动运行会调用外部模型、搜索 API、LangSmith 或产生费用的评估任务；
* 提交 `.env`、API key、私有 MCP 配置、敏感日志或敏感报告。

如果任务需要扩大范围，先说明原因并等待用户确认。

## 4. 环境与命令偏好

本项目默认本地环境：

```bash
conda activate open-deep-research
```

默认使用 conda / pip / Python 原生命令 / LangGraph 原生命令。除非用户明确要求或分析上游兼容性时必须涉及，否则不要推荐 uv 作为本项目默认操作路径。

常用命令：

```bash
pip install -e .
langgraph dev
python src/open_deep_research/run.py "你的研究问题"
```

完整 Deep Research Bench 评估可能调用外部服务并产生成本，不要主动运行：

```bash
python tests/run_evaluate.py
```

## 5. 验证策略

统一验证入口是：

```bash
./init.sh
```

但当前该脚本在 Windows / WSL / conda 链路中可能存在输出不可靠问题。因此：

1. 如果可以，优先运行 `./init.sh`。
2. 如果 `./init.sh` 失败、输出异常，或无法证明结果可靠，必须记录失败证据。
3. 根据任务范围运行可用的子检查，例如：

```bash
python -m compileall -q src
python -m pytest --collect-only -q src/legacy/tests
python -m ruff check .
python -m mypy src
```

如果 `ruff` 或 `mypy` 在当前环境中缺失，不要伪造通过；应明确记录为环境缺口。

## 6. 状态文件规则

状态文件是跨会话恢复的主要依据：

* `feature_list.json`：功能状态、依赖和 evidence 的来源。
* `progress.md`：当前状态、验证证据、决策、风险和下一步。
* `session-handoff.md`：下一次会话恢复入口。

状态值只能使用：

* `not-started`
* `in-progress`
* `blocked`
* `completed`

修改功能状态时，必须同步更新 evidence，避免只有状态没有证据。

## 7. docs/codebase 维护规则

`docs/codebase/` 是阶段性项目认知文档，不是每次小改都要全量重写。

默认策略：

* 新会话：只读取 `docs/codebase/`，不要自动重跑 skill。
* 小改动：仅当项目结构、入口、测试、配置、架构或集成结论发生变化时，局部更新相关文档。
* 大改动：当目录结构、核心流程、运行入口、技术栈或外部集成显著变化时，可以运行：

```text
$acquire-codebase-knowledge
```

更新文档时必须遵守：

1. 使用简体中文；
2. 代码、命令、路径、类名、函数名、包名、API 名、环境变量名和配置键名保持英文；
3. 新增或修改的结论必须有 evidence 文件路径；
4. 无法确认的内容标记为 `[TODO]`；
5. 需要用户确认团队意图的内容标记为 `[ASK USER]`；
6. 删除或修正已经过时的描述；
7. 最后说明更新了哪些文档，以及为什么更新。

## 8. 编码与修改原则

修改代码时遵守：

* 优先最小改动；
* 优先修复根因，不做表面绕过；
* 保持现有 LangGraph / LangChain 架构风格；
* 不引入不必要的新依赖；
* 新增依赖前先说明理由；
* 不把本地路径、个人环境或真实 secret 写入代码；
* 对涉及外部服务、费用、鉴权、MCP token、LangSmith 评估的改动保持保守。

核心主图相关改动通常需要优先阅读：

* `src/open_deep_research/deep_researcher.py`
* `src/open_deep_research/configuration.py`
* `src/open_deep_research/state.py`
* `src/open_deep_research/prompts.py`
* `src/open_deep_research/utils.py`
* `langgraph.json`

## 9. 完成标准

一个任务只有同时满足以下条件，才可以标记为完成：

* 范围内行为已实现；
* 相关源码、测试、配置或文档已按需修改；
* 相关验证已运行，或明确记录无法运行的原因；
* 命令结果、文件路径或其他 evidence 已记录；
* `feature_list.json`、`progress.md`、`session-handoff.md` 已按需更新；
* 最终回复说明：

  * 修改了什么；
  * 为什么这样改；
  * 运行了哪些验证；
  * 哪些问题仍然阻塞或需要用户确认。

## 10. 会话结束流程

结束一次开发会话前：

1. 更新 `feature_list.json` 中相关功能的 status 和 evidence。
2. 在 `progress.md` 记录本次修改、验证、决策、风险和下一步。
3. 如果任务未完成，更新 `session-handoff.md`。
4. 再次执行 `git status --short`。
5. 留下不依赖聊天记录即可恢复的下一步说明。

默认使用简体中文回复用户。
