# 会话交接

## 当前目标（Current Objective）

- `phase-0-baseline-references-001` 已完成。
- `phase-1-knowledge-evidence-models-001` 已于 2026-07-21 完成收口。
- 阶段 2 尚未开始；下一会话必须先重新核验阶段 1 状态与 T1 evidence。

## 阶段 1 交付物

- 领域模型与稳定 ID：`src/open_deep_research/knowledge/`、`src/open_deep_research/evidence/`。
- metadata Repository：同一 async contract 下的 `InMemoryRepository` 与 `SQLiteRepository`。
- 原始快照：`InMemoryBlobRepository` 与 `LocalBlobRepository`。
- SQLite migration v1：`src/open_deep_research/storage/migrations/v1.py`。
- additive state/config：`source_ids/evidence_ids/requirement_ids` 及默认关闭的 structured-evidence 配置。
- 阶段验证：`scripts/validate_phase.py --phase 1`。
- 测试：`tests/unit/knowledge/`、`tests/unit/evidence/`、`tests/integration/storage/`。
- 设计说明：`docs/codebase/KNOWLEDGE_EVIDENCE.md` 以及相关 codebase 文档局部更新。

## 已验证状态

- `scripts/validate_phase.py --phase 1`：退出码 0，T1-1 至 T1-16 全部 PASS。
- `scripts/validate_phase.py --phase 0`：退出码 0，T0-1 至 T0-12 全部 PASS。
- 全量离线 pytest：退出码 0，`81 passed, 1 skipped, 30 warnings`。
- 定向 unit：`20 passed`；integration/storage：`9 passed`；既有 `tests/test_research_limits.py`：`7 passed`。
- compileall：退出码 0。
- 未运行任何真实模型、搜索、LangSmith、Deep Research Bench 或 LLM Judge。
- T1-1 至 T1-16 的逐项证据见 `progress.md`。

## 关键契约

- 稳定 ID 和 Blob 去重始终包含 `KnowledgeScope`；跨 tenant/project/private 访问 fail closed。
- 原始 bytes 使用完整 SHA-256；同内容幂等，变化内容创建不可变新 Version。
- 所有删除均为 soft delete；Blob 不提供删除 API；状态改变写 audit。
- SQLite 并发依靠 scope-aware UNIQUE、外键和事务保证，不使用内存 check-then-write。
- `DocumentVersion` 生命周期与 Evidence validation 分离；引用资格由完整关系链派生。
- `notes`、`raw_notes`、`compressed_research` 保持兼容。
- `enable_structured_evidence=False` 为默认值；新模块尚未接入主图。

## 环境缺口与风险

- `ruff`、`mypy` 在目标 conda 环境未安装，命令退出码均为 1；不得伪报通过或未经授权安装。
- editable reinstall 受沙箱对 conda/临时目录写权限限制；阶段验证器已完成仓库外导入 smoke。
- Windows 固定 pytest 临时目录曾出现 ACL 锁定；使用唯一 `--basetemp=.phase-validation-tmp/<run-id>` 可稳定执行，旧受限目录不要擅自删除。
- SQLite metadata 与 Blob 文件不是单一跨资源事务；未来导入服务需使用幂等作业和可恢复步骤。
- 既有 Pydantic/LangGraph 弃用警告仍存在，阶段 1 未修改核心图。

## 恢复时必读

1. `AGENTS.md`
2. `feature_list.json`
3. `progress.md`
4. `session-handoff.md`
5. `doc/development_plan/README.md`
6. `doc/development_plan/architecture_target.md`
7. `doc/development_plan/reference_repositories.md`
8. `doc/development_plan/execution_protocol.md`
9. 用户明确指定的阶段文档

## 下一步

等待用户明确下达阶段 2 指令。收到后先确认 `phase-1-knowledge-evidence-models-001=completed`，并运行 `scripts/validate_phase.py --phase 1`；门禁未通过必须停止。不得自动实现阶段 2 或更后阶段。
