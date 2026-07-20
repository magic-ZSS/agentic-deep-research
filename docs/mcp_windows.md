# Windows MCP 安全部署说明

Phase 4 的两个新开关 `enable_filesystem_mcp` 和 `enable_knowledge_mcp` 默认均为 `false`。示例配置位于 `config/examples/mcp.windows.example.json`，只含环境变量占位符，不含个人路径或 secret。

Filesystem Server 固定为 `@modelcontextprotocol/server-filesystem@2026.1.14`。Windows/conda 环境使用 `cmd /c npx --offline @modelcontextprotocol/server-filesystem@2026.1.14 <temporary-root>`；首次获取包仍需用户明确批准网络/安装。禁止 `latest` 和 `npx -y`。

安全边界如下：

- 上游 server 进程只接收只读 roots，并只允许 read/list/search/info 工具；项目 registry 不暴露上游 `write_file`、edit、move、delete。
- import staging 使用独立 root 和项目自有 exclusive-create wrapper；已存在目标立即失败，suffix、media type、单文件和每 run quota 均在写前检查。
- `readOnlyHint` 等 annotations 只用于客户端提示。真正授权由 trusted runtime context、`AllowedRootsPolicy`、工具白名单和 Windows ACL/独立进程共同完成。
- Roots 为空、无效或运行中身份变化时 fail closed，不回退到 cwd。绝对路径、drive/UNC/WSL、`..`、null byte、symlink/junction 绕过均拒绝。
- 模型、日志和审计只看到 `root://<public-alias>/<relative-path>`；不返回 Allowed Root、blob ref 或数据库路径。
- Knowledge MCP 的 tenant/project/user 来自可信启动/auth context，不是工具参数。`kb_propose_*` 只创建 pending proposal；不提供 hard delete、force promotion 或 Memory stub。

生产部署应分别为只读 server 进程和 staging 进程设置最小 Windows ACL。ACL 是第二道防线，不能替代应用层 policy。关闭两个开关即可回到 Phase 3/旧 MCP 路径；已存在 proposal 和审计保留。

