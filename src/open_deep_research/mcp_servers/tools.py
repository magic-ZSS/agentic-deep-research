"""LangChain wrappers over the same scope-bound Knowledge MCP service."""

from __future__ import annotations

import json

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from open_deep_research.knowledge.retrieval.runtime import (
    get_governed_runtime,
    process_context,
)
from open_deep_research.mcp.staging import ExclusiveCreateStaging
from open_deep_research.mcp_servers.schemas import KnowledgeMCPContext
from open_deep_research.mcp_servers.services import KnowledgeMCPService


def _service(config: RunnableConfig | None) -> KnowledgeMCPService:
    runtime = get_governed_runtime(config)
    context = process_context(config)
    staging = context.get("mcp_staging")
    if staging is not None and not isinstance(staging, ExclusiveCreateStaging):
        raise PermissionError("invalid trusted MCP staging service")
    return KnowledgeMCPService(
        retriever=runtime.retriever,
        repository=runtime.repository,
        context=KnowledgeMCPContext(
            access=runtime.access,
            scope=runtime.scope,
            actor="knowledge-mcp-agent",
            run_id=runtime.run_id,
        ),
        staging=staging,
    )


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@tool("kb_search")
async def kb_search(query: str, limit: int = 10, *, config: RunnableConfig | None = None) -> str:
    """Search authorized local knowledge using the canonical retriever."""
    return _json(await _service(config).kb_search(query, limit=limit))


@tool("kb_read")
async def kb_read(stable_id: str, *, config: RunnableConfig | None = None) -> str:
    """Read an authorized evidence or chunk by stable ID."""
    return _json(await _service(config).kb_read(stable_id))


@tool("kb_get_source")
async def kb_get_source(source_id: str, *, config: RunnableConfig | None = None) -> str:
    """Return an authorized public Source view."""
    return _json(await _service(config).kb_get_source(source_id))


@tool("kb_search_past_queries")
async def kb_search_past_queries(query: str, limit: int = 10, *, config: RunnableConfig | None = None) -> str:
    """Search authorized past-query snapshots."""
    return _json(await _service(config).kb_search_past_queries(query, limit=limit))


@tool("kb_propose_ingest")
async def kb_propose_ingest(artifact_id: str, reason: str, *, config: RunnableConfig | None = None) -> str:
    """Create a pending ingest proposal for a same-run staging artifact."""
    return _json(await _service(config).kb_propose_ingest(artifact_id, reason=reason))


@tool("kb_propose_stale")
async def kb_propose_stale(version_id: str, reason: str, *, config: RunnableConfig | None = None) -> str:
    """Create a pending stale proposal; never changes knowledge directly."""
    return _json(await _service(config).kb_propose_stale(version_id, reason=reason))


@tool("kb_propose_quarantine")
async def kb_propose_quarantine(version_id: str, reason: str, *, config: RunnableConfig | None = None) -> str:
    """Create a pending quarantine proposal; never changes knowledge directly."""
    return _json(await _service(config).kb_propose_quarantine(version_id, reason=reason))


KNOWLEDGE_MCP_TOOLS = (
    kb_search,
    kb_read,
    kb_get_source,
    kb_search_past_queries,
    kb_propose_ingest,
    kb_propose_stale,
    kb_propose_quarantine,
)

