"""Fail-closed token budgets for calibration and full evaluation."""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


class EvaluationBudgetError(RuntimeError):
    """Reject a run before the next paid call when token evidence is unsafe."""


def load_full_plan(path: str | Path) -> dict:
    """Load and validate the committed low-token evaluation plan."""
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    calibration = plan["calibration"]
    budget = plan["token_budget"]
    if calibration["research_runs"] != (
        len(calibration["case_ids"])
        * len(calibration["variants"])
        * calibration["repeats"]
    ):
        raise EvaluationBudgetError("calibration run count does not match its matrix")
    if not (
        0 < calibration["hard_token_limit"] <= budget["soft_stop_tokens"]
        < budget["hard_stop_tokens"]
    ):
        raise EvaluationBudgetError("token budget ordering is invalid")
    estimate = budget["estimated_total_tokens"]
    if not (0 < estimate["lower"] <= estimate["upper"] <= budget["hard_stop_tokens"]):
        raise EvaluationBudgetError("estimated token range exceeds the hard stop")
    quota = budget["available_user_quota_tokens"]
    if quota["lower"] - budget["hard_stop_tokens"] < budget["minimum_reserved_tokens"]:
        raise EvaluationBudgetError("minimum quota reserve is not protected")
    failures = plan["failure_policy"]
    if not (0 < failures["max_failure_rate"] < 1):
        raise EvaluationBudgetError("failure-rate circuit breaker is invalid")
    if not (0 <= failures["max_retry_token_fraction"] <= 0.1):
        raise EvaluationBudgetError("retry token fraction exceeds ten percent")
    evaluation_environment = plan.get("evaluation_environment", {})
    if evaluation_environment != {
        "conda_file": "environment.phase7.yml",
        "constraints_file": "constraints/evaluation-py311.txt",
        "python": "3.11",
        "deepeval": "4.1.1",
        "require_pip_check": True,
    }:
        raise EvaluationBudgetError("paid evaluation environment lock is invalid")
    return plan


def resolve_models(plan: dict, environment: Mapping[str, str]) -> dict[str, str]:
    """Resolve model identifiers only; never read or return credentials."""
    fields = {
        "summarization": "SUMMARIZATION_MODEL",
        "research": "RESEARCH_MODEL",
        "compression": "COMPRESSION_MODEL",
        "final_report": "FINAL_REPORT_MODEL",
    }
    resolved: dict[str, str] = {}
    for role, key in fields.items():
        value = environment.get(key, "").strip()
        if not value:
            raise EvaluationBudgetError(f"missing model identifier: {key}")
        resolved[role] = value
    resolved["judge"] = (
        environment.get("EVALUATION_JUDGE_MODEL", "").strip()
        or resolved["research"]
    )
    expected = plan["models"]["expected_model_id"].lower()
    mismatched = {
        role: value
        for role, value in resolved.items()
        if expected not in value.lower()
    }
    if mismatched:
        raise EvaluationBudgetError(
            "resolved evaluation models do not match expected qwen model roles: "
            + ", ".join(sorted(mismatched))
        )
    return resolved


def validate_requested_budget(
    plan: dict, *, run_kind: str, repeats: int, requested_max_tokens: int | None
) -> int:
    """Require an explicit ceiling no larger than the committed plan."""
    if requested_max_tokens is None or requested_max_tokens <= 0:
        raise EvaluationBudgetError("--max-total-tokens is required and must be positive")
    if run_kind == "calibration":
        if repeats != plan["calibration"]["repeats"]:
            raise EvaluationBudgetError("calibration repeats must match the plan")
        limit = plan["calibration"]["hard_token_limit"]
    elif run_kind == "full":
        if repeats != plan["repeats"]:
            raise EvaluationBudgetError("full evaluation repeats must match the fixed plan")
        limit = plan["token_budget"]["hard_stop_tokens"]
    else:
        raise EvaluationBudgetError(f"unknown paid run kind: {run_kind}")
    if requested_max_tokens > limit:
        raise EvaluationBudgetError(
            f"requested token ceiling {requested_max_tokens} exceeds plan limit {limit}"
        )
    if run_kind == "full" and requested_max_tokens != limit:
        raise EvaluationBudgetError(
            f"full evaluation requires the fixed token ceiling {limit}"
        )
    return requested_max_tokens


@dataclass
class TokenBudgetLedger:
    """Track measured use and stop dispatch when evidence is unknown or exhausted."""

    soft_stop_tokens: int
    hard_stop_tokens: int
    per_run_tokens: int
    used_tokens: int = 0

    def can_dispatch(self) -> bool:
        """Allow a new run only below the soft stop."""
        return self.used_tokens < self.soft_stop_tokens

    def record_run(self, total_tokens: int | None) -> None:
        """Record one run, rejecting unknown or over-budget measurements."""
        if total_tokens is None:
            raise EvaluationBudgetError("token usage is unknown; paid evaluation fails closed")
        if total_tokens < 0:
            raise EvaluationBudgetError("token usage cannot be negative")
        if total_tokens > self.per_run_tokens:
            raise EvaluationBudgetError("per-run token ceiling exceeded")
        self.used_tokens += total_tokens
        if self.used_tokens > self.hard_stop_tokens:
            raise EvaluationBudgetError("experiment hard token ceiling exceeded")


@dataclass
class FailureCircuitBreaker:
    """Stop repeated failures before they consume another research run."""

    max_consecutive_failures: int
    same_error_signature_limit: int
    failure_rate_min_runs: int
    max_failure_rate: float
    max_failed_run_tokens: int
    total_runs: int = 0
    failed_runs: int = 0
    consecutive_failures: int = 0
    failed_run_tokens: int = 0
    error_signatures: Counter[str] = field(default_factory=Counter)

    def record(
        self, *, success: bool, total_tokens: int | None, error_signature: str | None = None
    ) -> None:
        """Record one terminal run and raise as soon as a breaker opens."""
        if total_tokens is None:
            raise EvaluationBudgetError("failed-run accounting requires known tokens")
        self.total_runs += 1
        if success:
            self.consecutive_failures = 0
            return
        if not error_signature:
            raise EvaluationBudgetError("failed run requires a stable error signature")
        self.failed_runs += 1
        self.consecutive_failures += 1
        self.failed_run_tokens += total_tokens
        self.error_signatures[error_signature] += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            raise EvaluationBudgetError("consecutive-failure circuit breaker opened")
        if self.error_signatures[error_signature] >= self.same_error_signature_limit:
            raise EvaluationBudgetError("repeated-error-signature circuit breaker opened")
        if (
            self.total_runs >= self.failure_rate_min_runs
            and self.failed_runs / self.total_runs > self.max_failure_rate
        ):
            raise EvaluationBudgetError("failure-rate circuit breaker opened")
        if self.failed_run_tokens > self.max_failed_run_tokens:
            raise EvaluationBudgetError("failed-run token budget exceeded")


@dataclass
class RetryBudgetLedger:
    """Bound retries independently from successful run consumption."""

    hard_stop_tokens: int
    max_retry_fraction: float
    retry_tokens: int = 0

    def record(self, total_tokens: int | None) -> None:
        """Reject unknown retry usage or more than the configured fraction."""
        if total_tokens is None:
            raise EvaluationBudgetError("retry token usage is unknown")
        self.retry_tokens += total_tokens
        if self.retry_tokens > self.hard_stop_tokens * self.max_retry_fraction:
            raise EvaluationBudgetError("retry token budget exceeded")


def project_total_tokens(
    calibration_tokens: list[int],
    *,
    remaining_runs: int,
    safety_multiplier: float,
    hard_stop_tokens: int,
) -> int:
    """Project conservatively from observed p95 and reject an unsafe main wave."""
    if not calibration_tokens or any(value <= 0 for value in calibration_tokens):
        raise EvaluationBudgetError("calibration tokens must be known positive values")
    ordered = sorted(calibration_tokens)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    projected = sum(ordered) + math.ceil(
        ordered[p95_index] * remaining_runs * safety_multiplier
    )
    if projected > hard_stop_tokens:
        raise EvaluationBudgetError(
            f"calibrated projection {projected} exceeds hard stop {hard_stop_tokens}"
        )
    return projected
