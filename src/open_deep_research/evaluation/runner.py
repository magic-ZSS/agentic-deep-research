"""Offline smoke runner and guarded full-evaluation entry contract."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from open_deep_research.evaluation.custom_metrics import (
    SCORER_VERSION,
    cost_completeness_metric,
    source_numbering_metric,
)
from open_deep_research.evaluation.dataset import (
    merge_evaluation_dataset,
    validate_tool_expectations,
)
from open_deep_research.evaluation.experiment_models import (
    EvaluationStatus,
    ExperimentMetricResult,
    ExperimentRun,
    ExperimentTelemetry,
)
from open_deep_research.evaluation.gates import require_full_eval_authorization
from open_deep_research.evaluation.reporting import (
    aggregate_runs,
    render_markdown,
    write_artifact_manifest,
)
from open_deep_research.evaluation.variants import load_variants


def _commit(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def deterministic_experiment_id(
    *, project_commit: str, dataset_version: str, variant_ids: list[str], mode: str, repeats: int
) -> str:
    """Return a rerunnable identity derived only from experiment inputs."""
    source = json.dumps(
        [project_commit, dataset_version, variant_ids, mode, repeats], separators=(",", ":")
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
    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
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
    commit = _commit(root)
    experiment_id = deterministic_experiment_id(
        project_commit=commit,
        dataset_version=dataset_version,
        variant_ids=[item.variant_id for item in variants],
        mode="smoke",
        repeats=1,
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
                cost_completeness_metric(tokens=None, cost=None),
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
                    trace={"synthetic": True, "available_tools": variant.available_tools},
                    retrieval_context=[],
                    telemetry=ExperimentTelemetry(),
                    metric_results=metrics,
                    status=EvaluationStatus.PASSED,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                )
            )
    output.mkdir(parents=True, exist_ok=True)
    (output / "runs.jsonl").write_text(
        "".join(json.dumps(item.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")) + "\n" for item in records),
        encoding="utf-8",
    )
    report = aggregate_runs(records)
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "report.md").write_text(render_markdown(report), encoding="utf-8")
    (output / "experiment.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "experiment_id": experiment_id,
                "mode": "smoke",
                "dataset_version": dataset_version,
                "canonical_cases": "tests/baseline/cases.jsonl",
                "golden_overlay": "tests/evaluation/goldens.v1.jsonl",
                "variants": [item.model_dump(mode="json") for item in variants],
                "scorer_version": SCORER_VERSION,
                "claims": {
                    "live_quality": False,
                    "citation_uplift": False,
                    "web_or_token_reduction": False,
                },
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    write_artifact_manifest(
        output,
        experiment_id=experiment_id,
        dataset_version=dataset_version,
        project_commit=commit,
    )
    return records


def require_full_run(*, confirm_cost: bool, repeats: int) -> None:
    """Fail before graph/DeepEval import unless every full-run gate is open."""
    if repeats < 3:
        raise ValueError("full evaluation requires repeats >= 3")
    if not confirm_cost:
        raise PermissionError("full evaluation requires --confirm-cost")
    require_full_eval_authorization()

