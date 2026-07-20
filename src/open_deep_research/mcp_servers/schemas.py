"""Strict, public-only schemas for the Knowledge MCP boundary."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from open_deep_research.knowledge.models import KnowledgeAccessContext, KnowledgeScope


class MCPBoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class KnowledgeMCPContext(MCPBoundaryModel):
    """Trusted runtime context; never accepted as model tool arguments."""

    access: KnowledgeAccessContext
    scope: KnowledgeScope
    actor: str = Field(min_length=1)
    run_id: str | None = None


class KnowledgeProposalView(MCPBoundaryModel):
    proposal_id: str
    action: str
    target_id: str
    status: str
    request_id: str

