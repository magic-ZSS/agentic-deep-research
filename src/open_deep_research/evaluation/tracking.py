"""Optional, failure-isolated mirrors for Phase 7 evaluation artifacts.

Local artifacts remain the authoritative evaluation record.  This module only
mirrors already-sanitized snapshots to an optional tracking backend; importing
it does not import an SDK, read credentials, or contact a service.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict

from open_deep_research.evaluation.artifact_safety import (
    redact_evaluation_text,
    sanitize_evaluation_value,
)

TrackingMode = Literal["local", "langsmith"]
TrackingOperation = Literal["experiment", "run", "metric"]
TrackingStatus = Literal["local_authoritative", "mirrored", "error"]

_TRACKING_NAMESPACE = UUID("a56a0a71-94eb-5f46-bf47-b5a62fbbf253")
_PROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_METRIC_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PUBLIC_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,255}$")
_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")

_EXPERIMENT_TEXT_FIELDS = (
    "schema_version",
    "experiment_id",
    "mode",
    "status",
    "dataset_version",
    "provenance",
)
_EXPERIMENT_HASH_FIELDS = {
    "git_head": _HEX_40,
    "dirty_diff_sha256": _HEX_64,
    "plan_sha256": _HEX_64,
    "ablation_sha256": _HEX_64,
}
_EXPERIMENT_NUMBER_FIELDS = (
    "repeats",
    "planned_runs",
    "paired_main_runs",
    "additional_warm_runs",
    "soft_token_limit",
    "hard_token_limit",
    "per_run_token_limit",
)
_PROJECTION_TEXT_FIELDS = ("calibration_experiment_id", "status")
_PROJECTION_NUMBER_FIELDS = (
    "calibration_runs",
    "full_runs",
    "safety_multiplier",
    "projected_tokens",
    "requested_max_tokens",
)
_RUN_TEXT_FIELDS = (
    "schema_version",
    "experiment_id",
    "run_id",
    "variant_id",
    "case_id",
    "difficulty",
    "mode",
    "dataset_version",
    "scorer_version",
    "status",
    "started_at",
    "finished_at",
)
_RUN_HASH_FIELDS = {
    "project_commit": _HEX_40,
    "output_sha256": _HEX_64,
}
_RUN_NUMBER_FIELDS = ("repeat",)
_TELEMETRY_NUMBER_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "research_input_tokens",
    "research_output_tokens",
    "research_total_tokens",
    "judge_input_tokens",
    "judge_output_tokens",
    "judge_total_tokens",
    "retry_tokens",
    "estimated_cost_usd",
    "wall_time_ms",
    "research_model_calls",
    "judge_model_calls",
    "search_calls",
    "researcher_runs",
)
_METRIC_TEXT_FIELDS = ("run_id", "metric_name", "metric_version", "status", "judge_model")
_METRIC_NUMBER_FIELDS = (
    "threshold",
    "estimated_cost_usd",
    "input_tokens",
    "output_tokens",
    "total_tokens",
)


class _TrackingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TrackingError(_TrackingModel):
    """Safe diagnostic for a tracking failure without exception text."""

    code: Literal[
        "missing_credentials",
        "client_initialization_failed",
        "invalid_payload",
        "upload_failed",
    ]
    operation: TrackingOperation
    backend: Literal["langsmith"] = "langsmith"
    error_type: str
    fingerprint: str = ""
    retryable: bool = False


class TrackingResult(_TrackingModel):
    """One non-throwing tracking outcome suitable for local artifacts."""

    backend: TrackingMode
    operation: TrackingOperation
    status: TrackingStatus
    authoritative_backend: Literal["local"] = "local"
    project_name: str | None = None
    remote_id: str | None = None
    error: TrackingError | None = None

    @property
    def ok(self) -> bool:
        """Return whether local recording can continue after this outcome."""
        return self.status != "error"


@runtime_checkable
class TrackingSink(Protocol):
    """Mirror sanitized experiment, run, and metric snapshots."""

    def track_experiment(self, payload: Mapping[str, Any]) -> TrackingResult:
        """Mirror one experiment snapshot without becoming authoritative."""

    def track_run(self, payload: Mapping[str, Any]) -> TrackingResult:
        """Mirror one completed or terminal run snapshot."""

    def track_metric(self, payload: Mapping[str, Any]) -> TrackingResult:
        """Mirror one metric result attached to its stable run identifier."""


@runtime_checkable
class LangSmithClientProtocol(Protocol):
    """Small subset of the LangSmith client used by the optional mirror."""

    def create_run(
        self,
        name: str,
        inputs: dict[str, Any],
        run_type: Literal[
            "tool", "chain", "llm", "retriever", "embedding", "prompt", "parser"
        ],
        **kwargs: Any,
    ) -> Any:
        """Create a completed root trace."""

    def create_feedback(self, **kwargs: Any) -> Any:
        """Attach one metric observation to a stable trace identifier."""


LangSmithClientFactory = Callable[[str], LangSmithClientProtocol]


def _default_langsmith_client(api_key: str) -> LangSmithClientProtocol:
    """Import the optional SDK only after an explicitly selected upload."""
    from langsmith import Client

    # Synchronous tracing makes an upload failure observable to this best-effort
    # boundary instead of surfacing later in an SDK background worker.
    return Client(api_key=api_key, auto_batch_tracing=False)


def _safe_project_name(explicit: str | None) -> str:
    value = explicit if explicit is not None else os.environ.get("LANGSMITH_PROJECT", "")
    normalized = value.strip()
    if not normalized or not _PROJECT_NAME.fullmatch(normalized):
        raise ValueError(
            "LangSmith tracking requires a non-empty public project name"
        )
    if redact_evaluation_text(normalized) != normalized:
        raise ValueError("LangSmith project name must not contain private data")
    return normalized


def _safe_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Sanitize a local-only payload without making it uploadable."""
    sanitized = sanitize_evaluation_value(payload)
    if not isinstance(sanitized, dict):
        raise TypeError("tracking payload must be a mapping")
    return sanitized


def _public_text(
    value: Any, *, field: str, pattern: re.Pattern[str] = _PUBLIC_TEXT
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"tracking payload requires public text field {field}")
    normalized = value.strip()
    if normalized != value or not pattern.fullmatch(normalized):
        raise ValueError(f"tracking payload field {field} is not a public identifier")
    if redact_evaluation_text(normalized) != normalized:
        raise ValueError(f"tracking payload field {field} contains private data")
    return normalized


def _copy_text_fields(
    source: Mapping[str, Any],
    target: dict[str, Any],
    fields: tuple[str, ...],
) -> None:
    for field in fields:
        if field in source and source[field] is not None:
            target[field] = _public_text(source[field], field=field)


def _copy_hash_fields(
    source: Mapping[str, Any],
    target: dict[str, Any],
    fields: Mapping[str, re.Pattern[str]],
) -> None:
    for field, pattern in fields.items():
        if field in source and source[field] is not None:
            target[field] = _public_text(source[field], field=field, pattern=pattern)


def _copy_number_fields(
    source: Mapping[str, Any],
    target: dict[str, Any],
    fields: tuple[str, ...],
) -> None:
    for field in fields:
        if field not in source:
            continue
        value = source[field]
        if value is None:
            target[field] = None
        elif (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and value >= 0
        ):
            target[field] = value
        else:
            raise ValueError(f"tracking payload field {field} must be a non-negative number or null")


def _public_string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"tracking payload field {field} must be a list")
    return [_public_text(item, field=field) for item in value]


def _public_string_map(value: Any, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"tracking payload field {field} must be an object")
    return {
        _public_text(key, field=f"{field}.key"): _public_text(
            item, field=f"{field}.{key}"
        )
        for key, item in value.items()
    }


def _public_bool_map(value: Any, *, field: str) -> dict[str, bool]:
    if not isinstance(value, Mapping):
        raise ValueError(f"tracking payload field {field} must be an object")
    result: dict[str, bool] = {}
    for key, item in value.items():
        public_key = _public_text(key, field=f"{field}.key")
        if not isinstance(item, bool):
            raise ValueError(f"tracking payload field {field}.{key} must be boolean")
        result[public_key] = item
    return result


def _experiment_upload_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build the only experiment fields allowed to leave the local machine."""
    result: dict[str, Any] = {}
    _copy_text_fields(payload, result, _EXPERIMENT_TEXT_FIELDS)
    _copy_hash_fields(payload, result, _EXPERIMENT_HASH_FIELDS)
    _copy_number_fields(payload, result, _EXPERIMENT_NUMBER_FIELDS)
    result["experiment_id"] = _safe_identifier(result, "experiment_id")
    for field in ("case_ids", "variants"):
        if field in payload:
            result[field] = _public_string_list(payload[field], field=field)
    if "model_ids" in payload:
        result["model_ids"] = _public_string_map(payload["model_ids"], field="model_ids")
    if "claims" in payload:
        result["claims"] = _public_bool_map(payload["claims"], field="claims")
    projection = payload.get("calibration_projection")
    if isinstance(projection, Mapping):
        safe_projection: dict[str, Any] = {}
        _copy_text_fields(projection, safe_projection, _PROJECTION_TEXT_FIELDS)
        _copy_number_fields(projection, safe_projection, _PROJECTION_NUMBER_FIELDS)
        observed = projection.get("observed_tokens")
        if observed is not None:
            if not isinstance(observed, list | tuple):
                raise ValueError("tracking calibration observed_tokens must be a list")
            token_payload = {str(index): item for index, item in enumerate(observed)}
            safe_tokens: dict[str, Any] = {}
            _copy_number_fields(token_payload, safe_tokens, tuple(token_payload))
            safe_projection["observed_tokens"] = [
                safe_tokens[str(index)] for index in range(len(observed))
            ]
        result["calibration_projection"] = safe_projection
    return result


def _telemetry_upload_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("tracking payload telemetry must be an object")
    result: dict[str, Any] = {}
    _copy_number_fields(value, result, _TELEMETRY_NUMBER_FIELDS)
    tool_calls = value.get("tool_calls_by_name")
    if tool_calls is not None:
        if not isinstance(tool_calls, Mapping):
            raise ValueError("tracking telemetry tool_calls_by_name must be an object")
        safe_calls: dict[str, int] = {}
        for key, count in tool_calls.items():
            name = _public_text(key, field="tool_calls_by_name.key")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("tracking tool call counts must be non-negative integers")
            safe_calls[name] = count
        result["tool_calls_by_name"] = safe_calls
    return result


def _run_upload_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build a run summary without output, trace, context, errors, or state."""
    result: dict[str, Any] = {}
    _copy_text_fields(payload, result, _RUN_TEXT_FIELDS)
    _copy_hash_fields(payload, result, _RUN_HASH_FIELDS)
    _copy_number_fields(payload, result, _RUN_NUMBER_FIELDS)
    result["run_id"] = _safe_identifier(result, "run_id")
    if "telemetry" in payload:
        result["telemetry"] = _telemetry_upload_payload(payload["telemetry"])
    return result


def _metric_upload_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build metric feedback without its free-text reason or evaluation context."""
    result: dict[str, Any] = {}
    _copy_text_fields(payload, result, _METRIC_TEXT_FIELDS)
    result["run_id"] = _safe_identifier(result, "run_id")
    metric_name = _safe_identifier(result, "metric_name")
    if not _METRIC_NAME.fullmatch(metric_name):
        raise ValueError("metric_name must be a short public identifier")
    result["metric_name"] = metric_name
    _copy_number_fields(payload, result, _METRIC_NUMBER_FIELDS)
    if "score" in payload:
        score = payload["score"]
        result["score"] = (
            score
            if isinstance(score, int | float)
            and not isinstance(score, bool)
            and math.isfinite(float(score))
            and 0 <= score <= 1
            else None
        )
    if "deterministic" in payload:
        if not isinstance(payload["deterministic"], bool):
            raise ValueError("tracking metric deterministic must be boolean")
        result["deterministic"] = payload["deterministic"]
    return result


def _safe_identifier(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"tracking payload requires {key}")
    sanitized = redact_evaluation_text(value.strip())
    if sanitized != value.strip():
        raise ValueError(f"tracking payload {key} contains private data")
    return sanitized


def _remote_uuid(project_name: str, kind: str, identifier: str) -> UUID:
    return uuid5(_TRACKING_NAMESPACE, f"{project_name}\0{kind}\0{identifier}")


def _failure_fingerprint(
    *, code: str, operation: TrackingOperation, error: BaseException | None
) -> str:
    error_type = type(error).__name__ if error is not None else "TrackingConfigurationError"
    safe_message = redact_evaluation_text(str(error)) if error is not None else ""
    encoded = json.dumps(
        ["phase7-tracking-v1", code, operation, error_type, safe_message],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _error_result(
    *,
    operation: TrackingOperation,
    project_name: str,
    code: Literal[
        "missing_credentials",
        "client_initialization_failed",
        "invalid_payload",
        "upload_failed",
    ],
    error: BaseException | None = None,
    retryable: bool = False,
) -> TrackingResult:
    return TrackingResult(
        backend="langsmith",
        operation=operation,
        status="error",
        project_name=project_name,
        error=TrackingError(
            code=code,
            operation=operation,
            error_type=(
                type(error).__name__
                if error is not None
                else "TrackingConfigurationError"
            ),
            fingerprint=_failure_fingerprint(
                code=code, operation=operation, error=error
            ),
            retryable=retryable,
        ),
    )


class LocalTrackingSink:
    """No-op sink documenting that local artifacts are authoritative."""

    @staticmethod
    def _result(operation: TrackingOperation, payload: Mapping[str, Any]) -> TrackingResult:
        # Sanitization is deliberately exercised even for the local-only path so
        # callers can pass the same payload to either backend safely.
        try:
            _safe_payload(payload)
        except Exception:
            # Local artifact persistence is owned by the runner, not this mirror.
            # A mirror payload must therefore never interfere with that record.
            pass
        return TrackingResult(
            backend="local",
            operation=operation,
            status="local_authoritative",
        )

    def track_experiment(self, payload: Mapping[str, Any]) -> TrackingResult:
        """Keep the experiment local without loading a tracking SDK."""
        return self._result("experiment", payload)

    def track_run(self, payload: Mapping[str, Any]) -> TrackingResult:
        """Keep the run local without loading a tracking SDK."""
        return self._result("run", payload)

    def track_metric(self, payload: Mapping[str, Any]) -> TrackingResult:
        """Keep the metric local without loading a tracking SDK."""
        return self._result("metric", payload)


class LangSmithTrackingSink:
    """Best-effort LangSmith mirror isolated from the paid evaluation path."""

    def __init__(
        self,
        *,
        project_name: str | None = None,
        client: LangSmithClientProtocol | None = None,
        client_factory: LangSmithClientFactory | None = None,
    ) -> None:
        """Configure a lazy mirror without reading credentials or importing SDKs."""
        self.project_name = _safe_project_name(project_name)
        self._injected_client = client
        self._client_factory = client_factory or _default_langsmith_client
        self._client: LangSmithClientProtocol | None = None
        self._client_lock = threading.Lock()

    def _get_client(self) -> LangSmithClientProtocol:
        api_key = os.environ.get("LANGSMITH_API_KEY", "").strip()
        if not api_key:
            raise _MissingCredentialsError
        with self._client_lock:
            if self._client is None:
                self._client = self._injected_client or self._client_factory(api_key)
            return self._client

    def _client_or_result(
        self, operation: TrackingOperation
    ) -> LangSmithClientProtocol | TrackingResult:
        try:
            return self._get_client()
        except _MissingCredentialsError:
            return _error_result(
                operation=operation,
                project_name=self.project_name,
                code="missing_credentials",
            )
        except Exception as exc:
            return _error_result(
                operation=operation,
                project_name=self.project_name,
                code="client_initialization_failed",
                error=exc,
                retryable=True,
            )

    def _track_snapshot(
        self, operation: Literal["experiment", "run"], payload: Mapping[str, Any]
    ) -> TrackingResult:
        try:
            sanitized = (
                _experiment_upload_payload(payload)
                if operation == "experiment"
                else _run_upload_payload(payload)
            )
            identifier_key = "experiment_id" if operation == "experiment" else "run_id"
            identifier = _safe_identifier(sanitized, identifier_key)
        except Exception as exc:
            return _error_result(
                operation=operation,
                project_name=self.project_name,
                code="invalid_payload",
                error=exc,
            )

        client = self._client_or_result(operation)
        if isinstance(client, TrackingResult):
            return client
        remote_id = _remote_uuid(self.project_name, operation, identifier)
        recorded_at = datetime.now(UTC)
        try:
            client.create_run(
                f"phase7-{operation}",
                {"tracking_kind": operation, "stable_id": identifier},
                "chain",
                id=remote_id,
                project_name=self.project_name,
                outputs={"evaluation": sanitized},
                extra={
                    "metadata": {
                        "phase7_tracking_kind": operation,
                        "phase7_authoritative_backend": "local",
                    }
                },
                start_time=recorded_at,
                end_time=recorded_at,
            )
        except Exception as exc:
            return _error_result(
                operation=operation,
                project_name=self.project_name,
                code="upload_failed",
                error=exc,
                retryable=True,
            )
        return TrackingResult(
            backend="langsmith",
            operation=operation,
            status="mirrored",
            project_name=self.project_name,
            remote_id=str(remote_id),
        )

    def track_experiment(self, payload: Mapping[str, Any]) -> TrackingResult:
        """Mirror experiment metadata as one completed root trace."""
        return self._track_snapshot("experiment", payload)

    def track_run(self, payload: Mapping[str, Any]) -> TrackingResult:
        """Mirror one terminal run as a completed, idempotent root trace."""
        return self._track_snapshot("run", payload)

    def track_metric(self, payload: Mapping[str, Any]) -> TrackingResult:
        """Mirror one metric as feedback attached to its stable run trace."""
        try:
            sanitized = _metric_upload_payload(payload)
            run_id = _safe_identifier(sanitized, "run_id")
            metric_name = _safe_identifier(sanitized, "metric_name")
            raw_score = sanitized.get("score")
            score = (
                float(raw_score)
                if isinstance(raw_score, int | float)
                and not isinstance(raw_score, bool)
                and math.isfinite(float(raw_score))
                else None
            )
        except Exception as exc:
            return _error_result(
                operation="metric",
                project_name=self.project_name,
                code="invalid_payload",
                error=exc,
            )

        client = self._client_or_result("metric")
        if isinstance(client, TrackingResult):
            return client
        remote_run_id = _remote_uuid(self.project_name, "run", run_id)
        try:
            client.create_feedback(
                run_id=remote_run_id,
                key=metric_name,
                score=score,
                value=sanitized.get("status"),
                source_info={
                    "phase7_tracking": sanitized,
                    "authoritative_backend": "local",
                },
            )
        except Exception as exc:
            return _error_result(
                operation="metric",
                project_name=self.project_name,
                code="upload_failed",
                error=exc,
                retryable=True,
            )
        return TrackingResult(
            backend="langsmith",
            operation="metric",
            status="mirrored",
            project_name=self.project_name,
            remote_id=str(remote_run_id),
        )


class _MissingCredentialsError(RuntimeError):
    """Internal sentinel whose message never contains environment values."""


def build_tracking_sink(
    mode: TrackingMode = "local",
    *,
    project_name: str | None = None,
    client: LangSmithClientProtocol | None = None,
    client_factory: LangSmithClientFactory | None = None,
) -> TrackingSink:
    """Build an inert local sink or an explicitly configured lazy mirror."""
    if mode == "local":
        return LocalTrackingSink()
    if mode == "langsmith":
        return LangSmithTrackingSink(
            project_name=project_name,
            client=client,
            client_factory=client_factory,
        )
    raise ValueError(f"unsupported tracking mode: {mode}")


__all__ = [
    "LangSmithClientFactory",
    "LangSmithClientProtocol",
    "LangSmithTrackingSink",
    "LocalTrackingSink",
    "TrackingError",
    "TrackingMode",
    "TrackingOperation",
    "TrackingResult",
    "TrackingSink",
    "TrackingStatus",
    "build_tracking_sink",
]
