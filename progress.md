# 会话进度记录

## 当前状态

**最后更新：** 2026-07-21

**当前功能：** `phase-5-memory-system-001`

**状态：** completed（阶段 5 已收口；阶段 6 未开始）

## 阶段门禁

- 阶段 0–3 均保持 `completed`。
- 阶段 4 开始前复核：`scripts/validate_phase.py --phase 4` 退出码 0，T4-1 至 T4-16 全部 PASS。
- 阶段 5：`scripts/validate_phase.py --phase 5` 退出码 0，T5-1 至 T5-16 全部 PASS。
- 阶段 6 未开始；本轮未实现 Claim/Citation Validator 或报告修复。

## 阶段 5 交付物

- `src/open_deep_research/runtime/`：可信 `RuntimeIdentity`、managed async `AsyncSqliteSaver/AsyncSqliteStore` lifespan、root graph factory、RunEvidenceStore 引用恢复。
- `src/open_deep_research/memory/`：五类 payload schema、稳定 ID、proposal/decision/audit、Repository Protocol、InMemory/SQLite 实现、七项 Write Gate、recall、proposal-only LangMem adapter。
- `src/open_deep_research/tools/memory.py` 与 `mcp_servers/`：只读且不能选择 Namespace 的 `memory_search`；没有 Agent raw put/delete/force write。
- `configuration.py`：新增默认关闭的 memory/checkpoint 配置；Checkpoint Store、Checkpoint、Knowledge、Run Evidence、Memory 路径必须分离。
- `state.py`：仅增加轻量 memory/proposal/evidence retention 引用字段，保留 `notes/raw_notes/compressed_research`。
- `scripts/resume_research.py`、`scripts/inspect_memory.py`、`scripts/validate_phase.py --phase 5`。
- `tests/unit/memory/`、`tests/integration/checkpoint/`、`tests/integration/memory/`、`tests/security/test_memory_namespace.py`。

## 关键决策

- 固定 `langgraph-checkpoint-sqlite==3.1.0` 与可选 `langmem==0.0.30`；SQLite Store 不启用 vector index/sqlite-vec 检索。
- `AsyncSqliteSaver.from_conn_string` 和 `AsyncSqliteStore.from_conn_string` 只在同一 `AsyncExitStack` 内存在；进入时 `setup()`，退出/异常/取消均关闭连接。
- Checkpoint serializer 显式 `pickle_fallback=False`，msgpack 使用安全 allowlist；不加载不可信 pickle。
- Namespace 固定为 `(odr, tenant_id, user_id, project_id, memory_type)`，只从可信 identity 生成，模型/MCP 参数不能覆盖。
- Working Memory 只进 checkpoint；长期写入必须先 proposal，再执行 importance/source/dedupe/freshness/sensitivity/quality/policy 七项 Gate。
- Semantic 每次 recall 重新验证 active Evidence；Episodic 需质量分；Procedural 至少三次独立成功并通过 regression/approval；Preference 必须有 explicit statement ref。
- RunEvidenceStore 在 checkpoint 中只保存 `run_id + evidence_ids + retention_status` 引用；恢复时用同一用户 scope/run 重开 SQLite store，跨用户/跨 run 拒绝。

## 验证证据

- Phase 4 门禁：退出码 0；T4-1～T4-16 PASS，内部 30 passed。
- 阶段 5 suite：21 passed，退出码 0。
- `scripts/resume_research.py --self-test`：PASS，退出码 0；测试数据库随后清理。
- Phase 5 validator：T5-1～T5-16 全部 PASS，退出码 0。
- Phase 3/baseline 回归：30 passed，退出码 0。
- Phase 4 MCP 回归：30 passed、1 skipped；skip 为需显式 marker 的 Windows stdio smoke。
- Ruff 精确阶段范围：All checks passed，退出码 0。
- mypy：原始命令会跟随导入并暴露既有跨阶段错误；使用 `--explicit-package-bases --follow-imports=skip` 隔离阶段 5 后 13 files 无问题，退出码 0。
- compileall、`git diff --check`：退出码 0。
- 未运行任何远程模型、Web 搜索、LangSmith、Deep Research Bench 或 LLM Judge。

## 风险与回退

- SQLite 仅作为本地第一版；多进程高写入吞吐仍不是目标。持久化域使用独立 DB，短事务且不在事务内调用模型。
- `enable_memory=False`、`enable_memory_writes=False`、`checkpointer_backend=off` 均为默认；关闭后沿用模块级旧图导出。
- 当前 recall 为确定性文本匹配与 token 近似预算；未启用外部向量库。
- 项目已有 Pydantic/LangGraph deprecation warnings 与全项目 mypy 债务未在本阶段扩张修复。

## 下一步

阶段 5 已满足完成定义并停止。只有用户明确要求阶段 6，且重新核验本页 T5 evidence 后，才可执行 `doc/development_plan/phase_6_citation_validation.md`。
