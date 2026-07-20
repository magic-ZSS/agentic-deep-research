from __future__ import annotations

import asyncio

import pytest
from langchain_core.tools import tool
from pydantic import ValidationError

from open_deep_research import utils
from open_deep_research.configuration import Configuration, MCPConfig
from open_deep_research.mcp import client as client_module
from open_deep_research.mcp.client import MCPToolLoadResult, load_external_mcp_tools
from open_deep_research.mcp.config import (
    MCPServerConfig,
    MCPTransport,
)


@tool("healthy")
def healthy() -> str:
    """Return a deterministic result."""
    return "ok"


def test_legacy_http_config_maps_without_behavioral_default_change() -> None:
    config = MCPConfig(url="https://mcp.example", tools=("healthy",))
    server = config.normalized_servers()["legacy_http"]
    assert server.url == "https://mcp.example/mcp"
    assert server.transport is MCPTransport.STREAMABLE_HTTP
    defaults = Configuration()
    assert not defaults.enable_filesystem_mcp
    assert not defaults.enable_knowledge_mcp


def test_forbidden_knowledge_and_raw_filesystem_tools_fail_validation(tmp_path) -> None:
    with pytest.raises(ValidationError):
        MCPConfig(url="https://mcp.example", tools=("memory_search",))
    with pytest.raises(ValidationError):
        MCPConfig(url="https://mcp.example", tools=("write_file",))
    with pytest.raises(ValidationError):
        MCPServerConfig(
            transport="stdio",
            kind="filesystem_read_only",
            command="node",
            args=(),
            allowed_tools=("write_file",),
            allowed_roots=(),
        )


def test_multi_server_partial_failure_is_explicit_and_isolated(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, connections):
            self.name = next(iter(connections))

        async def get_tools(self, server_name=None):
            if self.name == "broken":
                raise TimeoutError("offline")
            return [healthy]

    monkeypatch.setattr(client_module, "MultiServerMCPClient", FakeClient)
    servers = {
        "broken": MCPServerConfig(
            transport="streamable_http",
            url="https://broken.example/mcp",
            allowed_tools=("healthy",),
        ),
        "good": MCPServerConfig(
            transport="streamable_http",
            url="https://good.example/mcp",
            allowed_tools=("healthy",),
        ),
    }
    result = asyncio.run(
        load_external_mcp_tools(servers, existing_tool_names=set())
    )
    assert [tool.name for tool in result.tools] == ["healthy"]
    assert [(item.server_name, item.status) for item in result.diagnostics] == [
        ("broken", "failed"),
        ("good", "loaded"),
    ]


def test_registry_rejects_name_collision(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, connections):
            pass

        async def get_tools(self, server_name=None):
            return [healthy]

    monkeypatch.setattr(client_module, "MultiServerMCPClient", FakeClient)
    server = MCPServerConfig(
        transport="streamable_http",
        url="https://good.example/mcp",
        allowed_tools=("healthy",),
    )
    result = asyncio.run(
        load_external_mcp_tools({"good": server}, existing_tool_names={"healthy"})
    )
    assert result.tools == ()
    assert result.diagnostics[0].status == "failed"


def test_legacy_runtime_config_reaches_new_loader(monkeypatch) -> None:
    captured = {}

    async def fake_loader(servers, **kwargs):
        captured.update(servers)
        return MCPToolLoadResult((healthy,), ())

    monkeypatch.setattr(utils, "load_external_mcp_tools", fake_loader)
    tools = asyncio.run(
        utils.load_mcp_tools(
            {"configurable": {"mcp_config": {"url": "https://mcp.example", "tools": ["healthy"], "auth_required": False}}},
            set(),
        )
    )
    assert [item.name for item in tools] == ["healthy"]
    assert captured["legacy_http"].url == "https://mcp.example/mcp"
