"""Offline smoke runner and guarded full-evaluation entry contract."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from open_deep_research.evaluation.budget import (
    load_full_plan,
    resolve_models,
    validate_requested_budget,
)
from open_deep_research.evaluation.custom_metrics import (
    SCORER_VERSION,
    cost_completeness_metric,
    source_numbering_metric,
)
from open_deep_research.evaluation.eval_environment import (
    require_evaluation_environment,
)
from open_deep_research.evaluation.experiment_models import (
    EvaluationStatus,
    ExperimentMetricResult,
    ExperimentRun,
    ExperimentTelemetry,
)
from open_deep_research.evaluation.full_preflight import (
    require_completed_calibration,
)
from open_deep_research.evaluation.gates import (
    EvaluationAuthorizationError,
    require_full_eval_authorization,
)
from open_deep_research.evaluation.reporting import (
    aggregate_runs,
    render_markdown,
    write_artifact_manifest,
)
from open_deep_research.evaluation.source_gate import (
    EvaluationSourceGateError,
    capture_evaluation_source_snapshot,
    require_clean_evaluation_source,
)
from open_deep_research.evaluation.tracking import TrackingMode, build_tracking_sink


def deterministic_experiment_id(
    *,
    source_snapshot_sha256: str,
    dataset_version: str,
    variant_ids: list[str],
    mode: str,
    repeats: int,
    scorer_version: str,
) -> str:
    """Return a rerunnable identity bound to inputs and working source."""
    source = json.dumps(
        [
            source_snapshot_sha256,
            dataset_version,
            variant_ids,
            mode,
            repeats,
            scorer_version,
        ],
        separators=(",", ":"),
    )
    return hashlib.sha256(source.encode()).hexdigest()[:20]


def run_smoke(
    *,
    project_root: str | Path,
    output_dir: str | Path,
    dataset_version: str = "v1",
    variant_ids: list[str] | None = None,
) -> list[ExperimentRun]:
    """Run deterministic schema/governance smoke without graph or external calls."""
    from open_deep_research.evaluation.dataset import (
        merge_evaluation_dataset,
        validate_tool_expectations,
    )
    from open_deep_research.evaluation.variants import load_variants

    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    source_snapshot = capture_evaluation_source_snapshot(root)
    cases = merge_evaluation_dataset(
        root / "tests/baseline/cases.jsonl",
        root / "tests/evaluation/goldens.v1.jsonl",
        dataset_version=dataset_version,
    )
    variants = load_variants(root / "tests/evaluation/ablations.v1.json")
    if variant_ids:
        requested = set(variant_ids)
        variants = [item for item in variants if item.variant_id in requested]
        if {item.variant_id for item in variants} != requested:
            raise ValueError("unknown requested variant")
    validate_tool_expectations(
        cases, {item.variant_id: set(item.available_tools) for item in variants}
    )
    commit = source_snapshot.git_head
    experiment_id = deterministic_experiment_id(
        source_snapshot_sha256=source_snapshot.source_sha256,
        dataset_version=dataset_version,
        variant_ids=[item.variant_id for item in variants],
        mode="smoke",
        repeats=1,
        scorer_version=SCORER_VERSION,
    )
    records: list[ExperimentRun] = []
    for variant in variants:
        for case in cases:
            started = datetime.now(UTC)
            text = f"Offline smoke contract for {case.case_id}. No live research result is claimed."
            metrics = [
                ExperimentMetricResult(
                    metric_name="offline_contract",
                    metric_version="1.0",
                    score=1.0,
                    threshold=1.0,
                    status=EvaluationStatus.PASSED,
                    reason="canonical merge, variant policy, and artifact schema passed",
                    deterministic=True,
                ),
                source_numbering_metric(text),
                cost_completeness_metric(
                    tokens=None,
                    cost=None,
                    pricing_available=False,
                ),
            ]
            records.append(
                ExperimentRun(
                    experiment_id=experiment_id,
                    run_id=f"{experiment_id}-{variant.variant_id}-{case.case_id}-1",
                    variant_id=variant.variant_id,
                    case_id=case.case_id,
                    difficulty=case.difficulty,
                    repeat=1,
                    mode="smoke",
                    project_commit=commit,
                    dataset_version=dataset_version,
                    scorer_version=SCORER_VERSION,
                    output=text,
                    output_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    trace={
                        "synthetic": True,
                        "available_tools": variant.available_tools,
                        "source_snapshot_sha256": source_snapshot.source_sha256,
                        "source_snapshot_clean": source_snapshot.clean,
                    },
                    retrieval_context=[],
                    telemetry=ExperimentTelemetry(),
                    metric_results=metrics,
                    status=EvaluationStatus.PASSED,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                )
            )
    if capture_evaluation_source_snapshot(root) != source_snapshot:
        raise EvaluationSourceGateError(
            "evaluation source changed during smoke execution"
        )
    output.mkdir(parents=True, exist_ok=True)
    (output / "runs.jsonl").write_bytes(
        "".join(
            json.dumps(
                item.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
            for item in records
        ).encode("utf-8")
    )
    report = aggregate_runs(records)
    (output / "report.json").write_bytes(
        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    (output / "report.md").write_bytes(render_markdown(report).encode("utf-8"))
    (output / "experiment.json").write_bytes(
        (
            json.dumps(
                {
                    "schema_version": "1.0",
                    "experiment_id": experiment_id,
                    "mode": "smoke",
                    "dataset_version": dataset_version,
                    "canonical_cases": "tests/baseline/cases.jsonl",
                    "golden_overlay": "tests/evaluation/goldens.v1.jsonl",
                    "variants": [
                        item.model_dump(mode="json") for item in variants
                    ],
                    "scorer_version": SCORER_VERSION,
                    "source_snapshot": source_snapshot.as_dict(),
                    "claims": {
                        "live_quality": False,
                        "citation_uplift": False,
                        "web_or_token_reduction": False,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
    )
    write_artifact_manifest(
        output,
        experiment_id=experiment_id,
        dataset_version=dataset_version,
        project_commit=commit,
    )
    return records


def require_full_run(
    *,
    confirm_cost: bool,
    repeats: int,
    run_kind: str = "full",
    requested_max_tokens: int | None = None,
    plan_path: str | Path,
) -> None:
    """Fail before graph/DeepEval import unless every full-run gate is open."""
    plan = load_full_plan(plan_path)
    validate_requested_budget(
        plan,
        run_kind=run_kind,
        repeats=repeats,
        requested_max_tokens=requested_max_tokens,
    )
    if not confirm_cost:
        raise EvaluationAuthorizationError(
            "paid evaluation requires --confirm-cost"
        )
    require_full_eval_authorization()


async def run_authorized_calibration(
    *,
    project_root: str | Path,
    output_dir: str | Path,
    dataset_version: str,
    variant_ids: list[str] | None,
    repeats: int,
    requested_max_tokens: int | None,
    confirm_cost: bool,
    resume: bool = False,
):
    """Open all cost gates, then delegate only the fixed calibration matrix."""
    root = Path(project_root).resolve()
    plan_path = root / "tests/evaluation/full_plan.v1.json"
    require_full_run(
        confirm_cost=confirm_cost,
        repeats=repeats,
        run_kind="calibration",
        requested_max_tokens=requested_max_tokens,
        plan_path=plan_path,
    )
    assert requested_max_tokens is not None
    source_attestation = require_clean_evaluation_source(root)
    environment_report = require_evaluation_environment(
        import_modules=(
            "deepeval",
            "open_deep_research.evaluation.calibration_runner",
        )
    )
    from open_deep_research.evaluation.calibration_runner import run_calibration

    return await run_calibration(
        project_root=root,
        output_dir=output_dir,
        dataset_version=dataset_version,
        variant_ids=variant_ids,
        requested_max_tokens=requested_max_tokens,
        resume=resume,
        evaluation_environment=environment_report.as_dict(),
        source_attestation=source_attestation.as_dict(),
    )


async def _execute_full_matrix(**kwargs):
    """Import the costly execution stack only after every public gate passes."""
    from open_deep_research.evaluation.full_runner import run_full_matrix

    return await run_full_matrix(**kwargs)


def inspect_full_preflight(
    *,
    project_root: str | Path,
    output_dir: str | Path,
    calibration_dir: str | Path,
    dataset_version: str,
    variant_ids: list[str] | None,
    repeats: int,
    requested_max_tokens: int | None,
) -> dict[str, object]:
    """Validate and summarize full readiness without any external call.

    This read-only path intentionally does not accept cost authorization and
    cannot construct a tracking sink or invoke the full executor. It exists so
    calibration evidence and the conservative projection can be reviewed
    before the user grants a separate full-matrix authorization.
    """
    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    if variant_ids is not None:
        raise EvaluationAuthorizationError(
            "full evaluation always runs the exact five variants"
        )
    plan = load_full_plan(root / "tests/evaluation/full_plan.v1.json")
    validate_requested_budget(
        plan,
        run_kind="full",
        repeats=repeats,
        requested_max_tokens=requested_max_tokens,
    )
    assert requested_max_tokens is not None
    source_attestation = require_clean_evaluation_source(root)
    environment_report = require_evaluation_environment(
        import_modules=(
            "deepeval",
            "open_deep_research.evaluation.full_runner",
        )
    )
    load_dotenv(root / ".env", override=False)
    models = resolve_models(plan, os.environ)
    projection = require_completed_calibration(
        project_root=root,
        calibration_dir=calibration_dir,
        full_output_dir=output,
        dataset_version=dataset_version,
        requested_max_tokens=requested_max_tokens,
        environment=os.environ,
    )
    return {
        "status": "ready_for_separate_full_authorization",
        "mode": "full_preflight",
        "matrix": {
            "paired_main_runs": int(plan["research_runs"]["paired_main"]),
            "additional_warm_runs": int(
                plan["research_runs"]["additional_warm_runs"]
            ),
            "total_runs": int(plan["research_runs"]["total"]),
            "variants": list(plan["variants"]),
            "case_ids": list(plan["case_ids"]),
            "repeats": int(plan["repeats"]),
        },
        "models": models,
        "token_budget": dict(plan["token_budget"]),
        "estimated_calls": {
            "research_model": dict(plan["estimated_research_model_calls"]),
            "judge_model": dict(plan["estimated_judge_model_calls"]),
            "tavily_basic_credits": dict(
                plan["estimated_tavily_basic_credits"]
            ),
        },
        "estimated_cost_usd": None,
        "projection": projection.as_dict(),
        "evaluation_environment": environment_report.as_dict(),
        "source_attestation": source_attestation.as_dict(),
    }


async def run_authorized_full(
    *,
    project_root: str | Path,
    output_dir: str | Path,
    calibration_dir: str | Path,
    dataset_version: str,
    variant_ids: list[str] | None,
    repeats: int,
    requested_max_tokens: int | None,
    confirm_cost: bool,
    resume: bool = False,
    tracking: TrackingMode = "local",
    langsmith_project: str | None = None,
):
    """Open every paid-run gate before delegating the fixed 54-run matrix."""
    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    if variant_ids is not None:
        raise EvaluationAuthorizationError(
            "full evaluation always runs the exact five variants"
        )
    plan_path = root / "tests/evaluation/full_plan.v1.json"
    require_full_run(
        confirm_cost=confirm_cost,
        repeats=repeats,
        run_kind="full",
        requested_max_tokens=requested_max_tokens,
        plan_path=plan_path,
    )
    assert requested_max_tokens is not None
    preflight = inspect_full_preflight(
        project_root=root,
        output_dir=output,
        calibration_dir=calibration_dir,
        dataset_version=dataset_version,
        variant_ids=variant_ids,
        repeats=repeats,
        requested_max_tokens=requested_max_tokens,
    )
    projection = preflight["projection"]
    environment_report = preflight["evaluation_environment"]
    source_attestation = preflight["source_attestation"]
    if (
        not isinstance(projection, dict)
        or not isinstance(environment_report, dict)
        or not isinstance(source_attestation, dict)
    ):
        raise RuntimeError("full preflight returned an invalid internal payload")

    # LangSmith is a best-effort mirror: its sink imports the SDK lazily and
    # records an initialization error without blocking or replaying paid work.
    tracking_sink = build_tracking_sink(
        tracking,
        project_name=langsmith_project,
    )
    return await _execute_full_matrix(
        project_root=root,
        output_dir=output,
        dataset_version=dataset_version,
        requested_max_tokens=requested_max_tokens,
        resume=resume,
        tracking_sink=tracking_sink,
        provenance="live",
        require_deepeval=True,
        environment=os.environ,
        calibration_projection={
            **projection,
            "evaluation_environment": environment_report,
            "source_attestation": source_attestation,
        },
    )
