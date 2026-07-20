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
class KnowledgeEvidenceRepository(
    DocumentRepository,
    EvidenceRepository,
    RequirementRepository,
    AuditRepository,
    Protocol,
):
    """Aggregate contract shared by InMemory and SQLite backends."""
