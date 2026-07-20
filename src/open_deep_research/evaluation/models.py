"""Versioned data contracts shared by baseline runners and later evaluations."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "1.0"


class Difficulty(str, Enum):
    """Stable baseline difficulty buckets."""

    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class NetworkPolicy(str, Enum):
    """Whether a case may be selected by the explicitly authorized live runner."""

    OFFLINE_ONLY = "offline_only"
    LIVE_ALLOWED = "live_allowed"


class BudgetClass(str, Enum):
    """Relative budget labels; they are not currency estimates."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RunMode(str, Enum):
    """Supported baseline execution modes."""

    REPLAY = "replay"
    LIVE = "live"


class RunStatus(str, Enum):
    """Terminal and authorization statuses for an evaluation attempt."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NOT_RUN_NO_AUTHORIZATION = "not_run_no_authorization"


class StrictModel(BaseModel):
    """Base model that rejects silently ignored contract drift."""

    model_config = ConfigDict(extra="forbid")


class BaselineRequirement(StrictModel):
    """One observable requirement expected from a baseline answer."""

    id: str = Field(min_length=1, pattern=r"^[A-Z][A-Z0-9_-]*$")
    description: str = Field(min_length=1)
    required: bool = True


class BaselineCase(StrictModel):
    """One stable research case in the committed baseline dataset."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    id: str = Field(min_length=1, pattern=r"^(simple|medium|complex)-\d{3}$")
    difficulty: Difficulty
    prompt: str = Field(min_length=1)
    expected_requirements: list[BaselineRequirement] = Field(min_length=1)
    network_policy: NetworkPolicy
    budget_class: BudgetClass
    tags: list[str] = Field(default_factory=list)
    fixture_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_identity(self) -> BaselineCase:
        """Keep the ID bucket and requirement identifiers unambiguous."""
        if not self.id.startswith(f"{self.difficulty.value}-"):
            raise ValueError("case id prefix must match difficulty")
        requirement_ids = [item.id for item in self.expected_requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("expected requirement ids must be unique within a case")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("case tags must be unique")
        return self


class TelemetrySpan(StrictModel):
    """A local callback span; timestamps do not imply distributed tracing."""

    run_id: str = Field(min_length=1)
    parent_run_id: str | None = None
    kind: Literal["model", "tool", "chain"]
    name: str = Field(min_length=1)
    started_at: datetime
    finished_at: datetime
    duration_ms: float = Field(ge=0)
    status: Literal["completed", "failed", "cancelled"]
    error_type: str | None = None

    @model_validator(mode="after")
    def validate_time_order(self) -> TelemetrySpan:
        """Reject spans whose wall-clock timestamps move backward."""
        if self.finished_at < self.started_at:
            raise ValueError("span finished_at cannot precede started_at")
        if self.status == "completed" and self.error_type is not None:
            raise ValueError("completed span cannot contain error_type")
        return self


class RunTelemetry(StrictModel):
    """Observed runtime data, with unknown cost/token values represented by null."""

    started_at: datetime
    finished_at: datetime
    wall_time_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    model_calls: int = Field(default=0, ge=0)
    model_calls_with_usage: int = Field(default=0, ge=0)
    tool_requests_by_name: dict[str, int] = Field(default_factory=dict)
    tool_calls_by_name: dict[str, int] = Field(default_factory=dict)
    search_calls: int = Field(default=0, ge=0)
    search_calls_complete: bool = False
    researcher_runs: int | None = Field(default=None, ge=0)
    status: RunStatus
    error_type: str | None = None
    spans: list[TelemetrySpan] = Field(default_factory=list)

    @field_validator("tool_requests_by_name", "tool_calls_by_name")
    @classmethod
    def validate_tool_counts(cls, value: dict[str, int]) -> dict[str, int]:
        """Require auditable non-empty names and non-negative integer counts."""
        for name, count in value.items():
            if not name.strip():
                raise ValueError("tool count names cannot be empty")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("tool counts must be non-negative integers")
        return value

    @model_validator(mode="after")
    def validate_totals(self) -> RunTelemetry:
        """Reject partial token totals masquerading as complete measurements."""
        if self.finished_at < self.started_at:
            raise ValueError("telemetry finished_at cannot precede started_at")
        token_values = (self.input_tokens, self.output_tokens, self.total_tokens)
        if any(value is None for value in token_values) and any(
            value is not None for value in token_values
        ):
            raise ValueError("token fields must be all known or all null")
        if self.total_tokens is not None:
            assert self.input_tokens is not None
            assert self.output_tokens is not None
            if self.total_tokens != self.input_tokens + self.output_tokens:
                raise ValueError("total_tokens must equal input_tokens + output_tokens")
        if self.model_calls_with_usage > self.model_calls:
            raise ValueError("model_calls_with_usage cannot exceed model_calls")
        if self.status is RunStatus.COMPLETED and self.error_type is not None:
            raise ValueError("completed telemetry cannot contain error_type")
        if self.status in {RunStatus.FAILED, RunStatus.CANCELLED} and not self.error_type:
            raise ValueError("failed or cancelled telemetry requires error_type")
        return self


class MetricResult(StrictModel):
    """A deterministic metric result safe for daily smoke evaluation."""

    name: str = Field(min_length=1)
    passed: bool
    score: float = Field(ge=0, le=1)
    details: str = Field(min_length=1)


class BaselineRunRecord(StrictModel):
    """One replay or explicitly authorized live baseline result."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    mode: RunMode
    project_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    config_snapshot: dict[str, Any]
    output: str | None = None
    telemetry: RunTelemetry
    artifact_refs: list[str] = Field(default_factory=list)
    created_at: datetime
    fixture_version: str | None = None
    telemetry_source: Literal["fixture", "callback"]
    metrics: list[MetricResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result(self) -> BaselineRunRecord:
        """Keep run and telemetry statuses coherent."""
        if self.telemetry.status is RunStatus.NOT_RUN_NO_AUTHORIZATION:
            raise ValueError("authorization refusals are events, not baseline results")
        if self.telemetry.status is RunStatus.COMPLETED and not (
            self.output and self.output.strip()
        ):
            raise ValueError("completed run requires a non-empty output")
        return self


class ReplayFixture(StrictModel):
    """Committed, de-identified observations used to exercise the replay path."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    fixture_version: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    output: str = Field(min_length=1)
    telemetry: RunTelemetry
    source: Literal["saved_replay", "synthetic_fake"]
    notes: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_fixture(self) -> ReplayFixture:
        """Only successful terminal observations may be replayed as baselines."""
        if self.telemetry.status is not RunStatus.COMPLETED:
            raise ValueError("replay fixture telemetry must be completed")
        return self


class AuthorizationRefusal(StrictModel):
    """Machine-readable refusal emitted before any live imports or file writes."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    case_id: str
    mode: Literal["live"] = "live"
    status: Literal["not_run_no_authorization"] = "not_run_no_authorization"
    missing_gates: list[str] = Field(min_length=1)
    message: str = Field(min_length=1)
