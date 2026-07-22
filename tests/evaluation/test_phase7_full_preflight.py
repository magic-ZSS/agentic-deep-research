from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from open_deep_research.evaluation.calibration_state import (
    CalibrationJournalStore,
    CalibrationRunDefinition,
    make_experiment_identity,
)
from open_deep_research.evaluation.claim_scorer import CLAIM_SCORER_VERSION
from open_deep_research.evaluation.experiment_models import (
    EvaluationStatus,
    ExperimentRun,
    ExperimentTelemetry,
)
from open_deep_research.evaluation.full_metrics import (
    FULL_JUDGE_STEP_NAMES,
)
from open_deep_research.evaluation.full_preflight import (
    FullPreflightError,
    require_completed_calibration,
)
from open_deep_research.evaluation.live_budget import LiveTokenReservationLedger
from open_deep_research.evaluation.reporting import write_artifact_manifest
from open_deep_research.evaluation.source_gate import EVALUATION_SOURCE_PATHS

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _completed_calibration(
    output: Path, *, tokens: int | list[int] = 100
) -> object:
    output.mkdir(parents=True)
    identity = make_experiment_identity(
        git_head_value=COMMIT,
        dirty_diff_sha256="b" * 64,
        plan_sha256="c" * 64,
        ablation_sha256="d" * 64,
        dataset_id="v1",
        model_ids={
            "compression": "openai:qwen3.7-plus",
            "final_report": "openai:qwen3.7-plus",
            "judge": "openai:qwen3.7-plus",
            "research": "openai:qwen3.7-plus",
            "summarization": "openai:qwen3.7-plus",
            "protocol": "phase7-calibration-v1",
            "provenance": "live",
        },
    )
    run_specs = [
        (case_id, difficulty, variant_id)
        for case_id, difficulty in (
            ("simple-001", "simple"),
            ("medium-001", "medium"),
            ("complex-001", "complex"),
        )
        for variant_id in ("baseline", "citation_validator")
    ]
    token_values = [tokens] * len(run_specs) if isinstance(tokens, int) else tokens
    assert len(token_values) == len(run_specs)
    identity_payload = identity.model_dump(mode="json")
    definitions = [
        CalibrationRunDefinition(
            case_id=case_id,
            variant_id=variant_id,
            repeat=1,
        )
        for case_id, _, variant_id in run_specs
    ]
    journal_store = CalibrationJournalStore.create(
        output / "journal.json",
        identity=identity,
        runs=definitions,
        judge_metric_names=FULL_JUDGE_STEP_NAMES,
    )
    plans = {
        (plan.case_id, plan.variant_id, plan.repeat): plan
        for plan in journal_store.load().runs
    }
    runs: list[ExperimentRun] = []
    now = datetime.now(UTC)
    token_by_run: dict[str, int] = {}
    for (case_id, difficulty, variant_id), run_tokens in zip(
        run_specs, token_values, strict=True
    ):
        plan = plans[(case_id, variant_id, 1)]
        token_by_run[plan.run_id] = run_tokens
        runs.append(
            ExperimentRun(
                experiment_id=identity.experiment_id,
                run_id=plan.run_id,
                variant_id=variant_id,
                case_id=case_id,
                difficulty=difficulty,
                repeat=1,
                mode="calibration",
                project_commit=COMMIT,
                dataset_version="v1",
                scorer_version=CLAIM_SCORER_VERSION,
                output="fixture",
                output_sha256="e" * 64,
                trace={
                    "evaluation_provenance": "live",
                    "evaluation_claim_results": [
                        {
                            "claim_id": "fixture-claim-1",
                            "status": "fully_supported",
                        }
                    ],
                },
                telemetry=ExperimentTelemetry(total_tokens=run_tokens),
                metric_results=[],
                status=EvaluationStatus.PASSED,
                started_at=now,
                finished_at=now,
            )
        )
    ledger_store = LiveTokenReservationLedger(
        hard_token_limit=3_000_000,
        per_run_token_limit=800_000,
    )
    for plan in journal_store.load().runs:
        research_tokens = token_by_run[plan.run_id] - len(FULL_JUDGE_STEP_NAMES)
        research = ledger_store.reserve_before_call(
            run_id=plan.run_id,
            category="research",
            input_upper_bound=research_tokens,
            output_upper_bound=0,
        )
        journal_store.start_research(plan.run_id)
        ledger_store.settle_success(
            research.reservation_id,
            actual_input_tokens=research_tokens,
            actual_output_tokens=0,
        )
        journal_store.complete_research(
            plan.run_id,
            input_tokens=research_tokens,
            output_tokens=0,
            total_tokens=research_tokens,
        )
        for metric_name in FULL_JUDGE_STEP_NAMES:
            judge = ledger_store.reserve_before_call(
                run_id=plan.run_id,
                category="judge",
                input_upper_bound=1,
                output_upper_bound=0,
            )
            journal_store.start_judge_metric(plan.run_id, metric_name)
            ledger_store.settle_success(
                judge.reservation_id,
                actual_input_tokens=1,
                actual_output_tokens=0,
            )
            journal_store.complete_judge_metric(
                plan.run_id,
                metric_name,
                status="passed",
                input_tokens=1,
                output_tokens=0,
                total_tokens=1,
            )
        journal_store.complete_run(plan.run_id, status="completed")
    ledger = ledger_store.snapshot()
    (output / "runs.jsonl").write_text(
        "".join(
            json.dumps(run.model_dump(mode="json"), separators=(",", ":")) + "\n"
            for run in runs
        ),
        encoding="utf-8",
    )
    _write_json(
        output / "experiment.json",
        {
            "experiment_id": identity.experiment_id,
            "status": "completed",
            "provenance": "live",
            "dataset_version": "v1",
            "planned_runs": 6,
            "completed_run_records": 6,
            "source_attestation": {
                "git_head": COMMIT,
                "clean": True,
                "checked_paths": list(EVALUATION_SOURCE_PATHS),
            },
        },
    )
    _write_json(
        output / "report.json",
        {
            "experiment_id": identity.experiment_id,
            "calibration_status": "completed",
            "provenance": "live",
            "planned_runs": 6,
            "completed_run_records": 6,
            "token_budget": ledger,
        },
    )
    _write_json(
        output / "budget.json",
        {"calibration_identity": identity_payload, "ledger": ledger},
    )
    write_artifact_manifest(
        output,
        experiment_id=identity.experiment_id,
        dataset_version="v1",
        project_commit=COMMIT,
    )
    return identity


def test_completed_calibration_projects_all_54_new_runs_conservatively(
    tmp_path, monkeypatch
) -> None:
    calibration = tmp_path / "calibration"
    identity = _completed_calibration(calibration)
    monkeypatch.setattr(
        "open_deep_research.evaluation.full_preflight._require_identity",
        lambda **_: identity,
    )

    result = require_completed_calibration(
        project_root=ROOT,
        calibration_dir=calibration,
        full_output_dir=tmp_path / "full",
        dataset_version="v1",
        requested_max_tokens=42_000_000,
    )

    assert result.calibration_runs == 6
    assert result.full_runs == 54
    assert result.observed_tokens == [100] * 6
    assert result.projected_tokens == 7_350


def test_stopped_calibration_never_becomes_full_authorization(tmp_path) -> None:
    calibration = tmp_path / "calibration"
    _completed_calibration(calibration)
    report = json.loads((calibration / "report.json").read_text(encoding="utf-8"))
    report["calibration_status"] = "stopped"
    _write_json(calibration / "report.json", report)
    manifest = calibration / "manifest.json"
    manifest.unlink()
    write_artifact_manifest(
        calibration,
        experiment_id=report["experiment_id"],
        dataset_version="v1",
        project_commit=COMMIT,
    )

    with pytest.raises(FullPreflightError, match="not completed"):
        require_completed_calibration(
            project_root=ROOT,
            calibration_dir=calibration,
            full_output_dir=tmp_path / "full",
            dataset_version="v1",
            requested_max_tokens=42_000_000,
        )


def test_fake_calibration_never_authorizes_live_full(tmp_path) -> None:
    calibration = tmp_path / "calibration"
    _completed_calibration(calibration)
    experiment = json.loads(
        (calibration / "experiment.json").read_text(encoding="utf-8")
    )
    report = json.loads((calibration / "report.json").read_text(encoding="utf-8"))
    experiment["provenance"] = "fake"
    report["provenance"] = "fake"
    _write_json(calibration / "experiment.json", experiment)
    _write_json(calibration / "report.json", report)
    (calibration / "manifest.json").unlink()
    write_artifact_manifest(
        calibration,
        experiment_id=experiment["experiment_id"],
        dataset_version="v1",
        project_commit=COMMIT,
    )

    with pytest.raises(FullPreflightError, match="live provenance"):
        require_completed_calibration(
            project_root=ROOT,
            calibration_dir=calibration,
            full_output_dir=tmp_path / "full",
            dataset_version="v1",
            requested_max_tokens=42_000_000,
        )


def test_calibration_manifest_corruption_and_unsafe_projection_fail_closed(
    tmp_path, monkeypatch
) -> None:
    corrupted = tmp_path / "corrupted"
    identity = _completed_calibration(corrupted)
    (corrupted / "runs.jsonl").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(FullPreflightError, match="manifest integrity"):
        require_completed_calibration(
            project_root=ROOT,
            calibration_dir=corrupted,
            full_output_dir=tmp_path / "full-a",
            dataset_version="v1",
            requested_max_tokens=42_000_000,
        )

    oversized = tmp_path / "oversized"
    _completed_calibration(oversized, tokens=[800_000, 100, 100, 100, 100, 100])
    monkeypatch.setattr(
        "open_deep_research.evaluation.full_preflight._require_identity",
        lambda **_: identity,
    )
    with pytest.raises(FullPreflightError, match="projection"):
        require_completed_calibration(
            project_root=ROOT,
            calibration_dir=oversized,
            full_output_dir=tmp_path / "full-b",
            dataset_version="v1",
            requested_max_tokens=42_000_000,
        )


def test_preflight_rejects_noncanonical_ledger_incomplete_journal_and_manifest_escape(
    tmp_path,
) -> None:
    broken_ledger = tmp_path / "broken-ledger"
    _completed_calibration(broken_ledger)
    budget = json.loads((broken_ledger / "budget.json").read_text(encoding="utf-8"))
    budget["ledger"].pop("revision")
    report = json.loads((broken_ledger / "report.json").read_text(encoding="utf-8"))
    report["token_budget"] = budget["ledger"]
    _write_json(broken_ledger / "budget.json", budget)
    _write_json(broken_ledger / "report.json", report)
    (broken_ledger / "manifest.json").unlink()
    write_artifact_manifest(
        broken_ledger,
        experiment_id=report["experiment_id"],
        dataset_version="v1",
        project_commit=COMMIT,
    )
    with pytest.raises(FullPreflightError, match="ledger cannot be recovered"):
        require_completed_calibration(
            project_root=ROOT,
            calibration_dir=broken_ledger,
            full_output_dir=tmp_path / "full-ledger",
            dataset_version="v1",
            requested_max_tokens=42_000_000,
        )

    incomplete = tmp_path / "incomplete-journal"
    _completed_calibration(incomplete)
    journal = json.loads((incomplete / "journal.json").read_text(encoding="utf-8"))
    journal["events"] = journal["events"][:-1]
    _write_json(incomplete / "journal.json", journal)
    (incomplete / "manifest.json").unlink()
    write_artifact_manifest(
        incomplete,
        experiment_id=journal["identity"]["experiment_id"],
        dataset_version="v1",
        project_commit=COMMIT,
    )
    with pytest.raises(FullPreflightError, match="unsafe or incomplete"):
        require_completed_calibration(
            project_root=ROOT,
            calibration_dir=incomplete,
            full_output_dir=tmp_path / "full-journal",
            dataset_version="v1",
            requested_max_tokens=42_000_000,
        )

    escaping = tmp_path / "escaping-manifest"
    _completed_calibration(escaping)
    manifest = json.loads((escaping / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../outside.json"
    _write_json(escaping / "manifest.json", manifest)
    with pytest.raises(FullPreflightError, match="unsafe path"):
        require_completed_calibration(
            project_root=ROOT,
            calibration_dir=escaping,
            full_output_dir=tmp_path / "full-manifest",
            dataset_version="v1",
            requested_max_tokens=42_000_000,
        )
