from __future__ import annotations

import json
import re
from datetime import timedelta

import pytest

from open_deep_research.evidence.models import (
    EvidenceDirectness,
    EvidenceValidationStatus,
)
from open_deep_research.evidence.run_store import InMemoryRunEvidenceStore
from open_deep_research.evidence.validation.resolver import EvidenceResolver
from open_deep_research.evidence.validation.retriever import ClaimEvidenceRetriever
from open_deep_research.evidence.validation.validator import (
    CitationValidator,
    EntailmentDecision,
)
from open_deep_research.knowledge.in_memory_repository import InMemoryRepository
from open_deep_research.knowledge.models import (
    AuthorityClass,
    ChunkInput,
    ChunkLocatorType,
    ContentBlob,
    SourceKind,
    VersionLifecycleStatus,
)
from open_deep_research.reporting.models import (
    CitationValidationArtifact,
    LinkRelation,
    ValidationStatus,
)
from open_deep_research.reporting.pipeline import (
    CitationPipeline,
    citation_validation_node,
)
from tests.citation_helpers import NOW, identity, seed_canonical, seed_transient


class ExactFakeEntailment:
    def evaluate(self, claim: str, evidence: str) -> EntailmentDecision:
        def normalize(value: str) -> str:
            return re.sub(r"[^\w]+", " ", value.casefold()).strip()

        supported = normalize(claim) in normalize(evidence)
        return EntailmentDecision(
            score=1.0 if supported else 0.0,
            relation=LinkRelation.SUPPORTS if supported else LinkRelation.CONTEXT,
            rationale="exact-fixture",
        )


def pipeline(repository, scope, access, *, run_store=None, run_id=None):
    resolver = EvidenceResolver(
        repository=repository,
        access=access,
        scope=scope,
        run_store=run_store,
        run_id=run_id,
    )
    return CitationPipeline(
        retriever=ClaimEvidenceRetriever(resolver),
        validator=CitationValidator(evaluator=ExactFakeEntailment()),
    )


@pytest.mark.asyncio
async def test_enforce_removes_unsupported_claim_and_preserves_other_section_hash():
    scope, access = identity()
    repository = InMemoryRepository()
    chain = await seed_canonical(
        repository, scope, access, suffix="supported", text="Supported fact"
    )
    draft = (
        f"# Verified\nSupported fact [[evidence:{chain[-1].evidence_id}]].\n\n"
        "# Unsupported\nThe unsupported number is 99%."
    )
    output = await pipeline(repository, scope, access).run(
        draft, mode="enforce", as_of=NOW
    )
    assert "Supported fact.[1]" in output.final_report
    assert "99%" not in output.final_report
    assert any(
        result.status is ValidationStatus.UNSUPPORTED
        for result in output.artifact.results
    )
    assert output.artifact.patches
    unsupported_claim = next(
        claim for claim in output.artifact.claims if "99%" in claim.text
    )
    assert output.artifact.patches[0].section_id == unsupported_claim.section_id


@pytest.mark.asyncio
async def test_audit_is_byte_preserving_and_off_node_is_noop():
    scope, access = identity()
    repository = InMemoryRepository()
    draft = "# Draft\nUnverified fact."
    output = await pipeline(repository, scope, access).run(
        draft, mode="audit", as_of=NOW
    )
    assert output.final_report == draft
    assert output.artifact.mode == "audit"
    assert await citation_validation_node(
        {"final_report": draft}, {"configurable": {"citation_validation_mode": "off"}}
    ) == {}


@pytest.mark.asyncio
async def test_same_run_transient_is_citable_but_other_run_fails_closed():
    scope, access = identity()
    repository = InMemoryRepository()
    store = InMemoryRunEvidenceStore()
    bundle = await seed_transient(store, scope, run_id="run-a", text="Transient fact")
    draft = f"# Result\nTransient fact [[evidence:{bundle.evidence_id}]]."
    accepted = await pipeline(
        repository, scope, access, run_store=store, run_id="run-a"
    ).run(draft, mode="enforce", as_of=NOW)
    rejected = await pipeline(
        repository, scope, access, run_store=store, run_id="run-b"
    ).run(draft, mode="enforce", as_of=NOW)
    assert accepted.artifact.results[0].status is ValidationStatus.FULLY_SUPPORTED
    assert rejected.artifact.results[0].status is ValidationStatus.UNSUPPORTED
    assert "Transient fact" not in rejected.final_report


@pytest.mark.asyncio
async def test_local_source_never_leaks_internal_windows_path_or_blob_ref():
    scope, access = identity()
    repository = InMemoryRepository()
    chain = await seed_canonical(
        repository,
        scope,
        access,
        suffix="private",
        text="Local verified fact",
        source_kind=SourceKind.LOCAL_FILE,
        internal_ref=r"C:\private\index\secret.blob",
        public_uri="kb://public/private",
    )
    draft = f"# Local\nLocal verified fact [[evidence:{chain[-1].evidence_id}]]."
    output = await pipeline(repository, scope, access).run(
        draft, mode="enforce", as_of=NOW
    )
    serialized = json.dumps(output.model_dump(mode="json"), ensure_ascii=False)
    assert "kb://public/private" in serialized
    assert r"C:\private" not in serialized
    assert "secret.blob" not in serialized


@pytest.mark.asyncio
async def test_explicit_old_version_cannot_be_laundered_by_supplemental_new_version():
    scope, access = identity()
    repository = InMemoryRepository()
    old = await seed_canonical(
        repository,
        scope,
        access,
        suffix="rule-old",
        text="Rule applies",
        valid_to=NOW - timedelta(days=1),
    )
    new = await seed_canonical(
        repository, scope, access, suffix="rule-new", text="Rule applies"
    )
    draft = f"# Rule\nRule applies [[evidence:{old[-1].evidence_id}]]."
    preview = await pipeline(repository, scope, access).run(
        draft, mode="audit", as_of=NOW
    )
    claim_id = preview.artifact.claims[0].claim_id
    output = await pipeline(repository, scope, access).run(
        draft,
        mode="enforce",
        as_of=NOW,
        supplemental_evidence_ids={claim_id: (new[-1].evidence_id,)},
    )
    result = output.artifact.results[0]
    assert result.status is ValidationStatus.UNSUPPORTED
    assert "supplemental_cannot_override_explicit_failure" in result.failed_checks
    assert {link.origin.value for link in result.links} == {
        "explicit_draft_citation",
        "supplemental_retrieval",
    }


@pytest.mark.asyncio
async def test_self_report_is_only_valid_for_attributed_claim():
    scope, access = identity()
    repository = InMemoryRepository()
    chain = await seed_canonical(
        repository,
        scope,
        access,
        suffix="vendor",
        text="Acme claims its product leads the market",
        authority=AuthorityClass.SELF_REPORTED,
    )
    unqualified = await pipeline(repository, scope, access).run(
        f"# Claim\nIts product leads the market [[evidence:{chain[-1].evidence_id}]].",
        mode="enforce",
        as_of=NOW,
    )
    assert unqualified.artifact.results[0].status is ValidationStatus.UNSUPPORTED


@pytest.mark.asyncio
async def test_registry_is_stable_by_source_version_and_merges_same_version_locators():
    scope, access = identity()
    repository = InMemoryRepository()
    first = await seed_canonical(
        repository, scope, access, suffix="version-one", text="First fact"
    )
    second_blob = ContentBlob.from_bytes(
        scope_id=scope.scope_id,
        content=b"Second fact and extra fact",
        media_type="text/plain",
        storage_ref="phase6/version-two.blob",
    )
    second_document = await repository.upsert_document(
        access,
        scope,
        source_id=first[0].source_id,
        logical_key="version-two-document",
        title="Second document version fixture",
        media_type="text/plain",
    )
    second_version = await repository.add_version(
        access,
        scope,
        document_id=second_document.document_id,
        blob=second_blob,
        retrieved_at=NOW,
        valid_from=NOW - timedelta(days=1),
        valid_to=NOW + timedelta(days=1),
        lifecycle_status=VersionLifecycleStatus.ACTIVE,
    )
    second_chunks = await repository.add_chunks(
        access,
        scope,
        second_version.version_id,
        [
            ChunkInput(
                ordinal=0,
                text="Second fact",
                locator_type=ChunkLocatorType.PAGE,
                page_start=2,
            ),
            ChunkInput(
                ordinal=1,
                text="Extra fact",
                locator_type=ChunkLocatorType.PAGE,
                page_start=3,
            ),
        ],
    )
    second_evidence = [
        await repository.add_evidence(
            access,
            scope,
            chunk_id=chunk.chunk_id,
            excerpt=chunk.text,
            confidence=0.95,
            retrieval_method="phase6-fixture",
            directness=EvidenceDirectness.DIRECT,
            validation_status=EvidenceValidationStatus.VALIDATED,
        )
        for chunk in second_chunks
    ]
    draft = (
        f"# Versions\nFirst fact [[evidence:{first[-1].evidence_id}]]; "
        f"Second fact [[evidence:{second_evidence[0].evidence_id}]]; "
        f"Extra fact [[evidence:{second_evidence[1].evidence_id}]]."
    )
    output = await pipeline(repository, scope, access).run(
        draft, mode="enforce", as_of=NOW
    )
    assert [entry.display_number for entry in output.artifact.registry] == [1, 2], (
        output.artifact.model_dump(mode="json")
    )
    assert len({entry.citation_key.value for entry in output.artifact.registry}) == 2
    assert sorted(len(entry.locators_used) for entry in output.artifact.registry) == [1, 2]
    assert output.final_report.count("### Sources") == 1


@pytest.mark.asyncio
async def test_artifact_checkpoint_round_trip_and_replay_are_idempotent():
    scope, access = identity()
    repository = InMemoryRepository()
    chain = await seed_canonical(
        repository, scope, access, suffix="resume", text="Checkpoint fact"
    )
    draft = f"# Resume\nCheckpoint fact [[evidence:{chain[-1].evidence_id}]]."
    citation_pipeline = pipeline(repository, scope, access)
    first = await citation_pipeline.run(draft, mode="enforce", as_of=NOW)
    restored = CitationValidationArtifact.model_validate_json(
        first.artifact.model_dump_json()
    )
    replay = await citation_pipeline.run(draft, mode="enforce", as_of=NOW)
    assert restored == first.artifact
    assert replay.artifact.artifact_id == first.artifact.artifact_id
    assert replay.final_report == first.final_report


@pytest.mark.asyncio
async def test_enforce_fails_closed_when_pipeline_dependency_is_missing():
    result = await citation_validation_node(
        {"final_report": "Unsafe fact"},
        {"configurable": {"citation_validation_mode": "enforce"}},
    )
    assert result["citation_validation_error"] == "citation_pipeline_unavailable"
    assert "Unsafe fact" not in result["final_report"]
