"""Model-safe filesystem wrappers requiring an injected governed service."""

from __future__ import annotations

import json

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from open_deep_research.knowledge.retrieval.runtime import process_context
from open_deep_research.mcp.filesystem_adapter import GovernedFilesystemService


def _service(config: RunnableConfig | None) -> tuple[GovernedFilesystemService, str, str]:
    context = process_context(config)
    service = context.get("mcp_filesystem_service")
    if not isinstance(service, GovernedFilesystemService):
        raise PermissionError("trusted filesystem MCP service is unavailable")
    request_id = str(context.get("request_id") or context.get("run_id") or "mcp")
    return service, request_id, "filesystem-mcp-agent"


def _json(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, tuple):
        value = [item.model_dump(mode="json") for item in value]
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@tool("fs_read_text")
def fs_read_text(root_id: str, relative_locator: str, *, config: RunnableConfig | None = None) -> str:
    """Read UTF-8 text under an authorized read-only root."""
    service, request_id, actor = _service(config)
    return _json(service.read_text(root_id=root_id, relative_locator=relative_locator, request_id=request_id, actor=actor))


@tool("fs_list")
def fs_list(root_id: str, relative_locator: str = ".", *, config: RunnableConfig | None = None) -> str:
    """List public locators under an authorized read-only root."""
    service, request_id, actor = _service(config)
    return _json(service.list_directory(root_id=root_id, relative_locator=relative_locator, request_id=request_id, actor=actor))


@tool("fs_search")
def fs_search(root_id: str, relative_locator: str, pattern: str, *, config: RunnableConfig | None = None) -> str:
    """Search names under an authorized read-only root."""
    service, request_id, actor = _service(config)
    return _json(service.search(root_id=root_id, relative_locator=relative_locator, pattern=pattern, request_id=request_id, actor=actor))


@tool("fs_get_info")
def fs_get_info(root_id: str, relative_locator: str, *, config: RunnableConfig | None = None) -> str:
    """Get public metadata under an authorized read-only root."""
    service, request_id, actor = _service(config)
    return _json(service.get_info(root_id=root_id, relative_locator=relative_locator, request_id=request_id, actor=actor))


FILESYSTEM_MCP_TOOLS = (fs_read_text, fs_list, fs_search, fs_get_info)

