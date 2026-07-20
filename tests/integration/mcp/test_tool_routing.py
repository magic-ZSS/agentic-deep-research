from __future__ import annotations

import asyncio

from open_deep_research.deep_researcher import execute_tool_safely
from open_deep_research.mcp_servers.tools import KNOWLEDGE_MCP_TOOLS
from open_deep_research.mcp.tools import FILESYSTEM_MCP_TOOLS
from open_deep_research.utils import get_all_tools


def test_unknown_tool_is_controlled_and_known_tools_remain_available() -> None:
    assert asyncio.run(execute_tool_safely(None, {}, {})) == "Error executing tool [UnknownTool]: tool is not bound"
    assert len(KNOWLEDGE_MCP_TOOLS) == 7
    assert len(FILESYSTEM_MCP_TOOLS) == 4


def test_flags_off_preserve_baseline_and_flags_on_have_no_forbidden_names(monkeypatch) -> None:
    async def no_search(_):
        return []
    monkeypatch.setattr("open_deep_research.utils.get_search_tool", no_search)
    baseline = asyncio.run(get_all_tools({"configurable": {}}))
    enabled = asyncio.run(get_all_tools({"configurable": {"enable_knowledge_mcp": True, "enable_filesystem_mcp": True, "knowledge_tenant_id": "t", "knowledge_project_id": "p"}}))
    baseline_names = {item.name for item in baseline}
    enabled_names = {item.name for item in enabled}
    assert baseline_names == {"ResearchComplete", "think_tool"}
    assert baseline_names < enabled_names
    assert not {"hard_delete", "force_promote", "force_memory_write", "memory_search", "write_file", "edit_file", "move_file", "delete"} & enabled_names

