"""Deterministic allow-list registry for model-visible MCP tools."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.tools import BaseTool

from open_deep_research.mcp.config import FORBIDDEN_TOOL_NAMES, MCPServerConfig
from open_deep_research.mcp.errors import MCPConfigurationError


@dataclass(frozen=True, slots=True)
class RegisteredMCPTool:
    server_name: str
    tool: BaseTool


class MCPToolRegistry:
    def __init__(self, existing_tool_names: set[str]) -> None:
        self._names = set(existing_tool_names)
        self._registered: list[RegisteredMCPTool] = []

    def add_server_tools(
        self,
        server_name: str,
        server: MCPServerConfig,
        tools: list[BaseTool],
    ) -> None:
        allowed = set(server.allowed_tools)
        for tool in tools:
            if tool.name not in allowed:
                continue
            if tool.name in FORBIDDEN_TOOL_NAMES:
                raise MCPConfigurationError("forbidden MCP tool reached registry")
            if tool.name in self._names:
                raise MCPConfigurationError(f"MCP tool name collision: {tool.name}")
            self._names.add(tool.name)
            self._registered.append(RegisteredMCPTool(server_name, tool))

    @property
    def tools(self) -> list[BaseTool]:
        return [item.tool for item in self._registered]

