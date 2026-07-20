"""Offline-first evaluation contracts for Open Deep Research.

The package is intentionally inert on import: it does not load ``.env`` files,
construct a research graph, import DeepEval, or contact any external service.
"""

from open_deep_research.evaluation.metrics import evaluate_smoke
from open_deep_research.evaluation.models import (
    BaselineCase,
    BaselineRunRecord,
    MetricResult,
    ReplayFixture,
    RunMode,
    RunStatus,
    RunTelemetry,
)

__all__ = [
    "BaselineCase",
    "BaselineRunRecord",
    "MetricResult",
    "ReplayFixture",
    "RunMode",
    "RunStatus",
    "RunTelemetry",
    "evaluate_smoke",
]
