"""Scope-bound service backing both local tools and the Knowledge MCP server."""

from __future__ import annotations

from datetime import datetime

from open_deep_research.knowledge.lifecycle.models import (
    LifecycleProposalAction,
    LifecycleTargetType,
)
from open_deep_research.knowledge.lifecycle.service import KnowledgeLifecycleService
from open_deep_research.knowledge.models import SourceKind
from open_deep_research.knowledge.repositories import KnowledgeEvidenceRepository
from open_deep_research.knowledge.retrieval.models import (
    KnowledgeReadRequest,
    KnowledgeSearchRequest,
    RetrievalFilters,
)
from open_deep_research.knowledge.retrieval.protocols import KnowledgeRetriever
from open_deep_research.mcp.staging import ExclusiveCreateStaging
from open_deep_research.mcp_servers.schemas import (
    KnowledgeMCPContext,
    KnowledgeProposalView,
)


class KnowledgeMCPService:
    """No SQL/path inputs: all reads traverse repositories under trusted scope."""

    def __init__(
        self,
        *,
        retriever: KnowledgeRetriever,
        repository: KnowledgeEvidenceRepository,
        context: KnowledgeMCPContext,
        staging: ExclusiveCreateStaging | None = None,
    ) -> None:
        self.retriever = retriever
        self.repository = repository
        self.context = context
        self.lifecycle = KnowledgeLifecycleService(repository)
        self.staging = staging

    async def kb_search(
        self,
        query: str,
        *,
        limit: int = 10,
        as_of: datetime | None = None,
    ) -> dict:
        result = await self.retriever.search(
            KnowledgeSearchRequest(query=query, limit=limit, as_of=as_of),
            access=self.context.access,
            scope=self.context.scope,
        )
        return result.model_dump(mode="json")

    async def kb_read(self, stable_id: str, *, as_of: datetime | None = None) -> dict:
        result = await self.retriever.read(
            KnowledgeReadRequest(stable_id=stable_id, as_of=as_of),
            access=self.context.access,
            scope=self.context.scope,
        )
        return result.model_dump(mode="json")

    async def kb_get_source(self, source_id: str) -> dict:
        source = await self.repository.get_source(
            self.context.access, self.context.scope, source_id
        )
        return source.public_view().model_dump(mode="json")

    async def kb_search_past_queries(self, query: str, *, limit: int = 10) -> dict:
        result = await self.retriever.search(
            KnowledgeSearchRequest(
                query=query,
                limit=limit,
                filters=RetrievalFilters(source_kinds=(SourceKind.PAST_QUERY,)),
            ),
            access=self.context.access,
            scope=self.context.scope,
        )
        return result.model_dump(mode="json")

    async def _propose(
        self,
        *,
        target_id: str,
        action: LifecycleProposalAction,
        target_type: LifecycleTargetType,
        reason: str,
    ) -> dict:
        proposal = await self.lifecycle.propose(
            self.context.access,
            self.context.scope,
            target_entity_type=target_type,
            target_id=target_id,
            action=action,
            reason=reason,
            proposed_by=self.context.actor,
            run_id=self.context.run_id,
            correlation_id=self.context.access.request_id,
        )
        return KnowledgeProposalView(
            proposal_id=proposal.proposal_id,
            action=proposal.action.value,
            target_id=proposal.target_id,
            status=proposal.status.value,
            request_id=self.context.access.request_id,
        ).model_dump(mode="json")

    async def kb_propose_ingest(self, artifact_id: str, *, reason: str) -> dict:
        if self.staging is None or self.context.run_id is None:
            raise PermissionError("ingest proposal requires trusted run staging")
        self.staging.resolve_artifact(run_id=self.context.run_id, artifact_id=artifact_id)
        return await self._propose(
            target_id=artifact_id,
            action=LifecycleProposalAction.PROPOSE_INGEST,
            target_type=LifecycleTargetType.STAGING_ARTIFACT,
            reason=reason,
        )

    async def kb_propose_stale(self, version_id: str, *, reason: str) -> dict:
        return await self._propose(
            target_id=version_id,
            action=LifecycleProposalAction.PROPOSE_STALE,
            target_type=LifecycleTargetType.DOCUMENT_VERSION,
            reason=reason,
        )

    async def kb_propose_quarantine(self, version_id: str, *, reason: str) -> dict:
        return await self._propose(
            target_id=version_id,
            action=LifecycleProposalAction.PROPOSE_QUARANTINE,
            target_type=LifecycleTargetType.DOCUMENT_VERSION,
            reason=reason,
        )

