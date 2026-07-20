"""Production tool façades for Phase 3 retrieval modes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from open_deep_research.configuration import Configuration
from open_deep_research.evidence.models import (
    EvidenceDirectness,
    EvidenceRelation,
    EvidenceValidationStatus,
)
from open_deep_research.knowledge.models import VersionLifecycleStatus
from open_deep_research.knowledge.retrieval.models import (
    KnowledgeSearchRequest,
    RetrievalFilters,
    RetrievalRecord,
)
from open_deep_research.knowledge.retrieval.orchestrator import (
    GovernedRetrievalRequest,
    GovernedRetrievalResult,
)
from open_deep_research.knowledge.retrieval.repository_retriever import (
    RepositoryRetrievalCatalog,
)
from open_deep_research.knowledge.retrieval.runtime import (
    get_governed_runtime,
    process_context,
)
from open_deep_research.research import RequirementSet


def _trusted_requirement_set(config: RunnableConfig | None) -> RequirementSet:
    raw = process_context(config).get("requirement_set")
    if isinstance(raw, RequirementSet):
        return raw
    if isinstance(raw, dict):
        return RequirementSet.model_validate(raw)
    raise RuntimeError("governed retrieval requires a trusted RequirementSet")


@tool("governed_retrieval")
async def governed_retrieval(
    query: str,
    *,
    config: RunnableConfig | None = None,
) -> str:
    """Search governed local evidence first and use Web only for required gaps."""
    requirement_set = _trusted_requirement_set(config)
    context = process_context(config)
    runtime = get_governed_runtime(config, run_id=requirement_set.run_id)
    result = await runtime.orchestrator.retrieve(
        GovernedRetrievalRequest(
            run_id=requirement_set.run_id,
            researcher_id=str(context.get("researcher_id") or context.get("concurrency_id") or "researcher"),
            query=query,
            requirement_ids=requirement_set.requirement_ids,
            local_limit=Configuration.from_runnable_config(config).knowledge_search_limit,
        ),
        requirement_set=requirement_set,
        access=runtime.access,
        scope=runtime.scope,
        config=config,
    )
    return result.tool_content()


def parse_governed_result(value: Any) -> GovernedRetrievalResult | None:
    """Parse only the explicit governed JSON schema, never arbitrary Tool text."""
    content = getattr(value, "content", value)
    if not isinstance(content, str):
        return None
    try:
        return GovernedRetrievalResult.model_validate_json(content)
    except (ValueError, TypeError):
        return None


def _legacy_record_allowed(record: RetrievalRecord, *, at: datetime) -> bool:
    evidence = record.evidence
    return bool(
        evidence
        and record.version.lifecycle_status is VersionLifecycleStatus.ACTIVE
        and evidence.validation_status is EvidenceValidationStatus.VALIDATED
        and evidence.relation is EvidenceRelation.SUPPORTS
        and evidence.directness is EvidenceDirectness.DIRECT
        and record.source.soft_deleted_at is None
        and record.document.soft_deleted_at is None
        and record.version.soft_deleted_at is None
        and record.chunk.soft_deleted_at is None
        and evidence.soft_deleted_at is None
        and record.version.retrieved_at <= at
        and (
            record.version.published_at is None
            or record.version.published_at <= at
        )
        and (record.version.valid_from is None or at >= record.version.valid_from)
        and (record.version.valid_to is None or at <= record.version.valid_to)
    )


def _safe_record(record: RetrievalRecord) -> dict[str, Any]:
    evidence = record.evidence
    assert evidence is not None
    return {
        "evidence_id": evidence.evidence_id,
        "chunk_id": record.chunk.chunk_id,
        "version_id": record.version.version_id,
        "document_id": record.document.document_id,
        "source_id": record.source.source_id,
        "title": record.document.title,
        "source_uri": record.source.public_display_uri or record.source.canonical_uri,
        "text": record.chunk.text,
        "locator": {
            "type": record.chunk.locator_type.value,
            "page_start": record.chunk.page_start,
            "page_end": record.chunk.page_end,
            "heading_path": list(record.chunk.heading_path),
            "anchor": record.chunk.anchor,
        },
    }


@tool("knowledge_search")
async def knowledge_search_active(
    query: str,
    *,
    config: RunnableConfig | None = None,
) -> str:
    """Search only current active and validated local evidence."""
    runtime = get_governed_runtime(config)
    now = datetime.now(UTC)
    result = await runtime.retriever.search(
        KnowledgeSearchRequest(
            query=query,
            limit=Configuration.from_runnable_config(config).knowledge_search_limit,
            as_of=now,
            filters=RetrievalFilters(
                lifecycle_statuses=(VersionLifecycleStatus.ACTIVE,),
                validation_statuses=(EvidenceValidationStatus.VALIDATED,),
            ),
        ),
        access=runtime.access,
        scope=runtime.scope,
    )
    catalog = RepositoryRetrievalCatalog(runtime.repository)
    records = []
    for hit in result.hits:
        if not hit.evidence_id:
            continue
        record = await catalog.get_record(runtime.access, runtime.scope, hit.evidence_id)
        if _legacy_record_allowed(record, at=now):
            records.append(_safe_record(record))
    return json.dumps(
        {"mode": "knowledge_augmented_legacy", "query": query, "hits": records},
        ensure_ascii=False,
        sort_keys=True,
    )


@tool("knowledge_read")
async def knowledge_read_active(
    stable_id: str,
    *,
    config: RunnableConfig | None = None,
) -> str:
    """Resolve one active, validated Evidence/Chunk stable ID."""
    runtime = get_governed_runtime(config)
    now = datetime.now(UTC)
    catalog = RepositoryRetrievalCatalog(runtime.repository)
    record = await catalog.get_record(runtime.access, runtime.scope, stable_id)
    if not _legacy_record_allowed(record, at=now):
        raise LookupError("knowledge is not active, validated, direct, and current")
    return json.dumps(
        {"mode": "knowledge_augmented_legacy", "hit": _safe_record(record)},
        ensure_ascii=False,
        sort_keys=True,
    )


def governed_tool_names() -> tuple[str, ...]:
    """Stable tool snapshot used by routing tests and validation."""
    return (governed_retrieval.name,)


def legacy_knowledge_tool_names() -> tuple[str, ...]:
    """Stable active-only augmentation snapshot."""
    return tuple(sorted((knowledge_search_active.name, knowledge_read_active.name)))
