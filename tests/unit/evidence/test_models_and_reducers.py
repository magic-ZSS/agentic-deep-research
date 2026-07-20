from datetime import UTC, datetime, timedelta
from itertools import permutations

import pytest

from open_deep_research.evidence.models import (
    Evidence,
    EvidenceValidationStatus,
    Requirement,
    is_evidence_citable,
)
from open_deep_research.evidence.reducers import stable_id_reducer
from open_deep_research.knowledge.models import (
    Chunk,
    ChunkLocatorType,
    Document,
    DocumentVersion,
    Source,
    SourceKind,
    VersionLifecycleStatus,
)


def _citation_chain(version_status, evidence_status):
    now = datetime.now(UTC)
    source = Source(
        scope_id="scope_test",
        kind=SourceKind.WEB,
        canonical_uri="https://example.com/paper",
        display_name="Paper",
    )
    document = Document(
        scope_id="scope_test",
        source_id=source.source_id,
        logical_key="paper",
        title="Paper",
        media_type="text/plain",
    )
    version = DocumentVersion(
        scope_id="scope_test",
        document_id=document.document_id,
        blob_id="blob_test",
        content_sha256="a" * 64,
        version_number=1,
        retrieved_at=now,
        valid_from=now - timedelta(days=1),
        valid_to=now + timedelta(days=1),
        lifecycle_status=version_status,
    )
    chunk = Chunk(
        scope_id="scope_test",
        version_id=version.version_id,
        ordinal=0,
        text="Direct supporting text",
        locator_type=ChunkLocatorType.PAGE,
        page_start=1,
    )
    evidence = Evidence(
        scope_id="scope_test",
        chunk_id=chunk.chunk_id,
        excerpt="Direct supporting text",
        confidence=0.9,
        retrieval_method="unit-test",
        validation_status=evidence_status,
    )
    return source, document, version, chunk, evidence


def test_version_lifecycle_and_evidence_validation_are_independent():
    assert {item.value for item in VersionLifecycleStatus} == {
        "candidate",
        "active",
        "stale",
        "superseded",
        "quarantined",
        "archived",
    }
    assert {item.value for item in EvidenceValidationStatus} == {
        "pending",
        "validated",
        "rejected",
    }
    source, document, version, chunk, evidence = _citation_chain(
        VersionLifecycleStatus.ACTIVE, EvidenceValidationStatus.VALIDATED
    )
    assert is_evidence_citable(evidence, chunk, version, document, source)
    for version_status, evidence_status in (
        (VersionLifecycleStatus.CANDIDATE, EvidenceValidationStatus.VALIDATED),
        (VersionLifecycleStatus.ACTIVE, EvidenceValidationStatus.PENDING),
        (VersionLifecycleStatus.STALE, EvidenceValidationStatus.VALIDATED),
    ):
        chain = _citation_chain(version_status, evidence_status)
        assert not is_evidence_citable(
            chain[4], chain[3], chain[2], chain[1], chain[0]
        )

    unrelated = Document(
        scope_id="scope_test",
        source_id=source.source_id,
        logical_key="unrelated",
        title="Unrelated",
        media_type="text/plain",
    )
    assert not is_evidence_citable(
        evidence, chunk, version, unrelated, source
    )


def test_requirement_and_evidence_schema_round_trip():
    requirement = Requirement(scope_id="scope_test", run_id="run", text="Need proof")
    evidence = Evidence(
        scope_id="scope_test",
        chunk_id="chunk_test",
        requirement_id=requirement.requirement_id,
        excerpt="Proof",
        confidence=1,
        retrieval_method="unit-test",
    )
    assert Requirement.model_validate_json(requirement.model_dump_json()) == requirement
    assert Evidence.model_validate_json(evidence.model_dump_json()) == evidence


def test_stable_id_reducer_is_deduplicating_order_independent_and_batch_invariant():
    expected = ["evd_a", "evd_b", "src_a", "src_z"]
    values = ["src_z", "evd_b", "src_a", "evd_b", "evd_a"]
    for ordering in permutations(values[:3]):
        assert stable_id_reducer(list(ordering), values[3:]) == expected
    left = stable_id_reducer([], values[:2])
    batched = stable_id_reducer(left, values[2:])
    assert batched == stable_id_reducer([], values)
    assert stable_id_reducer(["old"], {"type": "override", "value": values}) == expected
    with pytest.raises(TypeError):
        stable_id_reducer([], {"unexpected": ["src_a"]})
