"""Management-only knowledge inspection contracts.

These functions are intentionally plain async callables. Phase 2 does not register
them with the production Researcher tool registry.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from open_deep_research.knowledge.models import (
    KnowledgeAccessContext,
    KnowledgeScope,
)
from open_deep_research.knowledge.retrieval.models import (
    KnowledgeReadRequest,
    KnowledgeReadResult,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
)
from open_deep_research.knowledge.retrieval.protocols import KnowledgeRetriever
from open_deep_research.knowledge.retrieval.repository_retriever import (
    CandidateInspectionRequiredError,
)


KnowledgeArtifact = Annotated[
    KnowledgeSearchResult | KnowledgeReadResult, Field(union_mode="left_to_right")
]


class KnowledgeToolResult(BaseModel):
    """Compact human-readable content plus a typed machine artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str
    artifact: KnowledgeArtifact


class KnowledgeInspectionService:
    """Capability-gated service for CLI and trusted internal inspection only."""

    def __init__(
        self,
        retriever: KnowledgeRetriever,
        *,
        allow_candidate_inspection: bool = False,
    ) -> None:
        self._retriever = retriever
        self._allow_candidate_inspection = allow_candidate_inspection

    def _authorize_candidate(self, requested: bool) -> None:
        if requested and not self._allow_candidate_inspection:
            raise CandidateInspectionRequiredError(
                "candidate knowledge requires a trusted inspection capability"
            )

    async def search(
        self,
        request: KnowledgeSearchRequest,
        *,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
    ) -> KnowledgeToolResult:
        self._authorize_candidate(request.include_candidate)
        result = await self._retriever.search(
            request, access=access, scope=scope
        )
        if not result.hits:
            content = "No matching imported knowledge was found."
        else:
            lines = [
                (
                    f"[{hit.rank}] {hit.document_title} | {hit.chunk_id} | "
                    f"score={hit.score:.6f} | "
                    f"{'inspection-only' if hit.inspection_only else 'citable'}"
                )
                for hit in result.hits
            ]
            content = "\n".join(lines)
        return KnowledgeToolResult(content=content, artifact=result)

    async def read(
        self,
        request: KnowledgeReadRequest,
        *,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
    ) -> KnowledgeToolResult:
        self._authorize_candidate(request.include_candidate)
        result = await self._retriever.read(request, access=access, scope=scope)
        hit = result.hit
        content = (
            f"{hit.document_title} | {hit.chunk_id} | "
            f"{'inspection-only' if hit.inspection_only else 'citable'}\n{hit.text}"
        )
        return KnowledgeToolResult(content=content, artifact=result)


async def knowledge_search(
    service: KnowledgeInspectionService,
    request: KnowledgeSearchRequest,
    *,
    access: KnowledgeAccessContext,
    scope: KnowledgeScope,
) -> KnowledgeToolResult:
    """Direct-call inspection contract; deliberately not a production tool object."""
    return await service.search(request, access=access, scope=scope)


async def knowledge_read(
    service: KnowledgeInspectionService,
    request: KnowledgeReadRequest,
    *,
    access: KnowledgeAccessContext,
    scope: KnowledgeScope,
) -> KnowledgeToolResult:
    """Read an imported snapshot by stable ID through the trusted service."""
    return await service.read(request, access=access, scope=scope)


__all__ = [
    "KnowledgeInspectionService",
    "KnowledgeToolResult",
    "knowledge_read",
    "knowledge_search",
]
