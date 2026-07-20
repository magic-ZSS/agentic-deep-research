# 会话进度记录

## 当前状态（Current State）

**最后更新（Last Updated）：** 2026-07-20

**当前功能：** `phase-1-knowledge-evidence-models-001`

**状态：** in-progress（只执行阶段 1；阶段 2 尚未开始）

## 阶段 1 启动门禁

- 启动前工作树仅包含未提交的阶段 0 交付；本轮将保留并在最终状态中区分。
- `feature_list.json` 中阶段 0 为 `completed`。
- `conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 0` 退出码 0，T0-1 至 T0-12 全部 PASS。
- 当前范围仅为领域模型、Repository、SQLite migration v1、Local Blob、审计、reducer、additive state/config、测试和文档；阶段 2 保持 `not-started`。

## 已完成（What's Done）

- 以阶段开始提交 `a86b588dcd011493651c24208b446872cb4d1228` 固定当前行为、环境、依赖、配置漂移和受保护核心文件 SHA-256。
- 用 `doc/reference/refs.lock.json` 固定 PaperQA2、DeepEval、LangMem、LangGraph、MCP Servers 五个浅克隆的 URL、commit、版本证据和许可证；使用 `.gitmodules` 明确获取方式，并新增 `THIRD_PARTY_NOTICES.md`。
- 建立 `src/open_deep_research/evaluation/`：严格版本化 schema、原子 JSONL 存储、确定性指标、默认关闭 telemetry、可选 DeepEval adapter、live/full-eval 费用门禁、baseline manifest。
- 建立 9 个 baseline case（3 simple、3 medium、3 complex）和一个明确标记为 `synthetic_fake` 的 replay fixture；fixture 不是 live 结果。
- 建立 `scripts/run_baseline.py`、`scripts/capture_baseline_manifest.py` 和 `scripts/validate_phase.py`；replay 默认离线，live 必须同时满足显式模式、环境变量和 `--confirm-cost`。
- 将现有外部评测脚本置于显式成本门禁之后；普通 pytest 收集不会调用外部模型、搜索、LangSmith、DeepEval 服务或浏览器。
- 新增 Phase 0 单元/集成测试，并将 `pyproject.toml` 的 Python 下限统一为 3.11、增加可选 `eval` extra、显式登记 evaluation 子包和安全 pytest 默认参数。
- 局部更新 `docs/codebase/` 与 `doc/development_plan/README.md`；未修改 `deep_researcher.py`、`prompts.py`、`utils.py`、`configuration.py` 或 `state.py` 的研究行为。

## 关键设计决定（Decisions）

- 所有新能力默认关闭。`EvaluationTelemetry(enabled=False)` 直接委托原调用，保留同一输入、配置、返回值和异常对象。
- telemetry 只记录可证明的数据：token 覆盖不完整时总 token 为 `null`；无法可靠从回调识别 Researcher 次数时为 `null`；callback 无法保证覆盖原生搜索时标记 `search_calls_complete=false`；不伪造成本。
- replay 记录使用 `mode=replay`、`telemetry_source=fixture` 和 fixture 引用；live 未授权只输出 `not_run_no_authorization` 拒绝事件，不写运行结果。
- DeepEval 只通过惰性 adapter 接入，生产导入路径不依赖 DeepEval；真实 DeepEval metric 属于 `full_eval`，本阶段未安装也未运行。
- JSONL 写入使用临时文件、`fsync` 和原子替换，并提供进程内锁；跨进程并发仍遵循单 writer 约定。
- 保留已发现的运行默认值/UI metadata 漂移，仅写入 manifest，不在阶段 0 静默修改业务配置。

## 验收证据（T0-1 至 T0-12）

- `conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 0`：退出码 0，T0-1 至 T0-12 全部 `PASS`。
- `conda run --no-capture-output -n open-deep-research python -m pytest -q`：退出码 0，`51 passed, 1 skipped, 30 warnings`；跳过项需要可选 DeepEval/full-eval 环境。
- `conda run --no-capture-output -n open-deep-research python -m pytest tests/evaluation tests/baseline -m "not live and not full_eval" -q`：退出码 0，`44 passed, 1 deselected`。
- `conda run --no-capture-output -n open-deep-research python -m pytest tests/test_research_limits.py -q`：退出码 0，`7 passed`，证明已有离线行为回归通过。
- `conda run --no-capture-output -n open-deep-research python -m pytest --collect-only -q`：退出码 0，收集 52 项，未收集参考仓库或外部评测脚本。
- `conda run --no-capture-output -n open-deep-research python -m compileall -q src scripts tests/evaluation tests/baseline tests/test_research_limits.py`：退出码 0。
- replay 命令退出码 0，生成可加载的 `artifacts/baseline/smoke.jsonl`。
- 未授权 live 命令捕获退出码 3，返回 `not_run_no_authorization`；`artifacts/baseline/live-refused.jsonl` 不存在。

## 未运行与环境缺口（Not Run / Gaps）

- 真实 simple live baseline：`not_run_no_authorization`；用户未授权费用，未调用外部模型或搜索。
- 完整 Deep Research Bench、LangSmith、DeepEval LLM Judge：按范围禁止，未运行。
- `ruff`：目标 conda 环境没有该模块，命令退出码 1；未安装依赖。
- `mypy`：目标 conda 环境没有该模块，命令退出码 1；未安装依赖。
- `./init.sh`：未运行，因为脚本内含 `ruff check .`，会扫描用户明确禁止 lint/格式化的 `doc/reference/`；已运行等价范围内的 compile、pytest 和 `git diff --check` 子检查。

## 已知风险（Risks）

- 真实供应商 token/费用字段和 DeepEval 4.1.1 适配仍需未来获得费用授权并安装 `eval` extra 后验证；这不影响确定性 Phase 0 smoke。
- LangChain callback 不能对所有原生搜索调用给出完备计数，因此 schema 明确暴露完整性状态，不把未知数据写成 0。
- `configuration.py` 与 LangGraph 现有 Pydantic/LangGraph 弃用警告仍存在；阶段 0 不改核心行为。
- `uv.lock` 未更新；项目约束要求 conda/pip 为默认路径，阶段 0 只修改 `pyproject.toml` 的包/可选评测配置。

## 回退与下一步

- 回退时禁用或不使用 evaluation runner 即可保持旧运行路径；删除 Phase 0 新增模块/脚本并还原 `pyproject.toml`、pytest 门禁与外部评测保护即可完整回滚，核心图无需迁移。
- 下一阶段仍为 `not-started`。只有用户明确要求执行阶段 1 时，才读取 `doc/development_plan/phase_1_knowledge_evidence_models.md` 并开始；当前会话到阶段 0 汇报后停止。
