"""Versioned contracts for Phase 7 evaluation experiments."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EXPERIMENT_SCHEMA_VERSION: Literal["1.0"] = "1.0"


class EvaluationStrictModel(BaseModel):
    """Reject unreviewed schema drift in committed evaluation artifacts."""

    model_config = ConfigDict(extra="forbid")


class EvaluationStatus(str, Enum):
    """Result states; skipped and not-applicable are never passes."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_APPLICABLE = "not_applicable"
    ERROR = "error"


class EvaluationGolden(EvaluationStrictModel):
    """Supplemental fields merged onto a canonical Phase 0 case."""

    schema_version: Literal["1.0"] = EXPERIMENT_SCHEMA_VERSION
    case_id: str = Field(pattern=r"^(simple|medium|complex)-\d{3}$")
    dataset_version: str = Field(min_length=1)
    expected_output: str | None = None
    reference_sources: list[str] = Field(default_factory=list)
    temporal_context: str | None = None
    memory_setup: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    full_rag_metrics: bool = False
    expected_tools_by_variant: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_rag_reference(self) -> EvaluationGolden:
        """Require expected output whenever full RAG metrics are enabled."""
        if self.full_rag_metrics and not (self.expected_output or "").strip():
            raise ValueError("full_rag_metrics requires expected_output")
        return self


class MergedEvaluationCase(EvaluationStrictModel):
    """Runtime-only merger; canonical prompt and requirements remain Phase 0-owned."""

    case_id: str
    difficulty: str
    prompt: str
    expected_requirements: list[dict[str, Any]]
    network_policy: str
    budget_class: str
    canonical_fixture_version: str
    dataset_version: str
    expected_output: str | None = None
    reference_sources: list[str] = Field(default_factory=list)
    temporal_context: str | None = None
    memory_setup: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    full_rag_metrics: bool = False
    expected_tools_by_variant: dict[str, list[str]] = Field(default_factory=dict)


class ExperimentVariant(EvaluationStrictModel):
    """One immutable row of the fixed ablation matrix."""

    variant_id: Literal[
        "baseline", "paperqa", "agentic_rag", "memory", "citation_validator"
    ]
    feature_flags: dict[str, bool | str]
    dataset_version: str
    model_settings: dict[str, str] = Field(alias="model_config")
    search_config: dict[str, Any]
    budget: dict[str, int | float]
    available_tools: list[str]


class ExperimentMetricResult(EvaluationStrictModel):
    """Metric observation with explicit eligibility and cost semantics."""

    metric_name: str
    metric_version: str
    score: float | None = Field(default=None, ge=0, le=1)
    threshold: float | None = Field(default=None, ge=0, le=1)
    status: EvaluationStatus
    reason: str
    deterministic: bool
    judge_model: str | None = None
    estimated_cost_usd: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_status(self) -> ExperimentMetricResult:
        """Keep score presence consistent with eligibility status."""
        if self.status in {EvaluationStatus.PASSED, EvaluationStatus.FAILED}:
            if self.score is None:
                raise ValueError("pass/fail metric requires score")
        elif self.score is not None and self.status is EvaluationStatus.SKIPPED:
            raise ValueError("skipped metric cannot carry a score")
        return self


class ExperimentTelemetry(EvaluationStrictModel):
    """Comparable telemetry; unknown measurements stay null."""

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    wall_time_ms: float | None = Field(default=None, ge=0)
    tool_calls_by_name: dict[str, int] = Field(default_factory=dict)
    search_calls: int | None = Field(default=None, ge=0)
    researcher_runs: int | None = Field(default=None, ge=0)


class ExperimentRun(EvaluationStrictModel):
    """One case/variant/repeat record, including failures and skips."""

    schema_version: Literal["1.0"] = EXPERIMENT_SCHEMA_VERSION
    experiment_id: str
    run_id: str
    variant_id: str
    case_id: str
    difficulty: str
    repeat: int = Field(ge=1)
    mode: Literal["smoke", "full"]
    project_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    dataset_version: str
    scorer_version: str
    output: str | None = None
    output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    trace: dict[str, Any] = Field(default_factory=dict)
    retrieval_context: list[str] = Field(default_factory=list)
    telemetry: ExperimentTelemetry
    metric_results: list[ExperimentMetricResult]
    status: EvaluationStatus
    error: str | None = None
    started_at: datetime
    finished_at: datetime


class ArtifactEntry(EvaluationStrictModel):
    """One content-addressed artifact entry."""

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class ArtifactManifest(EvaluationStrictModel):
    """Hash manifest excluding itself to avoid recursion."""

    schema_version: Literal["1.0"] = EXPERIMENT_SCHEMA_VERSION
    experiment_id: str
    dataset_version: str
    project_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    generated_at: datetime
    files: list[ArtifactEntry]
