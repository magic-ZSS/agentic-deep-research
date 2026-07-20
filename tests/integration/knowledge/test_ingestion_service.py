from __future__ import annotations

import asyncio
from pathlib import Path

import pymupdf
import pytest

from open_deep_research.evidence.models import (
    EvidenceRelation,
    EvidenceValidationStatus,
)
from open_deep_research.knowledge.ingestion.models import (
    ImportIndexStatus,
    ImportJobStatus,
)
from open_deep_research.knowledge.ingestion.parsers import (
    ChunkingConfig,
    DocumentInput,
    DocumentParseError,
    MarkdownParser,
    ParseErrorCode,
)
from open_deep_research.knowledge.ingestion.service import (
    IngestionService,
    IngestionServiceError,
)
from open_deep_research.knowledge.models import (
    ChunkLocatorType,
    KnowledgeAccessContext,
    KnowledgeScope,
    SourceKind,
    VersionLifecycleStatus,
)
from open_deep_research.knowledge.retrieval.models import (
    KnowledgeReadRequest,
    KnowledgeSearchRequest,
)
from open_deep_research.knowledge.retrieval.repository_retriever import (
    RepositoryKnowledgeRetriever,
    RepositoryRetrievalCatalog,
)
from open_deep_research.knowledge.sqlite_repository import SQLiteRepository
from open_deep_research.storage.blob_repository import LocalBlobRepository


FIXTURES = Path(__file__).parents[2] / "fixtures" / "knowledge"


def _identity():
    scope = KnowledgeScope(tenant_id="tenant-a", project_id="project-a")
    access = KnowledgeAccessContext(
        trusted_tenant_id="tenant-a",
        trusted_project_id="project-a",
        auth_source="phase2-ingestion-test",
        request_id="phase2-ingestion",
    )
    return scope, access


def _pdf_bytes(*pages: str) -> bytes:
    document = pymupdf.open()
    for value in pages:
        page = document.new_page()
        page.insert_textbox(page.rect + (36, 36, -36, -36), value, fontsize=11)
    raw = document.tobytes()
    document.close()
    return raw


def _input(
    raw_bytes: bytes,
    *,
    input_ref: str,
    media_type: str,
    source_kind: SourceKind = SourceKind.LOCAL_FILE,
    canonical_uri: str | None = None,
) -> DocumentInput:
    return DocumentInput(
        source_kind=source_kind,
        media_type=media_type,
        input_ref=input_ref,
        display_name=Path(input_ref.replace("\\", "/")).name,
        canonical_uri=canonical_uri,
        raw_bytes=raw_bytes,
    )


def _service(tmp_path, *, parsers=None, indexer=None):
    repository = SQLiteRepository(str(tmp_path / "knowledge.db"))
    blobs = LocalBlobRepository(tmp_path / "blobs")
    return repository, blobs, IngestionService(
        repository, blobs, parsers=parsers, indexer=indexer
    )


def test_four_formats_persist_candidate_chunks_and_pending_context_evidence(tmp_path):
    async def scenario():
        scope, access = _identity()
        repository, blobs, service = _service(tmp_path)
        inputs = (
            _input(
                _pdf_bytes("First page evidence", "Second page evidence"),
                input_ref=r"C:\authorized\paper.pdf",
                media_type="application/pdf",
            ),
            _input(
                (FIXTURES / "sample.md").read_bytes(),
                input_ref=r"C:\authorized\sample.md",
                media_type="text/markdown",
            ),
            _input(
                (FIXTURES / "sample.html").read_bytes(),
                input_ref=r"C:\authorized\sample.html",
                media_type="text/html",
                source_kind=SourceKind.WEB,
            ),
            _input(
                (FIXTURES / "past_queries.json").read_bytes(),
                input_ref=r"C:\authorized\past_queries.json",
                media_type="application/json",
                source_kind=SourceKind.PAST_QUERY,
            ),
        )
        results = [
            await service.ingest(
                item,
                access=access,
                scope=scope,
                chunking=ChunkingConfig(max_chars=1_000, overlap=0),
            )
            for item in inputs
        ]
        assert {result.job.status for result in results} == {
            ImportJobStatus.SUCCEEDED
        }
        assert {result.job.index_status for result in results} == {
            ImportIndexStatus.NOT_REQUESTED
        }
        assert all(
            result.version.lifecycle_status is VersionLifecycleStatus.CANDIDATE
            for result in results
        )
        for result in results:
            assert len(result.evidence) == len(result.chunks) > 0
            assert all(
                item.validation_status is EvidenceValidationStatus.PENDING
                and item.relation is EvidenceRelation.CONTEXT
                and item.confidence == 0
                for item in result.evidence
            )
            assert await blobs.verify(
                access,
                scope,
                result.job.blob_id,
                result.version.content_sha256,
            )
        assert all(
            chunk.locator_type is ChunkLocatorType.PAGE
            for chunk in results[0].chunks
        )
        assert any(chunk.heading_path for chunk in results[1].chunks)
        assert any(chunk.anchor for chunk in results[2].chunks)
        assert all(
            chunk.metadata["record_id"] == "query-record-001"
            for chunk in results[3].chunks
        )

        reopened_repository = SQLiteRepository(str(tmp_path / "knowledge.db"))
        reopened_blobs = LocalBlobRepository(tmp_path / "blobs")
        reopened_service = IngestionService(reopened_repository, reopened_blobs)
        for original in results:
            restored = await reopened_service.load_result(
                original.job.job_id, access=access, scope=scope
            )
            assert restored.job == original.job
            assert restored.version == original.version
            assert restored.chunks == original.chunks
            assert restored.evidence == original.evidence

        assert len(await repository.list_import_jobs(access, scope)) == 4

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("fixture_name", "media_type", "source_kind", "locator_field"),
    [
        ("sample.pdf", "application/pdf", SourceKind.LOCAL_FILE, "page_start"),
        ("sample.md", "text/markdown", SourceKind.LOCAL_FILE, "heading_path"),
        ("sample.html", "text/html", SourceKind.WEB, "anchor"),
    ],
)
def test_source_rewrite_and_deletion_keep_original_blob_and_locator(
    tmp_path, fixture_name, media_type, source_kind, locator_field
):
    async def scenario():
        scope, access = _identity()
        _, _, service = _service(tmp_path / "store")
        source_path = tmp_path / fixture_name
        source_path.write_bytes((FIXTURES / fixture_name).read_bytes())
        raw = source_path.read_bytes()
        document_input = _input(
            raw,
            input_ref=str(source_path),
            media_type=media_type,
            source_kind=source_kind,
        )
        result = await service.ingest(
            document_input, access=access, scope=scope
        )
        source_path.write_bytes(b"replacement bytes")
        source_path.unlink()
        assert not source_path.exists()
        reopened_repository = SQLiteRepository(
            str(tmp_path / "store" / "knowledge.db")
        )
        reopened_blobs = LocalBlobRepository(tmp_path / "store" / "blobs")
        assert await reopened_blobs.get(access, scope, result.job.blob_id) == raw
        assert await reopened_blobs.verify(
            access,
            scope,
            result.job.blob_id,
            result.version.content_sha256,
        )
        restored_version = await reopened_repository.get_version(
            access, scope, result.version.version_id
        )
        restored_chunks = await reopened_repository.list_chunks_for_version(
            access, scope, restored_version.version_id
        )
        assert restored_version == result.version
        assert restored_chunks == list(result.chunks)
        assert any(getattr(chunk, locator_field) for chunk in restored_chunks)

    asyncio.run(scenario())


def test_duplicate_content_is_idempotent_and_changed_content_creates_new_version(tmp_path):
    async def scenario():
        scope, access = _identity()
        repository, _, service = _service(tmp_path)
        first_input = _input(
            b"# Version\n\nOriginal.",
            input_ref=r"C:\authorized\versioned.md",
            media_type="text/markdown",
        )
        first = await service.ingest(first_input, access=access, scope=scope)
        duplicate = await service.ingest(first_input, access=access, scope=scope)
        changed = await service.ingest(
            first_input.model_copy(
                update={"raw_bytes": b"# Version\n\nChanged content."}
            ),
            access=access,
            scope=scope,
        )
        assert duplicate.job.job_id == first.job.job_id
        assert duplicate.version.version_id == first.version.version_id
        assert changed.job.job_id != first.job.job_id
        assert changed.version.version_id != first.version.version_id
        versions = await repository.list_versions(
            access, scope, first.document.document_id
        )
        assert [version.version_number for version in versions] == [1, 2]
        assert versions[0] == first.version
        assert len(await repository.list_import_jobs(access, scope)) == 2

    asyncio.run(scenario())


class _FlakyMarkdownParser:
    name = "flaky_markdown"
    version = "1"

    def __init__(self) -> None:
        self.calls = 0

    def supports(self, media_type: str, suffix: str) -> bool:
        return media_type == "text/markdown" or suffix == ".md"

    def parse(self, document, chunking=None):
        self.calls += 1
        if self.calls == 1:
            raise DocumentParseError(
                ParseErrorCode.UNSUPPORTED_FORMAT,
                self.name,
                "temporary parser capability failure",
            )
        parsed = MarkdownParser().parse(document, chunking)
        return parsed.model_copy(
            update={"parser_name": self.name, "parser_version": self.version}
        )


def test_structured_failed_job_retries_under_same_job_id(tmp_path):
    async def scenario():
        scope, access = _identity()
        parser = _FlakyMarkdownParser()
        repository, _, service = _service(tmp_path, parsers=(parser,))
        document_input = _input(
            b"# Retry\n\nRecovered.",
            input_ref=r"C:\authorized\retry.md",
            media_type="text/markdown",
        )
        with pytest.raises(IngestionServiceError) as first_error:
            await service.ingest(document_input, access=access, scope=scope)
        failed = first_error.value.job
        assert failed.status is ImportJobStatus.FAILED
        assert failed.error is not None
        assert failed.error.code == ParseErrorCode.UNSUPPORTED_FORMAT.value
        assert failed.error.retryable is True

        recovered = await service.ingest(
            document_input, access=access, scope=scope
        )
        assert recovered.job.job_id == failed.job_id
        assert recovered.job.status is ImportJobStatus.SUCCEEDED
        assert recovered.job.attempt_count == 2
        assert len(await repository.list_import_jobs(access, scope)) == 1

    asyncio.run(scenario())


class _FlakyIndexer:
    def __init__(self) -> None:
        self.calls = 0

    async def index(self, **kwargs):
        self.calls += 1
        assert kwargs["version"].lifecycle_status is VersionLifecycleStatus.CANDIDATE
        if self.calls == 1:
            raise RuntimeError("offline index fixture failure")


def test_index_failure_retries_without_promoting_or_duplicating_version(tmp_path):
    async def scenario():
        scope, access = _identity()
        indexer = _FlakyIndexer()
        repository, _, service = _service(tmp_path, indexer=indexer)
        document_input = _input(
            b"# Index\n\nCandidate only.",
            input_ref=r"C:\authorized\index.md",
            media_type="text/markdown",
        )
        with pytest.raises(IngestionServiceError) as first_error:
            await service.ingest(document_input, access=access, scope=scope)
        failed = first_error.value.job
        assert failed.status is ImportJobStatus.SUCCEEDED
        assert failed.index_status is ImportIndexStatus.FAILED
        assert failed.error is not None and failed.error.stage == "index"
        version = await repository.get_version(
            access, scope, failed.version_id
        )
        assert version.lifecycle_status is VersionLifecycleStatus.CANDIDATE

        recovered = await service.ingest(
            document_input, access=access, scope=scope
        )
        assert recovered.job.job_id == failed.job_id
        assert recovered.job.index_status is ImportIndexStatus.READY
        assert recovered.version.version_id == version.version_id
        assert recovered.version.lifecycle_status is VersionLifecycleStatus.CANDIDATE
        assert indexer.calls == 2
        assert len(
            await repository.list_versions(
                access, scope, recovered.document.document_id
            )
        ) == 1

    asyncio.run(scenario())


def test_reopened_repository_retrieves_pdf_markdown_and_html_locators(tmp_path):
    async def scenario():
        scope, access = _identity()
        repository, _, service = _service(tmp_path)
        inputs = (
            _input(
                (FIXTURES / "sample.pdf").read_bytes(),
                input_ref=r"C:\authorized\sample.pdf",
                media_type="application/pdf",
            ),
            _input(
                (FIXTURES / "sample.md").read_bytes(),
                input_ref=r"C:\authorized\sample.md",
                media_type="text/markdown",
            ),
            _input(
                (FIXTURES / "sample.html").read_bytes(),
                input_ref=r"C:\authorized\sample.html",
                media_type="text/html",
                source_kind=SourceKind.WEB,
            ),
        )
        imported = [
            await service.ingest(item, access=access, scope=scope) for item in inputs
        ]

        reopened = SQLiteRepository(str(tmp_path / "knowledge.db"))
        retriever = RepositoryKnowledgeRetriever(
            RepositoryRetrievalCatalog(reopened)
        )
        pdf = await retriever.search(
            KnowledgeSearchRequest(
                query="PDF page two storage evidence", include_candidate=True
            ),
            access=access,
            scope=scope,
        )
        assert pdf.hits
        assert pdf.hits[0].locator.page_start == 1
        assert pdf.hits[0].locator.page_end == 2
        read_pdf = await retriever.read(
            KnowledgeReadRequest(
                stable_id=pdf.hits[0].chunk_id, include_candidate=True
            ),
            access=access,
            scope=scope,
        )
        assert read_pdf.hit.version_id == imported[0].version.version_id
        assert read_pdf.hit.source_id == imported[0].source.source_id

        markdown = await retriever.search(
            KnowledgeSearchRequest(
                query="authoritative metadata store", include_candidate=True
            ),
            access=access,
            scope=scope,
        )
        assert markdown.hits[0].locator.heading_path == (
            "Architecture",
            "Storage",
        )

        html = await retriever.search(
            KnowledgeSearchRequest(
                query="Generated anchors deterministic", include_candidate=True
            ),
            access=access,
            scope=scope,
        )
        assert html.hits[0].locator.anchor == "details"
        assert html.hits[0].source.public_display_uri == (
            "https://example.com/wiki/Agent?a=1&z=2"
        )
        assert html.hits[0].version_id == imported[2].version.version_id

        changed_html = inputs[2].model_copy(
            update={
                "raw_bytes": inputs[2].raw_bytes.replace(
                    b"Generated anchors are deterministic.",
                    b"Updated anchors remain deterministic.",
                )
            }
        )
        changed = await service.ingest(
            changed_html, access=access, scope=scope
        )
        assert changed.source.source_id == imported[2].source.source_id
        assert changed.document.document_id == imported[2].document.document_id
        assert changed.version.version_id != imported[2].version.version_id
        versions = await reopened.list_versions(
            access, scope, imported[2].document.document_id
        )
        assert [version.version_number for version in versions] == [1, 2]
        old_snapshot = await retriever.search(
            KnowledgeSearchRequest(
                query="Generated anchors deterministic", include_candidate=True
            ),
            access=access,
            scope=scope,
        )
        assert old_snapshot.hits[0].version_id == imported[2].version.version_id

    asyncio.run(scenario())
