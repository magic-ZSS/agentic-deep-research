"""Offline evidence tests for the Phase 7 live/full artifact validator."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import types
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

# This suite validates persistence contracts, not UUIDv7 generation. Some
# locked-down Windows hosts reject uuid_utils' optional native DLL, so keep the
# same pure-Python, test-local fallback used by the full-runner suite.
try:
    import uuid_utils  # noqa: F401
except ImportError:
    uuid_fallback = types.ModuleType("uuid_utils")
    uuid_compat = types.ModuleType("uuid_utils.compat")
    uuid_compat.uuid7 = uuid.uuid4
    uuid_fallback.uuid7 = uuid.uuid4
    uuid_fallback.compat = uuid_compat
    sys.modules["uuid_utils"] = uuid_fallback
    sys.modules["uuid_utils.compat"] = uuid_compat

from open_deep_research.evaluation.calibration_runner import (
    ResearchObservation,
    _observation_payload,
    _write_hashed_json,
)
from open_deep_research.evaluation.calibration_state import (
    CalibrationJournalStore,
    make_experiment_identity,
    sha256_path,
)
from open_deep_research.evaluation.claim_scorer import (
    CLAIM_SCORER_STEP_NAME,
    CLAIM_SCORER_VERSION,
    ClaimScorerResult,
    ClaimSourceAuthority,
    ClaimValidationStatus,
    ScoredClaim,
    stable_evaluation_claim_id,
)
from open_deep_research.evaluation.custom_metrics import (
    score_citations,
    source_numbering_metric,
    source_quality_metric,
)
from open_deep_research.evaluation.eval_environment import EXPECTED_PACKAGES
from open_deep_research.evaluation.experiment_models import (
    EvaluationStatus,
    ExperimentMetricResult,
    ExperimentRun,
    ExperimentTelemetry,
)
from open_deep_research.evaluation.full_metrics import (
    FULL_JUDGE_STEP_NAMES,
    FULL_METRIC_NAMES,
)
from open_deep_research.evaluation.full_reporting import (
    build_full_report,
    render_full_report_markdown,
)
from open_deep_research.evaluation.full_state import build_full_run_definitions
from open_deep_research.evaluation.live_budget import LiveTokenReservationLedger
from open_deep_research.evaluation.models import RunStatus, RunTelemetry
from open_deep_research.evaluation.reporting import write_artifact_manifest
from open_deep_research.evaluation.source_gate import EVALUATION_SOURCE_PATHS
from open_deep_research.evaluation.trace_adapter import NormalizedTrace
from scripts.validate_phase import _check_phase7_full

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE_GATES = ("T7-3", "T7-4", "T7-6", "T7-9")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _metric(
    name: str,
    score: float,
    *,
    passed: bool = True,
    version: str = "deepeval-4.1.1",
) -> ExperimentMetricResult:
    return ExperimentMetricResult(
        metric_name=name,
        metric_version=version,
        score=score,
        threshold=0.5,
        status=(EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED),
        reason="deterministic validator fixture",
        deterministic=not version.startswith("deepeval-"),
        judge_model="openai:qwen3.7-plus" if version.startswith("deepeval-") else None,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
    )


def _run_tokens_and_search(definition) -> tuple[int, int]:
    if definition.phase == "warm":
        return {
            "baseline": (80, 8),
            "agentic_rag": (40, 2),
            "citation_validator": (30, 1),
        }[definition.variant_id]
    if definition.case_id == "complex-001":
        return {
            "baseline": (120, 10),
            "paperqa": (110, 9),
            "agentic_rag": (100, 6),
            "memory": (95, 5),
            "citation_validator": (90, 4),
        }[definition.variant_id]
    return 100, 5


def _claim_fixture(
    variant_id: str,
) -> tuple[str, list[str], ClaimScorerResult]:
    is_full = variant_id == "citation_validator"
    claim_text = (
        "The fixture claim is directly supported [1]."
        if is_full
        else "The fixture claim is unsupported [1]."
    )
    output = f"{claim_text}\n\n## Sources\n[1] Official fixture"
    context = [
        (
            "[1] Official fixture evidence directly supports the fixture claim."
            if is_full
            else "[1] Official fixture context does not support the fixture claim."
        )
    ]
    claim = ScoredClaim(
        claim_id=stable_evaluation_claim_id(0, claim_text),
        text=claim_text,
        checkable=True,
        citation_ids=(1,),
        validation_status=(
            ClaimValidationStatus.FULLY_SUPPORTED
            if is_full
            else ClaimValidationStatus.UNSUPPORTED
        ),
        evidence_valid=is_full,
        source_authority=(
            ClaimSourceAuthority.OFFICIAL
            if is_full
            else ClaimSourceAuthority.UNKNOWN
        ),
        correctly_qualified=False,
    )
    result = ClaimScorerResult(
        report_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
        candidate_count=1,
        bound_context_count=1,
        unbound_context_count=0,
        claims=(claim,),
    )
    return output, context, result


def _run_metrics(
    output: str,
    claim_result: ClaimScorerResult,
) -> list[ExperimentMetricResult]:
    metrics = [_metric(name, 0.8) for name in FULL_METRIC_NAMES]
    observations = claim_result.to_claim_observations()
    metrics.extend(score_citations(output, observations))
    metrics.append(source_quality_metric(observations))
    metrics.append(source_numbering_metric(output))
    return metrics


def _create_live_full_artifact(root: Path) -> Path:
    source_plan = PROJECT_ROOT / "tests/evaluation/full_plan.v1.json"
    source_ablations = PROJECT_ROOT / "tests/evaluation/ablations.v1.json"
    plan_path = root / "tests/evaluation/full_plan.v1.json"
    ablation_path = root / "tests/evaluation/ablations.v1.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_plan, plan_path)
    shutil.copyfile(source_ablations, ablation_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    definitions = build_full_run_definitions(plan)
    models = {
        "compression": "openai:qwen3.7-plus",
        "final_report": "openai:qwen3.7-plus",
        "judge": "openai:qwen3.7-plus",
        "research": "openai:qwen3.7-plus",
        "summarization": "openai:qwen3.7-plus",
    }
    identity = make_experiment_identity(
        git_head_value="a" * 40,
        dirty_diff_sha256="b" * 64,
        plan_sha256=sha256_path(plan_path),
        ablation_sha256=sha256_path(ablation_path),
        dataset_id="v1",
        model_ids={
            **models,
            "protocol": "phase7-full-v1",
            "provenance": "live",
        },
    )
    output = root / "artifacts/evaluation/full"
    output.mkdir(parents=True)
    store = CalibrationJournalStore.create(
        output / "journal.json",
        identity=identity,
        runs=[item.to_journal_definition() for item in definitions],
        judge_metric_names=FULL_JUDGE_STEP_NAMES,
    )
    journal = store.load()
    journal_by_key = {
        (item.case_id, item.variant_id, item.repeat): item for item in journal.runs
    }
    ledger = LiveTokenReservationLedger(
        hard_token_limit=42_000_000,
        per_run_token_limit=800_000,
    )
    records: list[ExperimentRun] = []
    now = datetime.now(UTC)
    for definition in definitions:
        plan_record = journal_by_key[
            (
                definition.case_id,
                definition.journal_variant_id,
                definition.repeat,
            )
        ]
        total_tokens, search_calls = _run_tokens_and_search(definition)
        reservation = ledger.reserve_before_call(
            run_id=plan_record.run_id,
            category="research",
            input_upper_bound=total_tokens,
            output_upper_bound=0,
            reservation_id=f"fixture:{plan_record.run_id}",
        )
        ledger.settle_success(
            reservation.reservation_id,
            actual_input_tokens=total_tokens,
            actual_output_tokens=0,
        )
        output_text, retrieval_context, claim_result = _claim_fixture(
            definition.variant_id
        )
        normalized_trace = NormalizedTrace(
            plan=["research", "report"],
            retrieval_context=retrieval_context,
            trace_dict={
                "name": "fixture",
                "type": "agent",
                "plan": ["research", "report"],
                "children": [],
            },
        )
        observation = ResearchObservation(
            output=output_text,
            telemetry=RunTelemetry(
                started_at=now,
                finished_at=now,
                wall_time_ms=1,
                input_tokens=total_tokens,
                output_tokens=0,
                total_tokens=total_tokens,
                model_calls=1,
                model_calls_with_usage=1,
                search_calls=search_calls,
                search_calls_complete=True,
                researcher_runs=1,
                status=RunStatus.COMPLETED,
            ),
            trace=normalized_trace,
            state_artifacts={},
            researcher_runs=1,
            search_calls=search_calls,
        )
        metrics = _run_metrics(output_text, claim_result)

        store.start_research(plan_record.run_id)
        _write_hashed_json(
            output / "steps" / plan_record.run_id / "research.json",
            _observation_payload(plan_record.run_id, observation),
        )
        store.complete_research(
            plan_record.run_id,
            input_tokens=total_tokens,
            output_tokens=0,
            total_tokens=total_tokens,
        )
        metrics_by_name = {item.metric_name: item for item in metrics}
        for metric_name in FULL_METRIC_NAMES:
            result = metrics_by_name[metric_name]
            store.start_judge_metric(plan_record.run_id, metric_name)
            _write_hashed_json(
                output
                / "steps"
                / plan_record.run_id
                / "metrics"
                / f"{metric_name}.json",
                {
                    "schema_version": "1.0",
                    "run_id": plan_record.run_id,
                    "metric_name": metric_name,
                    "duration_ms": 1,
                    "result": result.model_dump(mode="json"),
                },
            )
            store.complete_judge_metric(
                plan_record.run_id,
                metric_name,
                status=result.status.value,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
            )

        store.start_judge_metric(plan_record.run_id, CLAIM_SCORER_STEP_NAME)
        _write_hashed_json(
            output
            / "steps"
            / plan_record.run_id
            / "metrics"
            / f"{CLAIM_SCORER_STEP_NAME}.json",
            {
                "schema_version": "1.0",
                "run_id": plan_record.run_id,
                "metric_name": CLAIM_SCORER_STEP_NAME,
                "status": "passed",
                "duration_ms": 1,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "result": claim_result.model_dump(mode="json"),
            },
        )
        store.complete_judge_metric(
            plan_record.run_id,
            CLAIM_SCORER_STEP_NAME,
            status="passed",
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
        )

        claim_payload = claim_result.observations_payload
        record = ExperimentRun(
            experiment_id=identity.experiment_id,
            run_id=plan_record.run_id,
            variant_id=definition.variant_id,
            case_id=definition.case_id,
            difficulty=definition.case_id.split("-", 1)[0],
            repeat=definition.repeat,
            mode="full",
            project_commit=identity.git_head,
            dataset_version=identity.dataset_id,
            scorer_version=CLAIM_SCORER_VERSION,
            output=output_text,
            output_sha256=claim_result.report_sha256,
            trace={
                "evaluation_provenance": "live",
                "expected_output_present": True,
                "normalized": normalized_trace.model_dump(mode="json"),
                "evaluation_claim_results": claim_payload,
                "claim_observations": claim_payload,
                "claim_scorer_report_sha256": claim_result.report_sha256,
                "protocol": {
                    "kind": definition.kind,
                    "phase": definition.phase,
                    "pair_id": definition.pair_id,
                    "paired_key": definition.paired_key,
                    "snapshot_sha256": "d" * 64,
                    "runtime_state_sha256": "e" * 64,
                },
            },
            retrieval_context=retrieval_context,
            telemetry=ExperimentTelemetry(
                input_tokens=total_tokens,
                output_tokens=0,
                total_tokens=total_tokens,
                research_input_tokens=total_tokens,
                research_output_tokens=0,
                research_total_tokens=total_tokens,
                judge_input_tokens=0,
                judge_output_tokens=0,
                judge_total_tokens=0,
                retry_tokens=0,
                estimated_cost_usd=None,
                wall_time_ms=1,
                research_model_calls=1,
                judge_model_calls=len(FULL_JUDGE_STEP_NAMES),
                search_calls=search_calls,
                researcher_runs=1,
            ),
            metric_results=metrics,
            status=(
                EvaluationStatus.FAILED
                if any(item.status is EvaluationStatus.FAILED for item in metrics)
                else EvaluationStatus.PASSED
            ),
            started_at=now,
            finished_at=now,
        )
        _write_hashed_json(
            output / "run-records" / f"{record.run_id}.json",
            record.model_dump(mode="json"),
        )
        store.complete_run(plan_record.run_id, status="completed")
        records.append(record)

    (output / "runs.jsonl").write_text(
        "".join(record.model_dump_json() + "\n" for record in records),
        encoding="utf-8",
    )
    ledger_payload = ledger.snapshot()
    _write_json(
        output / "budget.json",
        {
            "schema_version": "1.0",
            "calibration_identity": identity.model_dump(mode="json"),
            "ledger": ledger_payload,
        },
    )
    projection = {
        "calibration_experiment_id": "cal-" + "e" * 32,
        "calibration_runs": 6,
        "full_runs": 54,
        "observed_tokens": [100, 110, 120, 130, 140, 150],
        "safety_multiplier": 1.25,
        "projected_tokens": 10_000,
        "requested_max_tokens": 42_000_000,
        "evaluation_environment": {
            "python": "3.11",
            "packages": dict(EXPECTED_PACKAGES),
            "pip_check": "passed",
            "import_smoke": [
                "deepeval",
                "open_deep_research.evaluation.full_runner",
            ],
        },
        "source_attestation": {
            "git_head": identity.git_head,
            "clean": True,
            "checked_paths": list(EVALUATION_SOURCE_PATHS),
        },
    }
    experiment = {
        "schema_version": "1.0",
        "experiment_id": identity.experiment_id,
        "mode": "full",
        "status": "completed",
        "dataset_version": "v1",
        "case_ids": list(plan["case_ids"]),
        "variants": list(plan["variants"]),
        "repeats": 3,
        "planned_runs": 54,
        "paired_main_runs": 45,
        "additional_warm_runs": 9,
        "completed_run_records": 54,
        "soft_token_limit": 36_000_000,
        "hard_token_limit": 42_000_000,
        "per_run_token_limit": 800_000,
        "model_ids": models,
        "provenance": "live",
        "plan_sha256": identity.plan_sha256,
        "ablation_sha256": identity.ablation_sha256,
        "git_head": identity.git_head,
        "dirty_diff_sha256": identity.dirty_diff_sha256,
        "calibration_projection": projection,
        "stopped_reason": None,
    }
    _write_json(output / "experiment.json", experiment)
    report = build_full_report(records)
    assert set(report["acceptance"].values()) == {"passed"}
    report.update(
        {
            "experiment_id": identity.experiment_id,
            "mode": "full",
            "status": "completed",
            "full_status": "completed",
            "planned_runs": 54,
            "completed_run_records": 54,
            "main_run_records": 45,
            "warm_run_records": 9,
            "token_budget": ledger_payload,
            "stopped_reason": None,
            "tracking_error_count": 0,
            "calibration_projection": projection,
            "claims": {"full_matrix_complete": True},
        }
    )
    _write_json(output / "report.json", report)
    (output / "report.md").write_text(
        render_full_report_markdown(report),
        encoding="utf-8",
    )
    write_artifact_manifest(
        output,
        experiment_id=identity.experiment_id,
        dataset_version=identity.dataset_id,
        project_commit=identity.git_head,
    )
    return output


def _rewrite_manifest(output: Path) -> None:
    experiment = json.loads((output / "experiment.json").read_text(encoding="utf-8"))
    write_artifact_manifest(
        output,
        experiment_id=experiment["experiment_id"],
        dataset_version=experiment["dataset_version"],
        project_commit=experiment["git_head"],
    )


def test_live_full_validator_reconstructs_all_four_acceptance_gates(tmp_path: Path):
    _create_live_full_artifact(tmp_path)

    for acceptance_id in LIVE_GATES:
        detail = _check_phase7_full(tmp_path, acceptance_id)
        assert f"{acceptance_id}=passed" in detail


def test_fake_provenance_can_never_satisfy_live_full_gates(tmp_path: Path):
    output = _create_live_full_artifact(tmp_path)
    records = [
        ExperimentRun.model_validate_json(line)
        for line in (output / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    records[0].trace["evaluation_provenance"] = "fake"
    (output / "runs.jsonl").write_text(
        "".join(record.model_dump_json() + "\n" for record in records),
        encoding="utf-8",
    )
    _rewrite_manifest(output)

    for acceptance_id in LIVE_GATES:
        with pytest.raises(ValueError, match="fake/offline/calibration"):
            _check_phase7_full(tmp_path, acceptance_id)


def test_full_validator_rejects_unlisted_files(tmp_path: Path):
    output = _create_live_full_artifact(tmp_path)
    (output / "unlisted.txt").write_text("not evidence\n", encoding="utf-8")

    with pytest.raises(ValueError, match="manifest integrity"):
        _check_phase7_full(tmp_path, "T7-3")


def test_full_validator_rejects_environment_source_and_claim_drift(tmp_path: Path):
    output = _create_live_full_artifact(tmp_path)
    experiment = json.loads((output / "experiment.json").read_text(encoding="utf-8"))
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    experiment["calibration_projection"]["evaluation_environment"]["pip_check"] = (
        "failed"
    )
    _write_json(output / "experiment.json", experiment)
    report["calibration_projection"] = experiment["calibration_projection"]
    _write_json(output / "report.json", report)
    _rewrite_manifest(output)

    with pytest.raises(ValueError, match="environment evidence"):
        _check_phase7_full(tmp_path, "T7-3")

    projection = experiment["calibration_projection"]
    projection["evaluation_environment"]["pip_check"] = "passed"
    projection["source_attestation"]["clean"] = False
    report["calibration_projection"] = projection
    _write_json(output / "experiment.json", experiment)
    _write_json(output / "report.json", report)
    _rewrite_manifest(output)

    with pytest.raises(ValueError, match="clean-source attestation"):
        _check_phase7_full(tmp_path, "T7-3")

    projection["source_attestation"]["clean"] = True
    report["calibration_projection"] = projection
    _write_json(output / "experiment.json", experiment)
    _write_json(output / "report.json", report)
    first_run = next(
        line
        for line in (output / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    )
    run_id = json.loads(first_run)["run_id"]
    claim_path = (
        output
        / "steps"
        / run_id
        / "metrics"
        / f"{CLAIM_SCORER_STEP_NAME}.json"
    )
    claim_payload = json.loads(claim_path.read_text(encoding="utf-8"))
    claim_payload["result"]["report_sha256"] = "f" * 64
    _write_hashed_json(claim_path, claim_payload)
    _rewrite_manifest(output)

    with pytest.raises(ValueError, match="immutable report"):
        _check_phase7_full(tmp_path, "T7-3")
