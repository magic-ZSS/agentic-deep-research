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
