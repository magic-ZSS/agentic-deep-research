"""Async repository Protocols and fail-closed domain errors."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from open_deep_research.evidence.models import (
    AuditEvent,
    Evidence,
    EvidenceDirectness,
    EvidenceRelation,
    EvidenceValidationStatus,
    Requirement,
    RequirementStatus,
)
from open_deep_research.knowledge.ingestion.models import (
    ImportIndexStatus,
    ImportJob,
    ImportJobError,
    ImportJobStatus,
)
from open_deep_research.knowledge.lifecycle.models import (
    LifecycleProposal,
    LifecycleProposalStatus,
)
from open_deep_research.knowledge.models import (
    AuthorityClass,
    Chunk,
    ChunkInput,
    ContentBlob,
    Document,
    DocumentVersion,
    KnowledgeAccessContext,
    KnowledgeScope,
    Source,
    SourceKind,
    VersionLifecycleStatus,
)


class RepositoryError(RuntimeError):
    """Base class for stable repository failure semantics."""


class RepositoryNotFoundError(RepositoryError):
    """Entity does not exist in the authorized scope."""


class RepositoryConflictError(RepositoryError):
    """An immutable identity was reused with conflicting contents."""


class RepositoryAccessError(RepositoryError, PermissionError):
    """Trusted access context cannot operate on the requested scope."""


class InvalidTransitionError(RepositoryError):
    """Requested mutation violates a Phase-1 domain invariant."""


class CorruptSchemaError(RepositoryError):
    """Persistent schema is newer, missing, or internally inconsistent."""


def authorize_scope(
    access: KnowledgeAccessContext,
    scope: KnowledgeScope,
) -> None:
    """Fail closed on tenant/project/visibility/owner mismatch."""
    allowed = (
        access.trusted_tenant_id == scope.tenant_id
        and access.trusted_project_id == scope.project_id
        and scope.visibility in access.allowed_visibilities
    )
    if scope.visibility.value == "private":
        allowed = allowed and bool(
            scope.owner_user_id
            and access.trusted_user_id
            and scope.owner_user_id == access.trusted_user_id
        )
    if not allowed:
        raise RepositoryAccessError("knowledge scope is not authorized")


@runtime_checkable
class BlobRepository(Protocol):
    """Immutable original-byte storage isolated by KnowledgeScope."""

    async def put(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        content: bytes,
        media_type: str,
    ) -> ContentBlob: ...

    async def get(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        blob_id: str,
    ) -> bytes: ...

    async def verify(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        blob_id: str,
        expected_sha256: str,
    ) -> bool: ...


@runtime_checkable
class DocumentRepository(Protocol):
    """Source/document/version/chunk metadata boundary."""

    async def upsert_source(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        kind: SourceKind,
        display_name: str,
        canonical_uri: str | None = None,
        internal_storage_ref: str | None = None,
        public_display_uri: str | None = None,
        publisher: str | None = None,
        authority_class: AuthorityClass = AuthorityClass.UNKNOWN,
        correlation_id: str = "repository",
    ) -> Source: ...

    async def get_source(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        source_id: str,
        *,
        include_deleted: bool = False,
    ) -> Source: ...

    async def list_sources(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        kind: SourceKind | None = None,
        include_deleted: bool = False,
    ) -> list[Source]: ...

    async def upsert_document(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        source_id: str,
        logical_key: str,
        title: str,
        media_type: str,
        correlation_id: str = "repository",
    ) -> Document: ...

    async def get_document(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        document_id: str,
        *,
        include_deleted: bool = False,
    ) -> Document: ...

    async def list_documents(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        source_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[Document]: ...

    async def get_content_blob_metadata(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        blob_id: str,
    ) -> ContentBlob: ...

    async def list_content_blob_metadata(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
    ) -> list[ContentBlob]: ...

    async def add_version(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        document_id: str,
        blob: ContentBlob,
        retrieved_at: datetime,
        published_at: datetime | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        supersedes_version_id: str | None = None,
        metadata: dict | None = None,
        lifecycle_status: VersionLifecycleStatus = VersionLifecycleStatus.CANDIDATE,
        correlation_id: str = "repository",
    ) -> DocumentVersion: ...

    async def get_version(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        version_id: str,
        *,
        include_deleted: bool = False,
    ) -> DocumentVersion: ...

    async def list_versions(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        document_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[DocumentVersion]: ...

    async def list_versions_for_scope(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        include_deleted: bool = False,
    ) -> list[DocumentVersion]: ...

    async def find_by_content_hash(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        content_sha256: str,
        *,
        include_deleted: bool = False,
    ) -> list[DocumentVersion]: ...

    async def add_chunks(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        version_id: str,
        chunks: Sequence[ChunkInput],
        *,
        correlation_id: str = "repository",
    ) -> list[Chunk]: ...

    async def get_chunk(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        chunk_id: str,
        *,
        include_deleted: bool = False,
    ) -> Chunk: ...

    async def list_chunks_for_version(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        version_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Chunk]: ...

    async def soft_delete(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        entity_type: str,
        entity_id: str,
        actor_type: str,
        reason: str,
        correlation_id: str,
    ) -> AuditEvent: ...

    async def transition_version_lifecycle(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        version_id: str,
        *,
        expected_status: VersionLifecycleStatus,
        status: VersionLifecycleStatus,
        actor_type: str,
        reason: str,
        policy_version: str,
        rule_results: Sequence[str],
        run_id: str | None,
        proposal_id: str | None,
        correlation_id: str,
    ) -> DocumentVersion: ...


@runtime_checkable
class EvidenceRepository(Protocol):
    """Evidence metadata linked to existing chunks and requirements."""

    async def add_evidence(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        chunk_id: str,
        excerpt: str,
        confidence: float,
        retrieval_method: str,
        requirement_id: str | None = None,
        relation: EvidenceRelation = EvidenceRelation.SUPPORTS,
        directness: EvidenceDirectness = EvidenceDirectness.UNKNOWN,
        valid_at: datetime | None = None,
        validation_status: EvidenceValidationStatus = EvidenceValidationStatus.PENDING,
        correlation_id: str = "repository",
    ) -> Evidence: ...

    async def get_evidence(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        evidence_id: str,
        *,
        include_deleted: bool = False,
    ) -> Evidence: ...

    async def list_evidence_for_requirement(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        requirement_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Evidence]: ...

    async def list_evidence_for_chunk(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        chunk_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Evidence]: ...

    async def list_evidence_for_source(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        source_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Evidence]: ...

    async def transition_evidence_validation(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        evidence_id: str,
        *,
        expected_status: EvidenceValidationStatus,
        status: EvidenceValidationStatus,
        relation: EvidenceRelation,
        directness: EvidenceDirectness,
        confidence: float,
        valid_at: datetime | None,
        actor_type: str,
        reason: str,
        policy_version: str,
        rule_results: Sequence[str],
        run_id: str | None,
        proposal_id: str | None,
        correlation_id: str,
    ) -> Evidence: ...


@runtime_checkable
class LifecycleProposalRepository(Protocol):
    """Agent proposals and review decisions; never a hard-delete surface."""

    async def create_lifecycle_proposal(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        proposal: LifecycleProposal,
    ) -> LifecycleProposal: ...

    async def get_lifecycle_proposal(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        proposal_id: str,
    ) -> LifecycleProposal: ...

    async def list_lifecycle_proposals(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        status: LifecycleProposalStatus | None = None,
        run_id: str | None = None,
    ) -> list[LifecycleProposal]: ...

    async def transition_lifecycle_proposal(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        proposal_id: str,
        *,
        expected_status: LifecycleProposalStatus,
        status: LifecycleProposalStatus,
        actor_type: str,
        reason: str,
        policy_version: str,
        rule_results: Sequence[str],
        correlation_id: str,
    ) -> LifecycleProposal: ...


@runtime_checkable
class RequirementRepository(Protocol):
    """Requirement persistence without extraction/completion policy."""

    async def add_requirement(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        text: str,
        run_id: str | None = None,
        template_id: str | None = None,
        acceptance_hint: str | None = None,
        priority: int = 0,
        parent_id: str | None = None,
        correlation_id: str = "repository",
    ) -> Requirement: ...

    async def get_requirement(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        requirement_id: str,
        *,
        include_deleted: bool = False,
    ) -> Requirement: ...

    async def list_requirements(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        run_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[Requirement]: ...

    async def update_requirement_status(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        requirement_id: str,
        status: RequirementStatus,
        *,
        actor_type: str,
        reason: str,
        correlation_id: str,
    ) -> Requirement: ...


@runtime_checkable
class AuditRepository(Protocol):
    """Append-only audit persistence."""

    async def append_audit(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        event: AuditEvent,
    ) -> AuditEvent: ...

    async def list_audit_for_entity(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        entity_type: str,
        entity_id: str,
    ) -> list[AuditEvent]: ...

    async def list_audit_for_correlation(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        correlation_id: str,
    ) -> list[AuditEvent]: ...


@runtime_checkable
class ImportJobRepository(Protocol):
    """Durable, retryable import jobs with compare-and-swap transitions."""

    async def create_import_job(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        job: ImportJob,
        *,
        correlation_id: str = "ingestion",
    ) -> ImportJob: ...

    async def get_import_job(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        job_id: str,
    ) -> ImportJob: ...

    async def list_import_jobs(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        status: ImportJobStatus | None = None,
        index_status: ImportIndexStatus | None = None,
    ) -> list[ImportJob]: ...

    async def transition_import_job(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        job_id: str,
        *,
        expected_status: ImportJobStatus,
        status: ImportJobStatus,
        expected_index_status: ImportIndexStatus | None = None,
        index_status: ImportIndexStatus | None = None,
        error: ImportJobError | None = None,
        blob_id: str | None = None,
        source_id: str | None = None,
        document_id: str | None = None,
        version_id: str | None = None,
        actor_type: str,
        reason: str,
        correlation_id: str,
    ) -> ImportJob: ...

@runtime_checkable
class KnowledgeEvidenceRepository(
    DocumentRepository,
    EvidenceRepository,
    RequirementRepository,
    AuditRepository,
    ImportJobRepository,
    LifecycleProposalRepository,
    Protocol,
):
    """Aggregate contract shared by InMemory and SQLite backends."""
