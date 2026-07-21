"""Resolve canonical or same-run transient evidence with trusted scope."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import ConfigDict

from open_deep_research.evidence.models import Evidence, is_evidence_citable
from open_deep_research.evidence.run_store import (
    RunEvidenceContext,
    RunEvidenceNotFoundError,
    RunEvidenceStore,
    RunEvidenceValidationStatus,
)
from open_deep_research.knowledge.models import (
    Chunk,
    ChunkLocatorType,
    Document,
    DocumentVersion,
    DomainModel,
    KnowledgeAccessContext,
    KnowledgeScope,
    Source,
    SourceKind,
)
from open_deep_research.knowledge.repositories import (
    KnowledgeEvidenceRepository,
    RepositoryNotFoundError,
)


class EvidenceOrigin(StrEnum):
    """Durability boundary of a resolved evidence chain."""

    CANONICAL = "canonical"
    SAME_RUN_TRANSIENT = "same_run_transient"


class ResolvedEvidence(DomainModel):
    """Complete evidence chain used transiently by validators."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    origin: EvidenceOrigin
    source: Source
    document: Document
    version: DocumentVersion
    chunk: Chunk
    evidence: Evidence
    eligible: bool
    eligibility_reason: str

    @property
    def citation_key(self) -> tuple[str, str]:
        """Return the registry identity."""
        return self.source.source_id, self.version.version_id

    def public_uri(self) -> str | None:
        """Return only a safe public URI or stable non-path alias."""
        view = self.source.public_view()
        if view.public_display_uri:
            return view.public_display_uri
        if self.source.kind is SourceKind.WEB and self.source.canonical_uri:
            return self.source.canonical_uri
        return f"source://{self.source.source_id}"

    def locator(self) -> str:
        """Render a public locator without storage references."""
        chunk = self.chunk
        if chunk.locator_type is ChunkLocatorType.PAGE:
            end = chunk.page_end or chunk.page_start
            return f"page:{chunk.page_start}" if end == chunk.page_start else f"pages:{chunk.page_start}-{end}"
        if chunk.locator_type is ChunkLocatorType.HEADING:
            return "heading:" + " > ".join(chunk.heading_path)
        if chunk.locator_type is ChunkLocatorType.ANCHOR:
            return f"anchor:{chunk.anchor}"
        return f"chunk:{chunk.ordinal}"


class EvidenceResolver:
    """Fail closed across canonical and run-scoped evidence stores."""

    def __init__(
        self,
        *,
        repository: KnowledgeEvidenceRepository,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        run_store: RunEvidenceStore | None = None,
        run_id: str | None = None,
    ) -> None:
        """Bind resolution to one trusted scope and optional run."""
        self.repository = repository
        self.access = access
        self.scope = scope
        self.run_store = run_store
        self.run_id = run_id

    async def resolve(
        self, evidence_id: str, *, as_of: datetime | None = None
    ) -> ResolvedEvidence:
        """Resolve a visible evidence ID; never fall across scope or run."""
        try:
            evidence = await self.repository.get_evidence(
                self.access, self.scope, evidence_id
            )
        except RepositoryNotFoundError:
            return await self._resolve_transient(evidence_id)
        chunk = await self.repository.get_chunk(
            self.access, self.scope, evidence.chunk_id
        )
        version = await self.repository.get_version(
            self.access, self.scope, chunk.version_id
        )
        document = await self.repository.get_document(
            self.access, self.scope, version.document_id
        )
        source = await self.repository.get_source(
            self.access, self.scope, document.source_id
        )
        eligible = is_evidence_citable(
            evidence, chunk, version, document, source, at=as_of
        )
        return ResolvedEvidence(
            origin=EvidenceOrigin.CANONICAL,
            source=source,
            document=document,
            version=version,
            chunk=chunk,
            evidence=evidence,
            eligible=eligible,
            eligibility_reason="canonical_citable" if eligible else "canonical_not_citable",
        )

    async def _resolve_transient(self, evidence_id: str) -> ResolvedEvidence:
        if self.run_store is None or not self.run_id:
            raise RepositoryNotFoundError(evidence_id)
        context = RunEvidenceContext(
            scope_id=self.scope.scope_id, run_id=self.run_id
        )
        try:
            bundle = await self.run_store.resolve(context, evidence_id)
        except RunEvidenceNotFoundError as exc:
            raise RepositoryNotFoundError(evidence_id) from exc
        eligible = (
            bundle.validation_status
            is RunEvidenceValidationStatus.VALIDATED_FOR_RUN
        )
        return ResolvedEvidence(
            origin=EvidenceOrigin.SAME_RUN_TRANSIENT,
            source=bundle.source,
            document=bundle.document,
            version=bundle.version,
            chunk=bundle.chunk,
            evidence=bundle.evidence,
            eligible=eligible,
            eligibility_reason=(
                "same_run_validated" if eligible else "same_run_not_validated"
            ),
        )

    async def evidence_for_source(
        self, source_id: str, *, as_of: datetime | None = None
    ) -> list[ResolvedEvidence]:
        """List evidence for a source without crossing the trusted run."""
        resolved: list[ResolvedEvidence] = []
        for evidence in await self.repository.list_evidence_for_source(
            self.access, self.scope, source_id
        ):
            resolved.append(await self.resolve(evidence.evidence_id, as_of=as_of))
        if self.run_store is not None and self.run_id:
            context = RunEvidenceContext(
                scope_id=self.scope.scope_id, run_id=self.run_id
            )
            for bundle in await self.run_store.list(context):
                if bundle.source.source_id == source_id:
                    resolved.append(await self.resolve(bundle.evidence_id, as_of=as_of))
        return sorted(resolved, key=lambda item: item.evidence.evidence_id)


def repository_supports_resolution(repository: Any) -> bool:
    """Return whether an injected repository exposes the required contract."""
    return all(
        hasattr(repository, name)
        for name in (
            "get_evidence",
            "get_chunk",
            "get_version",
            "get_document",
            "get_source",
            "list_evidence_for_source",
        )
    )
