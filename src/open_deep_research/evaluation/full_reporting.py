"""Build deterministic Phase 7 full-evaluation statistics and decisions."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from open_deep_research.evaluation.custom_metrics import SCORER_VERSION
from open_deep_research.evaluation.experiment_models import (
    EvaluationStatus,
    ExperimentMetricResult,
    ExperimentRun,
)
from open_deep_research.evaluation.full_metrics import FULL_METRIC_NAMES

REPORT_SCHEMA_VERSION = "1.0"
BASELINE_VARIANT = "baseline"
FULL_VARIANT = "citation_validator"
EXPECTED_VARIANTS = (
    "baseline",
    "paperqa",
    "agentic_rag",
    "memory",
    "citation_validator",
)
EXPECTED_DIFFICULTIES = ("simple", "medium", "complex")
AGENTIC_WARM_VARIANTS = ("agentic_rag", "citation_validator")
SOURCE_NUMBERING_METRIC = "source_numbering_error_rate"


def _status_value(status: EvaluationStatus) -> str:
    return status.value


def _statistics(values: Sequence[float]) -> dict[str, Any]:
    """Return JSON-safe sample statistics without inventing missing values."""
    if not values:
        return {
            "count": 0,
            "mean": None,
            "std": None,
            "ci95": {"low": None, "high": None, "margin": None},
        }
    mean = statistics.fmean(values)
    std = statistics.stdev(values) if len(values) > 1 else 0.0
    margin = 1.96 * std / math.sqrt(len(values))
    return {
        "count": len(values),
        "mean": mean,
        "std": std,
        "ci95": {
            "low": mean - margin,
            "high": mean + margin,
            "margin": margin,
        },
    }


def _measurement_statistics(
    values: Sequence[int | float | None],
) -> dict[str, Any]:
    known = [float(value) for value in values if value is not None]
    missing = len(values) - len(known)
    summary = _statistics(known)
    return {
        **summary,
        "known_count": len(known),
        "missing_count": missing,
        "known_sum": sum(known) if known else (0.0 if values else None),
        "complete_sum": sum(known) if known and not missing else None,
    }


def _metric_results(run: ExperimentRun, name: str) -> list[ExperimentMetricResult]:
    return [item for item in run.metric_results if item.metric_name == name]


def _scored_metric(run: ExperimentRun, name: str) -> ExperimentMetricResult | None:
    matches = _metric_results(run, name)
    if len(matches) != 1:
        return None
    result = matches[0]
    if result.status not in {EvaluationStatus.PASSED, EvaluationStatus.FAILED}:
        return None
    if result.score is None:
        return None
    return result


def _trace_protocol(run: ExperimentRun) -> dict[str, Any]:
    """Normalize nested and legacy-flat full-run protocol evidence."""
    nested = run.trace.get("protocol")
    protocol = nested if isinstance(nested, dict) else {}
    phase = protocol.get("phase", run.trace.get("protocol_phase"))
    kind = protocol.get("kind", run.trace.get("protocol_kind"))
    return {
        "kind": kind,
        "phase": phase,
        "snapshot_hash": protocol.get(
            "snapshot_sha256",
            protocol.get("snapshot_hash", run.trace.get("snapshot_hash")),
        ),
        "runtime_state_hash": protocol.get(
            "runtime_state_sha256",
            protocol.get(
                "runtime_state_hash",
                run.trace.get("runtime_state_hash"),
            ),
        ),
        "pair_id": protocol.get("pair_id", run.trace.get("pair_id")),
        "paired_key": protocol.get("paired_key", run.trace.get("paired_key")),
    }


def _is_main(run: ExperimentRun) -> bool:
    protocol = _trace_protocol(run)
    return protocol["kind"] == "main" and protocol["phase"] in {"main", "cold"}


def _is_live_full(run: ExperimentRun) -> bool:
    return run.mode == "full" and run.trace.get("evaluation_provenance") == "live"


def _cross_variant_key(run: ExperimentRun) -> tuple[str, int, str] | None:
    protocol = _trace_protocol(run)
    key = protocol["paired_key"] or protocol["pair_id"]
    if not isinstance(key, str) or not key:
        return None
    return run.case_id, run.repeat, key


def _cold_warm_key(run: ExperimentRun) -> tuple[str, int, str] | None:
    pair_id = _trace_protocol(run)["pair_id"]
    if not isinstance(pair_id, str) or not pair_id:
        return None
    return run.case_id, run.repeat, pair_id


def _group_with_all(
    runs: Sequence[ExperimentRun],
) -> list[tuple[tuple[str, str], list[ExperimentRun]]]:
    groups: dict[tuple[str, str], list[ExperimentRun]] = defaultdict(list)
    for run in runs:
        groups[(run.variant_id, run.difficulty)].append(run)
        groups[(run.variant_id, "all")].append(run)
    return sorted(groups.items())


def _run_status_rows(runs: Sequence[ExperimentRun]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (variant, difficulty), records in _group_with_all(runs):
        counts = Counter(_status_value(item.status) for item in records)
        rows.append(
            {
                "variant_id": variant,
                "difficulty": difficulty,
                "runs": len(records),
                "passed": counts[EvaluationStatus.PASSED.value],
                "failed": counts[EvaluationStatus.FAILED.value],
                "skipped": counts[EvaluationStatus.SKIPPED.value],
                "not_applicable": counts[EvaluationStatus.NOT_APPLICABLE.value],
                "errors": counts[EvaluationStatus.ERROR.value],
            }
        )
    return rows


def _metric_rows(runs: Sequence[ExperimentRun]) -> list[dict[str, Any]]:
    metric_names = sorted(
        {metric.metric_name for run in runs for metric in run.metric_results}
    )
    rows: list[dict[str, Any]] = []
    for (variant, difficulty), records in _group_with_all(runs):
        for name in metric_names:
            results_by_run = [_metric_results(run, name) for run in records]
            results = [result for matches in results_by_run for result in matches]
            status_counts = Counter(_status_value(item.status) for item in results)
            scores = [
                float(item.score)
                for item in results
                if item.score is not None
                and item.status in {EvaluationStatus.PASSED, EvaluationStatus.FAILED}
            ]
            versions = sorted({item.metric_version for item in results})
            rows.append(
                {
                    "variant_id": variant,
                    "difficulty": difficulty,
                    "metric_name": name,
                    "metric_version": versions[0] if len(versions) == 1 else None,
                    "metric_versions": versions,
                    "runs": len(records),
                    "observations": len(results),
                    "missing": sum(not matches for matches in results_by_run),
                    "duplicates": sum(max(0, len(matches) - 1) for matches in results_by_run),
                    "passed": status_counts[EvaluationStatus.PASSED.value],
                    "failed": status_counts[EvaluationStatus.FAILED.value],
                    "skipped": status_counts[EvaluationStatus.SKIPPED.value],
                    "not_applicable": status_counts[
                        EvaluationStatus.NOT_APPLICABLE.value
                    ],
                    "errors": status_counts[EvaluationStatus.ERROR.value],
                    **_statistics(scores),
                }
            )
    return rows


def _telemetry_rows(runs: Sequence[ExperimentRun]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (variant, difficulty), records in _group_with_all(runs):
        rows.append(
            {
                "variant_id": variant,
                "difficulty": difficulty,
                "runs": len(records),
                "total_tokens": _measurement_statistics(
                    [item.telemetry.total_tokens for item in records]
                ),
                "web_calls": _measurement_statistics(
                    [item.telemetry.search_calls for item in records]
                ),
            }
        )
    return rows


def _main_variant_records(
    runs: Sequence[ExperimentRun], variant: str, difficulty: str | None = None
) -> list[ExperimentRun]:
    return [
        run
        for run in runs
        if run.variant_id == variant
        and _is_main(run)
        and (difficulty is None or run.difficulty == difficulty)
    ]


def _paired_records(
    runs: Sequence[ExperimentRun],
    *,
    baseline_variant: str,
    comparison_variant: str,
    difficulty: str | None,
) -> tuple[list[tuple[ExperimentRun, ExperimentRun]], int, int]:
    baseline = _main_variant_records(runs, baseline_variant, difficulty)
    comparison = _main_variant_records(runs, comparison_variant, difficulty)

    def index(records: Iterable[ExperimentRun]) -> tuple[dict[Any, ExperimentRun], int]:
        indexed: dict[Any, ExperimentRun] = {}
        invalid = 0
        for record in records:
            key = _cross_variant_key(record)
            if key is None or key in indexed:
                invalid += 1
            else:
                indexed[key] = record
        return indexed, invalid

    baseline_by_key, baseline_invalid = index(baseline)
    comparison_by_key, comparison_invalid = index(comparison)
    shared = sorted(set(baseline_by_key) & set(comparison_by_key))
    pairs = [(baseline_by_key[key], comparison_by_key[key]) for key in shared]
    baseline_missing = len(set(baseline_by_key) - set(comparison_by_key)) + baseline_invalid
    comparison_missing = len(set(comparison_by_key) - set(baseline_by_key)) + comparison_invalid
    return pairs, baseline_missing, comparison_missing


def _paired_metric_rows(runs: Sequence[ExperimentRun]) -> list[dict[str, Any]]:
    variants = sorted({run.variant_id for run in runs} - {BASELINE_VARIANT})
    metric_names = sorted(
        {metric.metric_name for run in runs for metric in run.metric_results}
    )
    rows: list[dict[str, Any]] = []
    for variant in variants:
        for difficulty in (*EXPECTED_DIFFICULTIES, "all"):
            pairs, baseline_unmatched, comparison_unmatched = _paired_records(
                runs,
                baseline_variant=BASELINE_VARIANT,
                comparison_variant=variant,
                difficulty=None if difficulty == "all" else difficulty,
            )
            for name in metric_names:
                deltas: list[float] = []
                missing_metric_pairs = 0
                for baseline, comparison in pairs:
                    baseline_result = _scored_metric(baseline, name)
                    comparison_result = _scored_metric(comparison, name)
                    if baseline_result is None or comparison_result is None:
                        missing_metric_pairs += 1
                    else:
                        assert baseline_result.score is not None
                        assert comparison_result.score is not None
                        deltas.append(comparison_result.score - baseline_result.score)
                rows.append(
                    {
                        "baseline_variant": BASELINE_VARIANT,
                        "comparison_variant": variant,
                        "difficulty": difficulty,
                        "metric_name": name,
                        "matched_pairs": len(pairs),
                        "baseline_unmatched": baseline_unmatched,
                        "comparison_unmatched": comparison_unmatched,
                        "missing_metric_pairs": missing_metric_pairs,
                        "delta_direction": "comparison_minus_baseline",
                        **_statistics(deltas),
                    }
                )
    return rows


def _paired_telemetry_rows(runs: Sequence[ExperimentRun]) -> list[dict[str, Any]]:
    variants = sorted({run.variant_id for run in runs} - {BASELINE_VARIANT})
    rows: list[dict[str, Any]] = []
    for variant in variants:
        for difficulty in (*EXPECTED_DIFFICULTIES, "all"):
            pairs, baseline_unmatched, comparison_unmatched = _paired_records(
                runs,
                baseline_variant=BASELINE_VARIANT,
                comparison_variant=variant,
                difficulty=None if difficulty == "all" else difficulty,
            )
            for field, label in (("total_tokens", "total_tokens"), ("search_calls", "web_calls")):
                baseline_values: list[float] = []
                comparison_values: list[float] = []
                deltas: list[float] = []
                missing_pairs = 0
                for baseline, comparison in pairs:
                    baseline_value = getattr(baseline.telemetry, field)
                    comparison_value = getattr(comparison.telemetry, field)
                    if baseline_value is None or comparison_value is None:
                        missing_pairs += 1
                    else:
                        baseline_values.append(float(baseline_value))
                        comparison_values.append(float(comparison_value))
                        deltas.append(float(comparison_value - baseline_value))
                baseline_mean = (
                    statistics.fmean(baseline_values) if baseline_values else None
                )
                comparison_mean = (
                    statistics.fmean(comparison_values) if comparison_values else None
                )
                percent = (
                    (comparison_mean - baseline_mean) / baseline_mean * 100
                    if baseline_mean is not None
                    and baseline_mean != 0.0
                    and comparison_mean is not None
                    else None
                )
                rows.append(
                    {
                        "baseline_variant": BASELINE_VARIANT,
                        "comparison_variant": variant,
                        "difficulty": difficulty,
                        "measurement": label,
                        "matched_pairs": len(pairs),
                        "baseline_unmatched": baseline_unmatched,
                        "comparison_unmatched": comparison_unmatched,
                        "missing_measurement_pairs": missing_pairs,
                        "baseline_mean": baseline_mean,
                        "comparison_mean": comparison_mean,
                        "percent_difference": percent,
                        "delta_direction": "comparison_minus_baseline",
                        **_statistics(deltas),
                    }
                )
    return rows


def _claim_results_present(run: ExperimentRun) -> bool:
    candidates = (
        run.trace.get("evaluation_claim_results"),
        run.trace.get("claim_results"),
        run.trace.get("claim_observations"),
    )
    if any(isinstance(value, list) and bool(value) for value in candidates):
        return True
    artifacts = run.trace.get("state_artifacts")
    if not isinstance(artifacts, dict):
        return False
    citation = artifacts.get("citation")
    return isinstance(citation, dict) and isinstance(citation.get("results"), list) and bool(
        citation["results"]
    )


def _expected_output_present(run: ExperimentRun) -> bool:
    value = run.trace.get("expected_output_present")
    if isinstance(value, bool):
        return value
    context = run.trace.get("evaluation_context")
    return isinstance(context, dict) and context.get("expected_output_present") is True


def _plan_present(run: ExperimentRun) -> bool:
    explicit = run.trace.get("plan_present")
    if isinstance(explicit, bool):
        return explicit
    normalized = run.trace.get("normalized")
    return isinstance(normalized, dict) and bool(normalized.get("plan"))


def _integrity_report(runs: Sequence[ExperimentRun]) -> dict[str, Any]:
    scorer_versions = sorted({run.scorer_version for run in runs})
    scorer_violations = sorted(
        run.run_id for run in runs if run.scorer_version != SCORER_VERSION
    )
    # T7-5 is a contract of the complete/citation-validator variant.  Baseline
    # numbering defects are comparison data, not a reason to invalidate an
    # otherwise fair paired experiment before uplift can be measured.
    numbering_violations: list[str] = []
    numbering_runs = [run for run in runs if run.variant_id == FULL_VARIANT]
    for run in numbering_runs:
        matches = _metric_results(run, SOURCE_NUMBERING_METRIC)
        if (
            len(matches) != 1
            or matches[0].status is not EvaluationStatus.PASSED
            or matches[0].score != 0.0
        ):
            numbering_violations.append(run.run_id)
    return {
        "required_scorer_version": SCORER_VERSION,
        "observed_scorer_versions": scorer_versions,
        "uniform_scorer": not scorer_violations,
        "scorer_violation_run_ids": scorer_violations,
        "source_numbering_zero": not numbering_violations,
        "source_numbering_scope": FULL_VARIANT,
        "source_numbering_violation_run_ids": sorted(numbering_violations),
        "hard_rules_passed": not scorer_violations and not numbering_violations,
    }


def _metric_mean(records: Sequence[ExperimentRun], name: str) -> float | None:
    results = [_scored_metric(record, name) for record in records]
    if not records or any(result is None for result in results):
        return None
    return statistics.fmean(
        float(result.score) for result in results if result is not None and result.score is not None
    )


def _complete_live_pairs(
    runs: Sequence[ExperimentRun],
    *,
    comparison_variant: str,
    difficulties: Sequence[str],
    minimum_per_difficulty: int = 3,
) -> tuple[bool, list[tuple[ExperimentRun, ExperimentRun]], list[str]]:
    all_pairs: list[tuple[ExperimentRun, ExperimentRun]] = []
    reasons: list[str] = []
    for difficulty in difficulties:
        pairs, baseline_unmatched, comparison_unmatched = _paired_records(
            runs,
            baseline_variant=BASELINE_VARIANT,
            comparison_variant=comparison_variant,
            difficulty=difficulty,
        )
        if len(pairs) != minimum_per_difficulty:
            reasons.append(
                f"{difficulty} has {len(pairs)} paired runs; requires exactly "
                f"{minimum_per_difficulty}"
            )
        if baseline_unmatched or comparison_unmatched:
            reasons.append(
                f"{difficulty} has unmatched runs baseline={baseline_unmatched}, "
                f"comparison={comparison_unmatched}"
            )
        if any(not _is_live_full(record) for pair in pairs for record in pair):
            reasons.append(f"{difficulty} includes non-live/full paired evidence")
        all_pairs.extend(pairs)
    return not reasons, all_pairs, reasons


def _decision(
    *,
    evaluable: bool,
    passed: bool,
    not_evaluable_reasons: Sequence[str],
    failed_reasons: Sequence[str],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    if not evaluable:
        status = "not_evaluable"
        reasons = list(not_evaluable_reasons)
    elif passed:
        status = "passed"
        reasons = []
    else:
        status = "failed"
        reasons = list(failed_reasons)
    return {"status": status, "reasons": reasons, "evidence": evidence}


def _acceptance_t7_3(
    runs: Sequence[ExperimentRun], integrity: dict[str, Any]
) -> dict[str, Any]:
    coverage, pairs, coverage_reasons = _complete_live_pairs(
        runs,
        comparison_variant=FULL_VARIANT,
        difficulties=EXPECTED_DIFFICULTIES,
    )
    metric_complete = all(
        _scored_metric(record, "task_completion") is not None
        for pair in pairs
        for record in pair
    )
    if not metric_complete:
        coverage_reasons.append("task_completion has missing, duplicate, skipped, or errored results")
    by_difficulty: dict[str, Any] = {}
    for difficulty in EXPECTED_DIFFICULTIES:
        difficulty_pairs = [pair for pair in pairs if pair[0].difficulty == difficulty]
        baseline = [pair[0] for pair in difficulty_pairs]
        comparison = [pair[1] for pair in difficulty_pairs]
        by_difficulty[difficulty] = {
            "baseline_mean": _metric_mean(baseline, "task_completion"),
            "full_mean": _metric_mean(comparison, "task_completion"),
            "pairs": len(difficulty_pairs),
        }
    baseline_mean = _metric_mean([pair[0] for pair in pairs], "task_completion")
    full_mean = _metric_mean([pair[1] for pair in pairs], "task_completion")
    evaluable = coverage and metric_complete and baseline_mean is not None and full_mean is not None
    performance_passed = bool(
        evaluable and full_mean is not None and baseline_mean is not None and full_mean >= baseline_mean
    )
    hard_rules = bool(integrity["hard_rules_passed"])
    return _decision(
        evaluable=evaluable,
        passed=performance_passed and hard_rules,
        not_evaluable_reasons=coverage_reasons,
        failed_reasons=(
            ([] if performance_passed else ["full task_completion mean is below baseline"])
            + ([] if hard_rules else ["scorer/source-numbering hard rule failed"])
        ),
        evidence={
            "baseline_mean": baseline_mean,
            "full_mean": full_mean,
            "by_difficulty": by_difficulty,
            "paired_runs": len(pairs),
        },
    )


def _acceptance_t7_4(
    runs: Sequence[ExperimentRun], integrity: dict[str, Any]
) -> dict[str, Any]:
    coverage, pairs, reasons = _complete_live_pairs(
        runs,
        comparison_variant=FULL_VARIANT,
        difficulties=("complex",),
    )
    names = ("citation_fidelity", "citation_completeness", "unsupported_claim_rate")
    metric_complete = all(
        _scored_metric(record, name) is not None
        for pair in pairs
        for record in pair
        for name in names
    )
    claims_traceable = all(_claim_results_present(record) for pair in pairs for record in pair)
    if not metric_complete:
        reasons.append("citation metrics have missing, duplicate, skipped, or errored results")
    if not claims_traceable:
        reasons.append("claim-level evaluation results are not traceable for every paired run")
    means: dict[str, dict[str, float | None]] = {
        name: {
            "baseline": _metric_mean([pair[0] for pair in pairs], name),
            "full": _metric_mean([pair[1] for pair in pairs], name),
        }
        for name in names
    }
    evaluable = coverage and metric_complete and claims_traceable and all(
        value is not None for item in means.values() for value in item.values()
    )
    fidelity_baseline = means["citation_fidelity"]["baseline"]
    fidelity_full = means["citation_fidelity"]["full"]
    completeness_baseline = means["citation_completeness"]["baseline"]
    completeness_full = means["citation_completeness"]["full"]
    unsupported_baseline = means["unsupported_claim_rate"]["baseline"]
    unsupported_full = means["unsupported_claim_rate"]["full"]
    uplift_passed = bool(
        evaluable
        and fidelity_baseline is not None
        and fidelity_full is not None
        and completeness_baseline is not None
        and completeness_full is not None
        and unsupported_baseline is not None
        and unsupported_full is not None
        and fidelity_full > fidelity_baseline
        and completeness_full > completeness_baseline
        and unsupported_full < unsupported_baseline
    )
    hard_rules = bool(integrity["hard_rules_passed"])
    return _decision(
        evaluable=evaluable,
        passed=uplift_passed and hard_rules,
        not_evaluable_reasons=reasons,
        failed_reasons=(
            ([] if uplift_passed else ["complex citation uplift inequalities are not all strict"])
            + ([] if hard_rules else ["scorer/source-numbering hard rule failed"])
        ),
        evidence={"means": means, "paired_runs": len(pairs)},
    )


def _cold_warm_pairs(
    runs: Sequence[ExperimentRun], variant: str
) -> tuple[list[tuple[ExperimentRun, ExperimentRun]], list[str]]:
    cold = [
        run
        for run in runs
        if run.variant_id == variant
        and run.difficulty == "complex"
        and _trace_protocol(run)["kind"] == "main"
        and _trace_protocol(run)["phase"] == "cold"
    ]
    warm = [
        run
        for run in runs
        if run.variant_id == variant
        and run.difficulty == "complex"
        and _trace_protocol(run)["kind"] == "cold_warm"
        and _trace_protocol(run)["phase"] == "warm"
    ]

    def index(records: Sequence[ExperimentRun]) -> tuple[dict[Any, ExperimentRun], int]:
        indexed: dict[Any, ExperimentRun] = {}
        invalid = 0
        for record in records:
            key = _cold_warm_key(record)
            if key is None or key in indexed:
                invalid += 1
            else:
                indexed[key] = record
        return indexed, invalid

    cold_by_key, cold_invalid = index(cold)
    warm_by_key, warm_invalid = index(warm)
    shared = sorted(set(cold_by_key) & set(warm_by_key))
    pairs = [(cold_by_key[key], warm_by_key[key]) for key in shared]
    reasons: list[str] = []
    unmatched = len(set(cold_by_key) ^ set(warm_by_key)) + cold_invalid + warm_invalid
    if len(pairs) != 3:
        reasons.append(f"{variant} has {len(pairs)} cold/warm pairs; requires exactly 3")
    if unmatched:
        reasons.append(f"{variant} has {unmatched} unmatched or duplicate cold/warm runs")
    if any(not _is_live_full(record) for pair in pairs for record in pair):
        reasons.append(f"{variant} cold/warm evidence is not entirely live/full")
    for cold_run, warm_run in pairs:
        cold_hash = _trace_protocol(cold_run)["snapshot_hash"]
        warm_hash = _trace_protocol(warm_run)["snapshot_hash"]
        if not isinstance(cold_hash, str) or not cold_hash or cold_hash != warm_hash:
            reasons.append(
                f"{variant} pair {cold_run.run_id} lacks one fixed initial snapshot hash"
            )
        cold_runtime = _trace_protocol(cold_run)["runtime_state_hash"]
        warm_runtime = _trace_protocol(warm_run)["runtime_state_hash"]
        if (
            not isinstance(cold_runtime, str)
            or not cold_runtime
            or cold_runtime != warm_runtime
        ):
            reasons.append(
                f"{variant} pair {cold_run.run_id} lacks one fixed post-cold runtime state hash"
            )
    return pairs, reasons


def _reduction(
    pairs: Sequence[tuple[ExperimentRun, ExperimentRun]], field: str
) -> dict[str, Any]:
    cold_values: list[float] = []
    warm_values: list[float] = []
    for cold, warm in pairs:
        cold_value = getattr(cold.telemetry, field)
        warm_value = getattr(warm.telemetry, field)
        if cold_value is None or warm_value is None:
            return {
                "cold_mean": None,
                "warm_mean": None,
                "absolute_reduction": None,
                "percent_reduction": None,
                "complete": False,
            }
        cold_values.append(float(cold_value))
        warm_values.append(float(warm_value))
    cold_mean = statistics.fmean(cold_values) if cold_values else None
    warm_mean = statistics.fmean(warm_values) if warm_values else None
    absolute = cold_mean - warm_mean if cold_mean is not None and warm_mean is not None else None
    percent = absolute / cold_mean * 100 if absolute is not None and cold_mean else None
    return {
        "cold_mean": cold_mean,
        "warm_mean": warm_mean,
        "absolute_reduction": absolute,
        "percent_reduction": percent,
        "complete": cold_mean is not None and warm_mean is not None,
    }


def _warm_baseline_reduction(
    baseline_pairs: Sequence[tuple[ExperimentRun, ExperimentRun]],
    comparison_pairs: Sequence[tuple[ExperimentRun, ExperimentRun]],
    field: str,
) -> dict[str, Any]:
    baseline_warm: dict[tuple[str, int, str], ExperimentRun] = {}
    comparison_warm: dict[tuple[str, int, str], ExperimentRun] = {}
    for _, warm in baseline_pairs:
        key = _cross_variant_key(warm)
        if key is not None:
            baseline_warm[key] = warm
    for _, warm in comparison_pairs:
        key = _cross_variant_key(warm)
        if key is not None:
            comparison_warm[key] = warm
    shared = sorted(set(baseline_warm) & set(comparison_warm))
    if len(shared) < 3:
        return {
            "baseline_warm_mean": None,
            "comparison_warm_mean": None,
            "absolute_reduction": None,
            "percent_reduction": None,
            "matched_pairs": len(shared),
            "complete": False,
        }
    baseline_values: list[float] = []
    comparison_values: list[float] = []
    for key in shared:
        baseline_value = getattr(baseline_warm[key].telemetry, field)
        comparison_value = getattr(comparison_warm[key].telemetry, field)
        if baseline_value is None or comparison_value is None:
            return {
                "baseline_warm_mean": None,
                "comparison_warm_mean": None,
                "absolute_reduction": None,
                "percent_reduction": None,
                "matched_pairs": len(shared),
                "complete": False,
            }
        baseline_values.append(float(baseline_value))
        comparison_values.append(float(comparison_value))
    baseline_mean = statistics.fmean(baseline_values)
    comparison_mean = statistics.fmean(comparison_values)
    absolute = baseline_mean - comparison_mean
    return {
        "baseline_warm_mean": baseline_mean,
        "comparison_warm_mean": comparison_mean,
        "absolute_reduction": absolute,
        "percent_reduction": absolute / baseline_mean * 100 if baseline_mean else None,
        "matched_pairs": len(shared),
        "complete": True,
    }


def _cold_warm_report(runs: Sequence[ExperimentRun]) -> dict[str, Any]:
    variants = (BASELINE_VARIANT, *AGENTIC_WARM_VARIANTS)
    pairs_by_variant: dict[str, list[tuple[ExperimentRun, ExperimentRun]]] = {}
    reasons: list[str] = []
    results: dict[str, Any] = {}
    all_hashes: set[str] = set()
    runtime_state_hashes: set[str] = set()
    for variant in variants:
        pairs, pair_reasons = _cold_warm_pairs(runs, variant)
        pairs_by_variant[variant] = pairs
        reasons.extend(pair_reasons)
        snapshot_pairs: list[dict[str, Any]] = []
        for cold, warm in pairs:
            for record in (cold, warm):
                snapshot = _trace_protocol(record)["snapshot_hash"]
                if isinstance(snapshot, str) and snapshot:
                    all_hashes.add(snapshot)
                runtime_state = _trace_protocol(record)["runtime_state_hash"]
                if isinstance(runtime_state, str) and runtime_state:
                    runtime_state_hashes.add(runtime_state)
            snapshot_pairs.append(
                {
                    "pair_id": _trace_protocol(cold)["pair_id"],
                    "cold_run_id": cold.run_id,
                    "warm_run_id": warm.run_id,
                    "snapshot_hash": _trace_protocol(cold)["snapshot_hash"],
                    "runtime_state_hash": _trace_protocol(cold)[
                        "runtime_state_hash"
                    ],
                }
            )
        task_results = [
            _scored_metric(record, "task_completion")
            for pair in pairs
            for record in pair
        ]
        quality_complete = bool(pairs) and all(result is not None for result in task_results)
        quality_gate_passed = quality_complete and all(
            result.status is EvaluationStatus.PASSED
            for result in task_results
            if result is not None
        )
        results[variant] = {
            "pairs": len(pairs),
            "snapshot_hashes": sorted(
                {
                    str(_trace_protocol(record)["snapshot_hash"])
                    for pair in pairs
                    for record in pair
                    if _trace_protocol(record)["snapshot_hash"]
                }
            ),
            "snapshot_pairs": sorted(
                snapshot_pairs,
                key=lambda item: (str(item["pair_id"]), item["cold_run_id"]),
            ),
            "quality_gate_complete": quality_complete,
            "quality_gate_passed": quality_gate_passed,
            "total_tokens": _reduction(pairs, "total_tokens"),
            "web_calls": _reduction(pairs, "search_calls"),
        }
    baseline_pairs = pairs_by_variant[BASELINE_VARIANT]
    for variant in AGENTIC_WARM_VARIANTS:
        results[variant]["warm_vs_baseline"] = {
            "total_tokens": _warm_baseline_reduction(
                baseline_pairs, pairs_by_variant[variant], "total_tokens"
            ),
            "web_calls": _warm_baseline_reduction(
                baseline_pairs, pairs_by_variant[variant], "search_calls"
            ),
        }
    return {
        "snapshot_hashes": sorted(all_hashes),
        "runtime_state_hashes": sorted(runtime_state_hashes),
        "variants": results,
        "integrity_reasons": reasons,
        "evaluable": not reasons,
    }


def _acceptance_t7_6(
    cold_warm: dict[str, Any], integrity: dict[str, Any]
) -> dict[str, Any]:
    reasons = list(cold_warm["integrity_reasons"])
    targets = [cold_warm["variants"][variant] for variant in AGENTIC_WARM_VARIANTS]
    measurements_complete = all(
        result[measurement]["complete"]
        and result["warm_vs_baseline"][measurement]["complete"]
        for result in targets
        for measurement in ("total_tokens", "web_calls")
    )
    quality_complete = all(
        cold_warm["variants"][variant]["quality_gate_complete"]
        for variant in (BASELINE_VARIANT, *AGENTIC_WARM_VARIANTS)
    )
    if not measurements_complete:
        reasons.append("cold/warm token or Web measurements are incomplete")
    if not quality_complete:
        reasons.append("task_completion quality-gate results are incomplete")
    evaluable = bool(cold_warm["evaluable"] and measurements_complete and quality_complete)
    quality_passed = all(
        cold_warm["variants"][variant]["quality_gate_passed"]
        for variant in (BASELINE_VARIANT, *AGENTIC_WARM_VARIANTS)
    )
    reductions_passed = all(
        result[measurement]["absolute_reduction"] > 0
        and result["warm_vs_baseline"][measurement]["absolute_reduction"] > 0
        for result in targets
        for measurement in ("total_tokens", "web_calls")
    ) if evaluable else False
    hard_rules = bool(integrity["hard_rules_passed"])
    return _decision(
        evaluable=evaluable,
        passed=quality_passed and reductions_passed and hard_rules,
        not_evaluable_reasons=reasons,
        failed_reasons=(
            ([] if quality_passed else ["task_completion hard quality gate failed"])
            + ([] if reductions_passed else ["required warm reductions are not all strict"])
            + ([] if hard_rules else ["scorer/source-numbering hard rule failed"])
        ),
        evidence=cold_warm,
    )


def _acceptance_t7_9(
    runs: Sequence[ExperimentRun], integrity: dict[str, Any]
) -> dict[str, Any]:
    main = [run for run in runs if _is_main(run)]
    reasons: list[str] = []
    expected_pair_keys: set[tuple[str, int, str]] | None = None
    for variant in EXPECTED_VARIANTS:
        variant_records = [run for run in main if run.variant_id == variant]
        for difficulty in EXPECTED_DIFFICULTIES:
            count = sum(run.difficulty == difficulty for run in variant_records)
            if count != 3:
                reasons.append(
                    f"{variant}/{difficulty} has {count} main runs; requires exactly 3"
                )
        raw_keys = [_cross_variant_key(run) for run in variant_records]
        keys = set(raw_keys)
        if None in keys:
            reasons.append(f"{variant} has main runs without a paired key")
        clean_keys = {key for key in keys if key is not None}
        if len(clean_keys) != len(variant_records):
            reasons.append(f"{variant} has duplicate or missing main paired keys")
        if expected_pair_keys is None:
            expected_pair_keys = clean_keys
        elif clean_keys != expected_pair_keys:
            reasons.append(f"{variant} main paired-key coverage differs from baseline")
    if any(not _is_live_full(run) for run in main):
        reasons.append("main matrix includes fake, smoke, calibration, or unmarked evidence")
    if not main:
        reasons.append("no main full-evaluation records exist")
    missing_metrics: list[str] = []
    metric_versions: dict[str, set[str]] = defaultdict(set)
    for run in main:
        if not _expected_output_present(run):
            reasons.append(f"{run.run_id} does not prove expected_output eligibility")
        for name in FULL_METRIC_NAMES:
            matches = _metric_results(run, name)
            if len(matches) != 1 or _scored_metric(run, name) is None:
                missing_metrics.append(f"{run.run_id}:{name}")
            for match in matches:
                metric_versions[name].add(match.metric_version)
        plan = _metric_results(run, "plan_adherence")
        if not _plan_present(run) and any(
            item.status is EvaluationStatus.PASSED for item in plan
        ):
            reasons.append(f"{run.run_id} passed plan_adherence without a plan trace")
    if missing_metrics:
        reasons.append(
            f"{len(missing_metrics)} required metric observations are missing, duplicate, skipped, "
            "not-applicable, or errored"
        )
    inconsistent_versions = {
        name: sorted(versions)
        for name, versions in metric_versions.items()
        if len(versions) != 1
    }
    if inconsistent_versions:
        reasons.append("required full metrics do not each use one version")
    coverage_evaluable = not reasons
    hard_rules = bool(integrity["hard_rules_passed"])
    return _decision(
        evaluable=coverage_evaluable,
        passed=hard_rules,
        not_evaluable_reasons=reasons,
        failed_reasons=([] if hard_rules else ["scorer/source-numbering hard rule failed"]),
        evidence={
            "main_runs": len(main),
            "required_metrics": list(FULL_METRIC_NAMES),
            "missing_metric_observations": sorted(missing_metrics),
            "metric_versions": {
                name: sorted(versions) for name, versions in sorted(metric_versions.items())
            },
        },
    )


def build_full_report(runs: Sequence[ExperimentRun]) -> dict[str, Any]:
    """Build a machine-serializable full report without network or model calls.

    Acceptance is deliberately fail-safe. Only ``mode=full`` records carrying
    ``trace.evaluation_provenance=live`` and explicit pairing/protocol evidence can
    evaluate live gates. Smoke, calibration, fake, skipped, and incomplete records
    remain visible but can never establish a pass.
    """
    ordered = sorted(runs, key=lambda item: item.run_id)
    integrity = _integrity_report(ordered)
    cold_warm = _cold_warm_report(ordered)
    details = {
        "T7-3": _acceptance_t7_3(ordered, integrity),
        "T7-4": _acceptance_t7_4(ordered, integrity),
        "T7-6": _acceptance_t7_6(cold_warm, integrity),
        "T7-9": _acceptance_t7_9(ordered, integrity),
    }
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_kind": "phase7_full_statistics",
        "run_count": len(ordered),
        "mode_counts": dict(sorted(Counter(run.mode for run in ordered).items())),
        "provenance_counts": dict(
            sorted(
                Counter(str(run.trace.get("evaluation_provenance") or "missing") for run in ordered).items()
            )
        ),
        "run_status": _run_status_rows(ordered),
        "metric_aggregates": _metric_rows(ordered),
        "telemetry_aggregates": _telemetry_rows(ordered),
        "paired_deltas": _paired_metric_rows(ordered),
        "token_web_comparisons": _paired_telemetry_rows(ordered),
        "cold_warm": cold_warm,
        "integrity": integrity,
        "acceptance": {key: value["status"] for key, value in details.items()},
        "acceptance_details": details,
        "limitations": [
            "Scores exclude skipped, errored, not-applicable, and missing observations; their counts remain explicit.",
            "Unknown token and Web measurements remain null and cannot establish a reduction.",
            "Only explicitly marked live/full records can satisfy T7-3, T7-4, T7-6, or T7-9.",
        ],
    }


def render_full_report_markdown(report: Mapping[str, Any]) -> str:
    """Render the durable full report from machine data only."""
    status_rows = [
        item for item in report["run_status"] if item["difficulty"] == "all"
    ]
    lines = [
        "# Phase 7 full evaluation report",
        "",
        f"Status: `{report['status']}`  ",
        f"Runs: `{report['completed_run_records']}/{report['planned_runs']}`  ",
        f"Committed tokens: `{report['token_budget']['committed_tokens']}`",
        "",
        "## Acceptance",
        "",
        "| Gate | Status |",
        "|---|---|",
        *[
            f"| {name} | {value} |"
            for name, value in sorted(report["acceptance"].items())
        ],
        "",
        "## Run status",
        "",
        "| Variant | Runs | Passed | Failed | Skipped | Errors |",
        "|---|---:|---:|---:|---:|---:|",
        *[
            "| {variant_id} | {runs} | {passed} | {failed} | {skipped} | {errors} |".format(
                **item
            )
            for item in status_rows
        ],
        "",
        "## Limitations",
        "",
        *[f"- {item}" for item in report["limitations"]],
        "",
    ]
    return "\n".join(lines)
