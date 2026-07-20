"""Deterministic smoke metrics that never call an LLM or external service."""

from __future__ import annotations

from open_deep_research.evaluation.models import (
    BaselineCase,
    BaselineRunRecord,
    MetricResult,
    RunStatus,
)


def output_present_metric(run: BaselineRunRecord) -> MetricResult:
    """Check for a successful run with non-empty output."""
    passed = bool(
        run.telemetry.status is RunStatus.COMPLETED
        and run.output
        and run.output.strip()
    )
    return MetricResult(
        name="output_present",
        passed=passed,
        score=1.0 if passed else 0.0,
        details=(
            "completed run contains non-empty output"
            if passed
            else "run is incomplete or output is empty"
        ),
    )


def requirement_contract_metric(
    case: BaselineCase, run: BaselineRunRecord
) -> MetricResult:
    """Check stable case/run linkage and machine-checkable Requirement fields."""
    ids = [requirement.id for requirement in case.expected_requirements]
    passed = bool(
        run.case_id == case.id
        and ids
        and len(ids) == len(set(ids))
        and all(requirement.description.strip() for requirement in case.expected_requirements)
    )
    return MetricResult(
        name="requirement_contract",
        passed=passed,
        score=1.0 if passed else 0.0,
        details=(
            f"{len(ids)} unique requirements linked to {case.id}"
            if passed
            else "case/run mismatch or invalid Requirement structure"
        ),
    )


def evaluate_smoke(
    case: BaselineCase, run: BaselineRunRecord
) -> list[MetricResult]:
    """Run the complete zero-cost Phase 0 metric set."""
    return [output_present_metric(run), requirement_contract_metric(case, run)]
