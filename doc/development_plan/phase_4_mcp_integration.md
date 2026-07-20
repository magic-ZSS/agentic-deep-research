# 阶段 4：Filesystem MCP 与 Knowledge MCP

## 1. 阶段目标

在 Windows 原生环境中接入受限 Filesystem MCP，并实现调用阶段 1–3 Repository/Service 的自定义 Knowledge MCP。完成后 Agent 只能读取 Allowed Roots 内的知识源、在独立 staging 中执行受控写入，并通过 `kb_*` 工具搜索/读取知识或提出状态变更建议；不能路径穿越、硬删除、强制激活或绕过知识治理。

## 2. 为什么此阶段现在做

阶段 3 已提供受治理 Retriever、生命周期 proposal 和审计，本阶段才能安全向 MCP 暴露能力而不让外部工具直接操作 SQLite/索引。阶段 5 将复用 MCP Namespace/权限边界并在已有扩展点增加 `memory_search`。先完成文件访问和 Knowledge MCP 安全测试，可防止 Memory 阶段把未经授权文件或跨用户数据写入长期存储。

## 3. 范围

- 把当前单个 HTTP `MCPConfig` 扩展为向后兼容的多 server 配置，支持 `streamable_http` 与 Windows `stdio`；
- 保留当前 Supabase auth/token exchange 路径，但拆分连接、auth、tool filtering 和错误映射，避免继续堆在 `utils.py`；
- 为 Filesystem MCP 定义 `AllowedRoot(path, mode=read_only|import_staging)`；canonical/realpath、分隔符边界、null-byte、symlink、真实 parent 和 Roots 更新均 fail closed；
- 将只读源和可写staging分server/进程，并使用Windows ACL/进程权限作为第二层防线；
- 对Agent暴露经过项目wrapper的最小文件工具：只读根仅read/list/search/info；不暴露上游可覆盖文件的原始`write_file`。staging只提供项目自有exclusive-create工具，使用server生成或校验的相对路径、`O_EXCL`/等效原子创建，已存在即冲突，禁止overwrite/edit/move/delete；
- 实现 Knowledge MCP：`kb_search`、`kb_read`、`kb_get_source`、`kb_search_past_queries`、`kb_propose_ingest`、`kb_propose_stale`、`kb_propose_quarantine`；
- 所有 Knowledge 写工具只创建 proposal/audit，不直接改变 active 状态或写 Memory；
- Knowledge MCP复用阶段1 `KnowledgeScope/KnowledgeAccessContext`；tenant/project/user来自可信CLI/auth context，所有search/read/past-query/proposal均由Repository scope过滤，不接受模型自报namespace；
- tool annotations 明确 read-only/destructive/open-world 语义，同时承认 annotations 不是授权；
- 提供 Windows conda + `cmd /c npx` 或固定本地 Node 包的启动说明、配置模板和安全测试；
- Knowledge MCP 的检索输出与内部 `KnowledgeRetriever` 对同一请求一致。

## 4. 非目标

- 不开放 `hard_delete`、`force_promote`、`force_memory_write` 或数据库/索引文件路径；
- 不允许 Agent 直接调用未包装的 filesystem server 全量工具；
- 不依赖 `readOnlyHint` 作为唯一访问控制，不把一个可写进程同时赋予所有只读知识目录写权限；
- 不实现 Memory 内容；`memory_search` 在本阶段只保留 server registry/protocol 扩展点，阶段 5 有真实 MemoryRepository 后才注册，不能提供返回虚构结果的 stub；
- 不实现 OAuth 管理后台、远程多租户 MCP gateway 或复杂 UI；
- 不把 MCP tool result 直接 promotion 为 active knowledge；
- 不全面重写现有 MCP auth 或 Researcher 图；
- 不把内部Windows绝对路径、blob storage ref或真实Allowed Root写入模型可见结果/报告；只返回公开display URI、root ID和root-relative locator；
- 不运行未固定版本的 `npx -y` 生产配置，不把个人绝对路径/secret 提交到仓库。

## 5. 当前项目修改点

预计新增：

- `src/open_deep_research/mcp/client.py`、`config.py`、`auth.py`、`tool_registry.py`、`errors.py`；
- `src/open_deep_research/mcp/filesystem_policy.py`、`filesystem_adapter.py`；
- `src/open_deep_research/mcp/staging.py`：exclusive-create wrapper和公开locator映射；
- `src/open_deep_research/mcp_servers/knowledge_server.py`、`schemas.py`、`services.py`；
- `scripts/run_knowledge_mcp.py`、`scripts/validate_mcp_config.py`；
- `config/examples/mcp.windows.example.json`（占位路径，无 secret）；
- `docs/mcp_windows.md` 或 `doc/development_plan` 指向的运行说明；
- `tests/unit/mcp/`、`tests/integration/mcp/`、`tests/security/mcp/`。

预计修改：

- `configuration.py::MCPConfig`：兼容旧 `url/tools/auth_required`，新增 server map、transport、command/args、root policy；
- `utils.py`：将现有 MCP 函数迁移为兼容 façade/import，`get_all_tools` 使用新 registry；不重构 Tavily 等无关函数；
- `deep_researcher.py::researcher_tools`：只允许把未知tool name的直接字典索引改为受控ToolMessage错误，避免在安全包装外KeyError；不改变图结构；
- `deep_researcher.py`：原则上不改图，只通过既有工具装配取得 MCP tools；
- `pyproject.toml`：仅在现有 `mcp/langchain-mcp-adapters` 版本不足时固定兼容范围，不引入服务基础设施；
- `.gitignore`：忽略本地 MCP 配置/临时 staging，保留 example；
- `scripts/validate_phase.py` 和状态文件。

## 6. 参考仓库

- **MCP Servers Filesystem**：重点参考 `path-validation.ts`、`lib.ts::validatePath/writeFileContent`、`roots-utils.ts`、`path-utils.ts`、`index.ts` 和 `src/filesystem/__tests__/`。借鉴 realpath、parent、Windows drive/UNC/WSL、symlink/TOCTOU 和 tool annotations 测试；本项目 Roots 为空时采用更严格 fail-closed。
- 上游 filesystem server 没有每 root 的 read/write mode，Docker `ro` 也不是 MCP 属性。因此只读/写入必须靠分进程/工具白名单 + OS ACL，不照搬“一个 server 所有工具”的配置。
- 上游 README 与代码/许可存在漂移；仓库处于 MIT→Apache-2.0 过渡，文档 CC-BY-4.0。优先使用固定发布包和借鉴安全场景，不复制 TypeScript；复制前逐文件查历史/许可证并保留 notice。
- **当前项目 MCP**：参考 `utils.py` 的 Supabase token exchange、ToolException 交互错误和白名单；保留兼容，修复 silent failure 仅限输出结构化诊断，不改变 auth 语义。
- **LangGraph/LangChain MCP adapters**：使用 MultiServerMCPClient 的实际已安装版本 contract；当前硬编码 `server_1` 是要移除的实现限制。
- **阶段 1–3**：Knowledge MCP 只调用 `KnowledgeRetriever`、Repository 和 LifecycleProposal service，不自行实现存储/规则。

## 7. 数据结构和接口

```text
MCPServerConfig
  name, transport=stdio|streamable_http,
  command?, args?, url?, auth_required,
  allowed_tools, timeout, enabled

AllowedRoot
  root_id, canonical_path, mode=read_only|import_staging,
  allowed_operations, public_alias, follow_symlinks=false,
  allowed_suffixes/media_types, max_file_bytes,
  max_files_per_run, max_total_bytes_per_run

FilesystemAccessDecision
  requested_path, resolved_path?, root_id?, operation,
  allowed, reason, destructive, audit_id

KnowledgeMCPContext
  knowledge_access_context(scope from phase 1),
  trusted_tenant_id, trusted_user_id, project_id,
  request_id, capabilities

KnowledgeProposal
  proposal_id, action=ingest|stale|quarantine,
  target/source payload, reason, actor, status=pending,
  created_at, audit_id
```

Knowledge tool输出复用阶段2`EvidenceHit/SourceView` schema，并提供MCP JSON序列化版本。`kb_propose_ingest`只能引用由exclusive-create写入受控staging的root-relative artifact或已存在candidate，不能让server抓任意URL；内部canonical path/storage ref在序列化前必须去敏。

工具清单与 annotation：

- `kb_search/read/get_source/search_past_queries`：`readOnlyHint=true`；
- `kb_propose_*`：`readOnlyHint=false`、`destructiveHint=false`；
- 全部本地知识工具 `openWorldHint=false`；
- `memory_search` 由阶段 5 在真实实现存在且权限测试通过后注册。

## 8. 执行步骤

1. 为当前 MCP config/tool list 写行为快照和 adapter contract，确定旧配置兼容映射。
2. 定义多 server schema、可信 runtime context和显式错误；把 auth/client/registry 从 `utils.py` 最小抽离。
3. 实现 Windows path policy：normalize、resolve、边界分隔符、null、existing realpath、new-parent realpath、symlink/UNC/drive/WSL和空 Roots fail closed。
4. 设计并验证两类Filesystem进程/包装：知识源只读；import staging进程仅被项目exclusive-create wrapper调用。禁止绑定上游原始write/edit/move，记录OS ACL和实际工具集合。
5. 固定 filesystem server package/version/启动命令，生成无真实路径的 Windows 示例和 config validator。
6. 实现Knowledge MCP read tools，直接调用同一个Retriever/Repository service；从可信context构造阶段1KnowledgeScope，对结果做scope/schema/limit和公开locator去敏。
7. 实现三个 proposal tools；验证只产生 pending proposal/audit，不调用 promotion/delete/memory write。
8. 将多MCP tool registry接回Researcher；处理名称冲突、未知tool name、timeout、交互错误和单server故障隔离，旧配置仍工作。
9. 建立 in-process fake MCP 与真实本地 stdio smoke；安全测试使用临时 roots，不触及用户文件。
10. 验证工具清单中不存在禁止能力，执行阶段验收、状态更新并停止。

## 9. 配置和回退

- `enable_filesystem_mcp=False`、`enable_knowledge_mcp=False`；默认不启动子进程/端口。
- 旧 `mcp_config={url,tools,auth_required}` 自动映射为一个 `streamable_http` server，保持现有行为。
- 新 `mcp_servers` 是命名映射；每个 server 单独 enabled、timeout、allowed_tools 和 transport。
- Windows example 使用 `${ALLOWED_READ_ROOT}` 等占位符或本地未提交配置，绝不提交用户名绝对路径。
- Roots 为空、路径不存在或 config 无效时 server/tool disabled 并 fail closed，不回退到 cwd/仓库根。
- staging suffix/media type、单文件bytes、每run文件数和总bytes有保守上限；超限/类型不允许时在创建前或流式越界时原子拒绝并清理自身临时文件，不留下partial target。
- Filesystem/Knowledge MCP 失败时 Researcher 可继续阶段 3 内部 Retriever/Web 路径；不能把连接错误当搜索空结果。
- 回退只需关闭开关/恢复旧 config；pending proposal 和审计保留，不需迁移删除。

## 10. 单元测试

- Windows drive 大小写、UNC、WSL、`..`、相似前缀、null byte、绝对/相对路径；
- existing symlink/junction、new path parent、root itself 和 root update；
- empty/all-invalid Roots fail closed，旧 Roots 不继续有效；
- read-only root拒绝write/edit/move/delete；staging只允许exclusive create，重复文件名/已存在目标拒绝，不暴露上游overwrite-capable `write_file`；
- staging suffix/media type、单文件/每run文件数/总bytes quota的边界值、并发计数和超限无partial file；
- tool annotations 与实际 policy 一致，但绕过 annotation 仍被 server/service 拒绝；
- 旧单 server config 到新 schema 的转换；多 server 名称冲突/部分失败/timeout；
- Knowledge MCP schema、limit、ID、Namespace 和错误映射；
- Repository scope来自可信context，跨tenant/project/private owner读取与存在性探测均拒绝；
- proposal 初始 pending、无直接状态变化、无 hard delete symbol/tool；
- secret/path redaction 和审计记录。
- 未知tool name返回与tool_call_id配对的受控错误ToolMessage，不在安全包装外抛KeyError。

## 11. 集成测试

- 临时只读 root 内 PDF/Markdown/HTML 可列出/读取，root 外和 sibling-prefix 文件拒绝；
- symlink/junction 指向 root 外时拒绝；允许 root 内路径变化后再次 realpath 校验；
- staging可exclusive-create一个测试文件，第二次同名写入冲突且原bytes不变；不能overwrite/edit/move/delete或写只读root；
- staging超suffix/media/单文件/文件数/总容量限制时原子拒绝，目录中无partial target，审计包含quota reason；
- 同一 query 经内部 Retriever 和 `kb_search` 返回相同稳定 evidence/source IDs 与顺序；
- `kb_read/get_source/search_past_queries` 只返回当前 namespace 可见的数据；
- 本地Source返回public alias/root-relative locator，ToolMessage/日志不包含临时root或Windows绝对路径；
- `kb_propose_*` 后 Repository 状态不变，只新增 pending proposal/audit；
- Windows `cmd /c npx <fixed-package>` 或固定本地安装的 stdio handshake、tools/list 和一次只读调用通过；
- 现有 HTTP MCP config 通过 compatibility test；一个 server 失败不删除其他 server 工具。
- 模型请求不存在的tool name时图继续运行并收到结构化错误，其他已知tool仍可执行。

## 12. 阶段验收测试

- **T4-1**：Filesystem MCP 只能读取配置的 Allowed Roots，root 外、相似前缀、`..`、UNC/drive 绕过均拒绝。
- **T4-2**：symlink/junction 和真实 parent 校验阻止路径穿越；空/无效 Roots 为 fail closed。
- **T4-3**：只读目录无法执行任何写操作；staging只暴露exclusive create，已有文件拒绝覆盖且bytes不变，不能调用原始write/edit/move/delete。
- **T4-4**：相同 query/filter 下，`kb_search` 与内部 `KnowledgeRetriever` 的 ID、排序和状态过滤完全一致。
- **T4-5**：`kb_read`、`kb_get_source` 可回溯 Version/Chunk/Source，不能传路径或 SQL 绕过 Repository。
- **T4-6**：`kb_propose_ingest/stale/quarantine` 只产生 pending proposal和审计，目标 knowledge status 不直接变化。
- **T4-7**：tools/list 中不存在 `hard_delete`、`force_promote`、`force_memory_write`；阶段 5 前也不注册无实现的 `memory_search`。
- **T4-8**：旧单 HTTP MCP 配置仍能通过 contract；多 server 中单个失败不会静默变成“无结果”或移除其他工具。
- **T4-9**：Windows conda + 固定 Filesystem MCP stdio 启动、handshake、tools/list 和允许目录读取通过，配置不含真实 secret/个人路径。
- **T4-10**：tool annotations、server白名单、path policy 和 OS 权限分层有自动/手工 evidence，annotations 不是唯一防线。
- **T4-11**：所有 proposal/访问拒绝都有 request/actor/reason 审计，日志已去敏。
- **T4-12**：关闭两个新开关时不启动进程，阶段 3 内部检索和现有 MCP 行为回归通过。
- **T4-13**：Knowledge MCP所有读取/历史查询/提议均使用可信KnowledgeScope；跨tenant/project/private owner请求返回授权错误且不泄漏实体是否存在。
- **T4-14**：未知tool name不会触发未处理KeyError；返回配对错误ToolMessage并允许其他安全tool继续。
- **T4-15**：模型可见MCP结果、审计摘要和报告fixture不包含internal storage ref、真实Allowed Root或Windows绝对路径。
- **T4-16**：staging拒绝未允许suffix/media type及超单文件、每run文件数/总bytes quota的请求；并发超限也不留下partial file或覆盖已有文件。

## 13. 验收命令

```powershell
conda run --no-capture-output -n open-deep-research python -m pytest tests/unit/mcp tests/security/mcp -q
conda run --no-capture-output -n open-deep-research python -m pytest tests/integration/mcp -m "not live" -q
conda run --no-capture-output -n open-deep-research python scripts/validate_mcp_config.py --config config/examples/mcp.windows.example.json --no-start
conda run --no-capture-output -n open-deep-research python scripts/validate_phase.py --phase 4
conda run --no-capture-output -n open-deep-research python -m ruff check src/open_deep_research/mcp src/open_deep_research/mcp_servers tests/unit/mcp tests/security/mcp tests/integration/mcp scripts/validate_mcp_config.py
conda run --no-capture-output -n open-deep-research python -m mypy src/open_deep_research/mcp src/open_deep_research/mcp_servers
git diff --check
```

固定 package 已准备且用户批准必要网络安装后，执行 Windows smoke（实际命令由阶段实现的脚本输出，不在文档写死 `latest`）：

```powershell
conda run --no-capture-output -n open-deep-research python -m pytest tests/integration/mcp -m windows_stdio -q
```

## 14. 完成定义

T4-1至T4-16全部通过；Allowed Roots/realpath/Windows/path traversal、exclusive-create与quota读写隔离、scope授权、路径去敏和fail-closed有测试；Knowledge MCP与内部Retriever一致且所有变更只产生proposal；禁用工具不存在；未知工具安全处理；旧MCP config兼容；真实Windows stdio smoke有evidence；新开关默认关闭；状态文件完整。若Node/package/ACL条件无法验证，阶段不能标`completed`。

## 15. 风险与降级方案

- **API兼容**：`langchain-mcp-adapters`/MCP transport 版本变化；用 config/client adapter 和 fake server contract，旧 schema 保留。
- **安全**：TOCTOU 无法只靠字符串校验消除；每次 IO 前 realpath，最小 OS ACL，隔离写进程，不给不可信进程改变目录结构的权限。
- **Windows**：Node/npm、`cmd /c`、反斜杠、UNC、junction 与进程关闭；固定版本、启动诊断、临时 root smoke，失败则关闭 Filesystem MCP 而不是扩大 roots。
- **并发**：多个 MCP server 启停/timeout；单 server circuit breaker和独立错误，不共享可变工具名映射。
- **数据**：proposal 可能积压；不自动应用，提供只读审查 CLI和审计。
- **许可证**：MCP Servers 混合许可；使用发布依赖，不复制源码；必须锁 SHA/package并记录 attribution。
- **Token**：文件/MCP 结果截断并返回 artifact ID，避免整文件塞入 ToolMessage。
- **回退**：关闭新开关、保留旧 HTTP MCP和内部 Retriever，pending proposal 不影响 active 知识。

## 16. 本阶段 Codex 执行指令

```text
你现在只执行 doc/development_plan/phase_4_mcp_integration.md；先验证阶段 3 completed 且全部 T3 有 evidence，否则停止，不得进入阶段 5。

先读取 AGENTS.md、状态文件、本目录总览/架构/参考/协议/本阶段文档、configuration.py 的 MCPConfig、utils.py 中所有 MCP auth/client/tool代码与 get_all_tools、deep_researcher.py 工具路径、阶段 1–3 Repository/Retriever/LifecycleProposal/Audit 实现和测试。必须定点阅读 doc/reference/mcp-servers/src/filesystem 的 path-validation.ts、lib.ts、roots-utils.ts、path-utils.ts、index.ts、README 和 __tests__。先 git status --short 并保留用户改动。

允许范围：向后兼容多MCP config/client/auth/registry抽取、stdio/http transport、Windows Allowed Roots policy、只读进程与exclusive-create staging wrapper、阶段1KnowledgeScope授权、Knowledge MCP read/proposal tools、未知工具安全错误、公开locator去敏、示例/测试/脚本/状态文件及必要最小挂接。禁止实现Memory内容或虚假memory_search，禁止暴露原始overwrite-capable write_file、hard_delete/force_promote/force_memory_write、数据库/索引文件，禁止全面重写图/搜索，禁止真实路径/secret和修改src/legacy/。

所有测试使用临时roots；不得访问或修改用户真实文件。完成第10、11节测试并逐项执行T4-1至T4-16，固定filesystem package版本，证明空Roots fail closed、symlink/path traversal拒绝、staging拒绝覆盖/超类型/超quota且无partial file、scope隔离与路径去敏、Knowledge MCP与内部Retriever一致、未知工具不会崩图、所有写知识操作只产生proposal。任何安装/网络需要按规则取得授权。

完成后更新 feature_list.json、progress.md、session-handoff.md，报告修改、威胁模型、每项验收、命令/退出码、Windows/许可证证据、回退和最终 git status。完成后立即停止，不得自动开始阶段 5。
```
