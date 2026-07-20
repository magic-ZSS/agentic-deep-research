"""Deterministic repository retrieval and aggregate projection adapters."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from open_deep_research.evidence.models import (
    Evidence,
    EvidenceValidationStatus,
    is_evidence_citable,
)
from open_deep_research.knowledge.models import (
    KnowledgeAccessContext,
    KnowledgeScope,
    VersionLifecycleStatus,
)
from open_deep_research.knowledge.retrieval.models import (
    ChunkLocatorView,
    EvidenceHit,
    KnowledgeReadRequest,
    KnowledgeReadResult,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
    RetrievalRecord,
)
from open_deep_research.knowledge.retrieval.protocols import (
    RetrievalCatalog,
    RetrievalRepositoryProjection,
)


_WORD = re.compile(r"\w+", flags=re.UNICODE)


class RetrievalNotFoundError(LookupError):
    """Requested knowledge is absent or not visible under the requested policy."""


class CandidateInspectionRequiredError(PermissionError):
    """Candidate content was requested without an inspection capability."""


def _evidence_sort_key(evidence: Evidence) -> tuple[int, str]:
    status_order = {
        EvidenceValidationStatus.VALIDATED: 0,
        EvidenceValidationStatus.PENDING: 1,
        EvidenceValidationStatus.REJECTED: 2,
    }
    return status_order[evidence.validation_status], evidence.evidence_id


class RepositoryRetrievalCatalog:
    """Build aggregate records from public, scope-aware repository reads."""

    def __init__(self, repository: RetrievalRepositoryProjection) -> None:
        self._repository = repository

    async def list_records(
        self, access: KnowledgeAccessContext, scope: KnowledgeScope
    ) -> Sequence[RetrievalRecord]:
        sources = await self._repository.list_sources(access, scope)
        documents = await self._repository.list_documents(access, scope)
        sources_by_id = {source.source_id: source for source in sources}
        records: list[RetrievalRecord] = []
        for document in sorted(documents, key=lambda item: item.document_id):
            source = sources_by_id.get(document.source_id)
            if source is None:
                continue
            versions = await self._repository.list_versions(
                access, scope, document.document_id
            )
            for version in sorted(versions, key=lambda item: item.version_id):
                chunks = await self._repository.list_chunks_for_version(
                    access, scope, version.version_id
                )
                for chunk in sorted(chunks, key=lambda item: item.chunk_id):
                    evidence_items = await self._repository.list_evidence_for_chunk(
                        access, scope, chunk.chunk_id
                    )
                    evidence = (
                        sorted(evidence_items, key=_evidence_sort_key)[0]
                        if evidence_items
                        else None
                    )
                    records.append(
                        RetrievalRecord(
                            source=source,
                            document=document,
                            version=version,
                            chunk=chunk,
                            evidence=evidence,
                        )
                    )
        return tuple(sorted(records, key=lambda item: item.chunk.chunk_id))

    async def get_record(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        stable_id: str,
    ) -> RetrievalRecord:
        if stable_id.startswith("evd_"):
            evidence = await self._repository.get_evidence(
                access, scope, stable_id
            )
            chunk = await self._repository.get_chunk(
                access, scope, evidence.chunk_id
            )
        elif stable_id.startswith("chk_"):
            chunk = await self._repository.get_chunk(access, scope, stable_id)
            evidence_items = await self._repository.list_evidence_for_chunk(
                access, scope, chunk.chunk_id
            )
            evidence = (
                sorted(evidence_items, key=_evidence_sort_key)[0]
                if evidence_items
                else None
            )
        else:
            raise RetrievalNotFoundError("unsupported stable knowledge ID")
        version = await self._repository.get_version(
            access, scope, chunk.version_id
        )
        document = await self._repository.get_document(
            access, scope, version.document_id
        )
        source = await self._repository.get_source(
            access, scope, document.source_id
        )
        return RetrievalRecord(
            source=source,
            document=document,
            version=version,
            chunk=chunk,
            evidence=evidence,
        )


def record_is_eligible(
    record: RetrievalRecord,
    request: KnowledgeSearchRequest | KnowledgeReadRequest,
    *,
    scope_id: str | None = None,
) -> bool:
    """Apply lifecycle and temporal policy without trusting a retrieval backend."""
    if scope_id is not None and record.chunk.scope_id != scope_id:
        return False
    allowed_statuses = {VersionLifecycleStatus.ACTIVE}
    if request.include_candidate:
        allowed_statuses.add(VersionLifecycleStatus.CANDIDATE)
    if record.version.lifecycle_status not in allowed_statuses:
        return False

    if isinstance(request, KnowledgeSearchRequest):
        filters = request.filters
        if filters.source_ids and record.source.source_id not in filters.source_ids:
            return False
        if filters.document_ids and record.document.document_id not in filters.document_ids:
            return False
        if filters.version_ids and record.version.version_id not in filters.version_ids:
            return False
        if filters.source_kinds and record.source.kind not in filters.source_kinds:
            return False
        if filters.media_types and record.document.media_type not in filters.media_types:
            return False
        if (
            filters.lifecycle_statuses
            and record.version.lifecycle_status not in filters.lifecycle_statuses
        ):
            return False
        if filters.validation_statuses and (
            record.evidence is None
            or record.evidence.validation_status not in filters.validation_statuses
        ):
            return False

    instant = request.as_of
    if instant is not None:
        instant = instant.astimezone(UTC)
        if record.version.retrieved_at > instant:
            return False
        if record.version.published_at and record.version.published_at > instant:
            return False
        if record.version.valid_from and instant < record.version.valid_from:
            return False
        if record.version.valid_to and instant > record.version.valid_to:
            return False
    return True


def eligible_records(
    records: Iterable[RetrievalRecord],
    request: KnowledgeSearchRequest | KnowledgeReadRequest,
    *,
    scope_id: str | None = None,
) -> tuple[RetrievalRecord, ...]:
    return tuple(
        sorted(
            (
                record
                for record in records
                if record_is_eligible(record, request, scope_id=scope_id)
            ),
            key=lambda item: item.chunk.chunk_id,
        )
    )


def lexical_score(query: str, record: RetrievalRecord) -> float:
    """Return a deterministic local relevance score without model calls."""
    query_text = query.casefold()
    locator_text = " ".join(record.chunk.heading_path)
    haystack = "\n".join(
        (
            record.source.display_name,
            record.document.title,
            locator_text,
            record.chunk.text,
        )
    ).casefold()
    query_tokens = Counter(_WORD.findall(query_text))
    text_tokens = Counter(_WORD.findall(haystack))
    if not query_tokens:
        return 0.0
    overlap = sum(
        min(count, text_tokens.get(token, 0)) for token, count in query_tokens.items()
    )
    score = overlap / sum(query_tokens.values())
    if query_text in haystack:
        score += 0.25
    return round(score, 12)


def record_to_hit(
    record: RetrievalRecord,
    *,
    score: float,
    rank: int,
    retrieval_method: str,
    contextual_summary: str | None = None,
    at: datetime | None = None,
) -> EvidenceHit:
    evidence = record.evidence
    citable = bool(
        evidence
        and is_evidence_citable(
            evidence,
            record.chunk,
            record.version,
            record.document,
            record.source,
            at=at,
        )
    )
    return EvidenceHit(
        evidence_id=evidence.evidence_id if evidence else None,
        chunk_id=record.chunk.chunk_id,
        version_id=record.version.version_id,
        document_id=record.document.document_id,
        source_id=record.source.source_id,
        scope_id=record.chunk.scope_id,
        source=record.source.public_view(),
        document_title=record.document.title,
        media_type=record.document.media_type,
        text=record.chunk.text,
        contextual_summary=contextual_summary,
        score=score,
        rank=rank,
        locator=ChunkLocatorView.from_chunk(record.chunk),
        content_sha256=record.version.content_sha256,
        published_at=record.version.published_at,
        retrieved_at=record.version.retrieved_at,
        lifecycle_status=record.version.lifecycle_status,
        validation_status=evidence.validation_status if evidence else None,
        retrieval_method=retrieval_method,
        citable=citable,
        inspection_only=not citable,
    )


class RepositoryKnowledgeRetriever:
    """Deterministic no-model retriever and safe fallback implementation."""

    backend_name = "repository-keyword"

    def __init__(self, catalog: RetrievalCatalog) -> None:
        self.catalog = catalog

    async def search(
        self,
        request: KnowledgeSearchRequest,
        *,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
    ) -> KnowledgeSearchResult:
        records = eligible_records(
            await self.catalog.list_records(access, scope),
            request,
            scope_id=scope.scope_id,
        )
        scored = [
            (lexical_score(request.query, record), record) for record in records
        ]
        scored = [item for item in scored if item[0] > 0]
        scored.sort(
            key=lambda item: (
                -item[0],
                item[1].chunk.chunk_id,
                item[1].evidence.evidence_id if item[1].evidence else "",
            )
        )
        selected = scored[: request.limit]
        hits = tuple(
            record_to_hit(
                record,
                score=score,
                rank=rank,
                retrieval_method=self.backend_name,
                at=request.as_of,
            )
            for rank, (score, record) in enumerate(selected, start=1)
        )
        return KnowledgeSearchResult(
            query=request.query,
            hits=hits,
            backend=self.backend_name,
            empty_reason=None if hits else "no_matching_knowledge",
        )

    async def read(
        self,
        request: KnowledgeReadRequest,
        *,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
    ) -> KnowledgeReadResult:
        record = await self.catalog.get_record(access, scope, request.stable_id)
        if not record_is_eligible(record, request, scope_id=scope.scope_id):
            raise RetrievalNotFoundError(
                "knowledge is unavailable for the requested lifecycle/as_of policy"
            )
        return KnowledgeReadResult(
            hit=record_to_hit(
                record,
                score=1.0,
                rank=1,
                retrieval_method="repository-read",
                at=request.as_of,
            )
        )
