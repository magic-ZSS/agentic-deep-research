from __future__ import annotations

import builtins
import json
from typing import Any

import pytest

from open_deep_research.evaluation.tracking import (
    LangSmithTrackingSink,
    LocalTrackingSink,
    TrackingSink,
    build_tracking_sink,
)


class FakeLangSmithClient:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.runs: list[dict[str, Any]] = []
        self.feedback: list[dict[str, Any]] = []

    def create_run(
        self,
        name: str,
        inputs: dict[str, Any],
        run_type: str,
        **kwargs: Any,
    ) -> None:
        if self.failure is not None:
            raise self.failure
        self.runs.append(
            {"name": name, "inputs": inputs, "run_type": run_type, **kwargs}
        )

    def create_feedback(self, **kwargs: Any) -> None:
        if self.failure is not None:
            raise self.failure
        self.feedback.append(kwargs)


def test_local_is_default_authoritative_and_never_imports_langsmith(monkeypatch):
    imported: list[str] = []
    original_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any):
        if name.startswith("langsmith"):
            imported.append(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    sink = build_tracking_sink()
    assert isinstance(sink, LocalTrackingSink)
    assert isinstance(sink, TrackingSink)

    outcomes = [
        sink.track_experiment({"experiment_id": "exp-1"}),
        sink.track_run({"run_id": "run-1"}),
        sink.track_metric({"run_id": "run-1", "metric_name": "faithfulness"}),
    ]
    assert imported == []
    assert {item.status for item in outcomes} == {"local_authoritative"}
    assert all(item.authoritative_backend == "local" for item in outcomes)


def test_langsmith_mirrors_sanitized_snapshots_with_stable_ids(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-secret-only-for-fake")
    monkeypatch.setenv("LANGSMITH_PROJECT", "environment-project")
    fake = FakeLangSmithClient()
    sink = build_tracking_sink(
        "langsmith", project_name="explicit-project", client=fake
    )

    experiment = sink.track_experiment(
        {
            "experiment_id": "exp-123",
            "mode": "full",
            "status": "running",
            "planned_runs": 54,
            "api_key": "sk-never-upload-this",
            "base_url": "https://private-endpoint.example/v1",
            "config": {"cache_path": r"C:\\Users\\person\\private.json"},
        }
    )
    run = sink.track_run(
        {
            "run_id": "run-123",
            "status": "passed",
            "repeat": 1,
            "output_sha256": "a" * 64,
            "telemetry": {"total_tokens": 123, "search_calls": 2},
            "output": "saved at C:\\Users\\person\\report.md",
        }
    )
    metric = sink.track_metric(
        {
            "run_id": "run-123",
            "metric_name": "faithfulness",
            "score": 0.9,
            "status": "passed",
            "reason": "source C:\\Users\\person\\evidence.json",
        }
    )

    assert [experiment.status, run.status, metric.status] == [
        "mirrored",
        "mirrored",
        "mirrored",
    ]
    assert experiment.project_name == "explicit-project"
    assert run.remote_id == metric.remote_id
    assert len(fake.runs) == 2
    assert len(fake.feedback) == 1
    assert fake.runs[1]["id"] == fake.feedback[0]["run_id"]
    assert fake.feedback[0]["key"] == "faithfulness"
    assert fake.feedback[0]["score"] == 0.9

    experiment_snapshot = fake.runs[0]["outputs"]["evaluation"]
    assert experiment_snapshot == {
        "experiment_id": "exp-123",
        "mode": "full",
        "status": "running",
        "planned_runs": 54,
    }
    run_snapshot = fake.runs[1]["outputs"]["evaluation"]
    assert run_snapshot == {
        "run_id": "run-123",
        "status": "passed",
        "output_sha256": "a" * 64,
        "repeat": 1,
        "telemetry": {"total_tokens": 123, "search_calls": 2},
    }
    metric_snapshot = fake.feedback[0]["source_info"]["phase7_tracking"]
    assert metric_snapshot == {
        "run_id": "run-123",
        "metric_name": "faithfulness",
        "status": "passed",
        "score": 0.9,
    }

    uploaded = json.dumps(
        {
            "runs": fake.runs,
            "feedback": fake.feedback,
        },
        default=str,
    )
    assert "sk-never-upload-this" not in uploaded
    assert "private-endpoint.example" not in uploaded
    assert "C:\\\\Users" not in uploaded
    assert "lsv2-secret-only-for-fake" not in uploaded


@pytest.mark.parametrize(
    ("method", "payload", "expected"),
    [
        (
            "track_experiment",
            {"experiment_id": "exp-allowlist"},
            {"experiment_id": "exp-allowlist"},
        ),
        (
            "track_run",
            {"run_id": "run-allowlist"},
            {"run_id": "run-allowlist"},
        ),
        (
            "track_metric",
            {
                "run_id": "run-allowlist",
                "metric_name": "faithfulness",
                "status": "passed",
                "score": 0.75,
            },
            {
                "run_id": "run-allowlist",
                "metric_name": "faithfulness",
                "status": "passed",
                "score": 0.75,
            },
        ),
    ],
)
def test_langsmith_dtos_drop_every_non_allowlisted_secret_and_private_field(
    monkeypatch, method, payload, expected
):
    monkeypatch.setenv("LANGSMITH_API_KEY", "fake-key")
    fake = FakeLangSmithClient()
    sink = LangSmithTrackingSink(project_name="phase7-eval", client=fake)
    forbidden = {
        "LANGSMITH_API_KEY": "lsv2-secret-marker",
        "X-Api-Key": "x-api-key-secret-marker",
        "cookie": "session-cookie-secret-marker",
        "private_key": "private-key-secret-marker",
        "Authorization": "Bearer authorization-secret-marker",
        "endpoint": "https://private-endpoint-marker.example/v1",
        "local_path": r"C:\Users\person\private-marker.json",
        "output": "output-secret-marker",
        "trace": {"value": "trace-secret-marker"},
        "config": {"value": "config-secret-marker"},
        "reason": "reason-secret-marker",
        "state_artifacts": {"value": "state-secret-marker"},
    }

    result = getattr(sink, method)({**payload, **forbidden})

    assert result.status == "mirrored"
    if method == "track_metric":
        uploaded_payload = fake.feedback[0]["source_info"]["phase7_tracking"]
    else:
        uploaded_payload = fake.runs[0]["outputs"]["evaluation"]
    assert uploaded_payload == expected
    serialized = json.dumps({"runs": fake.runs, "feedback": fake.feedback}, default=str)
    for field, marker in forbidden.items():
        assert field not in uploaded_payload
        if isinstance(marker, str):
            assert marker not in serialized
        else:
            for value in marker.values():
                assert value not in serialized


def test_missing_credentials_is_a_structured_error_and_does_not_call_client(
    monkeypatch,
):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    fake = FakeLangSmithClient()
    sink = LangSmithTrackingSink(project_name="phase7-eval", client=fake)

    result = sink.track_run({"run_id": "run-no-key"})

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "missing_credentials"
    assert result.error.error_type == "TrackingConfigurationError"
    assert len(result.error.fingerprint) == 64
    assert result.error.retryable is False
    assert fake.runs == []


def test_client_initialization_failure_is_returned_not_raised(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "fake-key")

    def failing_factory(api_key: str):
        assert api_key == "fake-key"
        raise ImportError(r"SDK failed at C:\Users\person with sk-private-value")

    sink = LangSmithTrackingSink(
        project_name="phase7-eval", client_factory=failing_factory
    )
    result = sink.track_experiment({"experiment_id": "exp-1"})

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "client_initialization_failed"
    assert result.error.error_type == "ImportError"
    assert result.error.retryable is True
    serialized = result.model_dump_json()
    assert "person" not in serialized
    assert "sk-private-value" not in serialized


@pytest.mark.parametrize("operation", ["experiment", "run", "metric"])
def test_upload_failure_never_escapes_paid_runner(monkeypatch, operation):
    monkeypatch.setenv("LANGSMITH_API_KEY", "fake-key")
    fake = FakeLangSmithClient(
        failure=RuntimeError(
            "Authorization: Bearer private-token at C:\\Users\\person\\trace.json"
        )
    )
    sink = LangSmithTrackingSink(project_name="phase7-eval", client=fake)
    payloads = {
        "experiment": {"experiment_id": "exp-1"},
        "run": {"run_id": "run-1"},
        "metric": {
            "run_id": "run-1",
            "metric_name": "task_completion",
            "score": 0.5,
        },
    }

    result = getattr(sink, f"track_{operation}")(payloads[operation])

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "upload_failed"
    assert result.error.error_type == "RuntimeError"
    assert result.error.retryable is True
    serialized = result.model_dump_json()
    assert "private-token" not in serialized
    assert "person" not in serialized


@pytest.mark.parametrize(
    ("method", "payload"),
    [
        ("track_experiment", {}),
        ("track_run", {"run_id": ""}),
        ("track_metric", {"run_id": "run-1", "metric_name": "bad/name"}),
    ],
)
def test_invalid_payload_is_structured_and_does_not_upload(
    monkeypatch, method, payload
):
    monkeypatch.setenv("LANGSMITH_API_KEY", "fake-key")
    fake = FakeLangSmithClient()
    sink = LangSmithTrackingSink(project_name="phase7-eval", client=fake)

    result = getattr(sink, method)(payload)

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "invalid_payload"
    assert fake.runs == []
    assert fake.feedback == []


def test_explicit_project_overrides_environment_and_empty_or_private_is_rejected(
    monkeypatch,
):
    monkeypatch.setenv("LANGSMITH_PROJECT", "environment-project")
    sink = LangSmithTrackingSink(project_name="explicit-project", client=FakeLangSmithClient())
    assert sink.project_name == "explicit-project"

    with pytest.raises(ValueError, match="non-empty public project name"):
        LangSmithTrackingSink(project_name="")
    with pytest.raises(ValueError, match="non-empty public project name"):
        LangSmithTrackingSink(project_name="https://tracking.example")
    with pytest.raises(ValueError, match="non-empty public project name"):
        LangSmithTrackingSink(project_name=r"C:\Users\person\project")


def test_non_numeric_metric_score_stays_unknown_instead_of_becoming_zero(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "fake-key")
    fake = FakeLangSmithClient()
    sink = LangSmithTrackingSink(project_name="phase7-eval", client=fake)

    result = sink.track_metric(
        {
            "run_id": "run-1",
            "metric_name": "cost",
            "score": "unknown",
            "status": "skipped",
        }
    )

    assert result.status == "mirrored"
    assert fake.feedback[0]["score"] is None
