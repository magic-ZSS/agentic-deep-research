"""Deterministic, evaluation-only governance metrics."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from open_deep_research.evaluation.experiment_models import (
    EvaluationStatus,
    ExperimentMetricResult,
)

SCORER_VERSION = "evaluation-claim-scorer-v4"
_CITATION = re.compile(r"\[(\d+)\]")
_SOURCE_LINE = re.compile(r"^\s*\[(\d+)\]\s+", re.MULTILINE)


@dataclass(frozen=True)
class ClaimObservation:
    """Immutable claim-level input produced by fixtures or a full extractor."""

    claim_id: str
    checkable: bool
    citation_ids: tuple[int, ...] = ()
    validation_status: str = "not_checkable"
    evidence_valid: bool = False
    source_authority: str = "unknown"
    correctly_qualified: bool = False


def _result(
    name: str,
    score: float | None,
    *,
    threshold: float,
    reason: str,
    lower_is_better: bool = False,
) -> ExperimentMetricResult:
    if score is None:
        status = EvaluationStatus.NOT_APPLICABLE
    else:
        passed = score <= threshold if lower_is_better else score >= threshold
        status = EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED
    return ExperimentMetricResult(
        metric_name=name,
        metric_version="1.0",
        score=score,
        threshold=threshold,
        status=status,
        reason=reason,
        deterministic=True,
    )


def score_citations(
    output: str, claims: Iterable[ClaimObservation]
) -> list[ExperimentMetricResult]:
    """Score immutable output and observations; never repairs or rewrites text."""
    before = output
    observations = list(claims)
    checkable = [item for item in observations if item.checkable]
    cited = [item for item in checkable if item.citation_ids]
    fully_supported = [
        item
        for item in cited
        if item.validation_status == "fully_supported" and item.evidence_valid
    ]
    supported = [
        item
        for item in checkable
        if item.citation_ids
        and item.evidence_valid
        and item.validation_status in {"fully_supported", "partially_supported"}
    ]
    unsupported = [
        item
        for item in checkable
        if item.validation_status in {"unsupported", "contradicted"}
        and not item.correctly_qualified
    ]
    fidelity = len(fully_supported) / len(cited) if cited else (0.0 if checkable else None)
    completeness = len(supported) / len(checkable) if checkable else None
    unsupported_rate = len(unsupported) / len(checkable) if checkable else None
    results = [
        _result(
            "citation_fidelity",
            fidelity,
            threshold=0.8,
            reason=f"{len(fully_supported)}/{len(cited)} cited checkable claims fully supported",
        ),
        _result(
            "citation_completeness",
            completeness,
            threshold=0.8,
            reason=f"{len(supported)}/{len(checkable)} checkable claims have valid support",
        ),
        _result(
            "unsupported_claim_rate",
            unsupported_rate,
            threshold=0.0,
            lower_is_better=True,
            reason=f"{len(unsupported)}/{len(checkable)} unqualified unsupported claims",
        ),
    ]
    if output != before:
        raise AssertionError("evaluation scorer mutated report output")
    return results


def source_numbering_metric(output: str) -> ExperimentMetricResult:
    """Measure orphan, unused, duplicate, and non-contiguous source numbers."""
    body, separator, source_text = output.partition("## Sources")
    body_numbers = [int(item) for item in _CITATION.findall(body)]
    source_numbers = [int(item) for item in _SOURCE_LINE.findall(source_text)] if separator else []
    body_set, source_set = set(body_numbers), set(source_numbers)
    orphan = len(body_set - source_set)
    unused = len(source_set - body_set)
    duplicates = len(source_numbers) - len(source_set)
    non_contiguous = int(bool(source_numbers) and sorted(source_set) != list(range(1, max(source_set) + 1)))
    errors = orphan + unused + duplicates + non_contiguous
    denominator = max(len(body_numbers) + len(source_numbers), 1)
    rate = min(1.0, errors / denominator)
    return _result(
        "source_numbering_error_rate",
        rate,
        threshold=0.0,
        lower_is_better=True,
        reason=(
            f"errors={errors}; orphan={orphan}; unused={unused}; "
            f"duplicates={duplicates}; non_contiguous={non_contiguous}"
        ),
    )


def source_quality_metric(claims: Iterable[ClaimObservation]) -> ExperimentMetricResult:
    """Score authoritative support without promoting self-reported claims."""
    weighted = {"unknown": 0.0, "self_reported": 0.25, "secondary": 0.5, "primary": 0.8, "official": 1.0}
    supported = [item for item in claims if item.checkable and item.evidence_valid]
    score = (
        sum(weighted.get(item.source_authority, 0.0) for item in supported) / len(supported)
        if supported
        else None
    )
    return _result(
        "source_quality",
        score,
        threshold=0.5,
        reason=f"authority-weighted mean across {len(supported)} supported claims",
    )


def memory_reuse_metric(
    *, useful_hits: int, eligible_cases: int, cross_namespace_errors: int, stale_recalls: int
) -> ExperimentMetricResult:
    """Require useful controlled decisions and zero memory contamination."""
    if eligible_cases == 0:
        score = None
    elif cross_namespace_errors or stale_recalls:
        score = 0.0
    else:
        score = useful_hits / eligible_cases
    return _result(
        "memory_reuse",
        score,
        threshold=0.01,
        reason=(
            f"useful_hits={useful_hits}/{eligible_cases}; "
            f"cross_namespace_errors={cross_namespace_errors}; stale_recalls={stale_recalls}"
        ),
    )


def cost_completeness_metric(
    *,
    tokens: int | None,
    cost: float | None,
    pricing_available: bool,
) -> ExperimentMetricResult:
    """Require cost only when an explicit price table makes it calculable."""
    if tokens is None:
        passed = cost is None
        reason = "token usage is unknown, so estimated cost must remain null"
    elif pricing_available:
        passed = cost is not None
        reason = "configured pricing requires a cost estimate for known token usage"
    else:
        passed = cost is None
        reason = "pricing is unavailable, so estimated cost remains null rather than zero"
    return _result(
        "cost_field_integrity",
        1.0 if passed else 0.0,
        threshold=1.0,
        reason=reason,
    )
