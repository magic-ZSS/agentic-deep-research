import json
from datetime import UTC, datetime, timedelta

import pytest

from open_deep_research.evaluation.custom_metrics import SCORER_VERSION
from open_deep_research.evaluation.experiment_models import (
    EvaluationStatus,
    ExperimentMetricResult,
    ExperimentRun,
    ExperimentTelemetry,
)
from open_deep_research.evaluation.full_metrics import FULL_METRIC_NAMES
from open_deep_research.evaluation.full_reporting import (
    build_full_report,
    render_full_report_markdown,
)
from open_deep_research.evaluation.reporting import (
    render_markdown,
    render_readme_section,
)

VARIANTS = (
    "baseline",
    "paperqa",
    "agentic_rag",
    "memory",
    "citation_validator",
)
DIFFICULTIES = ("simple", "medium", "complex")
SNAPSHOT = "a" * 64
NOW = datetime(2026, 7, 22, tzinfo=UTC)


def _metric(
    name: str,
    score: float | None,
    *,
    status: EvaluationStatus | None = None,
    lower_is_better: bool = False,
) -> ExperimentMetricResult:
    threshold = 0.0 if lower_is_better else 0.5
    if status is None:
        assert score is not None
        passed = score <= threshold if lower_is_better else score >= threshold
        status = EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED
    return ExperimentMetricResult(
        metric_name=name,
        metric_version="1.0" if name not in FULL_METRIC_NAMES else "deepeval-4.1.1",
        score=score,
        threshold=threshold,
        status=status,
        reason=f"fixture:{name}",
        deterministic=name not in FULL_METRIC_NAMES,
        judge_model=None if name not in FULL_METRIC_NAMES else "openai:qwen3.7-plus",
    )


def _metrics(variant: str, *, task_score: float | None = None) -> list[ExperimentMetricResult]:
    task = task_score if task_score is not None else (0.8 if variant == "citation_validator" else 0.7)
    results = [
        _metric(name, task if name == "task_completion" else 0.8)
        for name in FULL_METRIC_NAMES
    ]
    if variant == "baseline":
        citation = (0.4, 0.5, 0.3)
    elif variant == "citation_validator":
        citation = (0.9, 0.9, 0.1)
    else:
        citation = (0.6, 0.65, 0.2)
    results.extend(
        (
            _metric("citation_fidelity", citation[0]),
            _metric("citation_completeness", citation[1]),
            _metric("unsupported_claim_rate", citation[2], lower_is_better=True),
            _metric("source_numbering_error_rate", 0.0, lower_is_better=True),
        )
    )
    return results


def _run(
    *,
    variant: str,
    difficulty: str,
    repeat: int,
    phase: str = "cold",
    kind: str = "main",
    provenance: str = "live",
    total_tokens: int = 1_000,
    web_calls: int = 10,
    metrics: list[ExperimentMetricResult] | None = None,
    run_status: EvaluationStatus = EvaluationStatus.PASSED,
    mode: str = "full",
) -> ExperimentRun:
    case_id = f"{difficulty}-001"
    pair_id = f"pair-{case_id}-{variant}-{repeat}"
    snapshot = SNAPSHOT if difficulty == "complex" and variant in {
        "baseline",
        "agentic_rag",
        "citation_validator",
    } else None
    return ExperimentRun(
        experiment_id="full-test",
        run_id=f"run-{kind}-{phase}-{case_id}-{variant}-{repeat}",
        variant_id=variant,
        case_id=case_id,
        difficulty=difficulty,
        repeat=repeat,
        mode=mode,
        project_commit="0" * 40,
        dataset_version="v1",
        scorer_version=SCORER_VERSION,
        output="Supported fact [1].\n\n## Sources\n[1] Official source",
        output_sha256="1" * 64,
        trace={
            "evaluation_provenance": provenance,
            "protocol": {
                "kind": kind,
                "phase": phase,
                "snapshot_sha256": snapshot,
                "runtime_state_sha256": snapshot,
                "pair_id": pair_id,
                "paired_key": f"paired-{case_id}-{repeat}",
            },
            "expected_output_present": True,
            "normalized": {"plan": ["inspect", "answer"]},
            "evaluation_claim_results": [{"claim_id": "claim-1", "status": "supported"}],
        },
        telemetry=ExperimentTelemetry(
            input_tokens=total_tokens - 100,
            output_tokens=100,
            total_tokens=total_tokens,
            search_calls=web_calls,
        ),
        metric_results=metrics if metrics is not None else _metrics(variant),
        status=run_status,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
    )


def _full_matrix(*, provenance: str = "live") -> list[ExperimentRun]:
    runs: list[ExperimentRun] = []
    for difficulty in DIFFICULTIES:
        for variant in VARIANTS:
            for repeat in range(1, 4):
                tokens = 1_000
                web = 10
                if difficulty == "complex" and variant == "agentic_rag":
                    tokens, web = 800, 8
                elif difficulty == "complex" and variant == "citation_validator":
                    tokens, web = 750, 7
                runs.append(
                    _run(
                        variant=variant,
                        difficulty=difficulty,
                        repeat=repeat,
                        provenance=provenance,
                        total_tokens=tokens,
                        web_calls=web,
                    )
                )
    warm_measurements = {
        "baseline": (900, 9),
        "agentic_rag": (600, 4),
        "citation_validator": (500, 3),
    }
    for variant, (tokens, web) in warm_measurements.items():
        for repeat in range(1, 4):
            runs.append(
                _run(
                    variant=variant,
                    difficulty="complex",
                    repeat=repeat,
                    phase="warm",
                    kind="cold_warm",
                    provenance=provenance,
                    total_tokens=tokens,
                    web_calls=web,
                )
            )
    return runs


def _replace_metric(
    run: ExperimentRun, name: str, replacement: ExperimentMetricResult
) -> ExperimentRun:
    return run.model_copy(
        update={
            "metric_results": [
                replacement if metric.metric_name == name else metric
                for metric in run.metric_results
            ]
        }
    )


def test_full_report_passes_four_acceptances_with_complete_live_evidence():
    report = build_full_report(_full_matrix())

    assert report["run_count"] == 54
    assert report["mode_counts"] == {"full": 54}
    assert report["acceptance"] == {
        "T7-3": "passed",
        "T7-4": "passed",
        "T7-6": "passed",
        "T7-9": "passed",
    }
    assert report["acceptance_details"]["T7-3"]["evidence"]["paired_runs"] == 9
    assert report["acceptance_details"]["T7-4"]["evidence"]["paired_runs"] == 3
    cold_warm = report["cold_warm"]["variants"]["citation_validator"]
    assert cold_warm["total_tokens"]["absolute_reduction"] == pytest.approx(250)
    assert cold_warm["total_tokens"]["percent_reduction"] == pytest.approx(100 / 3)
    assert cold_warm["warm_vs_baseline"]["web_calls"]["absolute_reduction"] == 6
    assert json.loads(json.dumps(report)) == report


def test_full_report_drives_markdown_and_readme_tables():
    report = {
        **build_full_report(_full_matrix()),
        "status": "completed",
        "planned_runs": 54,
        "completed_run_records": 54,
        "token_budget": {"committed_tokens": 123_456},
    }

    assert render_markdown(report) == render_full_report_markdown(report)
    readme = render_readme_section(report)
    assert "Latest source-bound local artifact mode: `full`" in readme
    assert "| citation_validator | 12 | 12 | 0 | 0 | 0 |" in readme
    assert "| T7-3 | passed |" in readme
    assert "artifacts/evaluation/full/manifest.json" in readme


def test_flat_protocol_fields_remain_backward_compatible():
    runs: list[ExperimentRun] = []
    for run in _full_matrix():
        trace = dict(run.trace)
        protocol = trace.pop("protocol")
        trace.update(
            {
                "protocol_kind": protocol["kind"],
                "protocol_phase": protocol["phase"],
                "snapshot_hash": protocol["snapshot_sha256"],
                "runtime_state_hash": protocol["runtime_state_sha256"],
                "pair_id": protocol["pair_id"],
                "paired_key": protocol["paired_key"],
            }
        )
        runs.append(run.model_copy(update={"trace": trace}))

    assert set(build_full_report(runs)["acceptance"].values()) == {"passed"}


def test_each_cold_warm_pair_may_use_a_distinct_post_cold_snapshot():
    runs = _full_matrix()
    pair_ids = sorted(
        {
            run.trace["protocol"]["pair_id"]
            for run in runs
            if run.difficulty == "complex"
            and run.variant_id in {"baseline", "agentic_rag", "citation_validator"}
        }
    )
    hashes = {pair_id: f"{index:064x}" for index, pair_id in enumerate(pair_ids, 1)}
    for index, run in enumerate(runs):
        protocol = run.trace["protocol"]
        pair_id = protocol["pair_id"]
        if pair_id not in hashes:
            continue
        trace = dict(run.trace)
        trace["protocol"] = {
            **protocol,
            "snapshot_sha256": hashes[pair_id],
            "runtime_state_sha256": hashes[pair_id],
        }
        runs[index] = run.model_copy(update={"trace": trace})

    report = build_full_report(runs)
    assert report["acceptance"]["T7-6"] == "passed"
    assert len(report["cold_warm"]["snapshot_hashes"]) == 9
    assert all(
        len(report["cold_warm"]["variants"][variant]["snapshot_pairs"]) == 3
        for variant in ("baseline", "agentic_rag", "citation_validator")
    )


def test_one_cold_warm_snapshot_mismatch_invalidates_the_protocol():
    runs = _full_matrix()
    index = next(
        index
        for index, run in enumerate(runs)
        if run.variant_id == "agentic_rag"
        and run.trace["protocol"]["phase"] == "warm"
        and run.repeat == 1
    )
    trace = dict(runs[index].trace)
    trace["protocol"] = {
        **trace["protocol"],
        "snapshot_sha256": "b" * 64,
    }
    runs[index] = runs[index].model_copy(update={"trace": trace})

    report = build_full_report(runs)
    assert report["cold_warm"]["evaluable"] is False
    assert report["acceptance"]["T7-6"] == "not_evaluable"
    assert "fixed initial snapshot hash" in " ".join(
        report["acceptance_details"]["T7-6"]["reasons"]
    )


def test_metric_statistics_and_status_counts_preserve_failures_and_skips():
    failed = _run(
        variant="baseline",
        difficulty="simple",
        repeat=1,
        mode="smoke",
        provenance="fake",
        run_status=EvaluationStatus.FAILED,
    )
    skipped_task = _metric(
        "task_completion", None, status=EvaluationStatus.SKIPPED
    )
    skipped = _replace_metric(
        _run(
            variant="baseline",
            difficulty="simple",
            repeat=2,
            mode="smoke",
            provenance="fake",
            run_status=EvaluationStatus.SKIPPED,
        ),
        "task_completion",
        skipped_task,
    )

    report = build_full_report([failed, skipped])
    run_row = next(
        row
        for row in report["run_status"]
        if row["variant_id"] == "baseline" and row["difficulty"] == "simple"
    )
    metric_row = next(
        row
        for row in report["metric_aggregates"]
        if row["variant_id"] == "baseline"
        and row["difficulty"] == "simple"
        and row["metric_name"] == "task_completion"
    )
    assert run_row["failed"] == 1
    assert run_row["skipped"] == 1
    assert metric_row["count"] == 1
    assert metric_row["skipped"] == 1
    assert metric_row["mean"] == pytest.approx(0.7)
    assert set(report["acceptance"].values()) == {"not_evaluable"}


@pytest.mark.parametrize("mode,provenance", [("smoke", "live"), ("calibration", "live"), ("full", "fake")])
def test_non_live_full_evidence_cannot_satisfy_acceptance(mode, provenance):
    runs = [
        run.model_copy(update={"mode": mode, "trace": {**run.trace, "evaluation_provenance": provenance}})
        for run in _full_matrix()
    ]
    report = build_full_report(runs)

    assert set(report["acceptance"].values()) == {"not_evaluable"}
    assert "non-live/full" in " ".join(
        report["acceptance_details"]["T7-3"]["reasons"]
    )


def test_skipped_required_metric_is_not_counted_as_pass_or_complete():
    runs = _full_matrix()
    index = next(
        index
        for index, run in enumerate(runs)
        if run.variant_id == "citation_validator"
        and run.case_id == "simple-001"
        and run.repeat == 1
        and run.trace["protocol"]["kind"] == "main"
    )
    runs[index] = _replace_metric(
        runs[index],
        "task_completion",
        _metric("task_completion", None, status=EvaluationStatus.SKIPPED),
    )

    report = build_full_report(runs)
    assert report["acceptance"]["T7-3"] == "not_evaluable"
    assert report["acceptance"]["T7-9"] == "not_evaluable"
    task_row = next(
        row
        for row in report["metric_aggregates"]
        if row["variant_id"] == "citation_validator"
        and row["difficulty"] == "simple"
        and row["metric_name"] == "task_completion"
    )
    assert task_row["count"] == 2
    assert task_row["skipped"] == 1


def test_complete_live_but_worse_results_are_failed_not_not_evaluable():
    runs = _full_matrix()
    for index, run in enumerate(runs):
        if run.variant_id == "citation_validator":
            for name, score, lower in (
                ("task_completion", 0.6, False),
                ("citation_fidelity", 0.3, False),
                ("citation_completeness", 0.4, False),
                ("unsupported_claim_rate", 0.4, True),
            ):
                run = _replace_metric(run, name, _metric(name, score, lower_is_better=lower))
            if run.trace["protocol"]["phase"] == "warm":
                run = run.model_copy(
                    update={
                        "telemetry": run.telemetry.model_copy(
                            update={"total_tokens": 900, "search_calls": 9}
                        )
                    }
                )
            runs[index] = run

    report = build_full_report(runs)
    assert report["acceptance"]["T7-3"] == "failed"
    assert report["acceptance"]["T7-4"] == "failed"
    assert report["acceptance"]["T7-6"] == "failed"
    assert report["acceptance"]["T7-9"] == "passed"


def test_scorer_is_global_and_full_variant_numbering_is_a_hard_rule():
    runs = _full_matrix()
    runs[0] = runs[0].model_copy(update={"scorer_version": "different-scorer"})
    numbering = _metric("source_numbering_error_rate", 0.1, lower_is_better=True)
    full_index = next(
        index
        for index, run in enumerate(runs)
        if run.variant_id == "citation_validator"
    )
    runs[full_index] = _replace_metric(
        runs[full_index], "source_numbering_error_rate", numbering
    )

    report = build_full_report(runs)
    assert report["integrity"]["uniform_scorer"] is False
    assert report["integrity"]["source_numbering_zero"] is False
    assert report["integrity"]["hard_rules_passed"] is False
    assert set(report["acceptance"].values()) == {"failed"}


def test_baseline_numbering_defect_is_reported_but_not_a_t7_5_hard_rule():
    runs = _full_matrix()
    numbering = _metric("source_numbering_error_rate", 0.1, lower_is_better=True)
    baseline_index = next(
        index for index, run in enumerate(runs) if run.variant_id == "baseline"
    )
    runs[baseline_index] = _replace_metric(
        runs[baseline_index], "source_numbering_error_rate", numbering
    )

    report = build_full_report(runs)

    assert report["integrity"]["source_numbering_scope"] == "citation_validator"
    assert report["integrity"]["source_numbering_zero"] is True
    assert set(report["acceptance"].values()) == {"passed"}


def test_unknown_telemetry_stays_null_and_cannot_prove_cold_warm_reduction():
    runs = _full_matrix()
    index = next(
        index
        for index, run in enumerate(runs)
        if run.variant_id == "agentic_rag"
        and run.trace["protocol"]["phase"] == "warm"
    )
    runs[index] = runs[index].model_copy(
        update={
            "telemetry": runs[index].telemetry.model_copy(
                update={"total_tokens": None, "search_calls": None}
            )
        }
    )

    report = build_full_report(runs)
    assert report["acceptance"]["T7-6"] == "not_evaluable"
    aggregate = next(
        row
        for row in report["telemetry_aggregates"]
        if row["variant_id"] == "agentic_rag" and row["difficulty"] == "complex"
    )
    assert aggregate["total_tokens"]["missing_count"] == 1
    assert aggregate["total_tokens"]["complete_sum"] is None


def test_missing_protocol_and_claim_evidence_remain_not_evaluable():
    runs = _full_matrix()
    runs[0] = runs[0].model_copy(update={"trace": {"evaluation_provenance": "live"}})
    complex_full_index = next(
        index
        for index, run in enumerate(runs)
        if run.variant_id == "citation_validator"
        and run.difficulty == "complex"
        and run.trace["protocol"]["kind"] == "main"
    )
    trace = dict(runs[complex_full_index].trace)
    trace.pop("evaluation_claim_results")
    runs[complex_full_index] = runs[complex_full_index].model_copy(update={"trace": trace})

    report = build_full_report(runs)
    assert report["acceptance"]["T7-3"] == "not_evaluable"
    assert report["acceptance"]["T7-4"] == "not_evaluable"
    assert report["acceptance"]["T7-9"] == "not_evaluable"
