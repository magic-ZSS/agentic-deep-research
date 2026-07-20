"""Scope-aware orchestration for deterministic local candidate ingestion."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import Field

from open_deep_research.evidence.models import (
    Evidence,
    EvidenceDirectness,
    EvidenceRelation,
    EvidenceValidationStatus,
)
from open_deep_research.knowledge.ids import sha256_bytes
from open_deep_research.knowledge.ingestion.models import (
    ImportIndexStatus,
    ImportInputKind,
    ImportJob,
    ImportJobError,
    ImportJobStatus,
)
from open_deep_research.knowledge.ingestion.parsers import (
    ChunkingConfig,
    DocumentInput,
    DocumentParseError,
    DocumentParser,
    HtmlSnapshotParser,
    MarkdownParser,
    ParseErrorCode,
    ParsedDocument,
    PastQueryParser,
    PdfParser,
)
from open_deep_research.knowledge.models import (
    Chunk,
    Document,
    DocumentVersion,
    DomainModel,
    KnowledgeAccessContext,
    KnowledgeScope,
    Source,
    SourceKind,
    VersionLifecycleStatus,
    utc_now,
)
from open_deep_research.knowledge.repositories import (
    BlobRepository,
    KnowledgeEvidenceRepository,
    RepositoryConflictError,
)


@runtime_checkable
class KnowledgeIndexer(Protocol):
    """Optional derived-index seam; Repository entities remain authoritative."""

    async def index(
        self,
        *,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        source: Source,
        document: Document,
        version: DocumentVersion,
        chunks: Sequence[Chunk],
    ) -> None: ...


class IngestionResult(DomainModel):
    """Complete persisted import result returned without exposing raw paths."""

    job: ImportJob
    source: Source
    document: Document
    version: DocumentVersion
    chunks: tuple[Chunk, ...]
    evidence: tuple[Evidence, ...]
    warnings: tuple[str, ...] = Field(default_factory=tuple)


class IngestionServiceError(RuntimeError):
    """Import failed after its durable job state was recorded."""

    def __init__(self, message: str, *, job: ImportJob) -> None:
        super().__init__(message)
        self.job = job


class IngestionService:
    """Persist supplied bytes as candidate knowledge without opening their input_ref."""

    def __init__(
        self,
        repository: KnowledgeEvidenceRepository,
        blob_repository: BlobRepository,
        *,
        parsers: Sequence[DocumentParser] | None = None,
        indexer: KnowledgeIndexer | None = None,
    ) -> None:
        self._repository = repository
        self._blob_repository = blob_repository
        self._parsers = tuple(
            parsers
            if parsers is not None
            else (PdfParser(), MarkdownParser(), HtmlSnapshotParser(), PastQueryParser())
        )
        self._indexer = indexer

    async def ingest(
        self,
        document_input: DocumentInput,
        *,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        chunking: ChunkingConfig | None = None,
        retrieved_at: datetime | None = None,
        published_at: datetime | None = None,
    ) -> IngestionResult:
        """Import one already-authorized byte snapshot and return its durable chain."""
        config = chunking or ChunkingConfig()
        parser = self._select_parser(document_input)
        input_kind = self._input_kind(document_input, parser)
        job = ImportJob(
            scope_id=scope.scope_id,
            input_kind=input_kind,
            input_ref=document_input.input_ref,
            content_sha256=sha256_bytes(document_input.raw_bytes),
            parser_name=parser.name if parser is not None else "unsupported",
            parser_version=parser.version if parser is not None else "0",
            chunk_config=config.model_dump(mode="json"),
        )

        try:
            blob = await self._blob_repository.put(
                access,
                scope,
                document_input.raw_bytes,
                document_input.normalized_media_type,
            )
        except Exception as exc:
            failed = await self._record_preclaim_failure(
                access,
                scope,
                job,
                ImportJobError(
                    code="blob_write_failed",
                    stage="blob",
                    message=self._safe_message(exc, "immutable blob write failed"),
                    retryable=True,
                ),
            )
            raise IngestionServiceError("immutable blob write failed", job=failed) from exc

        stored = await self._repository.create_import_job(access, scope, job)
        if stored.status is ImportJobStatus.SUCCEEDED:
            result = await self.load_result(
                stored.job_id, access=access, scope=scope
            )
            return await self._ensure_index(result, access=access, scope=scope)
        if stored.status is ImportJobStatus.RUNNING:
            raise RepositoryConflictError("import job is already running")
        if stored.status is ImportJobStatus.FAILED:
            if stored.error is not None and not stored.error.retryable:
                raise IngestionServiceError(
                    "import job failed permanently", job=stored
                )
            running = await self._repository.transition_import_job(
                access,
                scope,
                stored.job_id,
                expected_status=ImportJobStatus.FAILED,
                status=ImportJobStatus.RUNNING,
                actor_type="ingestion_service",
                reason="retry failed import",
                correlation_id=stored.job_id,
            )
        else:
            running = await self._repository.transition_import_job(
                access,
                scope,
                stored.job_id,
                expected_status=ImportJobStatus.PENDING,
                status=ImportJobStatus.RUNNING,
                actor_type="ingestion_service",
                reason="claim import job",
                correlation_id=stored.job_id,
            )

        try:
            if parser is None:
                raise DocumentParseError(
                    ParseErrorCode.UNSUPPORTED_FORMAT,
                    "unsupported",
                    "no local parser supports the supplied media type and suffix",
                )
            parsed = parser.parse(document_input, config)
            self._validate_past_query_scope(parsed, input_kind, scope)
            source = await self._repository.upsert_source(
                access,
                scope,
                kind=document_input.source_kind,
                display_name=document_input.display_name,
                canonical_uri=parsed.canonical_uri or document_input.canonical_uri,
                internal_storage_ref=document_input.input_ref,
                public_display_uri=parsed.canonical_uri or document_input.canonical_uri,
                correlation_id=running.job_id,
            )
            document = await self._repository.upsert_document(
                access,
                scope,
                source_id=source.source_id,
                logical_key="main",
                title=parsed.title or document_input.display_name,
                media_type=parsed.media_type,
                correlation_id=running.job_id,
            )
            version = await self._repository.add_version(
                access,
                scope,
                document_id=document.document_id,
                blob=blob,
                retrieved_at=retrieved_at or utc_now(),
                published_at=published_at,
                metadata={
                    "parser_name": parsed.parser_name,
                    "parser_version": parsed.parser_version,
                    "chunk_config": config.model_dump(mode="json"),
                    "chunk_config_sha256": running.chunk_config_sha256,
                    "parser_metadata": parsed.metadata,
                },
                lifecycle_status=VersionLifecycleStatus.CANDIDATE,
                correlation_id=running.job_id,
            )
            if version.lifecycle_status is not VersionLifecycleStatus.CANDIDATE:
                raise RepositoryConflictError(
                    "ingestion cannot reuse a non-candidate DocumentVersion"
                )
            chunks = tuple(
                await self._repository.add_chunks(
                    access,
                    scope,
                    version.version_id,
                    parsed.chunk_inputs(),
                    correlation_id=running.job_id,
                )
            )
            evidence = tuple(
                [
                    await self._repository.add_evidence(
                        access,
                        scope,
                        chunk_id=chunk.chunk_id,
                        excerpt=chunk.text,
                        confidence=0.0,
                        retrieval_method=f"candidate_import:{parsed.parser_name}",
                        relation=EvidenceRelation.CONTEXT,
                        directness=EvidenceDirectness.UNKNOWN,
                        validation_status=EvidenceValidationStatus.PENDING,
                        correlation_id=running.job_id,
                    )
                    for chunk in chunks
                ]
            )
            succeeded = await self._repository.transition_import_job(
                access,
                scope,
                running.job_id,
                expected_status=ImportJobStatus.RUNNING,
                status=ImportJobStatus.SUCCEEDED,
                blob_id=blob.blob_id,
                source_id=source.source_id,
                document_id=document.document_id,
                version_id=version.version_id,
                actor_type="ingestion_service",
                reason="candidate import persisted",
                correlation_id=running.job_id,
            )
        except Exception as exc:
            error = self._structured_error(exc)
            failed = await self._repository.transition_import_job(
                access,
                scope,
                running.job_id,
                expected_status=ImportJobStatus.RUNNING,
                status=ImportJobStatus.FAILED,
                error=error,
                actor_type="ingestion_service",
                reason=f"import failed at {error.stage}",
                correlation_id=running.job_id,
            )
            raise IngestionServiceError("candidate import failed", job=failed) from exc

        result = IngestionResult(
            job=succeeded,
            source=source,
            document=document,
            version=version,
            chunks=chunks,
            evidence=evidence,
        )
        return await self._ensure_index(result, access=access, scope=scope)

    async def load_result(
        self,
        job_id: str,
        *,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
    ) -> IngestionResult:
        """Rehydrate a successful import entirely from authoritative repositories."""
        job = await self._repository.get_import_job(access, scope, job_id)
        if job.status is not ImportJobStatus.SUCCEEDED:
            raise IngestionServiceError("import job is not successful", job=job)
        assert job.source_id is not None
        assert job.document_id is not None
        assert job.version_id is not None
        source = await self._repository.get_source(access, scope, job.source_id)
        document = await self._repository.get_document(
            access, scope, job.document_id
        )
        version = await self._repository.get_version(access, scope, job.version_id)
        chunks = tuple(
            await self._repository.list_chunks_for_version(
                access, scope, version.version_id
            )
        )
        evidence: list[Evidence] = []
        for chunk in chunks:
            evidence.extend(
                await self._repository.list_evidence_for_chunk(
                    access, scope, chunk.chunk_id
                )
            )
        return IngestionResult(
            job=job,
            source=source,
            document=document,
            version=version,
            chunks=chunks,
            evidence=tuple(evidence),
        )

    async def _ensure_index(
        self,
        result: IngestionResult,
        *,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
    ) -> IngestionResult:
        if self._indexer is None or result.job.index_status is ImportIndexStatus.READY:
            return result
        if result.job.index_status is ImportIndexStatus.PENDING:
            raise RepositoryConflictError("derived index is already being built")
        expected_index = result.job.index_status
        pending = await self._repository.transition_import_job(
            access,
            scope,
            result.job.job_id,
            expected_status=ImportJobStatus.SUCCEEDED,
            status=ImportJobStatus.SUCCEEDED,
            expected_index_status=expected_index,
            index_status=ImportIndexStatus.PENDING,
            actor_type="ingestion_service",
            reason="build derived index",
            correlation_id=result.job.job_id,
        )
        try:
            await self._indexer.index(
                access=access,
                scope=scope,
                source=result.source,
                document=result.document,
                version=result.version,
                chunks=result.chunks,
            )
        except Exception as exc:
            failed = await self._repository.transition_import_job(
                access,
                scope,
                pending.job_id,
                expected_status=ImportJobStatus.SUCCEEDED,
                status=ImportJobStatus.SUCCEEDED,
                expected_index_status=ImportIndexStatus.PENDING,
                index_status=ImportIndexStatus.FAILED,
                error=ImportJobError(
                    code="index_failed",
                    stage="index",
                    message=self._safe_message(exc, "derived index build failed"),
                    retryable=True,
                ),
                actor_type="ingestion_service",
                reason="derived index failed",
                correlation_id=pending.job_id,
            )
            raise IngestionServiceError("derived index build failed", job=failed) from exc
        ready = await self._repository.transition_import_job(
            access,
            scope,
            pending.job_id,
            expected_status=ImportJobStatus.SUCCEEDED,
            status=ImportJobStatus.SUCCEEDED,
            expected_index_status=ImportIndexStatus.PENDING,
            index_status=ImportIndexStatus.READY,
            actor_type="ingestion_service",
            reason="derived index ready",
            correlation_id=pending.job_id,
        )
        return result.model_copy(update={"job": ready})

    async def _record_preclaim_failure(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        job: ImportJob,
        error: ImportJobError,
    ) -> ImportJob:
        stored = await self._repository.create_import_job(access, scope, job)
        if stored.status is ImportJobStatus.FAILED:
            return stored
        if stored.status is not ImportJobStatus.PENDING:
            return stored
        return await self._repository.transition_import_job(
            access,
            scope,
            stored.job_id,
            expected_status=ImportJobStatus.PENDING,
            status=ImportJobStatus.FAILED,
            error=error,
            actor_type="ingestion_service",
            reason=f"import failed at {error.stage}",
            correlation_id=stored.job_id,
        )

    def _select_parser(self, document_input: DocumentInput) -> DocumentParser | None:
        matches = tuple(
            parser
            for parser in self._parsers
            if parser.supports(
                document_input.normalized_media_type, document_input.suffix
            )
        )
        if len(matches) > 1:
            raise RepositoryConflictError("multiple parsers claim the same input")
        return matches[0] if matches else None

    @staticmethod
    def _input_kind(
        document_input: DocumentInput, parser: DocumentParser | None
    ) -> ImportInputKind:
        if document_input.source_kind is SourceKind.PAST_QUERY:
            return ImportInputKind.PAST_QUERY
        parser_name = parser.name if parser is not None else ""
        if parser_name == PdfParser.name:
            return ImportInputKind.PDF
        if parser_name == MarkdownParser.name:
            return ImportInputKind.MARKDOWN
        if parser_name == HtmlSnapshotParser.name:
            return ImportInputKind.HTML_SNAPSHOT
        if parser_name == PastQueryParser.name:
            return ImportInputKind.PAST_QUERY
        suffix = document_input.suffix
        if suffix == ".pdf":
            return ImportInputKind.PDF
        if suffix in {".md", ".markdown"}:
            return ImportInputKind.MARKDOWN
        if suffix in {".html", ".htm"}:
            return ImportInputKind.HTML_SNAPSHOT
        return ImportInputKind.PAST_QUERY

    @staticmethod
    def _validate_past_query_scope(
        parsed: ParsedDocument,
        input_kind: ImportInputKind,
        scope: KnowledgeScope,
    ) -> None:
        if input_kind is not ImportInputKind.PAST_QUERY:
            return
        expected = {
            "tenant_id": scope.tenant_id,
            "project_id": scope.project_id,
            "owner_user_id": scope.owner_user_id,
            "visibility": scope.visibility.value,
        }
        for chunk in parsed.chunks:
            record_scope = chunk.metadata.get("scope")
            if not isinstance(record_scope, dict) or any(
                record_scope.get(name) != value
                for name, value in expected.items()
                if value is not None or name in {"tenant_id", "project_id", "visibility"}
            ):
                raise DocumentParseError(
                    ParseErrorCode.MISSING_SCOPE,
                    parsed.parser_name,
                    "past-query record scope does not match the import scope",
                )

    @staticmethod
    def _structured_error(exc: Exception) -> ImportJobError:
        if isinstance(exc, DocumentParseError):
            retryable = exc.code is ParseErrorCode.UNSUPPORTED_FORMAT
            return ImportJobError(
                code=exc.code.value,
                stage="parse",
                message=IngestionService._safe_message(exc, "document parse failed"),
                retryable=retryable,
            )
        return ImportJobError(
            code=type(exc).__name__.casefold(),
            stage="persist",
            message=IngestionService._safe_message(exc, "candidate persistence failed"),
            retryable=True,
        )

    @staticmethod
    def _safe_message(exc: Exception, fallback: str) -> str:
        message = str(exc).strip()
        return message[:1_000] if message else fallback


__all__ = [
    "IngestionResult",
    "IngestionService",
    "IngestionServiceError",
    "KnowledgeIndexer",
]
