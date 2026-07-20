# 关注点与风险

## 1) 最高优先级风险

| 严重性 | 问题 | 证据 | 影响 | 建议动作 |
|----------|---------|----------|--------|------------------|
| high | `init.sh` 在当前 Windows/WSL 路径下返回 0 但输出中有 CRLF/WSL 和 `python: command not found`。 | `init.sh`, `progress.md`, terminal output | 统一验证入口可能给出假阳性。 | 后续在最小化改动、最低修复工作量、不影响其他任务封装性的前提下，自行选择 Bash 修复或新增 conda-native/PowerShell 验证入口。 |
| high | `open-deep-research` conda 环境缺少 `ruff` 和 `mypy`，但 `init.sh` 依赖它们。 | `pyproject.toml`, `init.sh`, terminal output | 本地验证不可重复。 | 安装 dev optional dependencies 或提供 conda 环境文件。 |
| high | `supervisor_tools` 中 `if is_token_limit_exceeded(...) or True` 会把所有异常当作结束研究处理。 | `src/open_deep_research/deep_researcher.py` | 非 token-limit 错误被吞掉，报告可能缺失研究内容。 | 删除 `or True`，为 token-limit 和非 token-limit 分开处理并记录错误。 |
| medium | README 曾提到旧环境模板文件名，但作者确认 `.env` 是正式本地环境配置文件。 | `README.md`, `AGENTS.md`, author decision | 文档若继续引用旧模板名会误导新人。 | 所有后续文档统一引用 `.env`，不得提交 `.env` 或 API key。 |
| medium | `.github/dependabot.yml` 有两个顶层 `updates` key。 | `.github/dependabot.yml` | YAML 解析时可能只保留后一个 key，pip 更新配置失效。 | 合并到单个 `updates` 列表。 |
| medium | MCP 主实现当前只构造单个 `"server_1"`，并有 TODO 等待 OAP multi-MCP server 支持。 | `src/open_deep_research/utils.py`, author decision | 多 MCP server 需求无法按配置扩展。 | 已确认为明确后续 feature；开发时单独建功能项并更新状态文件。 |
| medium | Hosted auth 使用 `assert` 做 store namespace 校验。 | `src/security/auth.py` | Python optimized mode 会移除 assert，鉴权防线不应依赖 assert。 | 改成显式条件判断并抛出 auth exception。 |
| low | scan 脚本未识别 Python/LangGraph 入口点，但 `langgraph.json` 有明确入口。 | scan output, `langgraph.json` | 自动扫描结论可能误导 onboarding。 | 以 `langgraph.json` 为入口证据，必要时改进 scan 脚本。 |

## 2) 技术债

| 债务项 | 形成原因 | 位置 | 忽略风险 | 建议修复 |
|-----------|---------------|-------|-----------------|---------------|
| `init.sh` 与 Windows/WSL/conda 环境不匹配 | Bash 脚本直接调用 `python`，当前链路有 CRLF/WSL 问题。 | `init.sh`, `progress.md`, author decision | 验证结果不可信。 | 后续自行选择最小、隔离的 Bash 修复或 conda-native/PowerShell 入口。 |
| `.env` 规则需要持续一致 | 作者确认 `.env` 是正式本地配置文件，不再使用单独环境模板文件。 | `AGENTS.md`, author decision | 文档漂移会误导配置流程。 | README/AGENTS/docs 后续统一引用 `.env`，并强调不得提交 secrets。 |
| 主图异常处理过宽 | `or True` 使异常分类失效。 | `src/open_deep_research/deep_researcher.py` | 隐藏生产失败。 | 精确捕获 token-limit，其他异常记录并返回可诊断状态。 |
| MCP 连接失败无日志 | `load_mcp_tools` except 后直接返回 `[]`。 | `src/open_deep_research/utils.py` | 用户只看到没有工具，不知道连接失败原因。 | 增加 warning/error 日志和可见状态。 |
| Legacy API deprecation warnings | legacy 测试收集输出 LangGraph/langchain-community deprecation warnings。 | terminal output, `src/legacy/graph.py`, `src/legacy/multi_agent.py`, `src/legacy/utils.py`, author decision | 未来 LangGraph/LangChain 升级时 legacy 可能破裂。 | 后续不保证 legacy；只保证主实现 `src/open_deep_research/`。 |
| 大型集成工具文件 | `src/legacy/utils.py` 和 `src/open_deep_research/utils.py` 混合多个 provider/tool/token helper。 | scan output, `src/legacy/utils.py`, `src/open_deep_research/utils.py` | 修改 provider 时回归面大。 | 后续按 provider 拆分 adapter。 |

## 3) 安全关注点

| 风险 | OWASP 类别 | 证据 | 当前缓解 | 缺口 |
|------|----------------|----------|--------------------|-----|
| `.env`/API key 管理依赖约定 | N/A | `.gitignore`, `AGENTS.md`, `langgraph.json`, author decision | `.env` 被忽略；AGENTS 禁止提交 key；作者确认 `.env` 是正式本地配置文件。 | 未发现 secret scanning/security policy。 |
| Auth error 直接包含上游异常字符串 | A09 Security Logging and Monitoring Failures / N/A | `src/security/auth.py` | 返回 401。 | 可能暴露内部错误细节；应返回泛化错误并记录服务端日志。 |
| Store namespace 鉴权使用 `assert` | A01 Broken Access Control | `src/security/auth.py` | 非 optimized mode 下会校验。 | optimized mode 会移除 assert；需显式 check。 |
| MCP token 存储在 LangGraph store | N/A | `src/open_deep_research/utils.py` | 根据 `expires_in` 过期删除。 | 未见加密、审计、rotation 说明。[TODO] |
| Security config 缺失 | N/A | scan output | `.gitignore` 保护本地 env。 | 未发现 `SECURITY.md`、Snyk、secret scanning config 或 SBOM。 |

## 4) 性能与扩展关注点

| 问题 | 证据 | 当前表现 | 扩展风险 | 建议改进 |
|---------|----------|-----------------|--------------|-----------------------|
| 并行 researcher 可能触发 provider rate limit | `src/open_deep_research/configuration.py`, `README.md` | 配置 metadata 提示并发高会遇到 rate limit。 | 用户调高到 20 后可能批量失败。 | 增加 provider-aware throttling/backoff。 |
| Researcher 工具调用和 Tavily raw content 摘要 fan-out | `src/open_deep_research/configuration.py`, `src/open_deep_research/deep_researcher.py`, `src/open_deep_research/utils.py` | `max_concurrent_researcher_tool_calls` 限制 researcher 单轮工具并发；`max_queries_per_search_call` 限制 Tavily 单次 query 数并复用为摘要并发 semaphore。 | 默认值仍需按实际 API rate limit、模型 RPM/TPM 和部署并发调优。 | 后续如需更精细控制，可单独增加摘要并发配置、provider-aware throttling/backoff、成本预算和 cache。 |
| Final report token truncation 用字符数近似 token | `src/open_deep_research/deep_researcher.py`, `src/open_deep_research/utils.py` | `model_token_limit * 4` 截断 findings。 | 对非英文或不同 tokenizer 不稳定。 | 使用 provider tokenizer 或 LangChain token counter。 |
| Full benchmark cost high | `README.md`, `tests/run_evaluate.py`, `src/open_deep_research/evaluation/gates.py` | README 警告 100 examples 可能花费约 `$20-$100`；Phase 0 已增加 `ODR_EVAL_MODE=full` + `RUN_FULL_EVAL=1` 双门禁。 | 绕过项目入口直接调用第三方 SDK 仍可能产生成本。 | 保持 full/live 命令显式，不在普通 pytest 中开启费用开关。 |
| Callback 搜索/Researcher 计数不总是完整 | `src/open_deep_research/evaluation/telemetry.py` | StructuredTool 搜索按精确白名单计数；native provider 搜索不能可靠观测，因此 `search_calls_complete=false`；`researcher_runs` 暂为 `null`。 | 把不完整数据当精确值会误导消融对比。 | 阶段 7 再结合 provider metadata/稳定子图标识补全，当前保持保守的 null/incomplete 语义。 |

## 5) 脆弱/高变更区域

| 区域 | 脆弱原因 | 变更信号 | 安全修改策略 |
|------|-------------|--------------|----------------------|
| `feature_list.json`, `progress.md`, `session-handoff.md` | 是本地会话恢复和 Definition of Done 的状态源。 | git recent 90 days high-churn output 中每个 2 次。 | 修改功能时同步更新，避免只改代码不改状态。 |
| `src/open_deep_research/deep_researcher.py` | 主图、子图、并发、错误处理都集中在一个文件。 | scan/git output 显示近期修改；文件是主入口。 | 小步改动，先补针对节点/工具路径的验证。 |
| `src/open_deep_research/utils.py` | 搜索、MCP、token、API key、token limit 逻辑集中。 | TODO 和多 integration 集中。 | 按 provider 增加回归验证，避免跨集成破坏。 |
| `src/legacy/` | 已有 deprecation warnings，仍被 legacy tests 引用。 | pytest collect warnings；作者确认只保证主实现。 | 不把 legacy 作为保证范围；修改主实现时避免被 legacy 阻塞。 |

## 6) 作者已决策规则

1. `.env` 是正式本地环境配置文件；不再要求维护或引用单独环境模板文件，且不得提交 `.env`、API key 或私有 MCP 配置。
2. 后续文档、命令示例和操作路径统一使用 conda/pip 与 LangGraph 原生命令，不再推荐 uv。
3. `init.sh` 的修复路径由后续执行者在最小化改动、最低修复工作量、不影响其他任务封装性的前提下自行决定。
4. 后续只保证主实现 `src/open_deep_research/`；`src/legacy/` 仅作为历史参考。
5. 多 MCP server 支持是明确后续 feature，应单独建功能项并更新状态文件。

## 7) 证据

- `init.sh`
- `pyproject.toml`
- `README.md`
- `AGENTS.md`
- `.github/dependabot.yml`
- `.gitignore`
- `src/open_deep_research/deep_researcher.py`
- `src/open_deep_research/utils.py`
- `src/open_deep_research/configuration.py`
- `src/security/auth.py`
- `src/legacy/graph.py`
- `src/legacy/multi_agent.py`
- `src/legacy/utils.py`
- `progress.md`
- `session-handoff.md`
