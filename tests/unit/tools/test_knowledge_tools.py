from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from functools import wraps

import pytest
from pydantic import ValidationError

from open_deep_research.knowledge.models import (
    ChunkLocatorType,
    KnowledgeAccessContext,
    KnowledgeScope,
    SourceKind,
    SourcePublicView,
    VersionLifecycleStatus,
)
from open_deep_research.knowledge.retrieval.models import (
    ChunkLocatorView,
    EvidenceHit,
    KnowledgeReadRequest,
    KnowledgeReadResult,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
)
from open_deep_research.knowledge.retrieval.repository_retriever import (
    CandidateInspectionRequiredError,
)
from open_deep_research.tools.knowledge import (
    KnowledgeInspectionService,
    knowledge_read,
    knowledge_search,
)
from open_deep_research.utils import get_all_tools


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


def context() -> tuple[KnowledgeScope, KnowledgeAccessContext]:
    scope = KnowledgeScope(tenant_id="tenant", project_id="project")
    access = KnowledgeAccessContext(
        trusted_tenant_id="tenant",
        trusted_project_id="project",
        auth_source="test",
        request_id="request",
    )
    return scope, access


def hit(scope: KnowledgeScope, *, candidate: bool = False) -> EvidenceHit:
    digest = "a" * 64
    return EvidenceHit(
        evidence_id=None,
        chunk_id="chk_" + digest,
        version_id="ver_" + digest,
        document_id="doc_" + digest,
        source_id="src_" + digest,
        scope_id=scope.scope_id,
        source=SourcePublicView(
            source_id="src_" + digest,
            kind=SourceKind.LOCAL_FILE,
            display_name="Fixture",
            public_display_uri="https://example.test/fixture",
            publisher=None,
            authority_class="unknown",
        ),
        document_title="Fixture document",
        media_type="text/markdown",
        text="fixture body",
        score=1.0,
        rank=1,
        locator=ChunkLocatorView(
            locator_type=ChunkLocatorType.HEADING,
            heading_path=("Fixture",),
        ),
        content_sha256=digest,
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        lifecycle_status=(
            VersionLifecycleStatus.CANDIDATE
            if candidate
            else VersionLifecycleStatus.ACTIVE
        ),
        retrieval_method="fake",
        citable=False,
        inspection_only=True,
    )


class StubRetriever:
    def __init__(self, item: EvidenceHit | None) -> None:
        self.item = item

    async def search(self, request, *, access, scope):
        return KnowledgeSearchResult(
            query=request.query,
            hits=(self.item,) if self.item else (),
            backend="stub",
            empty_reason=None if self.item else "no_matching_knowledge",
        )

    async def read(self, request, *, access, scope):
        assert self.item is not None
        return KnowledgeReadResult(hit=self.item)


@async_test
async def test_knowledge_search_returns_typed_artifact_and_explicit_empty() -> None:
    scope, access = context()
    empty_service = KnowledgeInspectionService(StubRetriever(None))
    empty = await knowledge_search(
        empty_service,
        KnowledgeSearchRequest(query="missing"),
        access=access,
        scope=scope,
    )
    assert empty.artifact.hits == ()
    assert "No matching" in empty.content

    service = KnowledgeInspectionService(StubRetriever(hit(scope)))
    result = await knowledge_search(
        service,
        KnowledgeSearchRequest(query="fixture"),
        access=access,
        scope=scope,
    )
    assert result.artifact.hits[0].chunk_id.startswith("chk_")
    assert "inspection-only" in result.content


@async_test
async def test_candidate_search_and_read_require_service_capability() -> None:
    scope, access = context()
    candidate_hit = hit(scope, candidate=True)
    denied = KnowledgeInspectionService(StubRetriever(candidate_hit))
    search_request = KnowledgeSearchRequest(query="fixture", include_candidate=True)
    read_request = KnowledgeReadRequest(
        stable_id=candidate_hit.chunk_id, include_candidate=True
    )
    with pytest.raises(CandidateInspectionRequiredError):
        await denied.search(search_request, access=access, scope=scope)
    with pytest.raises(CandidateInspectionRequiredError):
        await denied.read(read_request, access=access, scope=scope)

    allowed = KnowledgeInspectionService(
        StubRetriever(candidate_hit), allow_candidate_inspection=True
    )
    searched = await knowledge_search(
        allowed, search_request, access=access, scope=scope
    )
    read = await knowledge_read(allowed, read_request, access=access, scope=scope)
    assert searched.artifact.hits[0].inspection_only
    assert read.artifact.hit.chunk_id == candidate_hit.chunk_id


def test_tool_requests_forbid_extra_fields_and_paths() -> None:
    with pytest.raises(ValidationError):
        KnowledgeSearchRequest.model_validate({"query": "x", "path": "secret.pdf"})
    with pytest.raises(ValidationError):
        KnowledgeReadRequest(stable_id="../secret.pdf")


@async_test
async def test_phase2_contract_is_not_registered_with_production_tools() -> None:
    tools = await get_all_tools({"configurable": {"search_api": "none"}})
    names = {
        item.name if hasattr(item, "name") else item.get("name", "web_search")
        for item in tools
    }
    assert "knowledge_search" not in names
    assert "knowledge_read" not in names
    assert "governed_retrieval" not in names
