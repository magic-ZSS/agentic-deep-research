"""Validate calibration and projection evidence before a paid full matrix."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from open_deep_research.evaluation.budget import (
    EvaluationBudgetError,
    load_full_plan,
    project_total_tokens,
    resolve_models,
)
from open_deep_research.evaluation.calibration_state import (
    CalibrationExperimentIdentity,
    CalibrationJournalStore,
    capture_experiment_identity,
)
from open_deep_research.evaluation.claim_scorer import CLAIM_SCORER_VERSION
from open_deep_research.evaluation.experiment_models import (
    EvaluationStatus,
    ExperimentRun,
)
from open_deep_research.evaluation.full_metrics import FULL_JUDGE_STEP_NAMES
from open_deep_research.evaluation.live_budget import LiveTokenReservationLedger
from open_deep_research.evaluation.reporting import validate_artifact_manifest
from open_deep_research.evaluation.source_gate import EVALUATION_SOURCE_PATHS
from open_deep_research.evaluation.storage import load_jsonl


class FullPreflightError(RuntimeError):
    """Reject full evaluation before any paid call when evidence is unsafe."""


@dataclass(frozen=True, slots=True)
class FullCalibrationProjection:
    """Non-secret calibration evidence copied into the full experiment record."""

    calibration_experiment_id: str
    calibration_runs: int
    full_runs: int
    observed_tokens: list[int]
    safety_multiplier: float
    projected_tokens: int
    requested_max_tokens: int

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-compatible projection record."""
        return asdict(self)


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FullPreflightError("calibration artifact cannot be read safely") from exc
    if not isinstance(payload, dict):
        raise FullPreflightError("calibration artifact must contain a JSON object")
    return payload


def _relative_exclusion(root: Path, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return None


def _validate_manifest_inventory(
    calibration: Path,
    *,
    required: set[str],
) -> dict:
    """Reject unlisted, escaping, duplicate, or symlinked artifact entries."""
    manifest_payload = _read_json(calibration / "manifest.json")
    files = manifest_payload.get("files")
    if not isinstance(files, list):
        raise FullPreflightError("calibration manifest file inventory is invalid")
    listed: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise FullPreflightError("calibration manifest file inventory is invalid")
        raw = item["path"]
        parsed = PurePosixPath(raw)
        if (
            not raw
            or "\\" in raw
            or parsed.is_absolute()
            or any(part in {"", ".", ".."} or ":" in part for part in parsed.parts)
            or parsed.as_posix() != raw
            or raw in listed
        ):
            raise FullPreflightError("calibration manifest contains an unsafe path")
        listed.add(raw)
    actual: set[str] = set()
    for path in calibration.rglob("*"):
        if path.is_symlink():
            raise FullPreflightError("calibration artifacts must not contain symlinks")
        if path.is_file() and path.name != "manifest.json":
            actual.add(path.relative_to(calibration).as_posix())
    if not required - {"manifest.json"} <= listed:
        raise FullPreflightError("calibration manifest omits a required artifact")
    if listed != actual:
        raise FullPreflightError("calibration manifest inventory differs from disk")
    try:
        manifest_errors = validate_artifact_manifest(calibration)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise FullPreflightError("calibration manifest cannot be validated") from exc
    if manifest_errors:
        raise FullPreflightError("calibration manifest integrity check failed")
    return manifest_payload


def _require_identity(
    *,
    root: Path,
    calibration: Path,
    full_output: Path,
    dataset_version: str,
    environment: Mapping[str, str],
    recorded: dict,
) -> CalibrationExperimentIdentity:
    exclusions = [
        item
        for item in (
            _relative_exclusion(root, calibration),
            _relative_exclusion(root, full_output),
        )
        if item is not None
    ]
    models = resolve_models(
        load_full_plan(root / "tests/evaluation/full_plan.v1.json"), environment
    )
    current = capture_experiment_identity(
        root,
        plan_path=root / "tests/evaluation/full_plan.v1.json",
        ablation_path=root / "tests/evaluation/ablations.v1.json",
        dataset_id=dataset_version,
        model_ids={
            **models,
            "protocol": "phase7-calibration-v1",
            "provenance": "live",
        },
        exclude_untracked_paths=exclusions,
    )
    try:
        prior = CalibrationExperimentIdentity.model_validate(recorded)
    except ValueError as exc:
        raise FullPreflightError("calibration identity schema is invalid") from exc
    if current != prior:
        raise FullPreflightError(
            "calibration identity does not match the current source, plan, dataset, or models"
        )
    return current


def require_completed_calibration(
    *,
    project_root: str | Path,
    calibration_dir: str | Path,
    full_output_dir: str | Path,
    dataset_version: str,
    requested_max_tokens: int,
    environment: Mapping[str, str] | None = None,
) -> FullCalibrationProjection:
    """Require a complete current calibration and a safe 54-run projection."""
    root = Path(project_root).resolve()
    calibration = Path(calibration_dir).resolve()
    full_output = Path(full_output_dir).resolve()
    plan = load_full_plan(root / "tests/evaluation/full_plan.v1.json")
    required = {
        "budget.json",
        "experiment.json",
        "journal.json",
        "manifest.json",
        "report.json",
        "runs.jsonl",
    }
    missing = sorted(name for name in required if not (calibration / name).is_file())
    if missing:
        raise FullPreflightError(
            "completed calibration artifact is required before full evaluation"
        )
    manifest_payload = _validate_manifest_inventory(
        calibration,
        required=required,
    )

    experiment = _read_json(calibration / "experiment.json")
    report = _read_json(calibration / "report.json")
    budget = _read_json(calibration / "budget.json")
    journal = _read_json(calibration / "journal.json")
    if experiment.get("status") != "completed" or report.get("calibration_status") != "completed":
        raise FullPreflightError("calibration is not completed and cannot authorize full evaluation")
    if experiment.get("provenance") != "live" or report.get("provenance") != "live":
        raise FullPreflightError(
            "calibration must contain live provenance before full evaluation"
        )
    expected_runs = int(plan["calibration"]["research_runs"])
    if (
        experiment.get("planned_runs") != expected_runs
        or experiment.get("completed_run_records") != expected_runs
        or report.get("planned_runs") != expected_runs
        or report.get("completed_run_records") != expected_runs
    ):
        raise FullPreflightError("calibration does not contain the fixed completed matrix")
    if experiment.get("dataset_version") != dataset_version:
        raise FullPreflightError("calibration dataset version does not match the full run")

    identity_payload = journal.get("identity")
    ledger = budget.get("ledger")
    if not isinstance(identity_payload, dict) or not isinstance(ledger, dict):
        raise FullPreflightError("calibration identity or token ledger is missing")
    if budget.get("calibration_identity") != identity_payload:
        raise FullPreflightError("calibration identity differs across durable artifacts")
    if report.get("token_budget") != ledger:
        raise FullPreflightError("calibration ledger differs across durable artifacts")
    try:
        recovered_ledger = LiveTokenReservationLedger.from_snapshot(ledger)
        normalized_ledger = recovered_ledger.snapshot()
        journal_store = CalibrationJournalStore(calibration / "journal.json")
        recovered_journal = journal_store.load()
        resume = journal_store.resume_summary()
    except (OSError, ValueError, RuntimeError) as exc:
        raise FullPreflightError(
            "calibration journal or token ledger cannot be recovered"
        ) from exc
    if normalized_ledger != ledger:
        raise FullPreflightError("calibration token ledger is not canonical")
    if recovered_journal.identity.model_dump(mode="json") != identity_payload:
        raise FullPreflightError("calibration journal identity is inconsistent")
    if (
        normalized_ledger["unknown_usage"] is not False
        or normalized_ledger["fail_closed"] is not False
        or normalized_ledger["active_calls"] != 0
        or normalized_ledger["active_reserved_tokens"] != 0
        or normalized_ledger["error_count"] != 0
        or not resume.can_resume
        or resume.blocked_in_flight_step_ids
        or resume.unknown_usage_step_ids
        or resume.pending_step_ids
        or resume.terminal_noncompleted_run_ids
        or len(resume.completed_run_ids) != expected_runs
        or len(resume.completed_metric_step_ids)
        != expected_runs * len(FULL_JUDGE_STEP_NAMES)
        or any(
            set(item.judge_step_ids) != set(FULL_JUDGE_STEP_NAMES)
            for item in recovered_journal.runs
        )
    ):
        raise FullPreflightError("calibration token evidence is unsafe or incomplete")

    current_identity = _require_identity(
        root=root,
        calibration=calibration,
        full_output=full_output,
        dataset_version=dataset_version,
        environment=environment or os.environ,
        recorded=identity_payload,
    )
    if (
        experiment.get("experiment_id") != current_identity.experiment_id
        or report.get("experiment_id") != current_identity.experiment_id
        or manifest_payload.get("experiment_id") != current_identity.experiment_id
        or manifest_payload.get("dataset_version") != dataset_version
        or manifest_payload.get("project_commit") != current_identity.git_head
    ):
        raise FullPreflightError("calibration experiment identity is inconsistent")
    source_attestation = experiment.get("source_attestation")
    if (
        not isinstance(source_attestation, dict)
        or source_attestation.get("clean") is not True
        or source_attestation.get("git_head") != current_identity.git_head
        or source_attestation.get("checked_paths") != list(EVALUATION_SOURCE_PATHS)
    ):
        raise FullPreflightError(
            "calibration has no matching clean-source attestation"
        )

    try:
        runs = load_jsonl(calibration / "runs.jsonl", ExperimentRun)
    except (OSError, UnicodeError, ValueError) as exc:
        raise FullPreflightError("calibration run records are invalid") from exc
    expected_matrix = {
        (case_id, variant_id, repeat)
        for case_id in plan["calibration"]["case_ids"]
        for variant_id in plan["calibration"]["variants"]
        for repeat in range(1, int(plan["calibration"]["repeats"]) + 1)
    }
    actual_matrix = {(run.case_id, run.variant_id, run.repeat) for run in runs}
    if len(runs) != expected_runs or actual_matrix != expected_matrix:
        raise FullPreflightError("calibration run records do not match the fixed matrix")
    if {run.run_id for run in runs} != set(resume.completed_run_ids):
        raise FullPreflightError("calibration runs do not match completed journal identities")
    if any(
        run.mode != "calibration"
        or run.trace.get("evaluation_provenance") != "live"
        or run.scorer_version != CLAIM_SCORER_VERSION
        or not run.trace.get("evaluation_claim_results")
        or run.experiment_id != current_identity.experiment_id
        or run.status in {
            EvaluationStatus.ERROR,
            EvaluationStatus.SKIPPED,
            EvaluationStatus.NOT_APPLICABLE,
        }
        for run in runs
    ):
        raise FullPreflightError("calibration contains ineligible terminal runs")
    observed_tokens = [run.telemetry.total_tokens for run in runs]
    if any(value is None for value in observed_tokens):
        raise FullPreflightError("calibration contains unknown token usage")
    known_tokens = [int(value) for value in observed_tokens if value is not None]
    journal_by_run = {run_id: 0 for run_id in resume.completed_run_ids}
    for event in recovered_journal.events:
        if event.event_type in {"research_completed", "judge_metric_terminal"}:
            journal_by_run[event.run_id] += int(event.total_tokens or 0)
    journal_tokens = sum(journal_by_run.values())
    artifact_by_run = {run.run_id: int(run.telemetry.total_tokens or 0) for run in runs}
    ledger_by_run = {
        run_id: int(payload["committed_tokens"])
        for run_id, payload in normalized_ledger["runs"].items()
    }
    if (
        sum(known_tokens) != journal_tokens
        or artifact_by_run != journal_by_run
        or ledger_by_run != journal_by_run
        or journal_tokens != normalized_ledger["committed_tokens"]
        or normalized_ledger["actual_input_tokens"]
        + normalized_ledger["actual_output_tokens"]
        != normalized_ledger["committed_tokens"]
        or normalized_ledger["unknown_charged_tokens"] != 0
    ):
        raise FullPreflightError(
            "calibration run, journal, and ledger token totals do not agree"
        )

    full_runs = int(plan["research_runs"]["total"])
    safety_multiplier = float(plan["dispatch_policy"]["projection_safety_multiplier"])
    try:
        projected = project_total_tokens(
            known_tokens,
            # Calibration usage already consumed quota.  Including all 54 new
            # runs here intentionally makes the authorization projection more
            # conservative than the full-run ledger itself.
            remaining_runs=full_runs,
            safety_multiplier=safety_multiplier,
            hard_stop_tokens=min(
                requested_max_tokens, int(plan["token_budget"]["hard_stop_tokens"])
            ),
        )
    except EvaluationBudgetError as exc:
        raise FullPreflightError(str(exc)) from exc
    return FullCalibrationProjection(
        calibration_experiment_id=current_identity.experiment_id,
        calibration_runs=expected_runs,
        full_runs=full_runs,
        observed_tokens=known_tokens,
        safety_multiplier=safety_multiplier,
        projected_tokens=projected,
        requested_max_tokens=requested_max_tokens,
    )


__all__ = [
    "FullCalibrationProjection",
    "FullPreflightError",
    "require_completed_calibration",
]
