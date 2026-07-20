from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from open_deep_research.evaluation.models import (
    BaselineCase,
    BaselineRunRecord,
    ReplayFixture,
    RunMode,
    RunStatus,
    RunTelemetry,
)


def _telemetry(**updates):
    started = datetime(2026, 7, 20, tzinfo=timezone.utc)
    values = {
        "started_at": started,
        "finished_at": started + timedelta(milliseconds=10),
        "wall_time_ms": 10,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "estimated_cost": None,
        "model_calls": 1,
        "model_calls_with_usage": 0,
        "tool_requests_by_name": {"tavily_search": 1},
        "tool_calls_by_name": {"tavily_search": 1},
        "search_calls": 1,
        "researcher_runs": 1,
        "status": RunStatus.COMPLETED,
        "error_type": None,
        "spans": [],
    }
    values.update(updates)
    return RunTelemetry(**values)


def test_case_and_run_schema_round_trip_preserves_unknown_cost():
    case = BaselineCase.model_validate_json(
        '{"schema_version":"1.0","id":"simple-999","difficulty":"simple",'
        '"prompt":"Question","expected_requirements":[{"id":"REQ_1",'
        '"description":"Observable"}],"network_policy":"offline_only",'
        '"budget_class":"low","tags":["replay"],"fixture_version":"1"}'
    )
    run = BaselineRunRecord(
        run_id="run-1",
        case_id=case.id,
        mode=RunMode.REPLAY,
        project_commit="a" * 40,
        config_snapshot={"evaluation_telemetry_enabled": False},
        output="Answer",
        telemetry=_telemetry(),
        created_at=datetime.now(timezone.utc),
        fixture_version="1",
        telemetry_source="fixture",
    )

    restored = BaselineRunRecord.model_validate_json(run.model_dump_json())

    assert restored == run
    assert restored.telemetry.estimated_cost is None
    assert restored.telemetry.input_tokens is None


@pytest.mark.parametrize("schema", ["0.9", "2.0"])
def test_schema_version_rejected(schema):
    with pytest.raises(ValidationError):
        ReplayFixture.model_validate(
            {
                "schema_version": schema,
                "fixture_version": "1",
                "case_id": "simple-001",
                "output": "answer",
                "telemetry": _telemetry(),
                "source": "synthetic_fake",
                "notes": "fixture",
            }
        )


def test_partial_token_measurement_rejected():
    with pytest.raises(ValidationError, match="all known or all null"):
        _telemetry(input_tokens=1)


def test_token_total_must_match_components():
    with pytest.raises(ValidationError, match="must equal"):
        _telemetry(input_tokens=2, output_tokens=3, total_tokens=99)


def test_completed_record_requires_output():
    with pytest.raises(ValidationError, match="non-empty output"):
        BaselineRunRecord(
            run_id="run-1",
            case_id="simple-001",
            mode="replay",
            project_commit="a" * 40,
            config_snapshot={},
            output=None,
            telemetry=_telemetry(),
            created_at=datetime.now(timezone.utc),
            telemetry_source="fixture",
        )


def test_telemetry_rejects_backward_time_and_invalid_tool_counts():
    started = datetime(2026, 7, 20, tzinfo=timezone.utc)
    with pytest.raises(ValidationError, match="cannot precede"):
        _telemetry(finished_at=started - timedelta(seconds=1))
    with pytest.raises(ValidationError, match="non-negative"):
        _telemetry(tool_calls_by_name={"tavily_search": -1})
    with pytest.raises(ValidationError, match="cannot be empty"):
        _telemetry(tool_requests_by_name={"": 1})
