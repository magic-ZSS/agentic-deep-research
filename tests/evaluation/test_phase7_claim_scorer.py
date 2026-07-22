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
        if callable(self.output):
            return self.output(prompt, schema)
        return self.output


def judge_claim(
    ordinal=0,
    *,
    checkable=True,
    status=ClaimValidationStatus.FULLY_SUPPORTED,
    evidence_valid=True,
    authority=ClaimSourceAuthority.OFFICIAL,
    qualified=False,
):
    return ClaimScorerJudgeClaim(
        ordinal=ordinal,
        checkable=checkable,
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
        ClaimScorerJudgeOutput(claims=(judge_claim(),))
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
    output = ClaimScorerJudgeOutput(claims=(judge_claim(),))
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
async def test_project_rejects_empty_claims():
    empty = QwenClaimCitationScorer(
        FakeStructuredAdapter(ClaimScorerJudgeOutput(claims=()))
    )
    with pytest.raises(ClaimScorerResponseError, match="empty claim list"):
        await empty.score(
            prompt="Question",
            report="A factual answer.",
            retrieval_context=(),
        )


@pytest.mark.asyncio
async def test_judge_cannot_omit_or_reorder_deterministic_candidates():
    report = (
        "First factual assertion [1]. Second uncited factual assertion.\n\n"
        "## Sources\n[1] Official source"
    )
    first = judge_claim(0)
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
        1,
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
    output = ClaimScorerJudgeOutput(claims=(judge_claim(),))
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
async def test_thirty_six_claims_are_scored_in_six_deterministic_evidence_batches():
    body = "\n".join(
        f"Claim {ordinal:02d} is supported [{ordinal + 1}]."
        for ordinal in range(36)
    )
    sources = "\n".join(
        f"[{identifier}] Official source {identifier}"
        for identifier in range(1, 37)
    )
    report = f"{body}\n\n## Sources\n{sources}"
    retrieval_context = tuple(
        f"[{identifier}] Bound evidence {identifier}"
        for identifier in range(1, 37)
    )

    def classify_batch(prompt, schema):
        assert schema is ClaimScorerJudgeOutput
        payload = json.loads(prompt[prompt.rfind("\n") + 1 :])
        return ClaimScorerJudgeOutput(
            claims=tuple(
                judge_claim(candidate["ordinal"])
                for candidate in payload["report"]["candidate_units"]
            )
        )

    adapter = FakeStructuredAdapter(classify_batch)
    scorer = QwenClaimCitationScorer(adapter, batch_size=6)
    first = await scorer.score(
        prompt="Classify all claims.",
        report=report,
        retrieval_context=retrieval_context,
    )
    first_prompts = [prompt for prompt, _schema in adapter.calls]
    second = await scorer.score(
        prompt="Classify all claims.",
        report=report,
        retrieval_context=retrieval_context,
    )

    assert first == second
    assert first.candidate_count == len(first.claims) == 36
    assert len(adapter.calls) == 12
    assert [prompt for prompt, _schema in adapter.calls[6:]] == first_prompts
    assert [claim.text for claim in first.claims] == [
        f"Claim {ordinal:02d} is supported [{ordinal + 1}]."
        for ordinal in range(36)
    ]
    assert [claim.citation_ids for claim in first.claims] == [
        (ordinal + 1,) for ordinal in range(36)
    ]

    for batch_index, rendered in enumerate(first_prompts):
        payload = json.loads(rendered[rendered.rfind("\n") + 1 :])
        start = batch_index * 6
        assert [
            candidate["ordinal"]
            for candidate in payload["report"]["candidate_units"]
        ] == list(range(start, start + 6))
        assert set(payload["retrieval_context"]["sources_registry"]) == {
            str(identifier) for identifier in range(start + 1, start + 7)
        }
        assert "raw_text" not in payload["report"]


@pytest.mark.asyncio
async def test_real_tavily_blocks_bind_by_canonical_url_not_local_source_number():
    report = (
        "Alpha is supported [1]. Beta is supported [2].\n\n"
        "## Sources\n"
        "[1] [Alpha](https://EXAMPLE.com:443/alpha?b=2&a=1#section)\n"
        "[2] [Beta](https://example.com/beta)"
    )
    tavily_context = """Search results:

--- SOURCE 1: Beta result ---
URL: https://example.com/beta

SUMMARY:
Beta evidence.

--------------------------------------------------------------------------------

--- SOURCE 2: Alpha result ---
URL: https://example.com/alpha?a=1&b=2

SUMMARY:
Alpha evidence.

--------------------------------------------------------------------------------
"""
    adapter = FakeStructuredAdapter(
        ClaimScorerJudgeOutput(claims=(judge_claim(0), judge_claim(1)))
    )
    result = await QwenClaimCitationScorer(adapter).score(
        prompt="Question",
        report=report,
        retrieval_context=(tavily_context,),
    )

    assert result.bound_context_count == 2
    assert result.unbound_context_count == 0
    payload = json.loads(adapter.calls[0][0][adapter.calls[0][0].rfind("\n") + 1 :])
    registry = payload["retrieval_context"]["sources_registry"]
    assert "SOURCE 2: Alpha result" in registry["1"]["bound_retrieval_context"][0]
    assert "SOURCE 1: Beta result" in registry["2"]["bound_retrieval_context"][0]
    assert "Beta evidence" not in registry["1"]["bound_retrieval_context"][0]
    assert "Alpha evidence" not in registry["2"]["bound_retrieval_context"][0]


@pytest.mark.asyncio
async def test_governed_json_binds_isolated_url_or_evidence_id_only():
    evidence_id = "evd_" + "a" * 64
    other_evidence_id = "evd_" + "b" * 64
    report = (
        "URL-backed fact [1]. ID-backed fact [2]. Topic-only claim [3].\n\n"
        "## Sources\n"
        "[1] Governed docs — https://docs.example/item\n"
        f"[2] Stable evidence {evidence_id}\n"
        "[3] Similar topic without identity"
    )
    governed_context = json.dumps(
        {
            "run_id": "run-fixture",
            "evidence": [
                {
                    "evidence_id": other_evidence_id,
                    "source_uri": "https://docs.example/item",
                    "excerpt": "Direct URL-backed evidence.",
                },
                {
                    "evidence_id": evidence_id,
                    "source_uri": "source://local-only",
                    "excerpt": "Direct ID-backed evidence.",
                },
                {
                    "title": "Similar topic without identity",
                    "excerpt": "Topic overlap is not a binding key.",
                },
            ],
        },
        ensure_ascii=False,
    )
    adapter = FakeStructuredAdapter(
        ClaimScorerJudgeOutput(
            claims=(judge_claim(0), judge_claim(1), judge_claim(2))
        )
    )
    result = await QwenClaimCitationScorer(adapter).score(
        prompt="Question",
        report=report,
        retrieval_context=(governed_context,),
    )

    assert result.bound_context_count == 2
    assert result.unbound_context_count == 1
    assert result.claims[0].evidence_valid is True
    assert result.claims[1].evidence_valid is True
    assert result.claims[2].evidence_valid is False
    assert result.claims[2].validation_status is ClaimValidationStatus.UNSUPPORTED
    payload = json.loads(adapter.calls[0][0][adapter.calls[0][0].rfind("\n") + 1 :])
    registry = payload["retrieval_context"]["sources_registry"]
    assert "URL-backed evidence" in registry["1"]["bound_retrieval_context"][0]
    assert "ID-backed evidence" in registry["2"]["bound_retrieval_context"][0]
    assert registry["3"]["bound_retrieval_context"] == []


@pytest.mark.asyncio
async def test_duplicate_bibliography_url_remains_ambiguous_and_unbound():
    report = (
        "First claim [1]. Second claim [2].\n\n"
        "## Sources\n"
        "[1] First — https://example.com/shared\n"
        "[2] Second — https://example.com/shared"
    )
    context = """Search results:

--- SOURCE 1: Shared result ---
URL: https://example.com/shared

SUMMARY:
Shared evidence.
"""
    adapter = FakeStructuredAdapter(
        ClaimScorerJudgeOutput(claims=(judge_claim(0), judge_claim(1)))
    )
    result = await QwenClaimCitationScorer(adapter).score(
        prompt="Question",
        report=report,
        retrieval_context=(context,),
    )

    assert result.bound_context_count == 0
    assert result.unbound_context_count == 1
    assert all(claim.evidence_valid is False for claim in result.claims)


def test_markdown_table_rows_are_stable_candidates_and_delimiter_is_skipped():
    report = (
        "| Metric | Value |\n"
        "| :--- | ---: |\n"
        "| Growth | 20% [1] |\n\n"
        "## Sources\n[1] Official source"
    )

    candidates = report_candidate_units(report)

    assert [(item.ordinal, item.text, item.citation_ids) for item in candidates] == [
        (0, "| Metric | Value |", ()),
        (1, "| Growth | 20% [1] |", (1,)),
    ]


@pytest.mark.asyncio
async def test_unsupported_html_table_fails_before_dispatch():
    adapter = FakeStructuredAdapter(ClaimScorerJudgeOutput(claims=()))
    scorer = QwenClaimCitationScorer(adapter)

    with pytest.raises(ClaimScorerCoverageError, match="HTML table"):
        await scorer.score(
            prompt="Question",
            report="<table><tr><td>Growth</td><td>20%</td></tr></table>",
            retrieval_context=(),
        )

    assert adapter.calls == []


@pytest.mark.asyncio
async def test_provider_call_ceiling_rejects_133_candidates_before_dispatch():
    report = "\n".join(f"Claim {ordinal}." for ordinal in range(133))
    adapter = FakeStructuredAdapter(ClaimScorerJudgeOutput(claims=()))
    scorer = build_live_qwen_claim_scorer(
        adapter=adapter,
        batch_size=6,
        max_provider_calls=22,
    )

    with pytest.raises(ClaimScorerCoverageError, match="provider-call ceiling"):
        await scorer.score(
            prompt="Question",
            report=report,
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
                    status=ClaimValidationStatus.UNSUPPORTED,
                    evidence_valid=False,
                    authority=ClaimSourceAuthority.UNKNOWN,
                ),
            ),
            repaired_report="Changed answer",
        )

    provider_fields = {
        "ordinal": 0,
        "checkable": True,
        "validation_status": ClaimValidationStatus.UNSUPPORTED,
        "evidence_valid": False,
        "source_authority": ClaimSourceAuthority.UNKNOWN,
        "correctly_qualified": False,
    }
    with pytest.raises(ValidationError, match="text"):
        ClaimScorerJudgeClaim(**provider_fields, text="Answer")
    with pytest.raises(ValidationError, match="citation_ids"):
        ClaimScorerJudgeClaim(**provider_fields, citation_ids=(1,))
    provider_schema = ClaimScorerJudgeOutput.model_json_schema()
    claim_properties = provider_schema["$defs"]["ClaimScorerJudgeClaim"][
        "properties"
    ]
    assert "text" not in claim_properties
    assert "citation_ids" not in claim_properties


def test_live_factory_accepts_adapter_or_forwards_metering_configuration():
    output = ClaimScorerJudgeOutput(
        claims=(
            judge_claim(
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


@pytest.mark.asyncio
async def test_live_factory_forwards_batch_size_to_constructed_scorer():
    def classify_batch(prompt, schema):
        assert schema is ClaimScorerJudgeOutput
        payload = json.loads(prompt[prompt.rfind("\n") + 1 :])
        return ClaimScorerJudgeOutput(
            claims=tuple(
                judge_claim(
                    candidate["ordinal"],
                    evidence_valid=False,
                    authority=ClaimSourceAuthority.UNKNOWN,
                    status=ClaimValidationStatus.UNSUPPORTED,
                )
                for candidate in payload["report"]["candidate_units"]
            )
        )

    adapter = FakeStructuredAdapter(classify_batch)
    scorer = build_live_qwen_claim_scorer(adapter=adapter, batch_size=2)
    result = await scorer.score(
        prompt="Question",
        report="First fact. Second fact. Third fact.",
        retrieval_context=(),
    )

    assert result.candidate_count == 3
    assert len(adapter.calls) == 2


def test_rendered_payload_contains_only_the_three_canonical_inputs():
    inputs = ClaimScorerInput(
        prompt="Question",
        report="Answer",
        retrieval_context=("Evidence",),
    )
    rendered = render_claim_scorer_prompt(inputs)
    payload = json.loads(rendered[rendered.rfind("\n") + 1 :])

    assert set(payload) == {"prompt", "report", "retrieval_context"}
    assert payload["report"]["sha256"] == hashlib.sha256(b"Answer").hexdigest()
    assert payload["report"]["candidate_units"] == [
        {"citation_ids": [], "ordinal": 0, "text": "Answer"}
    ]
    assert "raw_text" not in payload["report"]
    assert payload["retrieval_context"] == {
        "raw_unbound_context_omitted": True,
        "sources_registry": {},
        "unbound_item_count": 1,
    }
    assert "Evidence" not in rendered
    assert METRIC_SCORER_VERSION == CLAIM_SCORER_VERSION
