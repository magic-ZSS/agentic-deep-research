"""SQLite implementation of the structured metadata repository contracts."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime
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
from open_deep_research.storage.sqlite import SQLiteDatabase


ModelT = TypeVar("ModelT", bound=BaseModel)


def _dump(model: BaseModel) -> str:
    return model.model_dump_json()


def _load(model_type: type[ModelT], payload: str) -> ModelT:
    return model_type.model_validate_json(payload)


class SQLiteRepository:
    """Persist domain metadata with scope-aware constraints and transactions."""

    def __init__(self, path: str, *, busy_timeout_ms: int = 5_000) -> None:
        self.database = SQLiteDatabase(path, busy_timeout_ms=busy_timeout_ms)

    @property
    def schema_version(self) -> int:
        """Expose the validated migration version."""
        return self.database.schema_version()

    async def _run(self, operation: Callable, *args):
        return await asyncio.to_thread(operation, *args)

    @staticmethod
    def _begin(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _prepare(
        connection: sqlite3.Connection,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
    ) -> None:
        authorize_scope(access, scope)
        connection.execute(
            "INSERT OR IGNORE INTO knowledge_scopes "
            "(scope_id, tenant_id, project_id, owner_user_id, visibility, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                scope.scope_id,
                scope.tenant_id,
                scope.project_id,
                scope.owner_user_id,
                scope.visibility.value,
                _dump(scope),
            ),
        )
        row = connection.execute(
            "SELECT tenant_id, project_id, owner_user_id, visibility "
            "FROM knowledge_scopes WHERE scope_id = ?",
            (scope.scope_id,),
        ).fetchone()
        stored = (
            row["tenant_id"],
            row["project_id"],
            row["owner_user_id"],
            row["visibility"],
        )
        expected = (
            scope.tenant_id,
            scope.project_id,
            scope.owner_user_id,
            scope.visibility.value,
        )
        if stored != expected:
            raise RepositoryConflictError("scope identity conflicts with stored scope")

    @staticmethod
    def _get_row(
        connection: sqlite3.Connection,
        *,
        table: str,
        id_column: str,
        scope_id: str,
        entity_id: str,
        include_deleted: bool,
        entity_name: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            f"SELECT payload, soft_deleted_at FROM {table} "
            f"WHERE scope_id = ? AND {id_column} = ?",
            (scope_id, entity_id),
        ).fetchone()
        if row is None:
            other = connection.execute(
                f"SELECT 1 FROM {table} WHERE {id_column} = ? LIMIT 1",
                (entity_id,),
            ).fetchone()
            if other is not None:
                raise RepositoryAccessError(f"{entity_name} belongs to another scope")
            raise RepositoryNotFoundError(f"{entity_name} not found")
        if not include_deleted and row["soft_deleted_at"] is not None:
            raise RepositoryNotFoundError(f"{entity_name} not found")
        return row

    @staticmethod
    def _insert_audit(connection: sqlite3.Connection, event: AuditEvent) -> AuditEvent:
        try:
            connection.execute(
                "INSERT INTO audit_events "
                "(scope_id, event_id, entity_type, entity_id, action, correlation_id, "
                "created_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.scope_id,
                    event.event_id,
                    event.entity_type,
                    event.entity_id,
                    event.action,
                    event.correlation_id,
                    event.created_at.isoformat(),
                    _dump(event),
                ),
            )
        except sqlite3.IntegrityError as exc:
            row = connection.execute(
                "SELECT payload FROM audit_events "
                "WHERE scope_id = ? AND event_id = ?",
                (event.scope_id, event.event_id),
            ).fetchone()
            if row is None or _load(AuditEvent, row["payload"]) != event:
                raise RepositoryConflictError("audit event ID was reused") from exc
        return event

    @classmethod
    def _record_created(
        cls,
        connection: sqlite3.Connection,
        scope_id: str,
        entity_type: str,
        entity_id: str,
        correlation_id: str,
        *,
        after_status: str | None = None,
    ) -> None:
        cls._insert_audit(
            connection,
            AuditEvent(
                scope_id=scope_id,
                entity_type=entity_type,
                entity_id=entity_id,
                action="created",
                actor_type="repository",
                reason="repository create",
                after_status=after_status,
                correlation_id=correlation_id,
            ),
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

        def operation() -> Source:
            connection = self.database.connect()
            try:
                self._begin(connection)
                self._prepare(connection, access, scope)
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO sources "
                    "(scope_id, source_id, kind, identity_key, soft_deleted_at, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        scope.scope_id,
                        candidate.source_id,
                        candidate.kind.value,
                        candidate.identity_key,
                        None,
                        _dump(candidate),
                    ),
                )
                row = connection.execute(
                    "SELECT payload FROM sources WHERE scope_id = ? AND source_id = ?",
                    (scope.scope_id, candidate.source_id),
                ).fetchone()
                stored = _load(Source, row["payload"])
                if cursor.rowcount == 1:
                    self._record_created(
                        connection,
                        scope.scope_id,
                        "source",
                        candidate.source_id,
                        correlation_id,
                    )
                connection.commit()
                return stored
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

        return await self._run(operation)

    async def get_source(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        source_id: str,
        *,
        include_deleted: bool = False,
    ) -> Source:
        return await self._get_model(
            access,
            scope,
            "sources",
            "source_id",
            source_id,
            Source,
            include_deleted,
            "source",
        )

    async def list_sources(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        kind: SourceKind | None = None,
        include_deleted: bool = False,
    ) -> list[Source]:
        clauses: list[str] = []
        parameters: list[object] = []
        if kind is not None:
            clauses.append("kind = ?")
            parameters.append(kind.value)
        if not include_deleted:
            clauses.append("soft_deleted_at IS NULL")
        return await self._list_models(
            access,
            scope,
            table="sources",
            model_type=Source,
            clauses=clauses,
            parameters=parameters,
            order_by="source_id",
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

        def operation() -> Document:
            connection = self.database.connect()
            try:
                self._begin(connection)
                self._prepare(connection, access, scope)
                self._get_row(
                    connection,
                    table="sources",
                    id_column="source_id",
                    scope_id=scope.scope_id,
                    entity_id=source_id,
                    include_deleted=False,
                    entity_name="source",
                )
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO documents "
                    "(scope_id, document_id, source_id, logical_key, "
                    "soft_deleted_at, payload) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        scope.scope_id,
                        candidate.document_id,
                        source_id,
                        candidate.logical_key,
                        None,
                        _dump(candidate),
                    ),
                )
                row = connection.execute(
                    "SELECT payload FROM documents "
                    "WHERE scope_id = ? AND document_id = ?",
                    (scope.scope_id, candidate.document_id),
                ).fetchone()
                stored = _load(Document, row["payload"])
                if cursor.rowcount == 1:
                    self._record_created(
                        connection,
                        scope.scope_id,
                        "document",
                        candidate.document_id,
                        correlation_id,
                    )
                connection.commit()
                return stored
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

        return await self._run(operation)

    async def get_document(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        document_id: str,
        *,
        include_deleted: bool = False,
    ) -> Document:
        return await self._get_model(
            access,
            scope,
            "documents",
            "document_id",
            document_id,
            Document,
            include_deleted,
            "document",
        )

    async def list_documents(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        source_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[Document]:
        if source_id is not None:
            await self.get_source(
                access, scope, source_id, include_deleted=include_deleted
            )
        clauses: list[str] = []
        parameters: list[object] = []
        if source_id is not None:
            clauses.append("source_id = ?")
            parameters.append(source_id)
        if not include_deleted:
            clauses.append("soft_deleted_at IS NULL")
        return await self._list_models(
            access,
            scope,
            table="documents",
            model_type=Document,
            clauses=clauses,
            parameters=parameters,
            order_by="document_id",
        )

    async def get_content_blob_metadata(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        blob_id: str,
    ) -> ContentBlob:
        def operation() -> ContentBlob:
            connection = self.database.connect()
            try:
                self._prepare(connection, access, scope)
                row = connection.execute(
                    "SELECT payload FROM content_blobs "
                    "WHERE scope_id = ? AND blob_id = ?",
                    (scope.scope_id, blob_id),
                ).fetchone()
                if row is None:
                    other = connection.execute(
                        "SELECT 1 FROM content_blobs WHERE blob_id = ? LIMIT 1",
                        (blob_id,),
                    ).fetchone()
                    if other is not None:
                        raise RepositoryAccessError("blob belongs to another scope")
                    raise RepositoryNotFoundError("blob not found")
                return _load(ContentBlob, row["payload"])
            finally:
                connection.close()

        return await self._run(operation)

    async def list_content_blob_metadata(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
    ) -> list[ContentBlob]:
        return await self._list_models(
            access,
            scope,
            table="content_blobs",
            model_type=ContentBlob,
            clauses=[],
            parameters=[],
            order_by="blob_id",
        )

    async def _get_model(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        table: str,
        id_column: str,
        entity_id: str,
        model_type: type[ModelT],
        include_deleted: bool,
        entity_name: str,
    ) -> ModelT:
        def operation() -> ModelT:
            connection = self.database.connect()
            try:
                self._prepare(connection, access, scope)
                row = self._get_row(
                    connection,
                    table=table,
                    id_column=id_column,
                    scope_id=scope.scope_id,
                    entity_id=entity_id,
                    include_deleted=include_deleted,
                    entity_name=entity_name,
                )
                return _load(model_type, row["payload"])
            finally:
                connection.close()

        return await self._run(operation)

    async def _list_models(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        table: str,
        model_type: type[ModelT],
        clauses: list[str],
        parameters: list[object],
        order_by: str,
    ) -> list[ModelT]:
        def operation() -> list[ModelT]:
            connection = self.database.connect()
            try:
                self._prepare(connection, access, scope)
                conditions = ["scope_id = ?", *clauses]
                rows = connection.execute(
                    "SELECT payload FROM "
                    + table
                    + " WHERE "
                    + " AND ".join(conditions)
                    + " ORDER BY "
                    + order_by,
                    (scope.scope_id, *parameters),
                ).fetchall()
                return [_load(model_type, row["payload"]) for row in rows]
            finally:
                connection.close()

        return await self._run(operation)

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

        def operation() -> DocumentVersion:
            connection = self.database.connect()
            try:
                self._begin(connection)
                self._prepare(connection, access, scope)
                self._get_row(
                    connection,
                    table="documents",
                    id_column="document_id",
                    scope_id=scope.scope_id,
                    entity_id=document_id,
                    include_deleted=False,
                    entity_name="document",
                )
                existing_row = connection.execute(
                    "SELECT payload FROM document_versions "
                    "WHERE scope_id = ? AND document_id = ? AND content_sha256 = ?",
                    (scope.scope_id, document_id, blob.content_sha256),
                ).fetchone()
                if existing_row is not None:
                    connection.commit()
                    return _load(DocumentVersion, existing_row["payload"])
                if supersedes_version_id:
                    row = self._get_row(
                        connection,
                        table="document_versions",
                        id_column="version_id",
                        scope_id=scope.scope_id,
                        entity_id=supersedes_version_id,
                        include_deleted=True,
                        entity_name="version",
                    )
                    superseded = _load(DocumentVersion, row["payload"])
                    if superseded.document_id != document_id:
                        raise InvalidTransitionError(
                            "superseded version belongs to another document"
                        )
                connection.execute(
                    "INSERT OR IGNORE INTO content_blobs "
                    "(scope_id, blob_id, content_sha256, byte_size, media_type, "
                    "storage_ref, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        scope.scope_id,
                        blob.blob_id,
                        blob.content_sha256,
                        blob.byte_size,
                        blob.media_type,
                        blob.storage_ref,
                        _dump(blob),
                    ),
                )
                stored_blob = connection.execute(
                    "SELECT payload FROM content_blobs "
                    "WHERE scope_id = ? AND blob_id = ?",
                    (scope.scope_id, blob.blob_id),
                ).fetchone()
                if stored_blob is None:
                    raise RepositoryConflictError("blob identity conflicts")
                stored_blob_model = _load(ContentBlob, stored_blob["payload"])
                if (
                    stored_blob_model.content_sha256 != blob.content_sha256
                    or stored_blob_model.byte_size != blob.byte_size
                ):
                    raise RepositoryConflictError("blob metadata conflicts")
                row = connection.execute(
                    "SELECT COALESCE(MAX(version_number), 0) AS current "
                    "FROM document_versions WHERE scope_id = ? AND document_id = ?",
                    (scope.scope_id, document_id),
                ).fetchone()
                candidate = DocumentVersion(
                    scope_id=scope.scope_id,
                    document_id=document_id,
                    blob_id=blob.blob_id,
                    content_sha256=blob.content_sha256,
                    version_number=int(row["current"]) + 1,
                    retrieved_at=retrieved_at,
                    published_at=published_at,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    supersedes_version_id=supersedes_version_id,
                    metadata=metadata or {},
                    lifecycle_status=lifecycle_status,
                )
                connection.execute(
                    "INSERT INTO document_versions "
                    "(scope_id, version_id, document_id, blob_id, content_sha256, "
                    "version_number, supersedes_version_id, lifecycle_status, "
                    "soft_deleted_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        scope.scope_id,
                        candidate.version_id,
                        document_id,
                        blob.blob_id,
                        blob.content_sha256,
                        candidate.version_number,
                        supersedes_version_id,
                        candidate.lifecycle_status.value,
                        None,
                        _dump(candidate),
                    ),
                )
                self._record_created(
                    connection,
                    scope.scope_id,
                    "document_version",
                    candidate.version_id,
                    correlation_id,
                    after_status=candidate.lifecycle_status.value,
                )
                connection.commit()
                return candidate
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise RepositoryConflictError("version constraint conflict") from exc
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

        return await self._run(operation)

    async def get_version(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        version_id: str,
        *,
        include_deleted: bool = False,
    ) -> DocumentVersion:
        return await self._get_model(
            access,
            scope,
            "document_versions",
            "version_id",
            version_id,
            DocumentVersion,
            include_deleted,
            "version",
        )

    async def list_versions(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        document_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[DocumentVersion]:
        def operation() -> list[DocumentVersion]:
            connection = self.database.connect()
            try:
                self._prepare(connection, access, scope)
                self._get_row(
                    connection,
                    table="documents",
                    id_column="document_id",
                    scope_id=scope.scope_id,
                    entity_id=document_id,
                    include_deleted=include_deleted,
                    entity_name="document",
                )
                condition = "" if include_deleted else "AND soft_deleted_at IS NULL"
                rows = connection.execute(
                    "SELECT payload FROM document_versions "
                    "WHERE scope_id = ? AND document_id = ? "
                    f"{condition} ORDER BY version_number",
                    (scope.scope_id, document_id),
                ).fetchall()
                return [_load(DocumentVersion, row["payload"]) for row in rows]
            finally:
                connection.close()

        return await self._run(operation)

    async def list_versions_for_scope(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        include_deleted: bool = False,
    ) -> list[DocumentVersion]:
        clauses = [] if include_deleted else ["soft_deleted_at IS NULL"]
        return await self._list_models(
            access,
            scope,
            table="document_versions",
            model_type=DocumentVersion,
            clauses=clauses,
            parameters=[],
            order_by="version_id",
        )

    async def find_by_content_hash(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        content_sha256: str,
        *,
        include_deleted: bool = False,
    ) -> list[DocumentVersion]:
        def operation() -> list[DocumentVersion]:
            connection = self.database.connect()
            try:
                self._prepare(connection, access, scope)
                condition = "" if include_deleted else "AND soft_deleted_at IS NULL"
                rows = connection.execute(
                    "SELECT payload FROM document_versions "
                    "WHERE scope_id = ? AND content_sha256 = ? "
                    f"{condition} ORDER BY version_id",
                    (scope.scope_id, content_sha256.lower()),
                ).fetchall()
                return [_load(DocumentVersion, row["payload"]) for row in rows]
            finally:
                connection.close()

        return await self._run(operation)

    async def add_chunks(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        version_id: str,
        chunks: Sequence[ChunkInput],
        *,
        correlation_id: str = "repository",
    ) -> list[Chunk]:
        candidates = [
            Chunk(
                **item.model_dump(),
                scope_id=scope.scope_id,
                version_id=version_id,
            )
            for item in chunks
        ]
        if len({item.ordinal for item in candidates}) != len(candidates):
            raise RepositoryConflictError("duplicate chunk ordinal in batch")

        def operation() -> list[Chunk]:
            connection = self.database.connect()
            try:
                self._begin(connection)
                self._prepare(connection, access, scope)
                self._get_row(
                    connection,
                    table="document_versions",
                    id_column="version_id",
                    scope_id=scope.scope_id,
                    entity_id=version_id,
                    include_deleted=False,
                    entity_name="version",
                )
                results: list[Chunk] = []
                for candidate in candidates:
                    existing_ordinal = connection.execute(
                        "SELECT payload FROM chunks "
                        "WHERE scope_id = ? AND version_id = ? AND ordinal = ?",
                        (scope.scope_id, version_id, candidate.ordinal),
                    ).fetchone()
                    if existing_ordinal is not None:
                        existing = _load(Chunk, existing_ordinal["payload"])
                        if existing.chunk_id != candidate.chunk_id:
                            raise RepositoryConflictError(
                                "chunk ordinal already has different immutable content"
                            )
                        results.append(existing)
                        continue
                    connection.execute(
                        "INSERT INTO chunks "
                        "(scope_id, chunk_id, version_id, ordinal, text_sha256, "
                        "locator_key, soft_deleted_at, payload) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            scope.scope_id,
                            candidate.chunk_id,
                            version_id,
                            candidate.ordinal,
                            candidate.text_sha256,
                            candidate.locator_key(),
                            None,
                            _dump(candidate),
                        ),
                    )
                    self._record_created(
                        connection,
                        scope.scope_id,
                        "chunk",
                        candidate.chunk_id,
                        correlation_id,
                    )
                    results.append(candidate)
                connection.commit()
                return sorted(results, key=lambda item: (item.ordinal, item.chunk_id))
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise RepositoryConflictError("chunk constraint conflict") from exc
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

        return await self._run(operation)

    async def get_chunk(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        chunk_id: str,
        *,
        include_deleted: bool = False,
    ) -> Chunk:
        return await self._get_model(
            access,
            scope,
            "chunks",
            "chunk_id",
            chunk_id,
            Chunk,
            include_deleted,
            "chunk",
        )

    async def list_chunks_for_version(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        version_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Chunk]:
        await self.get_version(
            access, scope, version_id, include_deleted=include_deleted
        )
        clauses = ["version_id = ?"]
        if not include_deleted:
            clauses.append("soft_deleted_at IS NULL")
        return await self._list_models(
            access,
            scope,
            table="chunks",
            model_type=Chunk,
            clauses=clauses,
            parameters=[version_id],
            order_by="ordinal, chunk_id",
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
        candidate = Requirement(
            scope_id=scope.scope_id,
            run_id=run_id,
            template_id=template_id,
            text=text,
            acceptance_hint=acceptance_hint,
            priority=priority,
            parent_id=parent_id,
        )

        def operation() -> Requirement:
            connection = self.database.connect()
            try:
                self._begin(connection)
                self._prepare(connection, access, scope)
                if parent_id:
                    self._get_row(
                        connection,
                        table="requirements",
                        id_column="requirement_id",
                        scope_id=scope.scope_id,
                        entity_id=parent_id,
                        include_deleted=False,
                        entity_name="requirement",
                    )
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO requirements "
                    "(scope_id, requirement_id, parent_id, run_id, status, "
                    "soft_deleted_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        scope.scope_id,
                        candidate.requirement_id,
                        parent_id,
                        run_id,
                        candidate.status.value,
                        None,
                        _dump(candidate),
                    ),
                )
                row = connection.execute(
                    "SELECT payload FROM requirements "
                    "WHERE scope_id = ? AND requirement_id = ?",
                    (scope.scope_id, candidate.requirement_id),
                ).fetchone()
                stored = _load(Requirement, row["payload"])
                if cursor.rowcount == 1:
                    self._record_created(
                        connection,
                        scope.scope_id,
                        "requirement",
                        candidate.requirement_id,
                        correlation_id,
                        after_status=candidate.status.value,
                    )
                connection.commit()
                return stored
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise RepositoryConflictError("requirement constraint conflict") from exc
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

        return await self._run(operation)

    async def get_requirement(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        requirement_id: str,
        *,
        include_deleted: bool = False,
    ) -> Requirement:
        return await self._get_model(
            access,
            scope,
            "requirements",
            "requirement_id",
            requirement_id,
            Requirement,
            include_deleted,
            "requirement",
        )

    async def list_requirements(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        run_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[Requirement]:
        def operation() -> list[Requirement]:
            connection = self.database.connect()
            try:
                self._prepare(connection, access, scope)
                clauses = ["scope_id = ?"]
                parameters: list[str] = [scope.scope_id]
                if run_id is not None:
                    clauses.append("run_id = ?")
                    parameters.append(run_id)
                if not include_deleted:
                    clauses.append("soft_deleted_at IS NULL")
                rows = connection.execute(
                    "SELECT payload FROM requirements WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY requirement_id",
                    parameters,
                ).fetchall()
                return [_load(Requirement, row["payload"]) for row in rows]
            finally:
                connection.close()

        return await self._run(operation)

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
        def operation() -> Requirement:
            connection = self.database.connect()
            try:
                self._begin(connection)
                self._prepare(connection, access, scope)
                row = self._get_row(
                    connection,
                    table="requirements",
                    id_column="requirement_id",
                    scope_id=scope.scope_id,
                    entity_id=requirement_id,
                    include_deleted=False,
                    entity_name="requirement",
                )
                current = _load(Requirement, row["payload"])
                if current.status is status:
                    connection.commit()
                    return current
                updated = current.model_copy(
                    update={"status": status, "updated_at": utc_now()}
                )
                connection.execute(
                    "UPDATE requirements SET status = ?, payload = ? "
                    "WHERE scope_id = ? AND requirement_id = ?",
                    (
                        status.value,
                        _dump(updated),
                        scope.scope_id,
                        requirement_id,
                    ),
                )
                self._insert_audit(
                    connection,
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
                    ),
                )
                connection.commit()
                return updated
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

        return await self._run(operation)

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

        def operation() -> Evidence:
            connection = self.database.connect()
            try:
                self._begin(connection)
                self._prepare(connection, access, scope)
                self._get_row(
                    connection,
                    table="chunks",
                    id_column="chunk_id",
                    scope_id=scope.scope_id,
                    entity_id=chunk_id,
                    include_deleted=False,
                    entity_name="chunk",
                )
                if requirement_id:
                    self._get_row(
                        connection,
                        table="requirements",
                        id_column="requirement_id",
                        scope_id=scope.scope_id,
                        entity_id=requirement_id,
                        include_deleted=False,
                        entity_name="requirement",
                    )
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO evidence "
                    "(scope_id, evidence_id, chunk_id, requirement_id, "
                    "validation_status, soft_deleted_at, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        scope.scope_id,
                        candidate.evidence_id,
                        chunk_id,
                        requirement_id,
                        candidate.validation_status.value,
                        None,
                        _dump(candidate),
                    ),
                )
                row = connection.execute(
                    "SELECT payload FROM evidence "
                    "WHERE scope_id = ? AND evidence_id = ?",
                    (scope.scope_id, candidate.evidence_id),
                ).fetchone()
                stored = _load(Evidence, row["payload"])
                if cursor.rowcount == 1:
                    self._record_created(
                        connection,
                        scope.scope_id,
                        "evidence",
                        candidate.evidence_id,
                        correlation_id,
                        after_status=candidate.validation_status.value,
                    )
                connection.commit()
                return stored
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise RepositoryConflictError("evidence constraint conflict") from exc
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

        return await self._run(operation)

    async def get_evidence(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        evidence_id: str,
        *,
        include_deleted: bool = False,
    ) -> Evidence:
        return await self._get_model(
            access,
            scope,
            "evidence",
            "evidence_id",
            evidence_id,
            Evidence,
            include_deleted,
            "evidence",
        )

    async def _list_evidence_query(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        sql: str,
        parameters: tuple,
    ) -> list[Evidence]:
        def operation() -> list[Evidence]:
            connection = self.database.connect()
            try:
                self._prepare(connection, access, scope)
                rows = connection.execute(sql, parameters).fetchall()
                return [_load(Evidence, row["payload"]) for row in rows]
            finally:
                connection.close()

        return await self._run(operation)

    async def list_evidence_for_requirement(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        requirement_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Evidence]:
        await self.get_requirement(
            access, scope, requirement_id, include_deleted=include_deleted
        )
        condition = "" if include_deleted else "AND soft_deleted_at IS NULL"
        return await self._list_evidence_query(
            access,
            scope,
            "SELECT payload FROM evidence "
            "WHERE scope_id = ? AND requirement_id = ? "
            f"{condition} ORDER BY evidence_id",
            (scope.scope_id, requirement_id),
        )

    async def list_evidence_for_chunk(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        chunk_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Evidence]:
        await self.get_chunk(access, scope, chunk_id, include_deleted=include_deleted)
        condition = "" if include_deleted else "AND soft_deleted_at IS NULL"
        return await self._list_evidence_query(
            access,
            scope,
            "SELECT payload FROM evidence WHERE scope_id = ? AND chunk_id = ? "
            f"{condition} ORDER BY evidence_id",
            (scope.scope_id, chunk_id),
        )

    async def list_evidence_for_source(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        source_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Evidence]:
        await self.get_source(access, scope, source_id, include_deleted=include_deleted)
        condition = (
            ""
            if include_deleted
            else (
                "AND e.soft_deleted_at IS NULL "
                "AND c.soft_deleted_at IS NULL "
                "AND v.soft_deleted_at IS NULL "
                "AND d.soft_deleted_at IS NULL"
            )
        )
        return await self._list_evidence_query(
            access,
            scope,
            "SELECT e.payload FROM evidence e "
            "JOIN chunks c ON c.scope_id = e.scope_id AND c.chunk_id = e.chunk_id "
            "JOIN document_versions v "
            "ON v.scope_id = c.scope_id AND v.version_id = c.version_id "
            "JOIN documents d "
            "ON d.scope_id = v.scope_id AND d.document_id = v.document_id "
            "WHERE e.scope_id = ? AND d.source_id = ? "
            f"{condition} ORDER BY e.evidence_id",
            (scope.scope_id, source_id),
        )

    @staticmethod
    def _get_import_job_row(
        connection: sqlite3.Connection,
        scope_id: str,
        job_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT payload FROM import_jobs WHERE scope_id = ? AND job_id = ?",
            (scope_id, job_id),
        ).fetchone()
        if row is None:
            other = connection.execute(
                "SELECT 1 FROM import_jobs WHERE job_id = ? LIMIT 1",
                (job_id,),
            ).fetchone()
            if other is not None:
                raise RepositoryAccessError("import job belongs to another scope")
            raise RepositoryNotFoundError("import job not found")
        return row

    @classmethod
    def _validate_import_job_references(
        cls,
        connection: sqlite3.Connection,
        scope_id: str,
        job: ImportJob,
    ) -> None:
        blob = source = document = version = None
        if job.blob_id is not None:
            row = connection.execute(
                "SELECT payload FROM content_blobs "
                "WHERE scope_id = ? AND blob_id = ?",
                (scope_id, job.blob_id),
            ).fetchone()
            if row is None:
                other = connection.execute(
                    "SELECT 1 FROM content_blobs WHERE blob_id = ? LIMIT 1",
                    (job.blob_id,),
                ).fetchone()
                if other is not None:
                    raise RepositoryAccessError("blob belongs to another scope")
                raise RepositoryNotFoundError("blob not found")
            blob = _load(ContentBlob, row["payload"])
            if blob.content_sha256 != job.content_sha256:
                raise RepositoryConflictError("import job blob hash does not match")
        if job.source_id is not None:
            row = cls._get_row(
                connection,
                table="sources",
                id_column="source_id",
                scope_id=scope_id,
                entity_id=job.source_id,
                include_deleted=False,
                entity_name="source",
            )
            source = _load(Source, row["payload"])
        if job.document_id is not None:
            row = cls._get_row(
                connection,
                table="documents",
                id_column="document_id",
                scope_id=scope_id,
                entity_id=job.document_id,
                include_deleted=False,
                entity_name="document",
            )
            document = _load(Document, row["payload"])
        if job.version_id is not None:
            row = cls._get_row(
                connection,
                table="document_versions",
                id_column="version_id",
                scope_id=scope_id,
                entity_id=job.version_id,
                include_deleted=False,
                entity_name="version",
            )
            version = _load(DocumentVersion, row["payload"])
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

        def operation() -> ImportJob:
            connection = self.database.connect()
            try:
                self._begin(connection)
                self._prepare(connection, access, scope)
                self._validate_import_job_references(
                    connection, scope.scope_id, job
                )
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO import_jobs "
                    "(scope_id, job_id, input_kind, input_ref, content_sha256, "
                    "parser_name, parser_version, chunk_config_sha256, status, "
                    "index_status, attempt_count, blob_id, source_id, document_id, "
                    "version_id, updated_at, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        scope.scope_id,
                        job.job_id,
                        job.input_kind.value,
                        job.input_ref,
                        job.content_sha256,
                        job.parser_name,
                        job.parser_version,
                        job.chunk_config_sha256,
                        job.status.value,
                        job.index_status.value,
                        job.attempt_count,
                        job.blob_id,
                        job.source_id,
                        job.document_id,
                        job.version_id,
                        job.updated_at.isoformat(),
                        _dump(job),
                    ),
                )
                row = connection.execute(
                    "SELECT payload FROM import_jobs "
                    "WHERE scope_id = ? AND job_id = ?",
                    (scope.scope_id, job.job_id),
                ).fetchone()
                if row is None:
                    raise RepositoryConflictError("import job identity conflicts")
                stored = _load(ImportJob, row["payload"])
                if cursor.rowcount == 1:
                    self._record_created(
                        connection,
                        scope.scope_id,
                        "import_job",
                        job.job_id,
                        correlation_id,
                        after_status=f"{job.status.value}:{job.index_status.value}",
                    )
                connection.commit()
                return stored
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise RepositoryConflictError("import job constraint conflict") from exc
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

        return await self._run(operation)

    async def get_import_job(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        job_id: str,
    ) -> ImportJob:
        def operation() -> ImportJob:
            connection = self.database.connect()
            try:
                self._prepare(connection, access, scope)
                row = self._get_import_job_row(connection, scope.scope_id, job_id)
                return _load(ImportJob, row["payload"])
            finally:
                connection.close()

        return await self._run(operation)

    async def list_import_jobs(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        status: ImportJobStatus | None = None,
        index_status: ImportIndexStatus | None = None,
    ) -> list[ImportJob]:
        clauses: list[str] = []
        parameters: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status.value)
        if index_status is not None:
            clauses.append("index_status = ?")
            parameters.append(index_status.value)
        return await self._list_models(
            access,
            scope,
            table="import_jobs",
            model_type=ImportJob,
            clauses=clauses,
            parameters=parameters,
            order_by="job_id",
        )

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

        def operation() -> ImportJob:
            connection = self.database.connect()
            try:
                self._begin(connection)
                self._prepare(connection, access, scope)
                row = self._get_import_job_row(connection, scope.scope_id, job_id)
                current = _load(ImportJob, row["payload"])
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
                    connection.commit()
                    return current
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
                self._validate_import_job_references(
                    connection, scope.scope_id, updated
                )
                cursor = connection.execute(
                    "UPDATE import_jobs SET status = ?, index_status = ?, "
                    "attempt_count = ?, blob_id = ?, source_id = ?, document_id = ?, "
                    "version_id = ?, updated_at = ?, payload = ? "
                    "WHERE scope_id = ? AND job_id = ? AND status = ? "
                    "AND index_status = ?",
                    (
                        updated.status.value,
                        updated.index_status.value,
                        updated.attempt_count,
                        updated.blob_id,
                        updated.source_id,
                        updated.document_id,
                        updated.version_id,
                        updated.updated_at.isoformat(),
                        _dump(updated),
                        scope.scope_id,
                        job_id,
                        current.status.value,
                        current.index_status.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RepositoryConflictError("import job CAS update failed")
                self._insert_audit(
                    connection,
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
                        after_status=(
                            f"{updated.status.value}:{updated.index_status.value}"
                        ),
                        correlation_id=correlation_id,
                    ),
                )
                connection.commit()
                return updated
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise RepositoryConflictError("import job constraint conflict") from exc
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

        return await self._run(operation)

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
        targets: dict[str, tuple[str, str, type[BaseModel]]] = {
            "source": ("sources", "source_id", Source),
            "document": ("documents", "document_id", Document),
            "document_version": (
                "document_versions",
                "version_id",
                DocumentVersion,
            ),
            "chunk": ("chunks", "chunk_id", Chunk),
            "requirement": ("requirements", "requirement_id", Requirement),
            "evidence": ("evidence", "evidence_id", Evidence),
        }
        target = targets.get(entity_type)
        if target is None:
            raise InvalidTransitionError(f"unsupported soft-delete type: {entity_type}")
        table, id_column, model_type = target

        def operation() -> AuditEvent:
            connection = self.database.connect()
            try:
                self._begin(connection)
                self._prepare(connection, access, scope)
                row = self._get_row(
                    connection,
                    table=table,
                    id_column=id_column,
                    scope_id=scope.scope_id,
                    entity_id=entity_id,
                    include_deleted=True,
                    entity_name=entity_type,
                )
                current = _load(model_type, row["payload"])
                if current.soft_deleted_at is not None:
                    audit_row = connection.execute(
                        "SELECT payload FROM audit_events "
                        "WHERE scope_id = ? AND entity_type = ? AND entity_id = ? "
                        "AND action = 'soft_deleted' "
                        "ORDER BY created_at DESC, event_id DESC LIMIT 1",
                        (scope.scope_id, entity_type, entity_id),
                    ).fetchone()
                    if audit_row is None:
                        raise RepositoryConflictError(
                            "entity is deleted without an audit event"
                        )
                    connection.commit()
                    return _load(AuditEvent, audit_row["payload"])
                deleted_at = utc_now()
                updated = current.model_copy(update={"soft_deleted_at": deleted_at})
                connection.execute(
                    f"UPDATE {table} SET soft_deleted_at = ?, payload = ? "
                    f"WHERE scope_id = ? AND {id_column} = ?",
                    (
                        deleted_at.isoformat(),
                        _dump(updated),
                        scope.scope_id,
                        entity_id,
                    ),
                )
                event = AuditEvent(
                    scope_id=scope.scope_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    action="soft_deleted",
                    actor_type=actor_type,
                    reason=reason,
                    correlation_id=correlation_id,
                )
                self._insert_audit(connection, event)
                connection.commit()
                return event
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

        return await self._run(operation)

    async def append_audit(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        event: AuditEvent,
    ) -> AuditEvent:
        if event.scope_id != scope.scope_id:
            raise RepositoryAccessError("audit event belongs to another scope")

        def operation() -> AuditEvent:
            connection = self.database.connect()
            try:
                self._begin(connection)
                self._prepare(connection, access, scope)
                stored = self._insert_audit(connection, event)
                connection.commit()
                return stored
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

        return await self._run(operation)

    async def list_audit_for_entity(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        entity_type: str,
        entity_id: str,
    ) -> list[AuditEvent]:
        return await self._list_audit(
            access,
            scope,
            "entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        )

    async def list_audit_for_correlation(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        correlation_id: str,
    ) -> list[AuditEvent]:
        return await self._list_audit(
            access, scope, "correlation_id = ?", (correlation_id,)
        )

    async def _list_audit(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        condition: str,
        parameters: tuple,
    ) -> list[AuditEvent]:
        def operation() -> list[AuditEvent]:
            connection = self.database.connect()
            try:
                self._prepare(connection, access, scope)
                rows = connection.execute(
                    "SELECT payload FROM audit_events WHERE scope_id = ? AND "
                    + condition
                    + " ORDER BY created_at, event_id",
                    (scope.scope_id, *parameters),
                ).fetchall()
                return [_load(AuditEvent, row["payload"]) for row in rows]
            finally:
                connection.close()

        return await self._run(operation)

    def entity_counts(
        self, access: KnowledgeAccessContext, scope: KnowledgeScope
    ) -> dict[str, int]:
        """Return non-sensitive counts used by backend contract tests."""
        authorize_scope(access, scope)
        scope_id = scope.scope_id
        connection = self.database.connect()
        try:
            tables = (
                "sources",
                "documents",
                "content_blobs",
                "document_versions",
                "chunks",
                "requirements",
                "evidence",
            )
            values = {
                table: int(
                    connection.execute(
                        f"SELECT COUNT(*) AS count FROM {table} WHERE scope_id = ?",
                        (scope_id,),
                    ).fetchone()["count"]
                )
                for table in tables
            }
        finally:
            connection.close()
        return {
            "sources": values["sources"],
            "documents": values["documents"],
            "blobs": values["content_blobs"],
            "versions": values["document_versions"],
            "chunks": values["chunks"],
            "requirements": values["requirements"],
            "evidence": values["evidence"],
        }
