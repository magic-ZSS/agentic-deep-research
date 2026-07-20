from datetime import datetime, timedelta, timezone

import pytest

from open_deep_research.evaluation.models import (
    BaselineRunRecord,
    RunStatus,
    RunTelemetry,
)
from open_deep_research.evaluation.storage import (
    JsonlLoadError,
    append_jsonl_atomic,
    load_jsonl,
    write_jsonl_atomic,
)


def _record(run_id="run-1"):
    started = datetime(2026, 7, 20, tzinfo=timezone.utc)
    return BaselineRunRecord(
        run_id=run_id,
        case_id="simple-001",
        mode="replay",
        project_commit="a" * 40,
        config_snapshot={},
        output="answer",
        telemetry=RunTelemetry(
            started_at=started,
            finished_at=started + timedelta(milliseconds=1),
            wall_time_ms=1,
            input_tokens=1,
            output_tokens=2,
            total_tokens=3,
            estimated_cost=None,
            model_calls=1,
            model_calls_with_usage=1,
            status=RunStatus.COMPLETED,
        ),
        created_at=started,
        telemetry_source="fixture",
    )


def test_atomic_write_and_append_round_trip(tmp_path):
    path = tmp_path / "nested" / "records.jsonl"
    write_jsonl_atomic(path, [_record()])
    append_jsonl_atomic(path, _record("run-2"), BaselineRunRecord)

    records = load_jsonl(path, BaselineRunRecord)

    assert [record.run_id for record in records] == ["run-1", "run-2"]
    assert not list(path.parent.glob("*.tmp"))


def test_corrupt_tail_reports_file_and_line(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text(_record().model_dump_json() + "\n{broken", encoding="utf-8")

    with pytest.raises(JsonlLoadError) as exc_info:
        load_jsonl(path, BaselineRunRecord)

    message = str(exc_info.value)
    assert str(path) in message
    assert "line 2" in message


def test_append_refuses_to_replace_corrupt_file(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text("{broken\n", encoding="utf-8")

    with pytest.raises(JsonlLoadError):
        append_jsonl_atomic(path, _record(), BaselineRunRecord)

    assert path.read_text(encoding="utf-8") == "{broken\n"
