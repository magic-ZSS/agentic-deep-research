# 分阶段执行协议

## 1. 硬性门禁

- 每次会话只执行一个 `phase_N`；阶段内可分多个小提交，但不得顺手进入下一阶段。
- 必须按 0→1→2→3→4→5→6→7 顺序验收。当前阶段所有 `Tn-*` 通过前，下一阶段保持 `not-started`。
- 先读取 `AGENTS.md`、动态状态文件、本目录 README、目标阶段文档，以及阶段列出的当前源码、测试和参考文件。
- 任何外部模型、搜索 API、LangSmith、DeepEval Judge 或可能产生费用的命令，都需用户当轮明确授权；无授权时记录为待执行门禁，不能伪造通过。
- 功能开关默认关闭；每阶段都要验证关闭开关后的旧流程。
- 不清理、覆盖、格式化或提交用户已有的无关改动，不主动修改 `src/legacy/`。

## 2. 会话启动

在仓库根目录执行只读恢复：

```powershell
Get-Location
Get-Content AGENTS.md
Get-Content feature_list.json
Get-Content progress.md
Get-Content session-handoff.md
git status --short
```

然后阅读：

- `doc/development_plan/README.md`；
- `doc/development_plan/architecture_target.md`；
- 目标 `phase_*.md` 全文；
- 该阶段第 5、6 节列出的实际源码、测试和参考文件；
- `docs/codebase/` 中与阶段相关的文档。

若状态文件、源码和规划不一致，以当前源码与用户当轮要求为准，并在实施前说明差异。不得只根据 README 或历史聊天推断。

## 3. Branch 与提交策略

1. 确认当前分支、HEAD 和工作树；已有改动不属于当前阶段时保持原样。
2. 只有用户要求创建分支或授权发布时才执行 Git 写操作。建议分支名：`phase/N-short-name`。
3. 每个提交只覆盖一个可验证步骤；建议消息：`phaseN: <deliverable>`。
4. 不自动 push、不创建 PR、不改远端。
5. 阶段完成时保留一个可独立 revert 的提交序列；数据库 schema/迁移和代码必须处于同一阶段边界。

如工作树与阶段目标重叠且无法安全区分，停止并请求用户决定；禁止 `git reset --hard`、`git checkout --` 或删除未知文件。

用户明确授权创建分支/提交后，可使用以下逐条模板；`git add`必须列出本阶段精确路径，不能使用`git add .`吞入用户改动：

```powershell
git status --short
git switch -c phase/N-short-name
git diff -- src/open_deep_research tests scripts doc/development_plan feature_list.json progress.md session-handoff.md
git add -- path/to/phase-owned-file1 path/to/phase-owned-file2
git diff --cached --check
git commit -m "phaseN: <independent deliverable>"
```

失败回滚优先关闭feature flag；只有用户明确授权时才对本阶段独立提交执行`git revert <commit>`，不自动revert或push。

## 4. 阶段实施循环

对阶段第 8 节每个步骤依次执行：

1. 读取该步骤直接相关的源码与测试。
2. 先写或明确可观察的验收条件。
3. 做最小实现，不展开非目标。
4. 运行最窄的单元测试。
5. 记录命令、退出码和 evidence。
6. 完成该步骤后再进入阶段内下一步骤。

若实现暴露跨阶段依赖，优先增加 Protocol/fake/feature flag 隔离，不预实现下一阶段。只有范围无法保持时，停止并请用户调整计划。

## 5. 配置、数据与迁移纪律

- 配置新增为向后兼容字段，默认关闭或保守值；环境变量、Runnable config 和 Python default 的优先级必须有测试。
- 第一版数据目录可配置，测试使用 `tmp_path`，禁止把个人绝对路径写入代码。
- SQLite 迁移有 `schema_version`、事务和失败回滚；测试禁止接触真实用户数据库。
- Knowledge、Memory、Checkpoint 使用独立数据库文件；PaperQA 索引视为可重建缓存。
- 所有删除为 soft delete/状态转换；实施测试只能删除自身临时目录。
- secret、token、私有 MCP 配置和敏感原文不得进入 fixture、日志、报告或提交。

## 6. 验证层级

### 层级 A：每个小步骤

- 目标模块的单元测试；
- 新模型/协议的类型和序列化测试；
- 必要的 formatter/linter/type checker 最小范围。

### 层级 B：阶段验收

- 阶段文档第 12 节全部 `Tn-*`；
- 第 13 节的 unit、integration 和 `scripts/validate_phase.py --phase N`；
- 关闭新增开关后的兼容测试；
- `git diff --check` 和最终 `git status --short`。

### 层级 C：成本门禁

- smoke 必须默认无网络、无模型、可重复；
- live baseline/full eval 使用独立 marker 和显式环境变量；
- 用户未授权时跳过并记录，绝不把 skip 写成 pass；
- 阶段完成定义若要求 live evidence，则保持 `in-progress` 直到用户批准并取得结果。

仓库统一入口 `./init.sh` 在 Windows/WSL/conda 链路可能输出不可靠。应先尝试；失败或不可解释时保存证据，再用目标 conda 环境中的 `python -m ...` 子检查。`ruff`/`mypy` 缺失必须记录为环境缺口。

由于 `doc/reference/` 包含外部仓库，lint/typecheck 默认只覆盖本项目的 `src tests scripts`，不得对参考仓库运行全库格式化或 lint。

## 7. Evidence 记录格式

每个验收项至少记录：

```text
acceptance_id: Tn-x
command: <exact command>
exit_code: <integer>
result: passed | failed | skipped
observed: <test count / artifact / key assertion>
evidence: <repo-relative path>
timestamp: <ISO-8601 with timezone>
reason_if_skipped: <required when skipped>
```

机器结果放在该阶段定义的 `artifacts/` 或 `tests/fixtures/` 目录，敏感报告例外。不要把终端输出全文塞进 `feature_list.json`；其中只保存稳定文件路径和简短结论。

## 8. 状态文件更新

阶段开始：

- 在 `feature_list.json` 新增或更新该阶段功能为 `in-progress`；
- 写清 dependency 和计划 evidence；
- 在 `progress.md` 记录开始提交、范围与基线状态；
- 在 `session-handoff.md` 留下不依赖聊天记录的当前步骤。

阶段结束：

- 只有全部完成定义满足时才改为 `completed`；
- 每个状态必须有测试、代码或文档 evidence；
- `progress.md` 记录修改、验证、决策、风险和下一步；
- 未完成则更新 handoff，并保持 `in-progress` 或按仓库规则设为 `blocked`；
- 再次执行 `git status --short`，明确区分本阶段与用户已有改动。

## 9. 失败、降级与回滚

### 测试失败

- 停在当前阶段，保存最小复现和失败证据；
- 不降低断言或跳过测试来制造通过；
- 修复只限当前阶段范围。

### 外部依赖/API 不兼容

- 关闭对应 feature flag，使用 fake/contract test 保住旧流程；
- 记录固定版本、失败矩阵和替代 adapter；
- 不切换到更复杂基础设施规避问题。

### 数据迁移失败

- 回滚事务并保留原 DB/版本；
- 将新数据库或索引视为可丢弃派生物时，只清理本阶段明确创建的测试/临时路径；
- 不自动硬删除用户数据，不做不可逆 downgrade。

### 代码回滚

- 优先关闭开关；
- 若需回退提交，先展示精确 commit/文件范围并获得授权；
- 禁止破坏性 reset。已产生的新 schema 数据应保留或由兼容 reader 忽略。

## 10. 进入下一阶段的条件

只有同时满足以下条件，才允许用户在下一条消息要求下一个阶段：

- 当前阶段第 12 节所有自动验收通过；
- 必需的手工/付费验收获得明确结果，而不是未授权 skip；
- 功能开关关闭时旧行为回归通过；
- 代码、测试、配置和文档 evidence 齐全；
- `feature_list.json` 状态与 evidence 同步；
- `progress.md` 和 `session-handoff.md` 已更新；
- 工作树范围可解释；
- Codex 已停止，没有预做下一阶段。

## 11. 阶段完成汇报模板

```markdown
本次只完成阶段 N，未进入阶段 N+1。

- 修改：<按模块列出>
- 设计决策：<关键边界与原因>
- 验收：<Tn-x 列表与结果>
- 命令：<命令、退出码、测试数量>
- 兼容/回退：<关闭开关的验证>
- 未解决：<风险、skip、待确认>
- 状态文件：<更新路径>
- 工作树：<本阶段文件与用户原有改动区分>
```

报告后必须停止。下一阶段只能由用户新的明确指令启动。
