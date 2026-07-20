"""In-memory reference implementation of all metadata repository contracts."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from threading import RLock
from typing import TypeVar

from pydantic import BaseModel

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
from open_deep_research.knowledge.lifecycle.audit import governed_audit_event
from open_deep_research.knowledge.lifecycle.models import (
    LifecycleProposal,
    LifecycleProposalStatus,
)
from open_deep_research.knowledge.lifecycle.policy import ensure_version_transition
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
    utc_now,
)
from open_deep_research.knowledge.repositories import (
    InvalidTransitionError,
    RepositoryAccessError,
    RepositoryConflictError,
    RepositoryNotFoundError,
    authorize_scope,
)


ModelT = TypeVar("ModelT", bound=BaseModel)


def _copy(value: ModelT) -> ModelT:
    return value.model_copy(deep=True)


class InMemoryRepository:
    """Mirror SQLite's externally observable semantics for tests and fakes."""

    def __init__(self) -> None:
        self._scopes: dict[str, KnowledgeScope] = {}
        self._sources: dict[tuple[str, str], Source] = {}
        self._documents: dict[tuple[str, str], Document] = {}
        self._blobs: dict[tuple[str, str], ContentBlob] = {}
        self._versions: dict[tuple[str, str], DocumentVersion] = {}
        self._chunks: dict[tuple[str, str], Chunk] = {}
        self._requirements: dict[tuple[str, str], Requirement] = {}
        self._evidence: dict[tuple[str, str], Evidence] = {}
        self._audit: dict[tuple[str, str], AuditEvent] = {}
        self._import_jobs: dict[tuple[str, str], ImportJob] = {}
        self._lifecycle_proposals: dict[tuple[str, str], LifecycleProposal] = {}
        self._lock = RLock()

    def _prepare(
        self, access: KnowledgeAccessContext, scope: KnowledgeScope
    ) -> None:
        authorize_scope(access, scope)
        existing = self._scopes.get(scope.scope_id)
        if existing is not None:
            identity = (
                "tenant_id",
                "project_id",
                "owner_user_id",
                "visibility",
            )
            if any(getattr(existing, name) != getattr(scope, name) for name in identity):
                raise RepositoryConflictError("scope identity conflicts with stored scope")
        else:
            self._scopes[scope.scope_id] = _copy(scope)

    @staticmethod
    def _get_scoped(
        mapping: dict[tuple[str, str], ModelT],
        scope_id: str,
        entity_id: str,
        *,
        include_deleted: bool,
        entity_name: str,
    ) -> ModelT:
        value = mapping.get((scope_id, entity_id))
        if value is None:
            if any(other_id == entity_id for _, other_id in mapping):
                raise RepositoryAccessError(f"{entity_name} belongs to another scope")
            raise RepositoryNotFoundError(f"{entity_name} not found")
        if not include_deleted and getattr(value, "soft_deleted_at", None) is not None:
            raise RepositoryNotFoundError(f"{entity_name} not found")
        return value

    def _append_audit_unlocked(self, event: AuditEvent) -> AuditEvent:
        key = (event.scope_id, event.event_id)
        existing = self._audit.get(key)
        if existing is not None:
            if existing != event:
                raise RepositoryConflictError("audit event ID was reused")
            return existing
        self._audit[key] = _copy(event)
        return event

    def _record_created(
        self,
        scope_id: str,
        entity_type: str,
        entity_id: str,
        correlation_id: str,
        *,
        after_status: str | None = None,
    ) -> None:
        self._append_audit_unlocked(
            AuditEvent(
                scope_id=scope_id,
                entity_type=entity_type,
                entity_id=entity_id,
                action="created",
                actor_type="repository",
                reason="repository create",
                after_status=after_status,
                correlation_id=correlation_id,
            )
        )

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
    ) -> Source:
        candidate = Source(
            scope_id=scope.scope_id,
            kind=kind,
            display_name=display_name,
            canonical_uri=canonical_uri,
            internal_storage_ref=internal_storage_ref,
            public_display_uri=public_display_uri,
            publisher=publisher,
            authority_class=authority_class,
        )
        with self._lock:
            self._prepare(access, scope)
            key = (scope.scope_id, candidate.source_id)
            existing = self._sources.get(key)
            if existing is not None:
                return _copy(existing)
            self._sources[key] = _copy(candidate)
            self._record_created(
                scope.scope_id, "source", candidate.source_id, correlation_id
            )
            return _copy(candidate)

    async def get_source(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        source_id: str,
        *,
        include_deleted: bool = False,
    ) -> Source:
        with self._lock:
            self._prepare(access, scope)
            return _copy(
                self._get_scoped(
                    self._sources,
                    scope.scope_id,
                    source_id,
                    include_deleted=include_deleted,
                    entity_name="source",
                )
            )

    async def list_sources(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        kind: SourceKind | None = None,
        include_deleted: bool = False,
    ) -> list[Source]:
        with self._lock:
            self._prepare(access, scope)
            values = [
                value
                for (current_scope, _), value in self._sources.items()
                if current_scope == scope.scope_id
                and (kind is None or value.kind is kind)
                and (include_deleted or value.soft_deleted_at is None)
            ]
            return [
                _copy(value) for value in sorted(values, key=lambda item: item.source_id)
            ]

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
    ) -> Document:
        candidate = Document(
            scope_id=scope.scope_id,
            source_id=source_id,
            logical_key=logical_key,
            title=title,
            media_type=media_type,
        )
        with self._lock:
            self._prepare(access, scope)
            self._get_scoped(
                self._sources,
                scope.scope_id,
                source_id,
                include_deleted=False,
                entity_name="source",
            )
            key = (scope.scope_id, candidate.document_id)
            existing = self._documents.get(key)
            if existing is not None:
                return _copy(existing)
            self._documents[key] = _copy(candidate)
            self._record_created(
                scope.scope_id, "document", candidate.document_id, correlation_id
            )
            return _copy(candidate)

    async def get_document(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        document_id: str,
        *,
        include_deleted: bool = False,
    ) -> Document:
        with self._lock:
            self._prepare(access, scope)
            return _copy(
                self._get_scoped(
                    self._documents,
                    scope.scope_id,
                    document_id,
                    include_deleted=include_deleted,
                    entity_name="document",
                )
            )

    async def list_documents(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        source_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[Document]:
        with self._lock:
            self._prepare(access, scope)
            if source_id is not None:
                self._get_scoped(
                    self._sources,
                    scope.scope_id,
                    source_id,
                    include_deleted=include_deleted,
                    entity_name="source",
                )
            values = [
                value
                for (current_scope, _), value in self._documents.items()
                if current_scope == scope.scope_id
                and (source_id is None or value.source_id == source_id)
                and (include_deleted or value.soft_deleted_at is None)
            ]
            return [
                _copy(value)
                for value in sorted(values, key=lambda item: item.document_id)
            ]

    async def get_content_blob_metadata(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        blob_id: str,
    ) -> ContentBlob:
        with self._lock:
            self._prepare(access, scope)
            return _copy(
                self._get_scoped(
                    self._blobs,
                    scope.scope_id,
                    blob_id,
                    include_deleted=True,
                    entity_name="blob",
                )
            )

    async def list_content_blob_metadata(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
    ) -> list[ContentBlob]:
        with self._lock:
            self._prepare(access, scope)
            values = [
                value
                for (current_scope, _), value in self._blobs.items()
                if current_scope == scope.scope_id
            ]
            return [
                _copy(value) for value in sorted(values, key=lambda item: item.blob_id)
            ]

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
    ) -> DocumentVersion:
        if blob.scope_id != scope.scope_id:
            raise RepositoryAccessError("blob belongs to another scope")
        with self._lock:
            self._prepare(access, scope)
            self._get_scoped(
                self._documents,
                scope.scope_id,
                document_id,
                include_deleted=False,
                entity_name="document",
            )
            existing = next(
                (
                    version
                    for (current_scope, _), version in self._versions.items()
                    if current_scope == scope.scope_id
                    and version.document_id == document_id
                    and version.content_sha256 == blob.content_sha256
                ),
                None,
            )
            if existing is not None:
                return _copy(existing)
            if supersedes_version_id:
                superseded = self._get_scoped(
                    self._versions,
                    scope.scope_id,
                    supersedes_version_id,
                    include_deleted=True,
                    entity_name="version",
                )
                if superseded.document_id != document_id:
                    raise InvalidTransitionError(
                        "superseded version belongs to another document"
                    )
            blob_key = (scope.scope_id, blob.blob_id)
            stored_blob = self._blobs.get(blob_key)
            if stored_blob is not None and (
                stored_blob.content_sha256 != blob.content_sha256
                or stored_blob.byte_size != blob.byte_size
            ):
                raise RepositoryConflictError("blob metadata conflicts")
            self._blobs.setdefault(blob_key, _copy(blob))
            current_numbers = [
                version.version_number
                for (current_scope, _), version in self._versions.items()
                if current_scope == scope.scope_id
                and version.document_id == document_id
            ]
            candidate = DocumentVersion(
                scope_id=scope.scope_id,
                document_id=document_id,
                blob_id=blob.blob_id,
                content_sha256=blob.content_sha256,
                version_number=max(current_numbers, default=0) + 1,
                retrieved_at=retrieved_at,
                published_at=published_at,
                valid_from=valid_from,
                valid_to=valid_to,
                supersedes_version_id=supersedes_version_id,
                metadata=metadata or {},
                lifecycle_status=lifecycle_status,
            )
            self._versions[(scope.scope_id, candidate.version_id)] = _copy(candidate)
            self._record_created(
                scope.scope_id,
                "document_version",
                candidate.version_id,
                correlation_id,
                after_status=candidate.lifecycle_status.value,
            )
            return _copy(candidate)

    async def get_version(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        version_id: str,
        *,
        include_deleted: bool = False,
    ) -> DocumentVersion:
        with self._lock:
            self._prepare(access, scope)
            return _copy(
                self._get_scoped(
                    self._versions,
                    scope.scope_id,
                    version_id,
                    include_deleted=include_deleted,
                    entity_name="version",
                )
            )

    async def list_versions(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        document_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[DocumentVersion]:
        with self._lock:
            self._prepare(access, scope)
            self._get_scoped(
                self._documents,
                scope.scope_id,
                document_id,
                include_deleted=include_deleted,
                entity_name="document",
            )
            values = [
                value
                for (current_scope, _), value in self._versions.items()
                if current_scope == scope.scope_id
                and value.document_id == document_id
                and (include_deleted or value.soft_deleted_at is None)
            ]
            return [_copy(value) for value in sorted(values, key=lambda item: item.version_number)]

    async def list_versions_for_scope(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        include_deleted: bool = False,
    ) -> list[DocumentVersion]:
        with self._lock:
            self._prepare(access, scope)
            values = [
                value
                for (current_scope, _), value in self._versions.items()
                if current_scope == scope.scope_id
                and (include_deleted or value.soft_deleted_at is None)
            ]
            return [
                _copy(value) for value in sorted(values, key=lambda item: item.version_id)
            ]

    async def find_by_content_hash(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        content_sha256: str,
        *,
        include_deleted: bool = False,
    ) -> list[DocumentVersion]:
        with self._lock:
            self._prepare(access, scope)
            values = [
                value
                for (current_scope, _), value in self._versions.items()
                if current_scope == scope.scope_id
                and value.content_sha256 == content_sha256.lower()
                and (include_deleted or value.soft_deleted_at is None)
            ]
            return [_copy(value) for value in sorted(values, key=lambda item: item.version_id)]

    async def add_chunks(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        version_id: str,
        chunks: Sequence[ChunkInput],
        *,
        correlation_id: str = "repository",
    ) -> list[Chunk]:
        with self._lock:
            self._prepare(access, scope)
            self._get_scoped(
                self._versions,
                scope.scope_id,
                version_id,
                include_deleted=False,
                entity_name="version",
            )
            results: list[Chunk] = []
            seen_ordinals: set[int] = set()
            for chunk_input in chunks:
                if chunk_input.ordinal in seen_ordinals:
                    raise RepositoryConflictError("duplicate chunk ordinal in batch")
                seen_ordinals.add(chunk_input.ordinal)
                candidate = Chunk(
                    **chunk_input.model_dump(),
                    scope_id=scope.scope_id,
                    version_id=version_id,
                )
                ordinal_conflict = next(
                    (
                        value
                        for (current_scope, _), value in self._chunks.items()
                        if current_scope == scope.scope_id
                        and value.version_id == version_id
                        and value.ordinal == candidate.ordinal
                        and value.chunk_id != candidate.chunk_id
                    ),
                    None,
                )
                if ordinal_conflict is not None:
                    raise RepositoryConflictError(
                        "chunk ordinal already has different immutable content"
                    )
                key = (scope.scope_id, candidate.chunk_id)
                existing = self._chunks.get(key)
                if existing is None:
                    self._chunks[key] = _copy(candidate)
                    self._record_created(
                        scope.scope_id, "chunk", candidate.chunk_id, correlation_id
                    )
                    existing = candidate
                results.append(_copy(existing))
            return sorted(results, key=lambda item: (item.ordinal, item.chunk_id))

    async def get_chunk(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        chunk_id: str,
        *,
        include_deleted: bool = False,
    ) -> Chunk:
        with self._lock:
            self._prepare(access, scope)
            return _copy(
                self._get_scoped(
                    self._chunks,
                    scope.scope_id,
                    chunk_id,
                    include_deleted=include_deleted,
                    entity_name="chunk",
                )
            )

    async def list_chunks_for_version(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        version_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Chunk]:
        with self._lock:
            self._prepare(access, scope)
            self._get_scoped(
                self._versions,
                scope.scope_id,
                version_id,
                include_deleted=include_deleted,
                entity_name="version",
            )
            values = [
                value
                for (current_scope, _), value in self._chunks.items()
                if current_scope == scope.scope_id
                and value.version_id == version_id
                and (include_deleted or value.soft_deleted_at is None)
            ]
            return [
                _copy(value)
                for value in sorted(values, key=lambda item: (item.ordinal, item.chunk_id))
            ]

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
    ) -> Requirement:
        with self._lock:
            self._prepare(access, scope)
            if parent_id:
                self._get_scoped(
                    self._requirements,
                    scope.scope_id,
                    parent_id,
                    include_deleted=False,
                    entity_name="requirement",
                )
            candidate = Requirement(
                scope_id=scope.scope_id,
                run_id=run_id,
                template_id=template_id,
                text=text,
                acceptance_hint=acceptance_hint,
                priority=priority,
                parent_id=parent_id,
            )
            key = (scope.scope_id, candidate.requirement_id)
            existing = self._requirements.get(key)
            if existing is None:
                self._requirements[key] = _copy(candidate)
                self._record_created(
                    scope.scope_id,
                    "requirement",
                    candidate.requirement_id,
                    correlation_id,
                    after_status=candidate.status.value,
                )
                existing = candidate
            return _copy(existing)

    async def get_requirement(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        requirement_id: str,
        *,
        include_deleted: bool = False,
    ) -> Requirement:
        with self._lock:
            self._prepare(access, scope)
            return _copy(
                self._get_scoped(
                    self._requirements,
                    scope.scope_id,
                    requirement_id,
                    include_deleted=include_deleted,
                    entity_name="requirement",
                )
            )

    async def list_requirements(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        run_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[Requirement]:
        with self._lock:
            self._prepare(access, scope)
            values = [
                value
                for (current_scope, _), value in self._requirements.items()
                if current_scope == scope.scope_id
                and (run_id is None or value.run_id == run_id)
                and (include_deleted or value.soft_deleted_at is None)
            ]
            return [_copy(value) for value in sorted(values, key=lambda item: item.requirement_id)]

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
    ) -> Requirement:
        with self._lock:
            self._prepare(access, scope)
            current = self._get_scoped(
                self._requirements,
                scope.scope_id,
                requirement_id,
                include_deleted=False,
                entity_name="requirement",
            )
            if current.status is status:
                return _copy(current)
            updated = current.model_copy(update={"status": status, "updated_at": utc_now()})
            self._requirements[(scope.scope_id, requirement_id)] = updated
            self._append_audit_unlocked(
                AuditEvent(
                    scope_id=scope.scope_id,
                    entity_type="requirement",
                    entity_id=requirement_id,
                    action="status_changed",
                    actor_type=actor_type,
                    reason=reason,
                    before_status=current.status.value,
                    after_status=status.value,
                    correlation_id=correlation_id,
                )
            )
            return _copy(updated)

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
    ) -> Evidence:
        with self._lock:
            self._prepare(access, scope)
            self._get_scoped(
                self._chunks,
                scope.scope_id,
                chunk_id,
                include_deleted=False,
                entity_name="chunk",
            )
            if requirement_id:
                self._get_scoped(
                    self._requirements,
                    scope.scope_id,
                    requirement_id,
                    include_deleted=False,
                    entity_name="requirement",
                )
            candidate = Evidence(
                scope_id=scope.scope_id,
                chunk_id=chunk_id,
                requirement_id=requirement_id,
                excerpt=excerpt,
                relation=relation,
                directness=directness,
                confidence=confidence,
                valid_at=valid_at,
                retrieval_method=retrieval_method,
                validation_status=validation_status,
            )
            key = (scope.scope_id, candidate.evidence_id)
            existing = self._evidence.get(key)
            if existing is None:
                self._evidence[key] = _copy(candidate)
                self._record_created(
                    scope.scope_id,
                    "evidence",
                    candidate.evidence_id,
                    correlation_id,
                    after_status=candidate.validation_status.value,
                )
                existing = candidate
            return _copy(existing)

    async def get_evidence(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        evidence_id: str,
        *,
        include_deleted: bool = False,
    ) -> Evidence:
        with self._lock:
            self._prepare(access, scope)
            return _copy(
                self._get_scoped(
                    self._evidence,
                    scope.scope_id,
                    evidence_id,
                    include_deleted=include_deleted,
                    entity_name="evidence",
                )
            )

    def _list_evidence(
        self, scope_id: str, *, include_deleted: bool, predicate
    ) -> list[Evidence]:
        values = [
            value
            for (current_scope, _), value in self._evidence.items()
            if current_scope == scope_id
            and predicate(value)
            and (include_deleted or value.soft_deleted_at is None)
        ]
        return [_copy(value) for value in sorted(values, key=lambda item: item.evidence_id)]

    async def list_evidence_for_requirement(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        requirement_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Evidence]:
        with self._lock:
            self._prepare(access, scope)
            self._get_scoped(
                self._requirements,
                scope.scope_id,
                requirement_id,
                include_deleted=include_deleted,
                entity_name="requirement",
            )
            return self._list_evidence(
                scope.scope_id,
                include_deleted=include_deleted,
                predicate=lambda value: value.requirement_id == requirement_id,
            )

    async def list_evidence_for_chunk(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        chunk_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Evidence]:
        with self._lock:
            self._prepare(access, scope)
            self._get_scoped(
                self._chunks,
                scope.scope_id,
                chunk_id,
                include_deleted=include_deleted,
                entity_name="chunk",
            )
            return self._list_evidence(
                scope.scope_id,
                include_deleted=include_deleted,
                predicate=lambda value: value.chunk_id == chunk_id,
            )

    async def list_evidence_for_source(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        source_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Evidence]:
        with self._lock:
            self._prepare(access, scope)
            self._get_scoped(
                self._sources,
                scope.scope_id,
                source_id,
                include_deleted=include_deleted,
                entity_name="source",
            )
            document_ids = {
                item.document_id
                for (current_scope, _), item in self._documents.items()
                if current_scope == scope.scope_id
                and item.source_id == source_id
                and (include_deleted or item.soft_deleted_at is None)
            }
            version_ids = {
                item.version_id
                for (current_scope, _), item in self._versions.items()
                if current_scope == scope.scope_id
                and item.document_id in document_ids
                and (include_deleted or item.soft_deleted_at is None)
            }
            chunk_ids = {
                item.chunk_id
                for (current_scope, _), item in self._chunks.items()
                if current_scope == scope.scope_id
                and item.version_id in version_ids
                and (include_deleted or item.soft_deleted_at is None)
            }
            return self._list_evidence(
                scope.scope_id,
                include_deleted=include_deleted,
                predicate=lambda value: value.chunk_id in chunk_ids,
            )

    def _validate_import_job_references_unlocked(
        self, scope_id: str, job: ImportJob
    ) -> None:
        blob = source = document = version = None
        if job.blob_id is not None:
            blob = self._get_scoped(
                self._blobs,
                scope_id,
                job.blob_id,
                include_deleted=True,
                entity_name="blob",
            )
            if blob.content_sha256 != job.content_sha256:
                raise RepositoryConflictError("import job blob hash does not match")
        if job.source_id is not None:
            source = self._get_scoped(
                self._sources,
                scope_id,
                job.source_id,
                include_deleted=False,
                entity_name="source",
            )
        if job.document_id is not None:
            document = self._get_scoped(
                self._documents,
                scope_id,
                job.document_id,
                include_deleted=False,
                entity_name="document",
            )
        if job.version_id is not None:
            version = self._get_scoped(
                self._versions,
                scope_id,
                job.version_id,
                include_deleted=False,
                entity_name="version",
            )
        if document is not None and source is not None:
            if document.source_id != source.source_id:
                raise RepositoryConflictError("import job source/document chain conflicts")
        if version is not None and document is not None:
            if version.document_id != document.document_id:
                raise RepositoryConflictError("import job document/version chain conflicts")
        if version is not None and blob is not None:
            if (
                version.blob_id != blob.blob_id
                or version.content_sha256 != blob.content_sha256
            ):
                raise RepositoryConflictError("import job blob/version chain conflicts")

    async def create_import_job(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        job: ImportJob,
        *,
        correlation_id: str = "ingestion",
    ) -> ImportJob:
        if job.scope_id != scope.scope_id:
            raise RepositoryAccessError("import job belongs to another scope")
        if (
            job.status is not ImportJobStatus.PENDING
            or job.index_status is not ImportIndexStatus.NOT_REQUESTED
        ):
            raise InvalidTransitionError("new import job must start pending")
        with self._lock:
            self._prepare(access, scope)
            self._validate_import_job_references_unlocked(scope.scope_id, job)
            key = (scope.scope_id, job.job_id)
            existing = self._import_jobs.get(key)
            if existing is not None:
                return _copy(existing)
            self._import_jobs[key] = _copy(job)
            self._record_created(
                scope.scope_id,
                "import_job",
                job.job_id,
                correlation_id,
                after_status=f"{job.status.value}:{job.index_status.value}",
            )
            return _copy(job)

    async def get_import_job(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        job_id: str,
    ) -> ImportJob:
        with self._lock:
            self._prepare(access, scope)
            return _copy(
                self._get_scoped(
                    self._import_jobs,
                    scope.scope_id,
                    job_id,
                    include_deleted=True,
                    entity_name="import job",
                )
            )

    async def list_import_jobs(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        status: ImportJobStatus | None = None,
        index_status: ImportIndexStatus | None = None,
    ) -> list[ImportJob]:
        with self._lock:
            self._prepare(access, scope)
            values = [
                value
                for (current_scope, _), value in self._import_jobs.items()
                if current_scope == scope.scope_id
                and (status is None or value.status is status)
                and (index_status is None or value.index_status is index_status)
            ]
            return [
                _copy(value) for value in sorted(values, key=lambda item: item.job_id)
            ]

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
    ) -> ImportJob:
        if index_status is not None and expected_index_status is None:
            raise RepositoryConflictError(
                "index transition requires expected_index_status CAS"
            )
        with self._lock:
            self._prepare(access, scope)
            current = self._get_scoped(
                self._import_jobs,
                scope.scope_id,
                job_id,
                include_deleted=True,
                entity_name="import job",
            )
            if current.status is not expected_status or (
                expected_index_status is not None
                and current.index_status is not expected_index_status
            ):
                raise RepositoryConflictError("import job CAS precondition failed")
            if (
                current.status is status
                and (index_status is None or current.index_status is index_status)
                and error is None
                and all(
                    value is None
                    for value in (blob_id, source_id, document_id, version_id)
                )
            ):
                return _copy(current)
            try:
                updated = current.transition(
                    status=status,
                    index_status=index_status,
                    error=error,
                    blob_id=blob_id,
                    source_id=source_id,
                    document_id=document_id,
                    version_id=version_id,
                )
            except ValueError as exc:
                raise InvalidTransitionError(str(exc)) from exc
            self._validate_import_job_references_unlocked(scope.scope_id, updated)
            self._import_jobs[(scope.scope_id, job_id)] = _copy(updated)
            self._append_audit_unlocked(
                AuditEvent(
                    scope_id=scope.scope_id,
                    entity_type="import_job",
                    entity_id=job_id,
                    action="state_changed",
                    actor_type=actor_type,
                    reason=reason,
                    before_status=(
                        f"{current.status.value}:{current.index_status.value}"
                    ),
                    after_status=f"{updated.status.value}:{updated.index_status.value}",
                    correlation_id=correlation_id,
                )
            )
            return _copy(updated)

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
    ) -> DocumentVersion:
        """CAS a version status and atomically supersede its declared predecessor."""
        with self._lock:
            self._prepare(access, scope)
            current = self._get_scoped(
                self._versions,
                scope.scope_id,
                version_id,
                include_deleted=False,
                entity_name="version",
            )
            if current.lifecycle_status is status:
                return _copy(current)
            if current.lifecycle_status is not expected_status:
                raise RepositoryConflictError(
                    "document-version lifecycle compare-and-swap failed"
                )
            ensure_version_transition(current.lifecycle_status, status)
            if proposal_id is not None:
                proposal = self._get_scoped(
                    self._lifecycle_proposals,
                    scope.scope_id,
                    proposal_id,
                    include_deleted=True,
                    entity_name="lifecycle proposal",
                )
                if proposal.target_id != version_id:
                    raise RepositoryConflictError(
                        "proposal target does not match document version"
                    )

            predecessor: DocumentVersion | None = None
            if (
                status is VersionLifecycleStatus.ACTIVE
                and current.supersedes_version_id is not None
            ):
                predecessor = self._get_scoped(
                    self._versions,
                    scope.scope_id,
                    current.supersedes_version_id,
                    include_deleted=False,
                    entity_name="superseded version",
                )
                if predecessor.document_id != current.document_id:
                    raise RepositoryConflictError(
                        "replacement and predecessor belong to different documents"
                    )
                if predecessor.lifecycle_status not in {
                    VersionLifecycleStatus.ACTIVE,
                    VersionLifecycleStatus.SUPERSEDED,
                }:
                    raise RepositoryConflictError(
                        "replacement predecessor is not active"
                    )

            updated = current.model_copy(update={"lifecycle_status": status})
            predecessor_updated: DocumentVersion | None = None
            predecessor_event: AuditEvent | None = None
            if (
                predecessor is not None
                and predecessor.lifecycle_status is VersionLifecycleStatus.ACTIVE
            ):
                predecessor_updated = predecessor.model_copy(
                    update={"lifecycle_status": VersionLifecycleStatus.SUPERSEDED}
                )
                predecessor_event = governed_audit_event(
                    scope_id=scope.scope_id,
                    entity_type="document_version",
                    entity_id=predecessor.version_id,
                    action="lifecycle_transition",
                    actor_type=actor_type,
                    reason=f"superseded by {version_id}: {reason}",
                    before_status=VersionLifecycleStatus.ACTIVE.value,
                    after_status=VersionLifecycleStatus.SUPERSEDED.value,
                    correlation_id=correlation_id,
                    policy_version=policy_version,
                    rule_results=rule_results,
                    run_id=run_id,
                    proposal_id=proposal_id,
                    extra_metadata={"replacement_version_id": version_id},
                )
            event = governed_audit_event(
                scope_id=scope.scope_id,
                entity_type="document_version",
                entity_id=version_id,
                action="lifecycle_transition",
                actor_type=actor_type,
                reason=reason,
                before_status=current.lifecycle_status.value,
                after_status=status.value,
                correlation_id=correlation_id,
                policy_version=policy_version,
                rule_results=rule_results,
                run_id=run_id,
                proposal_id=proposal_id,
            )
            # Preflight deterministic IDs so an audit conflict cannot leave partial state.
            for candidate_event in (predecessor_event, event):
                if candidate_event is None:
                    continue
                existing = self._audit.get(
                    (candidate_event.scope_id, candidate_event.event_id)
                )
                if existing is not None and existing != candidate_event:
                    raise RepositoryConflictError("audit event ID was reused")
            if predecessor_updated is not None:
                self._versions[(scope.scope_id, predecessor_updated.version_id)] = (
                    predecessor_updated
                )
                self._append_audit_unlocked(predecessor_event)
            self._versions[(scope.scope_id, version_id)] = updated
            self._append_audit_unlocked(event)
            return _copy(updated)

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
    ) -> Evidence:
        """CAS evidence validation; relation changes create a soft-linked identity."""
        allowed = {
            EvidenceValidationStatus.PENDING: {
                EvidenceValidationStatus.VALIDATED,
                EvidenceValidationStatus.REJECTED,
            },
            EvidenceValidationStatus.VALIDATED: {
                EvidenceValidationStatus.PENDING,
                EvidenceValidationStatus.REJECTED,
            },
            EvidenceValidationStatus.REJECTED: {
                EvidenceValidationStatus.PENDING,
            },
        }
        with self._lock:
            self._prepare(access, scope)
            current = self._get_scoped(
                self._evidence,
                scope.scope_id,
                evidence_id,
                include_deleted=True,
                entity_name="evidence",
            )
            replacement = Evidence(
                scope_id=current.scope_id,
                chunk_id=current.chunk_id,
                requirement_id=current.requirement_id,
                excerpt=current.excerpt,
                relation=relation,
                directness=directness,
                confidence=confidence,
                valid_at=valid_at,
                retrieval_method=current.retrieval_method,
                created_at=current.created_at,
                validation_status=status,
            )
            if current.soft_deleted_at is not None:
                existing_replacement = self._evidence.get(
                    (scope.scope_id, replacement.evidence_id)
                )
                if existing_replacement == replacement:
                    return _copy(existing_replacement)
                raise RepositoryConflictError("evidence is already soft deleted")
            desired_same_identity = replacement.evidence_id == current.evidence_id
            if current.validation_status is not expected_status:
                if current == replacement:
                    return _copy(current)
                raise RepositoryConflictError(
                    "evidence validation compare-and-swap failed"
                )
            if status is not current.validation_status and status not in allowed[
                current.validation_status
            ]:
                raise InvalidTransitionError(
                    "invalid evidence validation transition: "
                    f"{current.validation_status.value}->{status.value}"
                )
            if current == replacement:
                return _copy(current)

            event = governed_audit_event(
                scope_id=scope.scope_id,
                entity_type="evidence",
                entity_id=replacement.evidence_id,
                action="validation_transition",
                actor_type=actor_type,
                reason=reason,
                before_status=current.validation_status.value,
                after_status=status.value,
                correlation_id=correlation_id,
                policy_version=policy_version,
                rule_results=rule_results,
                run_id=run_id,
                proposal_id=proposal_id,
                extra_metadata=(
                    {"replaces_evidence_id": current.evidence_id}
                    if not desired_same_identity
                    else None
                ),
            )
            replaced_event = None
            if not desired_same_identity:
                replaced_event = governed_audit_event(
                    scope_id=scope.scope_id,
                    entity_type="evidence",
                    entity_id=current.evidence_id,
                    action="evidence_replaced",
                    actor_type=actor_type,
                    reason=reason,
                    before_status=current.validation_status.value,
                    after_status="soft_deleted",
                    correlation_id=correlation_id,
                    policy_version=policy_version,
                    rule_results=rule_results,
                    run_id=run_id,
                    proposal_id=proposal_id,
                    extra_metadata={
                        "replacement_evidence_id": replacement.evidence_id
                    },
                )
            for candidate_event in (replaced_event, event):
                if candidate_event is None:
                    continue
                existing_event = self._audit.get(
                    (scope.scope_id, candidate_event.event_id)
                )
                if existing_event is not None and existing_event != candidate_event:
                    raise RepositoryConflictError("audit event ID was reused")
            if desired_same_identity:
                self._evidence[(scope.scope_id, evidence_id)] = replacement
            else:
                existing = self._evidence.get(
                    (scope.scope_id, replacement.evidence_id)
                )
                if existing is not None and existing != replacement:
                    raise RepositoryConflictError(
                        "replacement evidence identity already exists"
                    )
                self._evidence[(scope.scope_id, evidence_id)] = current.model_copy(
                    update={"soft_deleted_at": utc_now()}
                )
                self._evidence[(scope.scope_id, replacement.evidence_id)] = replacement
                self._append_audit_unlocked(replaced_event)
            self._append_audit_unlocked(event)
            return _copy(replacement)

    async def create_lifecycle_proposal(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        proposal: LifecycleProposal,
    ) -> LifecycleProposal:
        with self._lock:
            self._prepare(access, scope)
            if proposal.scope_id != scope.scope_id:
                raise RepositoryAccessError("lifecycle proposal belongs to another scope")
            targets = {
                "source": self._sources,
                "document": self._documents,
                "document_version": self._versions,
                "chunk": self._chunks,
                "requirement": self._requirements,
                "evidence": self._evidence,
            }
            self._get_scoped(
                targets[proposal.target_entity_type.value],
                scope.scope_id,
                proposal.target_id,
                include_deleted=True,
                entity_name=proposal.target_entity_type.value,
            )
            key = (scope.scope_id, proposal.proposal_id)
            existing = self._lifecycle_proposals.get(key)
            if existing is not None:
                if existing != proposal:
                    raise RepositoryConflictError("proposal ID was reused")
                return _copy(existing)
            self._lifecycle_proposals[key] = _copy(proposal)
            self._record_created(
                scope.scope_id,
                "lifecycle_proposal",
                proposal.proposal_id,
                proposal.correlation_id,
                after_status=proposal.status.value,
            )
            return _copy(proposal)

    async def get_lifecycle_proposal(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        proposal_id: str,
    ) -> LifecycleProposal:
        with self._lock:
            self._prepare(access, scope)
            return _copy(
                self._get_scoped(
                    self._lifecycle_proposals,
                    scope.scope_id,
                    proposal_id,
                    include_deleted=True,
                    entity_name="lifecycle proposal",
                )
            )

    async def list_lifecycle_proposals(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        status: LifecycleProposalStatus | None = None,
        run_id: str | None = None,
    ) -> list[LifecycleProposal]:
        with self._lock:
            self._prepare(access, scope)
            values = [
                proposal
                for (scope_id, _), proposal in self._lifecycle_proposals.items()
                if scope_id == scope.scope_id
                and (status is None or proposal.status is status)
                and (run_id is None or proposal.run_id == run_id)
            ]
            return [
                _copy(item)
                for item in sorted(values, key=lambda item: item.proposal_id)
            ]

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
    ) -> LifecycleProposal:
        allowed = {
            LifecycleProposalStatus.PENDING: {
                LifecycleProposalStatus.APPROVED,
                LifecycleProposalStatus.REJECTED,
                LifecycleProposalStatus.APPLIED,
            },
            LifecycleProposalStatus.APPROVED: {
                LifecycleProposalStatus.APPLIED,
                LifecycleProposalStatus.REJECTED,
            },
            LifecycleProposalStatus.REJECTED: set(),
            LifecycleProposalStatus.APPLIED: set(),
        }
        with self._lock:
            self._prepare(access, scope)
            current = self._get_scoped(
                self._lifecycle_proposals,
                scope.scope_id,
                proposal_id,
                include_deleted=True,
                entity_name="lifecycle proposal",
            )
            if current.status is status:
                return _copy(current)
            if current.status is not expected_status:
                raise RepositoryConflictError("proposal compare-and-swap failed")
            if status not in allowed[current.status]:
                raise InvalidTransitionError(
                    f"invalid proposal transition: {current.status.value}->{status.value}"
                )
            updated = current.model_copy(
                update={
                    "status": status,
                    "policy_version": policy_version,
                    "rule_results": tuple(rule_results),
                    "decision_reason": reason,
                    "updated_at": utc_now(),
                }
            )
            event = governed_audit_event(
                scope_id=scope.scope_id,
                entity_type="lifecycle_proposal",
                entity_id=proposal_id,
                action="proposal_transition",
                actor_type=actor_type,
                reason=reason,
                before_status=current.status.value,
                after_status=status.value,
                correlation_id=correlation_id,
                policy_version=policy_version,
                rule_results=rule_results,
                run_id=current.run_id,
                proposal_id=proposal_id,
            )
            existing_event = self._audit.get((scope.scope_id, event.event_id))
            if existing_event is not None and existing_event != event:
                raise RepositoryConflictError("audit event ID was reused")
            self._lifecycle_proposals[(scope.scope_id, proposal_id)] = updated
            self._append_audit_unlocked(event)
            return _copy(updated)

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
    ) -> AuditEvent:
        mappings: dict[str, dict] = {
            "source": self._sources,
            "document": self._documents,
            "document_version": self._versions,
            "chunk": self._chunks,
            "requirement": self._requirements,
            "evidence": self._evidence,
        }
        mapping = mappings.get(entity_type)
        if mapping is None:
            raise InvalidTransitionError(f"unsupported soft-delete type: {entity_type}")
        with self._lock:
            self._prepare(access, scope)
            current = self._get_scoped(
                mapping,
                scope.scope_id,
                entity_id,
                include_deleted=True,
                entity_name=entity_type,
            )
            if current.soft_deleted_at is not None:
                events = [
                    value
                    for (current_scope, _), value in self._audit.items()
                    if current_scope == scope.scope_id
                    and value.entity_type == entity_type
                    and value.entity_id == entity_id
                    and value.action == "soft_deleted"
                ]
                if events:
                    return _copy(sorted(events, key=lambda item: item.created_at)[-1])
                raise RepositoryConflictError("entity is deleted without an audit event")
            updated = current.model_copy(update={"soft_deleted_at": utc_now()})
            mapping[(scope.scope_id, entity_id)] = updated
            event = AuditEvent(
                scope_id=scope.scope_id,
                entity_type=entity_type,
                entity_id=entity_id,
                action="soft_deleted",
                actor_type=actor_type,
                reason=reason,
                correlation_id=correlation_id,
            )
            self._append_audit_unlocked(event)
            return _copy(event)

    async def append_audit(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        event: AuditEvent,
    ) -> AuditEvent:
        with self._lock:
            self._prepare(access, scope)
            if event.scope_id != scope.scope_id:
                raise RepositoryAccessError("audit event belongs to another scope")
            return _copy(self._append_audit_unlocked(event))

    async def list_audit_for_entity(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        entity_type: str,
        entity_id: str,
    ) -> list[AuditEvent]:
        with self._lock:
            self._prepare(access, scope)
            values = [
                value
                for (current_scope, _), value in self._audit.items()
                if current_scope == scope.scope_id
                and value.entity_type == entity_type
                and value.entity_id == entity_id
            ]
            return [_copy(value) for value in sorted(values, key=lambda item: (item.created_at, item.event_id))]

    async def list_audit_for_correlation(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        correlation_id: str,
    ) -> list[AuditEvent]:
        with self._lock:
            self._prepare(access, scope)
            values = [
                value
                for (current_scope, _), value in self._audit.items()
                if current_scope == scope.scope_id
                and value.correlation_id == correlation_id
            ]
            return [_copy(value) for value in sorted(values, key=lambda item: (item.created_at, item.event_id))]

    def entity_counts(
        self, access: KnowledgeAccessContext, scope: KnowledgeScope
    ) -> dict[str, int]:
        """Return non-sensitive counts used by backend contract tests."""
        with self._lock:
            self._prepare(access, scope)
            scope_id = scope.scope_id
            return {
                "sources": sum(1 for current, _ in self._sources if current == scope_id),
                "documents": sum(1 for current, _ in self._documents if current == scope_id),
                "blobs": sum(1 for current, _ in self._blobs if current == scope_id),
                "versions": sum(1 for current, _ in self._versions if current == scope_id),
                "chunks": sum(1 for current, _ in self._chunks if current == scope_id),
                "requirements": sum(1 for current, _ in self._requirements if current == scope_id),
                "evidence": sum(1 for current, _ in self._evidence if current == scope_id),
            }
