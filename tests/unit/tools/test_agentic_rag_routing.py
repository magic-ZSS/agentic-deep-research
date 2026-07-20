"""Mechanical tool-routing and active-only ablation tests."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from open_deep_research import utils
from open_deep_research.evidence.models import (
    Evidence,
    EvidenceDirectness,
    EvidenceRelation,
    EvidenceValidationStatus,
)
from open_deep_research.knowledge.models import (
    Chunk,
    Document,
    DocumentVersion,
    KnowledgeScope,
    Source,
    SourceKind,
    VersionLifecycleStatus,
)
from open_deep_research.knowledge.retrieval.models import RetrievalRecord
from open_deep_research.tools.governed_retrieval import _legacy_record_allowed


def _tool_names(config: dict) -> tuple[str, ...]:
    tools = asyncio.run(utils.get_all_tools({"configurable": config}))
    return tuple(
        item.name if hasattr(item, "name") else item.get("name", "web_search")
        for item in tools
    )


def test_tool_modes_are_mechanically_distinct_and_default_is_baseline(
    monkeypatch,
) -> None:
    for name in (
        "ENABLE_KNOWLEDGE_TOOLS",
        "ENABLE_AGENTIC_RAG",
        "ENABLE_KNOWLEDGE_WRITEBACK",
        "SEARCH_API",
    ):
        monkeypatch.delenv(name, raising=False)

    baseline = _tool_names({"search_api": "none"})
    explicit_baseline = _tool_names(
        {
            "search_api": "none",
            "enable_knowledge_tools": False,
            "enable_agentic_rag": False,
            "enable_knowledge_writeback": False,
        }
    )
    legacy = _tool_names(
        {"search_api": "none", "enable_knowledge_tools": True}
    )
    agentic = _tool_names(
        {"search_api": "none", "enable_agentic_rag": True}
    )

    assert baseline == explicit_baseline == ("ResearchComplete", "think_tool")
    assert set(legacy) == {
        "ResearchComplete",
        "think_tool",
        "knowledge_search",
        "knowledge_read",
    }
    assert set(agentic) == {
        "ResearchComplete",
        "think_tool",
        "governed_retrieval",
    }
    assert "governed_retrieval" not in legacy


def test_agentic_mode_has_no_web_or_mcp_bypass(monkeypatch) -> None:
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("agentic routing attempted an ungoverned provider")

    monkeypatch.setattr(utils, "get_search_tool", forbidden)
    monkeypatch.setattr(utils, "load_mcp_tools", forbidden)

    names = _tool_names(
        {
            "enable_agentic_rag": True,
            "search_api": "tavily",
            "mcp_config": {
                "url": "https://example.test",
                "tools": ["web_search"],
                "auth_required": False,
            },
        }
    )

    assert set(names) == {
        "ResearchComplete",
        "think_tool",
        "governed_retrieval",
    }
    assert "tavily_search" not in names
    assert "web_search" not in names


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_provider_native_agentic_configuration_fails_closed(provider) -> None:
    with pytest.raises(ValidationError, match="cannot govern provider-native"):
        _tool_names(
            {"enable_agentic_rag": True, "search_api": provider}
        )


def _record(
    *,
    lifecycle: VersionLifecycleStatus = VersionLifecycleStatus.ACTIVE,
    validation: EvidenceValidationStatus = EvidenceValidationStatus.VALIDATED,
    directness: EvidenceDirectness = EvidenceDirectness.DIRECT,
    relation: EvidenceRelation = EvidenceRelation.SUPPORTS,
) -> RetrievalRecord:
    scope = KnowledgeScope(tenant_id="tenant", project_id="project")
    source = Source(
        scope_id=scope.scope_id,
        kind=SourceKind.WEB,
        canonical_uri="https://example.test/evidence",
        display_name="Fixture",
    )
    document = Document(
        scope_id=scope.scope_id,
        source_id=source.source_id,
        logical_key="fixture",
        title="Fixture",
        media_type="text/html",
    )
    digest = hashlib.sha256(b"fixture").hexdigest()
    version = DocumentVersion(
        scope_id=scope.scope_id,
        document_id=document.document_id,
        blob_id=f"blob_{digest}",
        content_sha256=digest,
        version_number=1,
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        lifecycle_status=lifecycle,
    )
    chunk = Chunk(
        scope_id=scope.scope_id,
        version_id=version.version_id,
        ordinal=0,
        text="Direct fixture evidence.",
    )
    evidence = Evidence(
        scope_id=scope.scope_id,
        chunk_id=chunk.chunk_id,
        excerpt=chunk.text,
        relation=relation,
        directness=directness,
        confidence=0.9,
        retrieval_method="fixture",
        validation_status=validation,
    )
    return RetrievalRecord(
        source=source,
        document=document,
        version=version,
        chunk=chunk,
        evidence=evidence,
    )


def test_legacy_mode_allows_only_active_validated_direct_support() -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    assert _legacy_record_allowed(_record(), at=now)
    assert not _legacy_record_allowed(
        _record(lifecycle=VersionLifecycleStatus.CANDIDATE), at=now
    )
    assert not _legacy_record_allowed(
        _record(lifecycle=VersionLifecycleStatus.STALE), at=now
    )
    assert not _legacy_record_allowed(
        _record(validation=EvidenceValidationStatus.PENDING), at=now
    )
    assert not _legacy_record_allowed(
        _record(directness=EvidenceDirectness.INDIRECT), at=now
    )
    assert not _legacy_record_allowed(
        _record(relation=EvidenceRelation.CONTEXT), at=now
    )
