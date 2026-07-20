from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from functools import wraps
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from open_deep_research.evidence.models import (
    Evidence,
    EvidenceDirectness,
    EvidenceValidationStatus,
)
from open_deep_research.knowledge.models import (
    Chunk,
    ChunkInput,
    ChunkLocatorType,
    ContentBlob,
    Document,
    DocumentVersion,
    KnowledgeAccessContext,
    KnowledgeScope,
    Source,
    SourceKind,
    VersionLifecycleStatus,
)
from open_deep_research.knowledge.in_memory_repository import InMemoryRepository
from open_deep_research.knowledge.paperqa_adapter import (
    NativePaperQABackend,
    PaperQAAdapterError,
    PaperQABackendMatch,
    PaperQAKnowledgeRetriever,
)
from open_deep_research.knowledge.repositories import authorize_scope
from open_deep_research.knowledge.retrieval.models import (
    KnowledgeReadRequest,
    KnowledgeSearchRequest,
    RetrievalFilters,
    RetrievalRecord,
)
from open_deep_research.knowledge.retrieval.repository_retriever import (
    RepositoryRetrievalCatalog,
    RepositoryKnowledgeRetriever,
    RetrievalNotFoundError,
)


NOW = datetime(2026, 1, 10, tzinfo=UTC)


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


def make_scope(project: str = "project") -> tuple[KnowledgeScope, KnowledgeAccessContext]:
    scope = KnowledgeScope(tenant_id="tenant", project_id=project)
    access = KnowledgeAccessContext(
        trusted_tenant_id="tenant",
        trusted_project_id=project,
        auth_source="test",
        request_id=f"request-{project}",
    )
    return scope, access


def make_record(
    scope: KnowledgeScope,
    *,
    suffix: str,
    text: str,
    status: VersionLifecycleStatus = VersionLifecycleStatus.ACTIVE,
    retrieved_at: datetime = NOW,
    media_type: str = "text/markdown",
    evidence_status: EvidenceValidationStatus | None = None,
) -> RetrievalRecord:
    source = Source(
        scope_id=scope.scope_id,
        kind=SourceKind.LOCAL_FILE,
        internal_storage_ref=f"imports/{suffix}.md",
        public_display_uri=f"https://example.test/{suffix}",
        display_name=f"Source {suffix}",
    )
    document = Document(
        scope_id=scope.scope_id,
        source_id=source.source_id,
        logical_key=suffix,
        title=f"Document {suffix}",
        media_type=media_type,
    )
    digest = hashlib.sha256(suffix.encode()).hexdigest()
    version = DocumentVersion(
        scope_id=scope.scope_id,
        document_id=document.document_id,
        blob_id=f"blob_{digest}",
        content_sha256=digest,
        version_number=1,
        retrieved_at=retrieved_at,
        published_at=retrieved_at,
        lifecycle_status=status,
    )
    chunk = Chunk(
        scope_id=scope.scope_id,
        version_id=version.version_id,
        ordinal=0,
        text=text,
        locator_type=ChunkLocatorType.HEADING,
        heading_path=("Root", suffix),
    )
    evidence = None
    if evidence_status is not None:
        evidence = Evidence(
            scope_id=scope.scope_id,
            chunk_id=chunk.chunk_id,
            excerpt=text,
            confidence=0.9,
            directness=EvidenceDirectness.DIRECT,
            retrieval_method="fixture",
            validation_status=evidence_status,
        )
    return RetrievalRecord(
        source=source,
        document=document,
        version=version,
        chunk=chunk,
        evidence=evidence,
    )


class FakeCatalog:
    def __init__(self, records: list[RetrievalRecord]) -> None:
        self.records = tuple(records)

    async def list_records(self, access, scope):
        authorize_scope(access, scope)
        return tuple(record for record in self.records if record.chunk.scope_id == scope.scope_id)

    async def get_record(self, access, scope, stable_id):
        authorize_scope(access, scope)
        for record in self.records:
            if record.chunk.scope_id != scope.scope_id:
                continue
            if stable_id == record.chunk.chunk_id or (
                record.evidence and stable_id == record.evidence.evidence_id
            ):
                return record
        raise RetrievalNotFoundError(stable_id)


@async_test
async def test_repository_catalog_uses_real_scope_aware_repository_contract() -> None:
    scope, access = make_scope()
    other_scope, other_access = make_scope("other")
    repository = InMemoryRepository()

    async def seed(
        target_scope: KnowledgeScope,
        target_access: KnowledgeAccessContext,
        suffix: str,
    ) -> tuple[str, str]:
        source = await repository.upsert_source(
            target_access,
            target_scope,
            kind=SourceKind.LOCAL_FILE,
            internal_storage_ref=f"imports/{suffix}.md",
            display_name=f"Source {suffix}",
        )
        document = await repository.upsert_document(
            target_access,
            target_scope,
            source_id=source.source_id,
            logical_key=suffix,
            title=f"Document {suffix}",
            media_type="text/markdown",
        )
        content = f"repository catalog {suffix}".encode()
        blob = ContentBlob.from_bytes(
            scope_id=target_scope.scope_id,
            content=content,
            media_type="text/markdown",
            storage_ref=f"{target_scope.scope_id}/{suffix}.blob",
        )
        version = await repository.add_version(
            target_access,
            target_scope,
            document_id=document.document_id,
            blob=blob,
            retrieved_at=NOW,
            lifecycle_status=VersionLifecycleStatus.ACTIVE,
        )
        chunk = (
            await repository.add_chunks(
                target_access,
                target_scope,
                version.version_id,
                [
                    ChunkInput(
                        ordinal=0,
                        text=f"repository catalog {suffix}",
                        locator_type=ChunkLocatorType.HEADING,
                        heading_path=("Repository", suffix),
                    )
                ],
            )
        )[0]
        evidence = await repository.add_evidence(
            target_access,
            target_scope,
            chunk_id=chunk.chunk_id,
            excerpt=chunk.text,
            confidence=0.9,
            directness=EvidenceDirectness.DIRECT,
            retrieval_method="fixture",
            validation_status=EvidenceValidationStatus.VALIDATED,
        )
        return chunk.chunk_id, evidence.evidence_id

    chunk_id, evidence_id = await seed(scope, access, "visible")
    hidden_chunk_id, _ = await seed(other_scope, other_access, "hidden")
    retriever = RepositoryKnowledgeRetriever(RepositoryRetrievalCatalog(repository))

    result = await retriever.search(
        KnowledgeSearchRequest(query="repository catalog"),
        access=access,
        scope=scope,
    )
    assert [hit.chunk_id for hit in result.hits] == [chunk_id]
    assert hidden_chunk_id not in {hit.chunk_id for hit in result.hits}
    assert result.hits[0].evidence_id == evidence_id
    assert result.hits[0].citable

    read = await retriever.read(
        KnowledgeReadRequest(stable_id=evidence_id),
        access=access,
        scope=scope,
    )
    assert read.hit.chunk_id == chunk_id


@async_test
async def test_repository_retriever_scope_filters_as_of_and_candidate() -> None:
    scope, access = make_scope()
    other_scope, _ = make_scope("other")
    active = make_record(
        scope,
        suffix="active",
        text="alpha evidence",
        retrieved_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    candidate = make_record(
        scope,
        suffix="candidate",
        text="alpha candidate",
        status=VersionLifecycleStatus.CANDIDATE,
        retrieved_at=datetime(2026, 1, 3, tzinfo=UTC),
    )
    future = make_record(
        scope,
        suffix="future",
        text="alpha future",
        retrieved_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    cross_scope = make_record(other_scope, suffix="other", text="alpha secret")
    retriever = RepositoryKnowledgeRetriever(
        FakeCatalog([candidate, future, cross_scope, active])
    )

    result = await retriever.search(
        KnowledgeSearchRequest(
            query="alpha",
            as_of=datetime(2026, 1, 5, tzinfo=UTC),
        ),
        access=access,
        scope=scope,
    )
    assert [hit.chunk_id for hit in result.hits] == [active.chunk.chunk_id]

    inspection = await retriever.search(
        KnowledgeSearchRequest(
            query="alpha",
            as_of=datetime(2026, 1, 5, tzinfo=UTC),
            include_candidate=True,
            filters=RetrievalFilters(
                media_types=("text/markdown",),
                source_ids=(candidate.source.source_id,),
            ),
        ),
        access=access,
        scope=scope,
    )
    assert [hit.chunk_id for hit in inspection.hits] == [candidate.chunk.chunk_id]
    assert inspection.hits[0].inspection_only
    assert inspection.hits[0].lifecycle_status is VersionLifecycleStatus.CANDIDATE


@async_test
async def test_repository_retriever_empty_and_deterministic_tie_break() -> None:
    scope, access = make_scope()
    records = [
        make_record(scope, suffix="b", text="same query"),
        make_record(scope, suffix="a", text="same query"),
    ]
    retriever = RepositoryKnowledgeRetriever(FakeCatalog(list(reversed(records))))
    request = KnowledgeSearchRequest(query="same query")
    first = await retriever.search(request, access=access, scope=scope)
    second = await retriever.search(request, access=access, scope=scope)
    expected = sorted(record.chunk.chunk_id for record in records)
    assert [hit.chunk_id for hit in first.hits] == expected
    assert first == second

    empty = await retriever.search(
        KnowledgeSearchRequest(query="does-not-exist"),
        access=access,
        scope=scope,
    )
    assert empty.hits == ()
    assert empty.empty_reason == "no_matching_knowledge"


@async_test
async def test_read_uses_stable_chunk_or_evidence_id_and_citation_chain() -> None:
    scope, access = make_scope()
    record = make_record(
        scope,
        suffix="validated",
        text="validated statement",
        evidence_status=EvidenceValidationStatus.VALIDATED,
    )
    retriever = RepositoryKnowledgeRetriever(FakeCatalog([record]))
    by_chunk = await retriever.read(
        KnowledgeReadRequest(stable_id=record.chunk.chunk_id),
        access=access,
        scope=scope,
    )
    by_evidence = await retriever.read(
        KnowledgeReadRequest(stable_id=record.evidence.evidence_id),
        access=access,
        scope=scope,
    )
    assert by_chunk.hit.chunk_id == record.chunk.chunk_id
    assert by_evidence.hit.evidence_id == record.evidence.evidence_id
    assert by_evidence.hit.citable
    assert "imports/" not in by_evidence.hit.model_dump_json()


def test_search_and_read_requests_are_strict_and_read_rejects_paths() -> None:
    with pytest.raises(ValidationError, match="extra"):
        KnowledgeSearchRequest.model_validate({"query": "x", "unexpected": True})
    with pytest.raises(ValidationError, match="stable_id"):
        KnowledgeReadRequest(stable_id=r"C:\private\paper.pdf")
    with pytest.raises(ValidationError, match="stable_id"):
        KnowledgeReadRequest(stable_id="chk_short")


class StubPaperQABackend:
    name = "paperqa-stub"

    def __init__(self, matches):
        self.matches = matches
        self.calls = 0

    async def retrieve(self, query, records, *, limit, contextualize):
        self.calls += 1
        return self.matches


@async_test
async def test_paperqa_adapter_prefilters_postfilters_and_uses_project_ids() -> None:
    scope, access = make_scope()
    active = make_record(scope, suffix="active", text="paperqa query")
    candidate = make_record(
        scope,
        suffix="candidate",
        text="paperqa query",
        status=VersionLifecycleStatus.CANDIDATE,
    )
    backend = StubPaperQABackend(
        [
            PaperQABackendMatch(
                chunk_id="chk_" + "f" * 64,
                score=99,
            ),
            PaperQABackendMatch(chunk_id=active.chunk.chunk_id, score=0.8),
        ]
    )
    retriever = PaperQAKnowledgeRetriever(
        FakeCatalog([candidate, active]), backend=backend, enabled=True
    )
    result = await retriever.search(
        KnowledgeSearchRequest(query="paperqa query"),
        access=access,
        scope=scope,
    )
    assert backend.calls == 1
    assert [hit.chunk_id for hit in result.hits] == [active.chunk.chunk_id]
    assert result.hits[0].source_id == active.source.source_id
    assert result.hits[0].evidence_id is None


@async_test
async def test_disabled_paperqa_uses_repository_without_loading_module() -> None:
    scope, access = make_scope()
    record = make_record(scope, suffix="disabled", text="local result")
    loaded: list[str] = []
    parsing = SimpleNamespace(
        use_doc_details=False,
        defer_embedding=True,
        should_parse_and_enrich_media=(False, False),
    )
    native = NativePaperQABackend(
        settings=SimpleNamespace(parsing=parsing),
        embedding_model=object(),
        module_loader=lambda name: loaded.append(name),
    )
    retriever = PaperQAKnowledgeRetriever(
        FakeCatalog([record]), backend=native, enabled=False
    )
    result = await retriever.search(
        KnowledgeSearchRequest(query="local"), access=access, scope=scope
    )
    assert result.backend == "repository-keyword"
    assert loaded == []


@async_test
async def test_native_paperqa_seam_only_manual_loads_and_retrieves() -> None:
    scope, _ = make_scope()
    record = make_record(scope, suffix="native", text="native paperqa")
    calls: list[str] = []

    class StubDoc:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class StubText:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class StubDocs:
        def __init__(self):
            self.texts = []

        async def aadd_texts(self, *, texts, doc, settings, embedding_model):
            calls.append("aadd_texts")
            assert embedding_model is None
            self.texts.extend(texts)
            return True

        async def retrieve_texts(self, *, query, k, settings, embedding_model):
            calls.append("retrieve_texts")
            assert embedding_model is not None
            return list(reversed(self.texts))[:k]

    parsing = SimpleNamespace(
        use_doc_details=False,
        defer_embedding=True,
        should_parse_and_enrich_media=(False, False),
    )
    backend = NativePaperQABackend(
        settings=SimpleNamespace(parsing=parsing),
        embedding_model=object(),
        module_loader=lambda _: SimpleNamespace(
            Docs=StubDocs, Doc=StubDoc, Text=StubText
        ),
    )
    matches = await backend.retrieve(
        "native", [record], limit=10, contextualize=False
    )
    assert calls == ["aadd_texts", "retrieve_texts"]
    assert matches[0].chunk_id == record.chunk.chunk_id
    assert matches[0].score == 1.0


def test_native_paperqa_rejects_unsafe_defaults() -> None:
    unsafe = SimpleNamespace(
        parsing=SimpleNamespace(
            use_doc_details=True,
            defer_embedding=False,
            should_parse_and_enrich_media=(True, True),
        )
    )
    with pytest.raises(PaperQAAdapterError):
        NativePaperQABackend(settings=unsafe, embedding_model=object())
