import hashlib
import inspect
import json

import pytest
from pydantic import ValidationError

from open_deep_research.evaluation.claim_scorer import (
    CLAIM_SCORER_STEP_NAME,
    CLAIM_SCORER_VERSION,
    ClaimCitationScorer,
    ClaimScorerCoverageError,
    ClaimScorerInput,
    ClaimScorerJudgeClaim,
    ClaimScorerJudgeOutput,
    ClaimScorerResponseError,
    ClaimSourceAuthority,
    ClaimValidationStatus,
    QwenClaimCitationScorer,
    build_live_qwen_claim_scorer,
    claim_observations_payload,
    render_claim_scorer_prompt,
    report_candidate_units,
)
from open_deep_research.evaluation.custom_metrics import (
    SCORER_VERSION as METRIC_SCORER_VERSION,
)


class FakeStructuredAdapter:
    def __init__(self, output):
        self.output = output
        self.calls = []

    async def a_generate(self, prompt, schema=None):
        self.calls.append((prompt, schema))
        return self.output


def judge_claim(
    text,
    *,
    checkable=True,
    citation_ids=(1,),
    status=ClaimValidationStatus.FULLY_SUPPORTED,
    evidence_valid=True,
    authority=ClaimSourceAuthority.OFFICIAL,
    qualified=False,
):
    return ClaimScorerJudgeClaim(
        text=text,
        checkable=checkable,
        citation_ids=citation_ids,
        validation_status=status,
        evidence_valid=evidence_valid,
        source_authority=authority,
        correctly_qualified=qualified,
    )


@pytest.mark.asyncio
async def test_fake_adapter_produces_stable_strict_json_safe_observations():
    report = "The mission launched in 2024 [1].\n\n## Sources\n[1] NASA official record"
    digest = hashlib.sha256(report.encode()).hexdigest()
    adapter = FakeStructuredAdapter(
        ClaimScorerJudgeOutput(
            claims=(judge_claim("The mission launched in 2024 [1]."),)
        )
    )
    scorer = QwenClaimCitationScorer(adapter)

    first = await scorer.score(
        prompt="When did the mission launch?",
        report=report,
        retrieval_context=("[1] NASA says the mission launched in 2024.",),
    )
    second = await scorer.score(
        prompt="When did the mission launch?",
        report=report,
        retrieval_context=("[1] NASA says the mission launched in 2024.",),
    )

    assert isinstance(scorer, ClaimCitationScorer)
    assert first == second
    assert first.report_sha256 == digest
    assert hashlib.sha256(report.encode()).hexdigest() == digest
    assert first.claims[0].claim_id.startswith("eval-claim-")
    assert adapter.calls[0][1] is ClaimScorerJudgeOutput
    payload = claim_observations_payload(first)
    assert payload == first.observations_payload
    assert payload[0]["validation_status"] == "fully_supported"
    assert payload[0]["source_authority"] == "official"
    assert payload[0]["scorer_version"] == CLAIM_SCORER_VERSION
    json.dumps(payload)


@pytest.mark.asyncio
async def test_variant_is_not_an_input_and_same_inputs_render_identically():
    report = "A checkable statement [1].\n\n## Sources\n[1] Official record"
    output = ClaimScorerJudgeOutput(
        claims=(judge_claim("A checkable statement [1]."),)
    )
    adapter = FakeStructuredAdapter(output)
    scorer = QwenClaimCitationScorer(adapter)

    results = []
    for _experiment_variant in ("baseline", "citation_validator"):
        results.append(
            await scorer.score(
                prompt="Evaluate the statement.",
                report=report,
                retrieval_context=("[1] Official record",),
            )
        )

    assert results[0] == results[1]
    assert adapter.calls[0][0] == adapter.calls[1][0]
    rendered = json.dumps(results[0].model_dump(mode="json"), sort_keys=True)
    for variant_id in ("baseline", "citation_validator"):
        assert variant_id not in adapter.calls[0][0]
        assert variant_id not in rendered
    assert set(inspect.signature(ClaimCitationScorer.score).parameters) == {
        "self",
        "prompt",
        "report",
        "retrieval_context",
    }
    assert CLAIM_SCORER_STEP_NAME == "claim_citation_scorer"


@pytest.mark.asyncio
async def test_orphan_explicit_citation_cannot_be_washed_by_other_context():
    report = "The launch occurred in 2024 [9].\n\n## Sources\n[1] Unrelated source"
    adapter = FakeStructuredAdapter(
        ClaimScorerJudgeOutput(
            claims=(
                judge_claim(
                    "The launch occurred in 2024 [9].",
                    citation_ids=(9,),
                    status=ClaimValidationStatus.FULLY_SUPPORTED,
                    evidence_valid=True,
                    authority=ClaimSourceAuthority.OFFICIAL,
                ),
            )
        )
    )

    result = await QwenClaimCitationScorer(adapter).score(
        prompt="When was the launch?",
        report=report,
        retrieval_context=("A different source confirms the 2024 launch.",),
    )

    claim = result.claims[0]
    assert claim.citation_ids == (9,)
    assert claim.validation_status is ClaimValidationStatus.UNSUPPORTED
    assert claim.evidence_valid is False
    assert claim.source_authority is ClaimSourceAuthority.UNKNOWN
    assert "must never rescue, replace, or wash out" in adapter.calls[0][0]


@pytest.mark.asyncio
async def test_project_rejects_empty_claims_and_mismatched_citation_ids():
    empty = QwenClaimCitationScorer(
        FakeStructuredAdapter(ClaimScorerJudgeOutput(claims=()))
    )
    with pytest.raises(ClaimScorerResponseError, match="empty claim list"):
        await empty.score(
            prompt="Question",
            report="A factual answer.",
            retrieval_context=(),
        )

    mismatch = QwenClaimCitationScorer(
        FakeStructuredAdapter(
            ClaimScorerJudgeOutput(
                claims=(
                    judge_claim(
                        "A factual answer [1].",
                        citation_ids=(2,),
                    ),
                )
            )
        )
    )
    with pytest.raises(ClaimScorerResponseError, match="do not match"):
        await mismatch.score(
            prompt="Question",
            report="A factual answer [1].\n\n## Sources\n[1] Source",
            retrieval_context=("Source",),
        )


@pytest.mark.asyncio
async def test_judge_cannot_omit_or_reorder_deterministic_candidates():
    report = (
        "First factual assertion [1]. Second uncited factual assertion.\n\n"
        "## Sources\n[1] Official source"
    )
    first = judge_claim("First factual assertion [1].")
    omitted = QwenClaimCitationScorer(
        FakeStructuredAdapter(ClaimScorerJudgeOutput(claims=(first,)))
    )
    with pytest.raises(ClaimScorerResponseError, match="does not cover every"):
        await omitted.score(
            prompt="Question",
            report=report,
            retrieval_context=("[1] Bound evidence for the first assertion",),
        )

    second = judge_claim(
        "Second uncited factual assertion.",
        citation_ids=(),
        evidence_valid=False,
        authority=ClaimSourceAuthority.UNKNOWN,
    )
    reordered = QwenClaimCitationScorer(
        FakeStructuredAdapter(
            ClaimScorerJudgeOutput(claims=(second, first))
        )
    )
    with pytest.raises(ClaimScorerResponseError, match="ordered candidate"):
        await reordered.score(
            prompt="Question",
            report=report,
            retrieval_context=("[1] Bound evidence for the first assertion",),
        )

    candidates = report_candidate_units(report)
    assert [item.text for item in candidates] == [
        "First factual assertion [1].",
        "Second uncited factual assertion.",
    ]


@pytest.mark.asyncio
async def test_unkeyed_context_is_withheld_and_cannot_rebind_existing_source_id():
    report = "The launch occurred in 2024 [1].\n\n## Sources\n[1] Official source"
    output = ClaimScorerJudgeOutput(
        claims=(judge_claim("The launch occurred in 2024 [1]."),)
    )
    adapter = FakeStructuredAdapter(output)

    result = await QwenClaimCitationScorer(adapter).score(
        prompt="When was the launch?",
        report=report,
        retrieval_context=("UNBOUND-CONTEXT claims the launch occurred in 2024.",),
    )

    assert result.bound_context_count == 0
    assert result.unbound_context_count == 1
    assert result.coverage_complete is True
    assert result.claims[0].validation_status is ClaimValidationStatus.UNSUPPORTED
    assert result.claims[0].evidence_valid is False
    assert "UNBOUND-CONTEXT" not in adapter.calls[0][0]
    payload = json.loads(adapter.calls[0][0][adapter.calls[0][0].rfind("\n") + 1 :])
    assert payload["retrieval_context"]["sources_registry"]["1"] == {
        "bibliography_entries": ["Official source"],
        "binding_proven": False,
        "bound_retrieval_context": [],
        "citation_id": 1,
        "unambiguous": True,
    }


@pytest.mark.asyncio
async def test_unsupported_layout_fails_before_dispatch_instead_of_losing_claims():
    adapter = FakeStructuredAdapter(ClaimScorerJudgeOutput(claims=()))
    scorer = QwenClaimCitationScorer(adapter)

    with pytest.raises(ClaimScorerCoverageError, match="table content"):
        await scorer.score(
            prompt="Question",
            report="| Metric | Value |\n| --- | --- |\n| Growth | 20% |",
            retrieval_context=(),
        )

    assert adapter.calls == []


def test_input_and_provider_schemas_forbid_feature_and_repair_fields():
    with pytest.raises(ValidationError, match="variant_id"):
        ClaimScorerInput(
            prompt="Question",
            report="Answer",
            retrieval_context=(),
            variant_id="baseline",
        )
    with pytest.raises(ValidationError, match="citation_validation_artifact"):
        ClaimScorerInput(
            prompt="Question",
            report="Answer",
            retrieval_context=(),
            citation_validation_artifact={"claims": []},
        )
    with pytest.raises(ValidationError, match="repaired_report"):
        ClaimScorerJudgeOutput(
            claims=(
                judge_claim(
                    "Answer",
                    citation_ids=(),
                    status=ClaimValidationStatus.UNSUPPORTED,
                    evidence_valid=False,
                    authority=ClaimSourceAuthority.UNKNOWN,
                ),
            ),
            repaired_report="Changed answer",
        )


def test_live_factory_accepts_adapter_or_forwards_metering_configuration():
    output = ClaimScorerJudgeOutput(
        claims=(
            judge_claim(
                "Answer",
                citation_ids=(),
                status=ClaimValidationStatus.UNSUPPORTED,
                evidence_valid=False,
                authority=ClaimSourceAuthority.UNKNOWN,
            ),
        )
    )
    fake = FakeStructuredAdapter(output)
    direct = build_live_qwen_claim_scorer(adapter=fake)
    assert isinstance(direct, QwenClaimCitationScorer)

    captured = {}
    reservation_callback = object()

    def adapter_factory(**kwargs):
        captured.update(kwargs)
        return fake

    built = build_live_qwen_claim_scorer(
        adapter_factory=adapter_factory,
        audit_model_id="openai:qwen3.7-plus",
        environment={"SAFE": "value"},
        reservation_callback=reservation_callback,
        max_output_tokens=1234,
        timeout_seconds=12,
    )

    assert isinstance(built, QwenClaimCitationScorer)
    assert captured["reservation_callback"] is reservation_callback
    assert captured["audit_model_id"] == "openai:qwen3.7-plus"
    assert captured["max_output_tokens"] == 1234
    assert captured["timeout_seconds"] == 12


def test_rendered_payload_contains_only_the_three_canonical_inputs():
    inputs = ClaimScorerInput(
        prompt="Question",
        report="Answer",
        retrieval_context=("Evidence",),
    )
    rendered = render_claim_scorer_prompt(inputs)
    payload = json.loads(rendered[rendered.rfind("\n") + 1 :])

    assert set(payload) == {"prompt", "report", "retrieval_context"}
    assert payload["report"]["candidate_units"] == [
        {"citation_ids": [], "ordinal": 0, "text": "Answer"}
    ]
    assert payload["retrieval_context"] == {
        "raw_unbound_context_omitted": True,
        "sources_registry": {},
        "unbound_item_count": 1,
    }
    assert "Evidence" not in rendered
    assert METRIC_SCORER_VERSION == CLAIM_SCORER_VERSION
