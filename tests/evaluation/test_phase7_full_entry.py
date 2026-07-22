from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from open_deep_research.evaluation.eval_environment import (
    EvaluationEnvironmentError,
)
from open_deep_research.evaluation.gates import EvaluationAuthorizationError
from open_deep_research.evaluation.runner import (
    inspect_full_preflight,
    run_authorized_full,
)
from open_deep_research.evaluation.source_gate import EvaluationSourceGateError
from scripts import run_eval as run_eval_script
from scripts.run_eval import _safe_full_error, parser

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class _Record:
    payload: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return self.payload


def _authorize(monkeypatch) -> None:
    monkeypatch.setenv("ODR_EVAL_MODE", "full")
    monkeypatch.setenv("RUN_FULL_EVAL", "1")


def _clean_source(monkeypatch, calls: list[str] | None = None) -> None:
    def clean_source(_root):
        if calls is not None:
            calls.append("source")
        return _Record(
            {
                "git_head": "a" * 40,
                "clean": True,
                "checked_paths": ["tests/evaluation"],
            }
        )

    monkeypatch.setattr(
        "open_deep_research.evaluation.runner.require_clean_evaluation_source",
        clean_source,
    )


def test_authorized_full_hands_fixed_projection_environment_and_local_tracking(
    tmp_path, monkeypatch
) -> None:
    _authorize(monkeypatch)
    _clean_source(monkeypatch)
    monkeypatch.setattr(
        "open_deep_research.evaluation.runner.require_evaluation_environment",
        lambda **_: _Record({"python": "3.11", "pip_check": "passed"}),
    )
    monkeypatch.setattr(
        "open_deep_research.evaluation.runner.require_completed_calibration",
        lambda **_: _Record({"projected_tokens": 12_345, "full_runs": 54}),
    )
    monkeypatch.setattr(
        "open_deep_research.evaluation.runner.resolve_models",
        lambda *_: {
            role: "openai:qwen3.7-plus"
            for role in (
                "summarization",
                "research",
                "compression",
                "final_report",
                "judge",
            )
        },
    )
    captured: dict[str, object] = {}

    async def fake_matrix(**kwargs):
        captured.update(kwargs)
        return "completed"

    monkeypatch.setattr(
        "open_deep_research.evaluation.runner._execute_full_matrix", fake_matrix
    )
    result = asyncio.run(
        run_authorized_full(
            project_root=ROOT,
            output_dir=tmp_path / "full",
            calibration_dir=tmp_path / "calibration",
            dataset_version="v1",
            variant_ids=None,
            repeats=3,
            requested_max_tokens=42_000_000,
            confirm_cost=True,
        )
    )

    assert result == "completed"
    assert captured["requested_max_tokens"] == 42_000_000
    assert captured["provenance"] == "live"
    assert captured["require_deepeval"] is True
    assert captured["calibration_projection"] == {
        "projected_tokens": 12_345,
        "full_runs": 54,
        "evaluation_environment": {"python": "3.11", "pip_check": "passed"},
        "source_attestation": {
            "git_head": "a" * 40,
            "clean": True,
            "checked_paths": ["tests/evaluation"],
        },
    }
    assert type(captured["tracking_sink"]).__name__ == "LocalTrackingSink"


def test_read_only_full_preflight_reports_projection_without_authorization_or_executor(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("ODR_EVAL_MODE", raising=False)
    monkeypatch.delenv("RUN_FULL_EVAL", raising=False)
    _clean_source(monkeypatch)
    monkeypatch.setattr(
        "open_deep_research.evaluation.runner.require_evaluation_environment",
        lambda **_: _Record({"python": "3.11", "pip_check": "passed"}),
    )
    monkeypatch.setattr(
        "open_deep_research.evaluation.runner.require_completed_calibration",
        lambda **_: _Record(
            {
                "projected_tokens": 12_345,
                "requested_max_tokens": 42_000_000,
                "full_runs": 54,
            }
        ),
    )
    monkeypatch.setattr(
        "open_deep_research.evaluation.runner.resolve_models",
        lambda *_: {
            role: "openai:qwen3.7-plus"
            for role in (
                "summarization",
                "research",
                "compression",
                "final_report",
                "judge",
            )
        },
    )

    result = inspect_full_preflight(
        project_root=ROOT,
        output_dir=tmp_path / "full",
        calibration_dir=tmp_path / "calibration",
        dataset_version="v1",
        variant_ids=None,
        repeats=3,
        requested_max_tokens=42_000_000,
    )

    assert result["status"] == "ready_for_separate_full_authorization"
    assert result["matrix"]["total_runs"] == 54
    assert result["projection"]["projected_tokens"] == 12_345
    assert result["estimated_cost_usd"] is None


def test_cli_full_preflight_needs_no_paid_authorization(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("ODR_EVAL_MODE", raising=False)
    monkeypatch.delenv("RUN_FULL_EVAL", raising=False)
    monkeypatch.setattr(
        run_eval_script,
        "inspect_full_preflight",
        lambda **_: {
            "status": "ready_for_separate_full_authorization",
            "projection": {"projected_tokens": 12_345},
        },
    )

    code = run_eval_script.main(
        [
            "--mode",
            "full",
            "--preflight-only",
            "--variants",
            "all",
            "--dataset-version",
            "v1",
            "--repeats",
            "3",
            "--max-total-tokens",
            "42000000",
            "--calibration-output",
            str(tmp_path / "calibration"),
            "--output",
            str(tmp_path / "full"),
        ]
    )

    assert code == 0
    assert "ready_for_separate_full_authorization" in capsys.readouterr().out


def test_environment_failure_stops_before_calibration_or_executor(
    tmp_path, monkeypatch
) -> None:
    _authorize(monkeypatch)
    calls: list[str] = []
    _clean_source(monkeypatch, calls)

    def bad_environment(**kwargs):
        calls.append("environment")
        raise EvaluationEnvironmentError("bad eval environment")

    def forbidden_calibration(**kwargs):
        calls.append("calibration")
        raise AssertionError(kwargs)

    monkeypatch.setattr(
        "open_deep_research.evaluation.runner.require_evaluation_environment",
        bad_environment,
    )
    monkeypatch.setattr(
        "open_deep_research.evaluation.runner.require_completed_calibration",
        forbidden_calibration,
    )
    with pytest.raises(EvaluationEnvironmentError):
        asyncio.run(
            run_authorized_full(
                project_root=ROOT,
                output_dir=tmp_path / "full",
                calibration_dir=tmp_path / "calibration",
                dataset_version="v1",
                variant_ids=None,
                repeats=3,
                requested_max_tokens=42_000_000,
                confirm_cost=True,
            )
        )
    assert calls == ["source", "environment"]
    assert not (tmp_path / "full").exists()


def test_source_failure_stops_before_environment_calibration_or_executor(
    tmp_path, monkeypatch
) -> None:
    _authorize(monkeypatch)
    calls: list[str] = []

    def bad_source(_root):
        calls.append("source")
        raise EvaluationSourceGateError("dirty evaluation source")

    def forbidden(**kwargs):
        calls.append("forbidden")
        raise AssertionError(kwargs)

    monkeypatch.setattr(
        "open_deep_research.evaluation.runner.require_clean_evaluation_source",
        bad_source,
    )
    monkeypatch.setattr(
        "open_deep_research.evaluation.runner.require_evaluation_environment",
        forbidden,
    )
    monkeypatch.setattr(
        "open_deep_research.evaluation.runner.require_completed_calibration",
        forbidden,
    )
    monkeypatch.setattr(
        "open_deep_research.evaluation.runner._execute_full_matrix", forbidden
    )

    with pytest.raises(EvaluationSourceGateError, match="dirty evaluation source"):
        asyncio.run(
            run_authorized_full(
                project_root=ROOT,
                output_dir=tmp_path / "full",
                calibration_dir=tmp_path / "calibration",
                dataset_version="v1",
                variant_ids=None,
                repeats=3,
                requested_max_tokens=42_000_000,
                confirm_cost=True,
            )
        )

    assert calls == ["source"]
    assert not (tmp_path / "full").exists()


def test_full_entry_rejects_subset_and_cli_tracking_defaults_before_dispatch(
    tmp_path, monkeypatch
) -> None:
    with pytest.raises(EvaluationAuthorizationError, match="exact five variants"):
        asyncio.run(
            run_authorized_full(
                project_root=ROOT,
                output_dir=tmp_path / "full",
                calibration_dir=tmp_path / "calibration",
                dataset_version="v1",
                variant_ids=["baseline"],
                repeats=3,
                requested_max_tokens=42_000_000,
                confirm_cost=True,
            )
        )
    args = parser().parse_args(["--mode", "full", "--output", str(tmp_path / "x")])
    assert args.tracking == "local"
    selected = parser().parse_args(
        [
            "--mode",
            "full",
            "--tracking",
            "langsmith",
            "--langsmith-project",
            "phase7-local-mirror",
            "--output",
            str(tmp_path / "y"),
        ]
    )
    assert selected.tracking == "langsmith"
    assert selected.langsmith_project == "phase7-local-mirror"


def test_runtime_permission_or_schema_errors_never_claim_zero_paid_work() -> None:
    permission = _safe_full_error(PermissionError("persist failed"))
    schema = _safe_full_error(ValueError("artifact invalid"))

    assert permission["status"] == "stopped_or_paid_state_unknown"
    assert schema["status"] == "stopped_or_paid_state_unknown"
    assert "budget.json" in permission["message"]
    assert "--resume" in permission["message"]
