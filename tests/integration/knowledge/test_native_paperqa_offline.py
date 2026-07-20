from __future__ import annotations

import asyncio
import importlib.util
import socket

import pytest

from open_deep_research.knowledge.ingestion.parsers import DocumentInput
from open_deep_research.knowledge.ingestion.service import IngestionService
from open_deep_research.knowledge.models import (
    KnowledgeAccessContext,
    KnowledgeScope,
    SourceKind,
)
from open_deep_research.knowledge.paperqa_adapter import (
    DeterministicHashEmbedding,
    NativePaperQABackend,
    PaperQAKnowledgeRetriever,
    create_offline_paperqa_settings,
)
from open_deep_research.knowledge.retrieval.models import KnowledgeSearchRequest
from open_deep_research.knowledge.retrieval.repository_retriever import (
    RepositoryRetrievalCatalog,
)
from open_deep_research.knowledge.sqlite_repository import SQLiteRepository
from open_deep_research.storage.blob_repository import LocalBlobRepository


pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("paperqa") is None,
    reason="the optional Phase 2 knowledge dependency is not installed",
)


def test_native_paperqa_rehydrates_repository_with_local_embedding_only(
    tmp_path, monkeypatch
):
    """Exercise the real raw-retrieval API without an Agent, LLM, or network."""

    async def forbidden(*args, **kwargs):
        raise AssertionError("PaperQA answer/Agent APIs are forbidden in Phase 2")

    async def scenario():
        import paperqa

        monkeypatch.setattr(paperqa, "ask", forbidden, raising=False)
        monkeypatch.setattr(paperqa, "agent_query", forbidden, raising=False)
        monkeypatch.setattr(paperqa.Docs, "aquery", forbidden, raising=False)

        def forbidden_network(*args, **kwargs):
            raise AssertionError("network access is forbidden in Phase 2 tests")

        monkeypatch.setattr(socket, "create_connection", forbidden_network)
        monkeypatch.setattr(socket.socket, "connect", forbidden_network)
        monkeypatch.setattr(socket.socket, "connect_ex", forbidden_network)

        scope = KnowledgeScope(tenant_id="tenant-a", project_id="project-a")
        access = KnowledgeAccessContext(
            trusted_tenant_id="tenant-a",
            trusted_project_id="project-a",
            auth_source="phase2-native-paperqa-test",
            request_id="phase2-native-paperqa",
        )
        database = tmp_path / "knowledge.db"
        repository = SQLiteRepository(str(database))
        service = IngestionService(
            repository,
            LocalBlobRepository(tmp_path / "blobs"),
        )
        ingested = await service.ingest(
            DocumentInput(
                source_kind=SourceKind.LOCAL_FILE,
                media_type="text/markdown",
                input_ref=r"C:\\authorized\\native.md",
                display_name="native.md",
                raw_bytes=(
                    b"# Offline retrieval\n\n"
                    b"Native PaperQA indexes repository evidence locally."
                ),
            ),
            access=access,
            scope=scope,
        )
        request = KnowledgeSearchRequest(
            query="Native PaperQA repository evidence",
            include_candidate=True,
            limit=3,
        )

        async def retrieve_with_fresh_process_state():
            reopened = SQLiteRepository(str(database))
            settings = create_offline_paperqa_settings(tmp_path / "paperqa-index")
            retriever = PaperQAKnowledgeRetriever(
                RepositoryRetrievalCatalog(reopened),
                backend=NativePaperQABackend(
                    settings=settings,
                    embedding_model=DeterministicHashEmbedding(dimensions=1024),
                ),
                enabled=True,
                fallback_on_error=False,
            )
            return await retriever.search(request, access=access, scope=scope)

        first = await retrieve_with_fresh_process_state()
        second = await retrieve_with_fresh_process_state()
        assert first.backend == "paperqa-native"
        assert [hit.chunk_id for hit in first.hits] == [
            chunk.chunk_id for chunk in ingested.chunks
        ]
        assert second.hits == first.hits
        assert all(hit.inspection_only and not hit.citable for hit in first.hits)
        assert all(hit.scope_id == scope.scope_id for hit in first.hits)
        assert not (tmp_path / "paperqa-index").exists()

    asyncio.run(scenario())
