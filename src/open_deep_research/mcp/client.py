"""Per-server MCP loading with failure isolation and explicit diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from open_deep_research.mcp.config import MCPServerConfig, MCPServerKind
from open_deep_research.mcp.tool_registry import MCPToolRegistry


@dataclass(frozen=True, slots=True)
class MCPServerDiagnostic:
    server_name: str
    status: str
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class MCPToolLoadResult:
    tools: tuple[BaseTool, ...]
    diagnostics: tuple[MCPServerDiagnostic, ...]


async def load_external_mcp_tools(
    servers: dict[str, MCPServerConfig],
    *,
    existing_tool_names: set[str],
    headers_by_server: dict[str, dict[str, str] | None] | None = None,
) -> MCPToolLoadResult:
    """Load servers independently so one outage cannot erase healthy tools."""
    registry = MCPToolRegistry(existing_tool_names)
    diagnostics: list[MCPServerDiagnostic] = []
    for name, server in sorted(servers.items()):
        if not server.enabled or server.kind is not MCPServerKind.EXTERNAL:
            continue
        connection = server.adapter_connection(
            headers=(headers_by_server or {}).get(name)
        )
        try:
            client = MultiServerMCPClient({name: connection})
            available = await client.get_tools(server_name=name)
            registry.add_server_tools(name, server, available)
            diagnostics.append(MCPServerDiagnostic(name, "loaded"))
        except Exception as exc:
            diagnostics.append(
                MCPServerDiagnostic(name, "failed", type(exc).__name__)
            )
    return MCPToolLoadResult(tuple(registry.tools), tuple(diagnostics))
