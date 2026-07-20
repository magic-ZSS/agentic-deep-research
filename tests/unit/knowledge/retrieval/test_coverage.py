from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from open_deep_research.evidence.models import (
    Evidence,
    EvidenceDirectness,
    EvidenceRelation,
    EvidenceValidationStatus,
)
from open_deep_research.knowledge.ids import sha256_bytes
from open_deep_research.knowledge.models import (
    AuthorityClass,
    Chunk,
    ChunkInput,
    Document,
    DocumentVersion,
    Source,
    SourceKind,
    VersionLifecycleStatus,
)
from open_deep_research.research.coverage import (
    CoveragePolicy,
    CoverageStatus,
    GovernedEvidenceRef,
)
from open_deep_research.research.requirements import RequirementMaterializer


NOW = datetime(2026, 1, 10, tzinfo=UTC)


def plan(brief="Establish the requirement"):
    return asyncio.run(
        RequirementMaterializer().materialize(
            research_brief=brief,
            scope_id="scope-test",
            run_id="run-test",
            created_at=NOW,
        )
    )


def evidence_ref(
    requirement_id,
    *,
    relation=EvidenceRelation.SUPPORTS,
    directness=EvidenceDirectness.DIRECT,
    validation_status=EvidenceValidationStatus.VALIDATED,
    authority=AuthorityClass.PRIMARY,
    lifecycle=VersionLifecycleStatus.ACTIVE,
    confidence=0.9,
    valid_to=None,
    source_suffix="one",
):
    scope_id = "scope-test"
    source = Source(
        scope_id=scope_id,
        kind=SourceKind.WEB,
        canonical_uri=f"https://example.com/{source_suffix}",
        display_name=f"Source {source_suffix}",
        authority_class=authority,
    )
    document = Document(
        scope_id=scope_id,
        source_id=source.source_id,
        logical_key=f"doc-{source_suffix}",
        title="Evidence document",
        media_type="text/html",
    )
    content = f"Direct evidence {source_suffix}"
    digest = sha256_bytes(content.encode())
    version = DocumentVersion(
        scope_id=scope_id,
        document_id=document.document_id,
        blob_id=f"blob-{source_suffix}",
        content_sha256=digest,
        version_number=1,
        retrieved_at=NOW - timedelta(days=3),
        published_at=NOW - timedelta(days=4),
        valid_to=valid_to,
        lifecycle_status=lifecycle,
    )
    chunk = Chunk(
        **ChunkInput(ordinal=0, text=content).model_dump(
            exclude={"schema_version"}
        ),
        scope_id=scope_id,
        version_id=version.version_id,
    )
    evidence = Evidence(
        scope_id=scope_id,
        chunk_id=chunk.chunk_id,
        requirement_id=requirement_id,
        excerpt=content,
        relation=relation,
        directness=directness,
        confidence=confidence,
        retrieval_method="fixture",
        validation_status=validation_status,
    )
    return GovernedEvidenceRef(
        evidence=evidence,
        chunk=chunk,
        version=version,
        document=document,
        source=source,
    )


def test_active_validated_direct_authoritative_evidence_covers_requirement():
    requirement_set = plan()
    ref = evidence_ref(requirement_set.requirements[0].requirement_id)

    report = CoveragePolicy().assess(requirement_set, (ref,), as_of=NOW)

    assert report.required_complete
    assert report.assessments[0].status is CoverageStatus.COVERED
    assert report.covered_requirement_ids == requirement_set.requirement_ids


@pytest.mark.parametrize(
    ("changes", "expected_reason"),
    [
        (
            {"validation_status": EvidenceValidationStatus.PENDING},
            "evidence_not_validated",
        ),
        ({"directness": EvidenceDirectness.INDIRECT}, "evidence_not_direct"),
        ({"authority": AuthorityClass.UNKNOWN}, "source_authority_not_accepted"),
        (
            {"lifecycle": VersionLifecycleStatus.STALE},
            "version_not_active",
        ),
        ({"confidence": 0.2}, "evidence_below_confidence_threshold"),
        ({"valid_to": NOW - timedelta(days=1)}, "version_no_longer_valid"),
    ],
)
def test_hard_quality_gate_reports_partial_coverage(changes, expected_reason):
    requirement_set = plan()
    ref = evidence_ref(requirement_set.requirements[0].requirement_id, **changes)

    assessment = CoveragePolicy().assess(
        requirement_set, (ref,), as_of=NOW
    ).assessments[0]

    assert assessment.status is CoverageStatus.PARTIAL
    assert expected_reason in assessment.reasons
    assert not assessment.evidence_ids


def test_soft_deleted_chain_is_not_usable():
    requirement_set = plan()
    ref = evidence_ref(requirement_set.requirements[0].requirement_id)
    deleted_source = ref.source.model_copy(update={"soft_deleted_at": NOW})
    deleted_ref = GovernedEvidenceRef(
        evidence=ref.evidence,
        chunk=ref.chunk,
        version=ref.version,
        document=ref.document,
        source=deleted_source,
    )

    assessment = CoveragePolicy().assess(
        requirement_set, (deleted_ref,), as_of=NOW
    ).assessments[0]

    assert assessment.status is CoverageStatus.PARTIAL
    assert "evidence_chain_soft_deleted" in assessment.reasons


def test_validated_direct_contradiction_dominates_support():
    requirement_set = plan()
    requirement_id = requirement_set.requirements[0].requirement_id
    support = evidence_ref(requirement_id, source_suffix="support")
    conflict = evidence_ref(
        requirement_id,
        relation=EvidenceRelation.CONTRADICTS,
        source_suffix="conflict",
    )

    assessment = CoveragePolicy().assess(
        requirement_set, (support, conflict), as_of=NOW
    ).assessments[0]

    assert assessment.status is CoverageStatus.CONTRADICTED
    assert assessment.missing_aspects == ("resolve_conflicting_evidence",)


def test_evidence_bound_to_one_requirement_does_not_cover_another():
    first = plan("First distinct requirement")
    second = plan("Second distinct requirement")
    ref = evidence_ref(first.requirements[0].requirement_id)

    assessment = CoveragePolicy().assess(second, (ref,), as_of=NOW).assessments[0]

    assert assessment.status is CoverageStatus.MISSING
    assert assessment.reasons == ("no_evidence_for_requirement",)


def test_minimum_distinct_sources_and_age_are_enforced():
    requirement_set = plan()
    requirement_id = requirement_set.requirements[0].requirement_id
    ref = evidence_ref(requirement_id)
    policy = CoveragePolicy(
        min_direct_evidence=2,
        min_distinct_sources=2,
        max_evidence_age_days=2,
    )

    assessment = policy.assess(requirement_set, (ref,), as_of=NOW).assessments[0]

    assert assessment.status is CoverageStatus.PARTIAL
    assert "evidence_too_old" in assessment.reasons
    assert set(assessment.missing_aspects) == {
        "direct_validated_evidence",
        "distinct_authoritative_sources",
    }


def test_governed_reference_rejects_a_broken_chain():
    requirement_set = plan()
    ref = evidence_ref(requirement_set.requirements[0].requirement_id)
    other = evidence_ref(
        requirement_set.requirements[0].requirement_id,
        source_suffix="other",
    )

    with pytest.raises(ValueError, match="not resolvable"):
        GovernedEvidenceRef(
            evidence=ref.evidence,
            chunk=ref.chunk,
            version=ref.version,
            document=ref.document,
            source=other.source,
        )
