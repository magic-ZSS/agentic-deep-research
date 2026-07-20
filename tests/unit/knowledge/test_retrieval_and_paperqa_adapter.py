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
    BoundedContextualizingBackend,
    DeterministicHashEmbedding,
    FakePaperQABackend,
    NativePaperQABackend,
    PaperQAAdapterError,
    PaperQABackendMatch,
    PaperQAKnowledgeRetriever,
    create_offline_paperqa_settings,
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
        self.last_records = ()

    async def retrieve(self, query, records, *, limit, contextualize):
        self.calls += 1
        self.last_records = tuple(records)
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
    future = make_record(
        scope,
        suffix="future-paperqa",
        text="paperqa query",
        retrieved_at=datetime(2026, 2, 1, tzinfo=UTC),
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
        FakeCatalog([candidate, future, active]), backend=backend, enabled=True
    )
    result = await retriever.search(
        KnowledgeSearchRequest(
            query="paperqa query",
            as_of=datetime(2026, 1, 15, tzinfo=UTC),
            filters=RetrievalFilters(
                source_ids=(active.source.source_id, future.source.source_id)
            ),
        ),
        access=access,
        scope=scope,
    )
    assert backend.calls == 1
    assert [record.chunk.chunk_id for record in backend.last_records] == [
        active.chunk.chunk_id
    ]
    assert [hit.chunk_id for hit in result.hits] == [active.chunk.chunk_id]
    assert result.hits[0].source_id == active.source.source_id
    assert result.hits[0].evidence_id is None


@async_test
async def test_paperqa_adapter_deduplicates_and_sorts_by_score_then_chunk_id() -> None:
    scope, access = make_scope()
    records = [
        make_record(scope, suffix=suffix, text="paperqa stable order")
        for suffix in ("a", "b", "c")
    ]
    by_chunk = {record.chunk.chunk_id: record for record in records}
    tied = sorted(records[:2], key=lambda item: item.chunk.chunk_id)
    backend = StubPaperQABackend(
        [
            PaperQABackendMatch(chunk_id=tied[1].chunk.chunk_id, score=0.7),
            PaperQABackendMatch(chunk_id=records[2].chunk.chunk_id, score=0.9),
            PaperQABackendMatch(chunk_id=tied[0].chunk.chunk_id, score=0.7),
            PaperQABackendMatch(chunk_id=tied[1].chunk.chunk_id, score=0.6),
            PaperQABackendMatch(chunk_id="chk_" + "f" * 64, score=1.0),
        ]
    )
    result = await PaperQAKnowledgeRetriever(
        FakeCatalog(records), backend=backend, enabled=True
    ).search(
        KnowledgeSearchRequest(query="paperqa stable order"),
        access=access,
        scope=scope,
    )
    expected = [records[2].chunk.chunk_id] + [
        record.chunk.chunk_id for record in tied
    ]
    assert [hit.chunk_id for hit in result.hits] == expected
    assert [hit.rank for hit in result.hits] == [1, 2, 3]
    assert all(
        hit.source_id == by_chunk[hit.chunk_id].source.source_id
        and hit.version_id == by_chunk[hit.chunk_id].version.version_id
        for hit in result.hits
    )


@async_test
async def test_retrievers_fail_closed_when_catalog_leaks_another_scope() -> None:
    requested_scope, access = make_scope("requested")
    leaked_scope, _ = make_scope("leaked")
    leaked = make_record(leaked_scope, suffix="leaked", text="scope leak query")
    request = KnowledgeSearchRequest(query="scope leak query")

    class LeakyCatalog(FakeCatalog):
        async def list_records(self, access, scope):
            return self.records

    repository_result = await RepositoryKnowledgeRetriever(
        LeakyCatalog([leaked])
    ).search(request, access=access, scope=requested_scope)
    paperqa_result = await PaperQAKnowledgeRetriever(
        LeakyCatalog([leaked]), backend=FakePaperQABackend(), enabled=True
    ).search(request, access=access, scope=requested_scope)

    assert repository_result.hits == ()
    assert paperqa_result.hits == ()
    assert paperqa_result.empty_reason == "no_eligible_knowledge"


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
async def test_paperqa_failure_is_structured_or_explicitly_falls_back() -> None:
    scope, access = make_scope()
    record = make_record(scope, suffix="failure", text="local fallback evidence")

    class RaisingBackend:
        name = "paperqa-raising"

        async def retrieve(self, query, records, *, limit, contextualize):
            raise RuntimeError("offline derived index failed")

    catalog = FakeCatalog([record])
    fallback = await PaperQAKnowledgeRetriever(
        catalog,
        backend=RaisingBackend(),
        enabled=True,
        fallback_on_error=True,
    ).search(
        KnowledgeSearchRequest(query="fallback evidence"),
        access=access,
        scope=scope,
    )
    assert fallback.backend == "repository-keyword"
    assert fallback.hits
    assert fallback.warnings == ("paperqa_fallback:RuntimeError",)

    with pytest.raises(PaperQAAdapterError, match="retrieval failed"):
        await PaperQAKnowledgeRetriever(
            catalog,
            backend=RaisingBackend(),
            enabled=True,
            fallback_on_error=False,
        ).search(
            KnowledgeSearchRequest(query="fallback evidence"),
            access=access,
            scope=scope,
        )


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
            for text in texts:
                text.embedding = [1.0, 0.0]
            self.texts.extend(texts)
            return True

        async def retrieve_texts(self, *, query, k, settings, embedding_model):
            calls.append("retrieve_texts")
            assert embedding_model is not None
            return list(reversed(self.texts))[:k]

    class StubEmbedding:
        def __init__(self):
            self.modes = []

        def set_mode(self, mode):
            self.mode = mode
            self.modes.append(mode)

        async def embed_documents(self, texts):
            return [[1.0, 0.0] for _ in texts]

    parsing = SimpleNamespace(
        use_doc_details=False,
        defer_embedding=True,
        should_parse_and_enrich_media=(False, False),
    )
    embedding = StubEmbedding()
    backend = NativePaperQABackend(
        settings=SimpleNamespace(parsing=parsing),
        embedding_model=embedding,
        module_loader=lambda name: (
            SimpleNamespace(Docs=StubDocs, Doc=StubDoc, Text=StubText)
            if name == "paperqa"
            else SimpleNamespace(
                EmbeddingModes=SimpleNamespace(QUERY="query", DOCUMENT="document")
            )
        ),
    )
    matches = await backend.retrieve(
        "native", [record], limit=10, contextualize=False
    )
    assert calls == ["aadd_texts", "retrieve_texts"]
    assert matches[0].chunk_id == record.chunk.chunk_id
    assert matches[0].score == 1.0
    assert embedding.modes == ["document", "query", "document"]


@async_test
async def test_native_paperqa_zero_similarity_is_an_explicit_empty_result() -> None:
    scope, _ = make_scope()
    record = make_record(scope, suffix="orthogonal", text="document vector")

    class StubText:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class StubDocs:
        def __init__(self):
            self.texts = []

        async def aadd_texts(self, *, texts, **_kwargs):
            for text in texts:
                text.embedding = [1.0, 0.0]
            self.texts.extend(texts)

        async def retrieve_texts(self, **_kwargs):
            return self.texts

    class OrthogonalEmbedding:
        def set_mode(self, mode):
            self.mode = mode

        async def embed_documents(self, texts):
            return [[0.0, 1.0] for _ in texts]

    paperqa = SimpleNamespace(
        Docs=StubDocs,
        Doc=lambda **kwargs: SimpleNamespace(**kwargs),
        Text=StubText,
    )
    lmi = SimpleNamespace(
        EmbeddingModes=SimpleNamespace(QUERY="query", DOCUMENT="document")
    )
    parsing = SimpleNamespace(
        use_doc_details=False,
        defer_embedding=True,
        should_parse_and_enrich_media=(False, False),
    )
    backend = NativePaperQABackend(
        settings=SimpleNamespace(parsing=parsing),
        embedding_model=OrthogonalEmbedding(),
        module_loader=lambda name: paperqa if name == "paperqa" else lmi,
    )
    assert await backend.retrieve(
        "unrelated query", [record], limit=5, contextualize=False
    ) == ()


def test_native_paperqa_rejects_non_finite_embedding_scores() -> None:
    assert NativePaperQABackend._cosine_similarity(
        [float("nan"), 0.0], [1.0, 0.0]
    ) is None
    assert NativePaperQABackend._cosine_similarity(
        [float("inf"), 0.0], [1.0, 0.0]
    ) is None
    with pytest.raises(ValidationError):
        PaperQABackendMatch(chunk_id="chk_" + "a" * 64, score=float("nan"))


@async_test
async def test_contextual_backend_is_opt_in_bounded_and_order_preserving() -> None:
    scope, _ = make_scope()
    records = [
        make_record(scope, suffix=str(index), text=f"contextual evidence {index}")
        for index in range(3)
    ]
    raw = StubPaperQABackend(
        [
            PaperQABackendMatch(chunk_id=record.chunk.chunk_id, score=1 - index / 10)
            for index, record in enumerate(records)
        ]
    )

    class RecordingContextualizer:
        def __init__(self):
            self.calls = []
            self.active = 0
            self.maximum_active = 0

        async def summarize(self, query, record, *, token_limit):
            self.calls.append((query, record.chunk.chunk_id, token_limit))
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            await asyncio.sleep(0)
            self.active -= 1
            return f"Grounded: {record.chunk.text}"

    contextualizer = RecordingContextualizer()
    backend = BoundedContextualizingBackend(
        raw,
        contextualizer=contextualizer,
        evidence_k=2,
        max_concurrency=1,
        timeout_seconds=1,
        token_limit=32,
    )
    raw_matches = await backend.retrieve(
        "contextual evidence", records, limit=3, contextualize=False
    )
    assert len(raw_matches) == 3
    assert contextualizer.calls == []

    contextual_matches = await backend.retrieve(
        "contextual evidence", records, limit=3, contextualize=True
    )
    assert [match.chunk_id for match in contextual_matches] == [
        record.chunk.chunk_id for record in records[:2]
    ]
    assert [match.contextual_summary for match in contextual_matches] == [
        f"Grounded: {record.chunk.text}" for record in records[:2]
    ]
    assert contextualizer.maximum_active == 1
    assert all(call[2] == 32 for call in contextualizer.calls)


@pytest.mark.parametrize("mode", ["timeout", "exception"])
def test_contextual_backend_reports_timeout_and_exception(mode) -> None:
    async def scenario():
        scope, _ = make_scope()
        record = make_record(scope, suffix=mode, text="contextual failure")
        raw = StubPaperQABackend(
            [PaperQABackendMatch(chunk_id=record.chunk.chunk_id, score=1)]
        )

        class FailingContextualizer:
            async def summarize(self, query, record, *, token_limit):
                if mode == "timeout":
                    await asyncio.sleep(0.05)
                    return "late"
                raise RuntimeError("local fake failed")

        backend = BoundedContextualizingBackend(
            raw,
            contextualizer=FailingContextualizer(),
            timeout_seconds=0.001,
        )
        with pytest.raises(PaperQAAdapterError, match=mode):
            await backend.retrieve(
                "failure", [record], limit=1, contextualize=True
            )

    asyncio.run(scenario())


@async_test
async def test_deterministic_hash_embedding_is_local_and_stable() -> None:
    embedding = DeterministicHashEmbedding(dimensions=64)
    first = await embedding.embed_documents(["local storage evidence"])
    second = await embedding.embed_documents(["local storage evidence"])
    empty = await embedding.embed_documents([""])
    assert first == second
    assert len(first[0]) == 64
    assert any(first[0])
    assert empty == [[0.0] * 64]


def test_offline_settings_factory_never_uses_default_user_index() -> None:
    captured = {}

    class StubSettings:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    result = create_offline_paperqa_settings(
        "workspace/paperqa-index",
        module_loader=lambda name: SimpleNamespace(Settings=StubSettings),
    )
    assert isinstance(result, StubSettings)
    assert captured["parsing"] == {
        "use_doc_details": False,
        "multimodal": False,
        "defer_embedding": True,
    }
    assert captured["answer"]["evidence_skip_summary"] is True
    assert captured["agent"]["rebuild_index"] is False
    assert captured["agent"]["index"] == {
        "index_directory": "workspace/paperqa-index",
        "sync_with_paper_directory": False,
    }


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
