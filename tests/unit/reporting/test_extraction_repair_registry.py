from __future__ import annotations

import pytest

from open_deep_research.reporting.extraction import (
    DeterministicClaimExtractor,
    parse_draft,
    strip_untrusted_citation_syntax,
)
from open_deep_research.reporting.models import (
    AtomicClaim,
    AuthorityStatus,
    CitationKey,
    ClaimEvidenceLink,
    LinkOrigin,
    LinkRelation,
    RequiredAction,
    TemporalStatus,
    ValidationResult,
    ValidationStatus,
)
from open_deep_research.reporting.renderer import (
    ReportRenderer,
    validate_registry_consistency,
)
from open_deep_research.reporting.repair import ReportRepairer


@pytest.mark.asyncio
async def test_claim_extraction_is_stable_atomic_and_filters_legacy_diagnostics():
    text = (
        "# Findings\n"
        "Alpha works [[evidence:evd_alpha]]; beta costs 20% [1].\n"
        "think_tool: internal plan\n"
        "Gamma SOURCE 7 is separate."
    )
    draft = parse_draft(text)
    extractor = DeterministicClaimExtractor()
    first = await extractor.extract(draft, ("req-1",))
    second = await extractor.extract(draft, ("req-1",))
    assert [item.claim_id for item in first] == [item.claim_id for item in second]
    assert len(first) == 3
    assert first[0].cited_evidence_ids == ("evd_alpha",)
    assert all("think_tool" not in item.text for item in first)
    assert all("SOURCE 7" not in item.text and "[1]" not in item.text for item in first)


def _result(claim, status, action):
    return ValidationResult(
        claim_id=claim.claim_id,
        status=status,
        links=(),
        failed_checks=("fixture",),
        required_action=action,
        confidence=0,
        policy_version="test-v1",
    )


def test_local_repair_preserves_other_section_hash_and_rejects_stale_patch():
    draft = parse_draft("# Safe\nKeep this section.\n\n# Bad\nUnsupported number is 99.")
    bad = draft.sections[1]
    claim = AtomicClaim(
        section_id=bad.section_id,
        text="Unsupported number is 99.",
        span_start=0,
        span_end=len("Unsupported number is 99."),
    )
    repairer = ReportRepairer()
    patches = repairer.create_patches(
        draft,
        (claim,),
        (_result(claim, ValidationStatus.UNSUPPORTED, RequiredAction.REMOVE),),
    )
    repaired = repairer.apply(draft, patches)
    assert repaired[0].canonical_hash == draft.sections[0].canonical_hash
    assert "99" not in repaired[1].text
    stale = patches[0].model_copy(update={"original_hash": "0" * 64})
    with pytest.raises(ValueError, match="original_hash"):
        repairer.apply(draft, (stale,))


def test_renderer_uses_only_registry_numbers_and_detects_bidirectional_error():
    draft = parse_draft("# Result\nSupported fact [[evidence:evd_one]] [9].")
    claim = AtomicClaim(
        section_id=draft.sections[0].section_id,
        text="Supported fact",
        span_start=0,
        span_end=len("Supported fact"),
    )
    key = CitationKey(source_id="src-one", version_id="ver-one")
    link = ClaimEvidenceLink(
        claim_id=claim.claim_id,
        evidence_id="evd_one",
        chunk_id="chk_one",
        citation_key=key,
        relation=LinkRelation.SUPPORTS,
        origin=LinkOrigin.EXPLICIT_DRAFT_CITATION,
        entailment_score=1,
        directness="direct",
        temporal_status=TemporalStatus.CURRENT,
        authority_status=AuthorityStatus.SUFFICIENT,
        locator="page:1",
        rationale="fixture",
        validator_version="v1",
        accepted=True,
    )
    result = ValidationResult(
        claim_id=claim.claim_id,
        status=ValidationStatus.FULLY_SUPPORTED,
        links=(link,),
        required_action=RequiredAction.KEEP,
        confidence=1,
        policy_version="v1",
    )
    from open_deep_research.reporting.models import SourceRegistryEntry

    registry = (
        SourceRegistryEntry(
            citation_key=key,
            display_number=1,
            title="Source",
            canonical_uri="https://example.test",
            locators_used=("page:1",),
        ),
    )
    rendered = ReportRenderer().render(draft.sections, (claim,), (result,), registry)
    assert "Supported fact[1]" in rendered
    assert "[9]" not in rendered
    with pytest.raises(ValueError):
        validate_registry_consistency(rendered.replace("[1]", "[2]", 1), registry)


def test_legacy_marker_stripping_does_not_treat_diagnostics_as_sources():
    value = strip_untrusted_citation_syntax("Fact [3] SOURCE 4 [[evidence:evd_x]]")
    assert value == "Fact"
