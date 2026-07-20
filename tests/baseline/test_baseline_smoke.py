import asyncio
import socket

import pytest

from open_deep_research.evaluation.baseline import (
    LiveAuthorizationError,
    live_authorization_refusal,
    load_cases,
    run_live_authorized,
    run_replay,
    select_case,
)
from open_deep_research.evaluation.models import BaselineRunRecord, RunStatus
from open_deep_research.evaluation.storage import load_jsonl
from scripts import run_baseline


def test_dataset_has_three_cases_per_difficulty_and_unique_ids():
    cases = load_cases()
    counts = {difficulty: 0 for difficulty in ("simple", "medium", "complex")}
    for case in cases:
        counts[case.difficulty.value] += 1

    assert counts == {"simple": 3, "medium": 3, "complex": 3}
    assert len({case.id for case in cases}) == len(cases)


def test_replay_runs_offline_and_produces_complete_record(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network access attempted by replay")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    output = tmp_path / "smoke.jsonl"

    record = run_replay("simple-001", output, commit="a" * 40)
    loaded = load_jsonl(output, BaselineRunRecord)

    assert loaded == [record]
    assert record.telemetry.status is RunStatus.COMPLETED
    assert record.telemetry.wall_time_ms == 128.5
    assert record.telemetry.input_tokens == 180
    assert record.telemetry.estimated_cost is None
    assert record.telemetry.tool_calls_by_name == {"local_fixture_lookup": 1}
    assert record.output
    assert all(metric.passed for metric in record.metrics)


def test_live_refusal_identifies_every_missing_gate():
    refusal = live_authorization_refusal(
        "simple-001", confirm_cost=False, environment={}
    )

    assert refusal is not None
    assert refusal.status == "not_run_no_authorization"
    assert refusal.missing_gates == [
        "ODR_EVAL_MODE=live",
        "RUN_LIVE_RESEARCH=1",
        "--confirm-cost",
    ]


def test_live_cli_refuses_before_external_call_or_output(tmp_path, monkeypatch, capsys):
    output = tmp_path / "must-not-exist.jsonl"
    monkeypatch.delenv("ODR_EVAL_MODE", raising=False)
    monkeypatch.delenv("RUN_LIVE_RESEARCH", raising=False)

    async def forbidden(*args, **kwargs):
        raise AssertionError("live callable reached before authorization")

    monkeypatch.setattr(run_baseline, "run_live_authorized", forbidden)
    exit_code = run_baseline.main(
        [
            "--mode",
            "live",
            "--case",
            "simple-001",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 3
    assert not output.exists()
    refusal = capsys.readouterr().err
    assert '"status":"not_run_no_authorization"' in refusal


def test_live_execution_api_cannot_bypass_gate(tmp_path):
    class ForbiddenRunnable:
        async def ainvoke(self, input_value, config):
            raise AssertionError("runnable reached before authorization")

    output = tmp_path / "must-not-exist.jsonl"

    with pytest.raises(LiveAuthorizationError):
        asyncio.run(
            run_live_authorized(
                select_case("simple-001"),
                output,
                commit="a" * 40,
                environment={},
                _runnable=ForbiddenRunnable(),
            )
        )

    assert not output.exists()


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (RuntimeError("fake failure"), RunStatus.FAILED),
        (asyncio.CancelledError(), RunStatus.CANCELLED),
    ],
)
def test_authorized_fake_live_failure_or_cancel_is_persisted(
    tmp_path, error, expected_status
):
    class FailingRunnable:
        async def ainvoke(self, input_value, config):
            raise error

    output = tmp_path / f"{expected_status.value}.jsonl"
    environment = {"ODR_EVAL_MODE": "live", "RUN_LIVE_RESEARCH": "1"}

    with pytest.raises(type(error)):
        asyncio.run(
            run_live_authorized(
                select_case("simple-001"),
                output,
                commit="a" * 40,
                confirm_cost=True,
                environment=environment,
                _runnable=FailingRunnable(),
            )
        )

    record = load_jsonl(output, BaselineRunRecord)[0]
    assert record.telemetry.status is expected_status
    assert record.telemetry.error_type == type(error).__name__
    assert record.output is None


def test_live_cli_maps_cancelled_error_to_130(tmp_path, monkeypatch, capsys):
    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(run_baseline, "run_live_authorized", cancel)
    monkeypatch.setenv("ODR_EVAL_MODE", "live")
    monkeypatch.setenv("RUN_LIVE_RESEARCH", "1")

    exit_code = run_baseline.main(
        [
            "--mode",
            "live",
            "--case",
            "simple-001",
            "--confirm-cost",
            "--output",
            str(tmp_path / "cancelled.jsonl"),
        ]
    )

    assert exit_code == 130
    assert '"status": "cancelled"' in capsys.readouterr().err


def test_replay_cli_is_default_mode(tmp_path, capsys):
    output = tmp_path / "replay.jsonl"

    exit_code = run_baseline.main(
        ["--case", "simple-001", "--output", str(output)]
    )

    assert exit_code == 0
    assert output.exists()
    assert '"mode":"replay"' in capsys.readouterr().out
