import hashlib

from open_deep_research.evaluation.custom_metrics import (
    ClaimObservation,
    cost_completeness_metric,
    memory_reuse_metric,
    score_citations,
    source_numbering_metric,
    source_quality_metric,
)
from open_deep_research.evaluation.experiment_models import EvaluationStatus


def test_unreferenced_checkable_claim_cannot_receive_false_full_score():
    output = "A numeric claim without citation."
    digest = hashlib.sha256(output.encode()).hexdigest()
    results = score_citations(
        output,
        [ClaimObservation("c1", True, validation_status="unsupported")],
    )
    assert results[0].score == 0
    assert results[1].score == 0
    assert results[2].score == 1
    assert hashlib.sha256(output.encode()).hexdigest() == digest


def test_zero_citations_fail_completeness_even_if_observation_claims_support():
    results = score_citations(
        "An uncited factual claim.",
        [
            ClaimObservation(
                "c1",
                True,
                citation_ids=(),
                validation_status="fully_supported",
                evidence_valid=True,
                source_authority="official",
            )
        ],
    )

    assert results[0].score == 0
    assert results[1].score == 0


def test_claim_statuses_are_scored_independently():
    claims = [
        ClaimObservation("a", True, (1,), "fully_supported", True, "official"),
        ClaimObservation("b", True, (2,), "partially_supported", True, "primary"),
        ClaimObservation("c", True, (3,), "contradicted", False, "secondary"),
        ClaimObservation("d", False),
    ]
    results = score_citations("text", claims)
    assert [item.score for item in results] == [1 / 3, 2 / 3, 1 / 3]
    assert source_quality_metric(claims).score == 0.9


def test_zero_denominator_semantics_are_not_false_passes():
    results = score_citations("non-checkable", [])
    assert all(item.status is EvaluationStatus.NOT_APPLICABLE for item in results)
    assert all(item.score is None for item in results)


def test_source_numbering_detects_orphan_unused_duplicate_and_gap():
    valid = "Fact [1].\n\n## Sources\n[1] Official source"
    assert source_numbering_metric(valid).score == 0
    invalid = "Fact [2].\n\n## Sources\n[1] Unused\n[1] Duplicate\n[3] Gap"
    result = source_numbering_metric(invalid)
    assert result.score > 0
    assert result.status is EvaluationStatus.FAILED


def test_memory_and_cost_metrics_preserve_hard_errors_and_unknowns():
    assert memory_reuse_metric(
        useful_hits=2, eligible_cases=4, cross_namespace_errors=0, stale_recalls=0
    ).score == 0.5
    assert memory_reuse_metric(
        useful_hits=2, eligible_cases=4, cross_namespace_errors=1, stale_recalls=0
    ).score == 0
    assert cost_completeness_metric(
        tokens=None, cost=None, pricing_available=False
    ).score == 1
    assert cost_completeness_metric(
        tokens=10, cost=None, pricing_available=False
    ).score == 1
    assert cost_completeness_metric(
        tokens=10, cost=None, pricing_available=True
    ).score == 0
    assert cost_completeness_metric(
        tokens=10, cost=0.01, pricing_available=True
    ).score == 1
    assert cost_completeness_metric(
        tokens=10, cost=0.01, pricing_available=False
    ).score == 0
