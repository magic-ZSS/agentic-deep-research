from datetime import datetime, timedelta, timezone

from open_deep_research.evaluation.metrics import evaluate_smoke
from open_deep_research.evaluation.models import (
    BaselineCase,
    BaselineRunRecord,
    RunStatus,
    RunTelemetry,
)


def _case():
    return BaselineCase(
        id="simple-001",
        difficulty="simple",
        prompt="Question",
        expected_requirements=[{"id": "REQ", "description": "Answer it"}],
        network_policy="offline_only",
        budget_class="low",
        tags=[],
        fixture_version="1",
    )


def _run(case_id="simple-001", status=RunStatus.COMPLETED, output="Answer"):
    started = datetime(2026, 7, 20, tzinfo=timezone.utc)
    error_type = None if status is RunStatus.COMPLETED else "RuntimeError"
    return BaselineRunRecord(
        run_id="run",
        case_id=case_id,
        mode="replay",
        project_commit="a" * 40,
        config_snapshot={},
        output=output,
        telemetry=RunTelemetry(
            started_at=started,
            finished_at=started + timedelta(milliseconds=1),
            wall_time_ms=1,
            status=status,
            error_type=error_type,
        ),
        created_at=started,
        telemetry_source="fixture",
    )


def test_deterministic_metrics_pass_valid_fixture():
    metrics = evaluate_smoke(_case(), _run())

    assert [metric.name for metric in metrics] == [
        "output_present",
        "requirement_contract",
    ]
    assert all(metric.passed and metric.score == 1 for metric in metrics)


def test_deterministic_metrics_report_failure_without_judge():
    metrics = evaluate_smoke(_case(), _run(case_id="simple-002"))

    assert metrics[0].passed
    assert not metrics[1].passed
    assert metrics[1].score == 0


def test_output_present_metric_fails_for_failed_run():
    metrics = evaluate_smoke(
        _case(), _run(status=RunStatus.FAILED, output=None)
    )

    assert not metrics[0].passed
    assert metrics[0].score == 0
