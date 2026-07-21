"""Lazy DeepEval metric construction for explicitly authorized full runs."""

from __future__ import annotations

from typing import Any

from open_deep_research.evaluation.deepeval_adapter import (
    DeepEvalUnavailableError,
    _guarded_deepeval_import,
    deepeval_version,
)
from open_deep_research.evaluation.experiment_models import (
    EvaluationStatus,
    ExperimentMetricResult,
)

FULL_METRIC_NAMES = (
    "task_completion",
    "tool_correctness",
    "step_efficiency",
    "plan_adherence",
    "faithfulness",
    "contextual_precision",
    "contextual_recall",
)


def build_full_metrics(*, judge_model: str) -> list[Any]:
    """Construct all paid metrics lazily with platform upload disabled by adapter guards."""
    if deepeval_version() != "4.1.1":
        raise DeepEvalUnavailableError("full metrics require deepeval==4.1.1")
    with _guarded_deepeval_import():
        from deepeval.metrics import (  # type: ignore[import-not-found]
            ContextualPrecisionMetric,
            ContextualRecallMetric,
            FaithfulnessMetric,
            PlanAdherenceMetric,
            StepEfficiencyMetric,
            TaskCompletionMetric,
            ToolCorrectnessMetric,
        )

        return [
            TaskCompletionMetric(model=judge_model),
            ToolCorrectnessMetric(model=judge_model),
            StepEfficiencyMetric(model=judge_model),
            PlanAdherenceMetric(model=judge_model),
            FaithfulnessMetric(model=judge_model),
            ContextualPrecisionMetric(model=judge_model),
            ContextualRecallMetric(model=judge_model),
        ]


def metric_result_from_deepeval(metric: Any, *, plan_present: bool) -> ExperimentMetricResult:
    """Project one DeepEval metric while enforcing the missing-plan hard gate."""
    name = metric.__class__.__name__.removesuffix("Metric")
    normalized = "".join(("_" + char.lower()) if char.isupper() else char for char in name).lstrip("_")
    score = getattr(metric, "score", None)
    error = getattr(metric, "error", None)
    if normalized.endswith("plan_adherence") and not plan_present:
        score = 0.0
        error = "plan was absent from trace"
    if score is None:
        status = EvaluationStatus.ERROR if error else EvaluationStatus.SKIPPED
    else:
        threshold = float(getattr(metric, "threshold", 0.5))
        status = EvaluationStatus.PASSED if score >= threshold else EvaluationStatus.FAILED
    return ExperimentMetricResult(
        metric_name=normalized,
        metric_version="deepeval-4.1.1",
        score=float(score) if score is not None else None,
        threshold=float(getattr(metric, "threshold", 0.5)),
        status=status,
        reason=str(error or getattr(metric, "reason", "no reason returned")),
        deterministic=False,
        judge_model=getattr(metric, "evaluation_model", None),
        estimated_cost_usd=getattr(metric, "evaluation_cost", None),
    )


def skipped_full_metrics(reason: str) -> list[ExperimentMetricResult]:
    """Record missing eligibility without treating any skip as success."""
    return [
        ExperimentMetricResult(
            metric_name=name,
            metric_version="deepeval-4.1.1",
            score=None,
            threshold=0.5,
            status=EvaluationStatus.SKIPPED,
            reason=reason,
            deterministic=False,
        )
        for name in FULL_METRIC_NAMES
    ]
