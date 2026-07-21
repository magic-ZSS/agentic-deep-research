"""FastMCP adapter for a pre-authorized KnowledgeMCPService."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from open_deep_research.mcp_servers.services import KnowledgeMCPService


def create_knowledge_server(service: KnowledgeMCPService) -> FastMCP:
    """Build a server without exposing scope/identity as tool arguments."""
    server = FastMCP("open-deep-research-knowledge")
    read_annotations = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, openWorldHint=False
    )
    proposal_annotations = ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, openWorldHint=False
    )

    @server.tool(annotations=read_annotations)
    async def kb_search(query: str, limit: int = 10) -> dict:
        return await service.kb_search(query, limit=limit)

    @server.tool(annotations=read_annotations)
    async def kb_read(stable_id: str) -> dict:
        return await service.kb_read(stable_id)

    @server.tool(annotations=read_annotations)
    async def kb_get_source(source_id: str) -> dict:
        return await service.kb_get_source(source_id)

    @server.tool(annotations=read_annotations)
    async def kb_search_past_queries(query: str, limit: int = 10) -> dict:
        return await service.kb_search_past_queries(query, limit=limit)

    if service.memory_recall is not None and service.context.runtime_identity is not None:
        @server.tool(annotations=read_annotations)
        async def memory_search(query: str, limit: int = 5) -> dict:
            """Search authorized active memories without accepting a namespace."""
            return await service.memory_search(query, limit=limit)

    @server.tool(annotations=proposal_annotations)
    async def kb_propose_ingest(artifact_id: str, reason: str) -> dict:
        return await service.kb_propose_ingest(artifact_id, reason=reason)

    @server.tool(annotations=proposal_annotations)
    async def kb_propose_stale(version_id: str, reason: str) -> dict:
        return await service.kb_propose_stale(version_id, reason=reason)

    @server.tool(annotations=proposal_annotations)
    async def kb_propose_quarantine(version_id: str, reason: str) -> dict:
        return await service.kb_propose_quarantine(version_id, reason=reason)

    return server


__all__ = ["create_knowledge_server"]
