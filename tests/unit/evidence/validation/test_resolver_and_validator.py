from __future__ import annotations

from datetime import timedelta

import pytest

from open_deep_research.evidence.models import EvidenceDirectness
from open_deep_research.evidence.run_store import InMemoryRunEvidenceStore
from open_deep_research.evidence.validation.resolver import EvidenceResolver
from open_deep_research.evidence.validation.validator import (
    CitationValidator,
    EntailmentDecision,
)
from open_deep_research.knowledge.in_memory_repository import InMemoryRepository
from open_deep_research.knowledge.models import AuthorityClass
from open_deep_research.knowledge.repositories import RepositoryNotFoundError
from open_deep_research.reporting.models import (
    AtomicClaim,
    CitationKey,
    ClaimType,
    LinkOrigin,
    LinkRelation,
    ValidationStatus,
)
from tests.citation_helpers import NOW, identity, seed_canonical, seed_transient


class FakeEntailment:
    def __init__(self, score=1.0, relation=LinkRelation.SUPPORTS):
        self.score = score
        self.relation = relation

    def evaluate(self, claim, evidence):
        return EntailmentDecision(
            score=self.score, relation=self.relation, rationale="fake"
        )


def claim(text, evidence_id=None, *, claim_type=ClaimType.FACTUAL, key=None):
    return AtomicClaim(
        section_id="section-test",
        text=text,
        span_start=0,
        span_end=len(text),
        claim_type=claim_type,
        cited_evidence_ids=(evidence_id,) if evidence_id else (),
        cited_citation_keys=(key,) if key else (),
    )


@pytest.mark.asyncio
async def test_canonical_and_same_run_transient_resolve_but_other_run_fails():
    scope, access = identity()
    repository = InMemoryRepository()
    chain = await seed_canonical(
        repository, scope, access, suffix="canonical", text="Canonical fact"
    )
    run_store = InMemoryRunEvidenceStore()
    transient = await seed_transient(
        run_store, scope, run_id="run-one", text="Transient fact"
    )
    resolver = EvidenceResolver(
        repository=repository,
        access=access,
        scope=scope,
        run_store=run_store,
        run_id="run-one",
    )
    assert (await resolver.resolve(chain[-1].evidence_id, as_of=NOW)).eligible
    assert (await resolver.resolve(transient.evidence_id, as_of=NOW)).eligible
    other = EvidenceResolver(
        repository=repository,
        access=access,
        scope=scope,
        run_store=run_store,
        run_id="run-other",
    )
    with pytest.raises(RepositoryNotFoundError):
        await other.resolve(transient.evidence_id, as_of=NOW)


@pytest.mark.asyncio
async def test_related_but_indirect_evidence_is_not_fully_supported():
    scope, access = identity()
    repository = InMemoryRepository()
    chain = await seed_canonical(
        repository,
        scope,
        access,
        suffix="indirect",
        text="The topic is related but does not establish the mechanism",
        directness=EvidenceDirectness.INDIRECT,
    )
    resolved = await EvidenceResolver(
        repository=repository, access=access, scope=scope
    ).resolve(chain[-1].evidence_id, as_of=NOW)
    target = claim("The mechanism is proven", chain[-1].evidence_id)
    result = await CitationValidator(evaluator=FakeEntailment()).validate(
        target, [(resolved, LinkOrigin.EXPLICIT_DRAFT_CITATION)], as_of=NOW
    )
    assert result.status is ValidationStatus.UNSUPPORTED
    assert "not_direct" in result.failed_checks
    assert result.links[0].chunk_id == chain[-2].chunk_id


@pytest.mark.asyncio
async def test_stale_version_and_numeric_mismatch_fail_hard_checks():
    scope, access = identity()
    repository = InMemoryRepository()
    old = await seed_canonical(
        repository,
        scope,
        access,
        suffix="old",
        text="The threshold is 10 percent",
        valid_to=NOW - timedelta(days=1),
    )
    resolved = await EvidenceResolver(
        repository=repository, access=access, scope=scope
    ).resolve(old[-1].evidence_id, as_of=NOW)
    target = claim(
        "The threshold is 20%",
        old[-1].evidence_id,
        claim_type=ClaimType.NUMERIC,
    )
    result = await CitationValidator(evaluator=FakeEntailment()).validate(
        target, [(resolved, LinkOrigin.EXPLICIT_DRAFT_CITATION)], as_of=NOW
    )
    assert result.status is ValidationStatus.UNSUPPORTED
    assert "numeric_mismatch" in result.failed_checks
    assert "temporal:stale" in result.failed_checks


@pytest.mark.asyncio
async def test_corporate_self_report_only_supports_attributed_claim():
    scope, access = identity()
    repository = InMemoryRepository()
    chain = await seed_canonical(
        repository,
        scope,
        access,
        suffix="company",
        text="Acme says its service leads the market",
        authority=AuthorityClass.SELF_REPORTED,
    )
    resolved = await EvidenceResolver(
        repository=repository, access=access, scope=scope
    ).resolve(chain[-1].evidence_id, as_of=NOW)
    validator = CitationValidator(evaluator=FakeEntailment())
    generic = await validator.validate(
        claim("The service leads the market", chain[-1].evidence_id),
        [(resolved, LinkOrigin.EXPLICIT_DRAFT_CITATION)],
        as_of=NOW,
    )
    attributed = await validator.validate(
        claim(
            "Acme claims the service leads the market",
            chain[-1].evidence_id,
            claim_type=ClaimType.CORPORATE_ATTRIBUTION,
        ),
        [(resolved, LinkOrigin.EXPLICIT_DRAFT_CITATION)],
        as_of=NOW,
    )
    assert generic.status is ValidationStatus.UNSUPPORTED
    assert attributed.status is ValidationStatus.FULLY_SUPPORTED


@pytest.mark.asyncio
async def test_explicit_wrong_version_is_not_laundered_by_supplemental_support():
    scope, access = identity()
    repository = InMemoryRepository()
    old = await seed_canonical(
        repository,
        scope,
        access,
        suffix="old-rule",
        text="Old rule",
        valid_to=NOW - timedelta(days=1),
    )
    new = await seed_canonical(
        repository, scope, access, suffix="new-rule", text="New rule applies"
    )
    resolver = EvidenceResolver(repository=repository, access=access, scope=scope)
    target = claim(
        "New rule applies",
        key=CitationKey(
            source_id=old[0].source_id, version_id=old[2].version_id
        ),
    )
    result = await CitationValidator(evaluator=FakeEntailment()).validate(
        target,
        [
            (
                await resolver.resolve(old[-1].evidence_id, as_of=NOW),
                LinkOrigin.EXPLICIT_DRAFT_CITATION,
            ),
            (
                await resolver.resolve(new[-1].evidence_id, as_of=NOW),
                LinkOrigin.SUPPLEMENTAL_RETRIEVAL,
            ),
        ],
        as_of=NOW,
    )
    assert result.status is ValidationStatus.UNSUPPORTED
    assert "supplemental_cannot_override_explicit_failure" in result.failed_checks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("score", "relation", "kind", "expected"),
    [
        (1.0, LinkRelation.SUPPORTS, ClaimType.FACTUAL, ValidationStatus.FULLY_SUPPORTED),
        (0.5, LinkRelation.SUPPORTS, ClaimType.FACTUAL, ValidationStatus.PARTIALLY_SUPPORTED),
        (0.0, LinkRelation.CONTEXT, ClaimType.FACTUAL, ValidationStatus.UNSUPPORTED),
        (1.0, LinkRelation.CONTRADICTS, ClaimType.FACTUAL, ValidationStatus.CONTRADICTED),
        (1.0, LinkRelation.SUPPORTS, ClaimType.SUBJECTIVE, ValidationStatus.NOT_CHECKABLE),
    ],
)
async def test_five_way_status_schema(score, relation, kind, expected):
    scope, access = identity()
    repository = InMemoryRepository()
    chain = await seed_canonical(
        repository, scope, access, suffix=f"five-{score}-{relation}", text="Supported fact"
    )
    resolved = await EvidenceResolver(
        repository=repository, access=access, scope=scope
    ).resolve(chain[-1].evidence_id, as_of=NOW)
    result = await CitationValidator(
        evaluator=FakeEntailment(score, relation)
    ).validate(
        claim("Supported fact", chain[-1].evidence_id, claim_type=kind),
        [(resolved, LinkOrigin.EXPLICIT_DRAFT_CITATION)],
        as_of=NOW,
    )
    assert result.status is expected
