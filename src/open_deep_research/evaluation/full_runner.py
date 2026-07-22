"""Crash-safe execution of the fixed 54-run Phase 7 full evaluation.

Authorization is deliberately owned by the public runner/CLI.  This module is
the execution core: it accepts already-authorized limits, fixes the matrix,
persists every paid step, and never treats the earlier calibration artifact as
full-evaluation evidence.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import os
import shutil
import threading
import time
from collections import Counter
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv

from open_deep_research.evaluation.artifact_safety import sanitize_evaluation_value
from open_deep_research.evaluation.budget import load_full_plan, resolve_models
from open_deep_research.evaluation.calibration_runner import (
    CalibrationBudgetStore,
    ClaimScorerFactory,
    MetricFactory,
    ResearchExecutor,
    ResearchObservation,
    _atomic_write_json,
    _atomic_write_text,
    _category_actual,
    _claim_result_from_payload,
    _complete_failure_metrics,
    _delta,
    _error_fingerprint,
    _failure_terminal_status,
    _find_research_event,
    _judge_usage_from_store,
    _load_completed_runs,
    _metric_path,
    _observation_from_payload,
    _observation_payload,
    _output_exclusion,
    _persist_run_record,
    _read_hashed_json,
    _reconcile_journal_and_budget,
    _research_path,
    _run_status,
    _settle_active_run_reservations,
    _write_hashed_json,
    _write_runs,
    build_live_claim_scorer,
    build_live_metric_calls,
    execute_live_research,
)
from open_deep_research.evaluation.calibration_runtime import (
    build_variant_config,
    inject_governed_runtime,
    runtime_tool_names,
    validate_calibration_matrix,
)
from open_deep_research.evaluation.calibration_state import (
    CalibrationExperimentIdentity,
    CalibrationJournalStore,
    capture_experiment_identity,
)
from open_deep_research.evaluation.claim_scorer import (
    CLAIM_SCORER_STEP_NAME,
    CLAIM_SCORER_VERSION,
    ClaimCitationScorer,
    ClaimScorerError,
    ClaimScorerResult,
    validate_claim_scorer_coverage,
)
from open_deep_research.evaluation.custom_metrics import (
    cost_completeness_metric,
    memory_reuse_metric,
    score_citations,
    source_numbering_metric,
    source_quality_metric,
)
from open_deep_research.evaluation.dataset import merge_evaluation_dataset
from open_deep_research.evaluation.deepeval_adapter import (
    EXPECTED_DEEPEVAL_VERSION,
    deepeval_version,
)
from open_deep_research.evaluation.experiment_models import (
    EvaluationStatus,
    ExperimentMetricResult,
    ExperimentRun,
    ExperimentTelemetry,
    ExperimentVariant,
    MergedEvaluationCase,
)
from open_deep_research.evaluation.full_metrics import (
    FULL_JUDGE_STEP_NAMES,
    FULL_METRIC_NAMES,
)
from open_deep_research.evaluation.full_reporting import (
    build_full_report,
    render_full_report_markdown,
)
from open_deep_research.evaluation.full_state import (
    FullRunDefinition,
    build_full_run_definitions,
    cold_definition_for,
)
from open_deep_research.evaluation.live_budget import (
    LiveTokenBudgetError,
    LiveTokenBudgetExceeded,
    LiveTokenReservationLedger,
)
from open_deep_research.evaluation.reporting import write_artifact_manifest
from open_deep_research.evaluation.tracking import TrackingResult, TrackingSink
from open_deep_research.evaluation.variants import VARIANT_ORDER, load_variants


class FullRunnerError(RuntimeError):
    """The fixed full matrix cannot safely dispatch another paid step."""


class _SoftDispatchLedger:
    """Full-only proxy enforcing the soft stop before every paid reservation."""

    def __init__(
        self, ledger: LiveTokenReservationLedger, *, soft_token_limit: int
    ) -> None:
        self._ledger = ledger
        self.soft_token_limit = soft_token_limit
        self._dispatch_lock = threading.RLock()

    def reserve_before_call(
        self,
        *,
        run_id: str,
        category: Any,
        input_upper_bound: int,
        output_upper_bound: int,
        reservation_id: str | None = None,
    ) -> Any:
        # The soft check and the hard-ledger reservation form one local critical
        # section.  The outer process lease excludes a second runner process.
        with self._dispatch_lock:
            snapshot = self._ledger.snapshot()
            prospective = (
                int(snapshot["committed_tokens"])
                + int(snapshot["active_reserved_tokens"])
                + input_upper_bound
                + output_upper_bound
            )
            if prospective > self.soft_token_limit:
                raise LiveTokenBudgetExceeded(
                    "experiment soft token ceiling would be exceeded before call"
                )
            return self._ledger.reserve_before_call(
                run_id=run_id,
                category=category,
                input_upper_bound=input_upper_bound,
                output_upper_bound=output_upper_bound,
                reservation_id=reservation_id,
            )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ledger, name)


def _retry_permission(operation: Any) -> Any:
    """Retry only transient Windows file-lock denials, never paid work."""
    for attempt in range(8):
        try:
            return operation()
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(min(0.02 * (attempt + 1), 0.1))
    raise AssertionError("unreachable persistence retry state")


@contextmanager
def _exclusive_full_run_lease(project_root: Path, output: Path):
    """Hold an OS-released cross-process lease for one exact output path."""
    output_key = hashlib.sha256(
        str(output.resolve()).replace("\\", "/").casefold().encode()
    ).hexdigest()
    lock_root = project_root.resolve() / ".phase-validation-tmp" / "phase7-full-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / f"{output_key}.lock"
    handle = lock_path.open("a+b")
    acquired = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                lock_module = importlib.import_module("msvcrt")
                lock_module.locking(handle.fileno(), lock_module.LK_NBLCK, 1)
            else:
                lock_module = importlib.import_module("fcntl")
                lock_module.flock(
                    handle.fileno(), lock_module.LOCK_EX | lock_module.LOCK_NB
                )
        except OSError as error:
            raise FullRunnerError(
                "another Phase 7 full process already owns this output lease"
            ) from error
        acquired = True
        yield
    finally:
        if acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    lock_module = importlib.import_module("msvcrt")
                    lock_module.locking(handle.fileno(), lock_module.LK_UNLCK, 1)
                else:
                    lock_module = importlib.import_module("fcntl")
                    lock_module.flock(handle.fileno(), lock_module.LOCK_UN)
            finally:
                handle.close()
        else:
            handle.close()


@dataclass(frozen=True, slots=True)
class FullOutcome:
    """Machine-readable result returned to the guarded public entry point."""

    status: str
    experiment_id: str
    completed_runs: int
    planned_runs: int
    committed_tokens: int
    output_dir: str
    stopped_reason: str | None = None


@dataclass(slots=True)
class _TrackingState:
    sink: TrackingSink | None
    output: Path
    errors: list[dict[str, Any]]


_SNAPSHOT_ROOT_NAMES = {"knowledge-blobs", "paperqa-index"}
_SNAPSHOT_FILE_PREFIXES = ("knowledge.db", "memory.db")
_SNAPSHOT_COMPLETE = ".phase7-snapshot-complete.json"


def _full_private_runtime_root(
    project_root: Path, output: Path, experiment_id: str
) -> Path:
    """Keep mutable stores and fixed warm snapshots outside public artifacts."""
    output_key = hashlib.sha256(
        str(output.resolve()).replace("\\", "/").casefold().encode()
    ).hexdigest()[:20]
    try:
        relative = output.relative_to(project_root)
    except ValueError:
        return (
            output.parent
            / f".{output.name}.{output_key}.private-runtime"
            / experiment_id
        )
    if relative.parts and relative.parts[0] == ".phase-validation-tmp":
        return (
            output.parent
            / f".{output.name}.{output_key}.private-runtime"
            / experiment_id
        )
    return (
        project_root
        / ".phase-validation-tmp"
        / "phase7-full"
        / output_key
        / experiment_id
    )


def _claim_runtime_root(
    runtime_root: Path,
    *,
    output: Path,
    identity: CalibrationExperimentIdentity,
    resume: bool,
) -> None:
    output_key = hashlib.sha256(
        str(output.resolve()).replace("\\", "/").casefold().encode()
    ).hexdigest()
    expected = {
        "schema_version": "1.0",
        "experiment_id": identity.experiment_id,
        "output_path_sha256": output_key,
    }
    marker = runtime_root / ".phase7-full-owner.json"
    if resume:
        try:
            observed = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise FullRunnerError("full runtime owner marker is missing or invalid") from error
        if observed != expected:
            raise FullRunnerError("full runtime owner differs from the persisted output")
        return
    if runtime_root.exists():
        raise FullRunnerError("full runtime root already exists for this output identity")
    runtime_root.mkdir(parents=True, exist_ok=False)
    _retry_permission(
        lambda: marker.write_text(json.dumps(expected), encoding="utf-8")
    )


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256(b"phase7-fixed-runtime-snapshot-v1\0")
    if not root.is_dir():
        raise FullRunnerError("fixed runtime snapshot is missing")
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise FullRunnerError("fixed runtime snapshot contains a symlink")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == _SNAPSHOT_COMPLETE:
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _validated_snapshot_hash(snapshot_dir: Path) -> str:
    marker = snapshot_dir / _SNAPSHOT_COMPLETE
    if not marker.is_file():
        raise FullRunnerError("fixed runtime snapshot is incomplete")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise FullRunnerError("fixed runtime snapshot marker is invalid") from error
    digest = _tree_hash(snapshot_dir)
    if payload != {"schema_version": "1.0", "sha256": digest}:
        raise FullRunnerError("fixed runtime snapshot marker does not match content")
    return digest


def _snapshot_candidates(runtime_dir: Path) -> list[Path]:
    if not runtime_dir.is_dir():
        raise FullRunnerError("cold runtime is unavailable for fixed warm snapshot")
    result: list[Path] = []
    for child in runtime_dir.iterdir():
        if child.is_symlink():
            raise FullRunnerError("cold runtime contains an unsafe symlink")
        if child.name in _SNAPSHOT_ROOT_NAMES or child.name.startswith(
            _SNAPSHOT_FILE_PREFIXES
        ):
            result.append(child)
    return sorted(result)


def _copy_path_exclusive(source: Path, destination: Path) -> None:
    if source.is_symlink():
        raise FullRunnerError("runtime snapshot refuses symlink content")
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=False)
        for child in sorted(source.iterdir()):
            _copy_path_exclusive(child, destination / child.name)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
    else:
        raise FullRunnerError("runtime snapshot contains an unsupported entry")


def _capture_runtime_snapshot(runtime_dir: Path, snapshot_dir: Path) -> str:
    """Capture only reusable knowledge/memory state, never checkpoints/run state."""
    if snapshot_dir.exists():
        return _validated_snapshot_hash(snapshot_dir)
    snapshot_dir.parent.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(exist_ok=False)
    for source in _snapshot_candidates(runtime_dir):
        _copy_path_exclusive(source, snapshot_dir / source.name)
    digest = _tree_hash(snapshot_dir)
    marker = snapshot_dir / _SNAPSHOT_COMPLETE
    _retry_permission(
        lambda: marker.write_text(
            json.dumps({"schema_version": "1.0", "sha256": digest}),
            encoding="utf-8",
        )
    )
    return _validated_snapshot_hash(snapshot_dir)


def _restore_runtime_snapshot(snapshot_dir: Path, runtime_dir: Path) -> str:
    """Restore a fixed snapshot into a distinct warm-run runtime directory."""
    digest = _validated_snapshot_hash(snapshot_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    expected = {
        path.relative_to(snapshot_dir).as_posix()
        for path in snapshot_dir.rglob("*")
        if path.is_file() and path.name != _SNAPSHOT_COMPLETE
    }
    observed = {
        path.relative_to(runtime_dir).as_posix()
        for path in runtime_dir.rglob("*")
        if path.is_file()
        and (
            path.relative_to(runtime_dir).parts[0] in _SNAPSHOT_ROOT_NAMES
            or path.relative_to(runtime_dir).parts[0].startswith(_SNAPSHOT_FILE_PREFIXES)
        )
    }
    if observed - expected:
        raise FullRunnerError("warm runtime already contains state outside its snapshot")
    for source in sorted(snapshot_dir.iterdir()):
        if source.name == _SNAPSHOT_COMPLETE:
            continue
        destination = runtime_dir / source.name
        if destination.exists():
            if source.is_file() and destination.is_file() and source.read_bytes() == destination.read_bytes():
                continue
            if source.is_dir() and destination.is_dir():
                if _tree_hash(source) == _tree_hash(destination):
                    continue
            raise FullRunnerError("warm runtime conflicts with its fixed snapshot")
        _copy_path_exclusive(source, destination)
    if _tree_hash(runtime_dir) != digest:
        # The directory must still contain only snapshot state before clients are built.
        raise FullRunnerError("warm runtime snapshot restoration is not byte stable")
    return digest


def _memory_metric(
    definition: FullRunDefinition,
    variant: ExperimentVariant,
    observation: ResearchObservation,
) -> ExperimentMetricResult:
    eligible = int(
        definition.phase == "warm"
        and variant.feature_flags.get("enable_memory") is True
    )
    useful = sum(
        1
        for item in observation.trace.tool_calls
        if item.get("name") == "memory_search" and item.get("output")
    )
    return memory_reuse_metric(
        useful_hits=useful,
        eligible_cases=eligible,
        cross_namespace_errors=0,
        stale_recalls=0,
    )


def _protocol_trace(
    *,
    definition: FullRunDefinition,
    provenance: Literal["live", "fake"],
    snapshot_sha256: str | None,
    runtime_state_sha256: str | None,
    case: MergedEvaluationCase,
    observation: ResearchObservation | None,
    registry: list[str],
    execution_success: bool,
    claim_result: ClaimScorerResult | None = None,
) -> dict[str, Any]:
    claim_results = (
        claim_result.observations_payload if claim_result is not None else []
    )
    return {
        "evaluation_provenance": provenance,
        "expected_output_present": bool((case.expected_output or "").strip()),
        "protocol": {
            "kind": definition.kind,
            "phase": definition.phase,
            "snapshot_sha256": snapshot_sha256,
            "runtime_state_sha256": runtime_state_sha256,
            "pair_id": definition.pair_id,
            "paired_key": definition.paired_key,
        },
        "normalized": (
            observation.trace.model_dump(mode="json") if observation is not None else {}
        ),
        "state_artifacts": (
            observation.state_artifacts if observation is not None else {}
        ),
        "registry": registry,
        "execution_success": execution_success,
        "evaluation_claim_results": sanitize_evaluation_value(claim_results),
        "claim_observations": sanitize_evaluation_value(claim_results),
        "claim_scorer_report_sha256": (
            claim_result.report_sha256 if claim_result is not None else None
        ),
    }


def _known_usage(event: Any | None) -> tuple[int, int, int] | None:
    if event is None or event.total_tokens is None:
        return None
    return (
        int(event.input_tokens or 0),
        int(event.output_tokens or 0),
        int(event.total_tokens),
    )


def _full_failure_record(
    *,
    identity: CalibrationExperimentIdentity,
    dataset_version: str,
    case: MergedEvaluationCase,
    variant: ExperimentVariant,
    definition: FullRunDefinition,
    run_id: str,
    provenance: Literal["live", "fake"],
    snapshot_sha256: str | None,
    runtime_state_sha256: str | None,
    observation: ResearchObservation | None,
    metric_results: list[ExperimentMetricResult],
    failed_step: str,
    error: BaseException,
    terminal_status: str,
    research_usage: tuple[int, int, int] | None,
    judge_usage: tuple[int, int, int] | None,
    research_model_calls: int | None,
    judge_model_calls: int | None,
    actual_registry: list[str],
) -> ExperimentRun:
    fingerprint = _error_fingerprint(failed_step, error)
    now = datetime.now(UTC)
    output = observation.output if observation is not None else None
    complete_metrics = _complete_failure_metrics(
        metric_results,
        reason=f"not executed because {failed_step} failed",
        judge_model=identity.model_ids["judge"],
    )
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    if research_usage is not None and judge_usage is not None:
        input_tokens = research_usage[0] + judge_usage[0]
        output_tokens = research_usage[1] + judge_usage[1]
        total_tokens = input_tokens + output_tokens
    else:
        input_tokens = output_tokens = total_tokens = None
    trace = _protocol_trace(
        definition=definition,
        provenance=provenance,
        snapshot_sha256=snapshot_sha256,
        runtime_state_sha256=runtime_state_sha256,
        case=case,
        observation=observation,
        registry=actual_registry,
        execution_success=False,
    )
    trace.update(
        {
            "failed_step": failed_step,
            "error_type": type(error).__name__,
            "error_fingerprint": fingerprint,
            "terminal_status": terminal_status,
        }
    )
    return ExperimentRun(
        experiment_id=identity.experiment_id,
        run_id=run_id,
        variant_id=variant.variant_id,
        case_id=case.case_id,
        difficulty=case.difficulty,
        repeat=definition.repeat,
        mode="full",
        project_commit=identity.git_head,
        dataset_version=dataset_version,
        scorer_version=CLAIM_SCORER_VERSION,
        output=output,
        output_sha256=(
            hashlib.sha256(output.encode("utf-8")).hexdigest() if output else None
        ),
        trace=trace,
        retrieval_context=(observation.trace.retrieval_context if observation else []),
        telemetry=ExperimentTelemetry(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            research_input_tokens=research_usage[0] if research_usage else None,
            research_output_tokens=research_usage[1] if research_usage else None,
            research_total_tokens=research_usage[2] if research_usage else None,
            judge_input_tokens=judge_usage[0] if judge_usage else None,
            judge_output_tokens=judge_usage[1] if judge_usage else None,
            judge_total_tokens=judge_usage[2] if judge_usage else None,
            retry_tokens=0,
            estimated_cost_usd=None,
            wall_time_ms=observation.telemetry.wall_time_ms if observation else None,
            research_model_calls=research_model_calls,
            judge_model_calls=judge_model_calls,
            tool_calls_by_name=(observation.telemetry.tool_calls_by_name if observation else {}),
            search_calls=observation.search_calls if observation else None,
            researcher_runs=observation.researcher_runs if observation else None,
        ),
        metric_results=complete_metrics,
        status=EvaluationStatus.ERROR,
        error=f"{failed_step}:{type(error).__name__}:{fingerprint}",
        started_at=observation.telemetry.started_at if observation else now,
        finished_at=now,
    )


def _success_record(
    *,
    identity: CalibrationExperimentIdentity,
    dataset_version: str,
    case: MergedEvaluationCase,
    variant: ExperimentVariant,
    definition: FullRunDefinition,
    run_id: str,
    provenance: Literal["live", "fake"],
    snapshot_sha256: str | None,
    runtime_state_sha256: str | None,
    observation: ResearchObservation,
    claim_result: ClaimScorerResult,
    metric_results: list[ExperimentMetricResult],
    research_usage: tuple[int, int, int],
    judge_usage: tuple[int, int, int],
    ledger: LiveTokenReservationLedger,
    actual_registry: list[str],
) -> ExperimentRun:
    judge_input, judge_output, judge_total = judge_usage
    claims = claim_result.to_claim_observations()
    custom = [
        *score_citations(observation.output, claims),
        source_quality_metric(claims),
        source_numbering_metric(observation.output),
        _memory_metric(definition, variant, observation),
        cost_completeness_metric(
            tokens=research_usage[2] + judge_total,
            cost=None,
            pricing_available=False,
        ),
    ]
    metrics = [*metric_results, *custom]
    snapshot = ledger.snapshot()
    run_counts = snapshot["runs"].get(run_id, {})
    dispatched = int(run_counts.get("dispatched_calls", 0))
    research_calls = observation.telemetry.model_calls
    if dispatched < research_calls:
        raise FullRunnerError("token ledger call count is below research telemetry")
    return ExperimentRun(
        experiment_id=identity.experiment_id,
        run_id=run_id,
        variant_id=variant.variant_id,
        case_id=case.case_id,
        difficulty=case.difficulty,
        repeat=definition.repeat,
        mode="full",
        project_commit=identity.git_head,
        dataset_version=dataset_version,
        scorer_version=CLAIM_SCORER_VERSION,
        output=observation.output,
        output_sha256=hashlib.sha256(observation.output.encode("utf-8")).hexdigest(),
        trace=_protocol_trace(
            definition=definition,
            provenance=provenance,
            snapshot_sha256=snapshot_sha256,
            runtime_state_sha256=runtime_state_sha256,
            case=case,
            observation=observation,
            registry=actual_registry,
            execution_success=True,
            claim_result=claim_result,
        ),
        retrieval_context=observation.trace.retrieval_context,
        telemetry=ExperimentTelemetry(
            input_tokens=research_usage[0] + judge_input,
            output_tokens=research_usage[1] + judge_output,
            total_tokens=research_usage[2] + judge_total,
            research_input_tokens=research_usage[0],
            research_output_tokens=research_usage[1],
            research_total_tokens=research_usage[2],
            judge_input_tokens=judge_input,
            judge_output_tokens=judge_output,
            judge_total_tokens=judge_total,
            retry_tokens=snapshot["categories"]["retry"]["committed_tokens"],
            estimated_cost_usd=None,
            wall_time_ms=observation.telemetry.wall_time_ms,
            research_model_calls=research_calls,
            judge_model_calls=dispatched - research_calls,
            tool_calls_by_name=observation.telemetry.tool_calls_by_name,
            search_calls=observation.search_calls,
            researcher_runs=observation.researcher_runs,
        ),
        metric_results=metrics,
        status=_run_status(metrics),
        started_at=observation.telemetry.started_at,
        finished_at=datetime.now(UTC),
    )


def _tracking_error_entry(operation: str, error: BaseException) -> dict[str, Any]:
    return {
        "operation": operation,
        "error_type": type(error).__name__,
        "fingerprint": hashlib.sha256(
            f"tracking:{operation}:{type(error).__name__}".encode()
        ).hexdigest(),
    }


def _write_tracking_errors(state: _TrackingState) -> None:
    if not state.errors:
        return
    lines = "".join(
        json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
        for item in state.errors
    )
    _atomic_write_text(state.output / "tracking-errors.jsonl", lines)


async def _track(
    state: _TrackingState,
    operation: Literal["experiment", "run", "metric"],
    payload: Mapping[str, Any],
) -> None:
    if state.sink is None:
        return
    try:
        method = getattr(state.sink, f"track_{operation}")
        result = method(payload)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, TrackingResult) and result.status == "error":
            state.errors.append(
                sanitize_evaluation_value(result.model_dump(mode="json"))
            )
            _write_tracking_errors(state)
    except BaseException as error:
        state.errors.append(_tracking_error_entry(operation, error))
        _write_tracking_errors(state)


def _failure_circuit_reason(
    records: list[ExperimentRun], plan: Mapping[str, Any]
) -> str | None:
    policy = plan["failure_policy"]
    technical = [
        item for item in records if item.trace.get("execution_success") is False
    ]
    consecutive = 0
    for item in reversed(records):
        if item.trace.get("execution_success") is False:
            consecutive += 1
        else:
            break
    if consecutive >= int(policy["max_consecutive_failed_runs"]):
        return "circuit:consecutive_failures"
    fingerprints = Counter(str(item.trace.get("error_fingerprint")) for item in technical)
    if any(
        count >= int(policy["same_error_signature_limit"])
        for fingerprint, count in fingerprints.items()
        if fingerprint and fingerprint != "None"
    ):
        return "circuit:repeated_error_signature"
    if len(records) >= int(policy["failure_rate_min_runs"]):
        if len(technical) / len(records) > float(policy["max_failure_rate"]):
            return "circuit:failure_rate"
    failed_tokens = [item.telemetry.total_tokens for item in technical]
    if any(item is None for item in failed_tokens):
        return "circuit:unknown_failed_run_usage"
    if sum(int(item or 0) for item in failed_tokens) > int(
        policy["max_failed_run_tokens"]
    ):
        return "circuit:failed_run_token_budget"
    return None


def _dispatch_stop_reason(
    ledger: LiveTokenReservationLedger, plan: Mapping[str, Any]
) -> str | None:
    snapshot = ledger.snapshot()
    if snapshot["unknown_usage"] or snapshot["fail_closed"]:
        return "token_ledger_fail_closed"
    if snapshot["committed_tokens"] >= int(plan["token_budget"]["soft_stop_tokens"]):
        return "soft_token_stop"
    return None


def _recover_durable_steps(
    store: CalibrationJournalStore,
    output: Path,
    records: list[ExperimentRun],
) -> None:
    """Finish journal bookkeeping only when a hashed terminal artifact exists."""
    record_by_id = {item.run_id: item for item in records}
    journal = store.load()
    for plan in journal.runs:
        events = [item for item in store.load().events if item.run_id == plan.run_id]
        research_started = any(
            item.event_type == "started" and item.step_id == plan.research_step_id
            for item in events
        )
        research_terminal = any(item.event_type == "research_completed" for item in events)
        if research_started and not research_terminal:
            path = _research_path(output, plan.run_id)
            if path.is_file():
                observation = _observation_from_payload(_read_hashed_json(path))
                telemetry = observation.telemetry
                store.complete_research(
                    plan.run_id,
                    input_tokens=telemetry.input_tokens,
                    output_tokens=telemetry.output_tokens,
                    total_tokens=telemetry.total_tokens,
                )
        events = [item for item in store.load().events if item.run_id == plan.run_id]
        for metric_name, step_id in plan.judge_step_ids.items():
            started = any(
                item.event_type == "started" and item.step_id == step_id
                for item in events
            )
            terminal = any(
                item.event_type == "judge_metric_terminal" and item.step_id == step_id
                for item in events
            )
            metric_path = _metric_path(output, plan.run_id, metric_name)
            if started and not terminal and metric_path.is_file():
                payload = _read_hashed_json(metric_path)
                if metric_name == CLAIM_SCORER_STEP_NAME:
                    status = str(payload.get("status", ""))
                    if status == "passed":
                        _claim_result_from_payload(payload)
                        fingerprint = None
                    elif status == "error":
                        fingerprint = str(payload.get("error_fingerprint") or "")
                        if len(fingerprint) != 64:
                            raise FullRunnerError(
                                "claim scorer failure artifact has no fingerprint"
                            )
                    else:
                        raise FullRunnerError(
                            "claim scorer artifact has an invalid terminal status"
                        )
                    store.complete_judge_metric(
                        plan.run_id,
                        metric_name,
                        status=status,
                        input_tokens=payload.get("input_tokens"),
                        output_tokens=payload.get("output_tokens"),
                        total_tokens=payload.get("total_tokens"),
                        error_fingerprint=fingerprint,
                    )
                else:
                    result = ExperimentMetricResult.model_validate(payload["result"])
                    store.complete_judge_metric(
                        plan.run_id,
                        metric_name,
                        status=result.status.value,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        total_tokens=result.total_tokens,
                        error_fingerprint=(
                            payload.get("error_fingerprint")
                            if result.status is EvaluationStatus.ERROR
                            else None
                        ),
                    )
                events = [
                    item for item in store.load().events if item.run_id == plan.run_id
                ]
        if any(item.event_type == "run_terminal" for item in events):
            continue
        record = record_by_id.get(plan.run_id)
        if record is None:
            continue
        fingerprint = str(record.trace.get("error_fingerprint") or "") or None
        if record.trace.get("execution_success") is True:
            store.complete_run(plan.run_id, status="completed")
        else:
            store.complete_run(
                plan.run_id,
                status=(
                    "budget_stopped"
                    if record.trace.get("terminal_status") == "budget_stopped"
                    else "failed"
                ),
                error_fingerprint=fingerprint,
            )


def _persist_summary(
    *,
    output: Path,
    identity: CalibrationExperimentIdentity,
    dataset_version: str,
    records: list[ExperimentRun],
    ledger: LiveTokenReservationLedger,
    tracking_errors: list[dict[str, Any]],
    status: str,
    stopped_reason: str | None,
) -> None:
    report = build_full_report(records)
    experiment_path = output / "experiment.json"
    experiment = json.loads(experiment_path.read_text(encoding="utf-8"))
    statistical_limitations = list(report.get("limitations", []))
    acceptance = report.get("acceptance", {})
    unique_run_ids = {item.run_id for item in records}
    all_runs_succeeded = all(
        item.trace.get("execution_success") is True for item in records
    )
    claims = {
        "full_matrix_complete": (
            status == "completed"
            and len(unique_run_ids) == 54
            and len(records) == 54
            and all_runs_succeeded
        ),
        "quality_uplift_established": (
            acceptance.get("T7-3") == "passed"
            and acceptance.get("T7-4") == "passed"
        ),
        "cold_warm_established": acceptance.get("T7-6") == "passed",
    }
    report.update(
        {
            "experiment_id": identity.experiment_id,
            "mode": "full",
            "status": status,
            "full_status": status,
            "planned_runs": 54,
            "completed_run_records": len(records),
            "main_run_records": sum(
                item.trace.get("protocol", {}).get("kind") == "main"
                for item in records
            ),
            "warm_run_records": sum(
                item.trace.get("protocol", {}).get("phase") == "warm"
                for item in records
            ),
            "token_budget": ledger.snapshot(),
            "stopped_reason": stopped_reason,
            "tracking_error_count": len(tracking_errors),
            "calibration_projection": experiment.get("calibration_projection"),
            "claims": claims,
            "limitations": [
                *statistical_limitations,
                "Local artifacts are authoritative; optional tracking is best-effort only.",
                "Unknown dollar pricing remains null rather than zero.",
                "Calibration artifacts are diagnostic inputs, never full-matrix pass evidence.",
            ],
        }
    )
    _retry_permission(lambda: _atomic_write_json(output / "report.json", report))
    _retry_permission(
        lambda: _atomic_write_text(
            output / "report.md",
            render_full_report_markdown(report),
        )
    )
    experiment.update(
        {
            "status": status,
            "completed_run_records": len(records),
            "stopped_reason": stopped_reason,
            "tracking_error_count": len(tracking_errors),
            "claims": claims,
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )
    _retry_permission(lambda: _atomic_write_json(experiment_path, experiment))
    _retry_permission(
        lambda: write_artifact_manifest(
            output,
            experiment_id=identity.experiment_id,
            dataset_version=dataset_version,
            project_commit=identity.git_head,
        )
    )


def _outcome(
    *,
    status: str,
    identity: CalibrationExperimentIdentity,
    records: list[ExperimentRun],
    ledger: LiveTokenReservationLedger,
    output: Path,
    stopped_reason: str | None,
) -> FullOutcome:
    return FullOutcome(
        status=status,
        experiment_id=identity.experiment_id,
        completed_runs=len(records),
        planned_runs=54,
        committed_tokens=int(ledger.snapshot()["committed_tokens"]),
        output_dir=str(output),
        stopped_reason=stopped_reason,
    )


async def _run_full_matrix_locked(
    *,
    project_root: str | Path,
    output_dir: str | Path,
    dataset_version: str = "v1",
    requested_max_tokens: int = 42_000_000,
    resume: bool = False,
    research_executor: ResearchExecutor = execute_live_research,
    metric_factory: MetricFactory = build_live_metric_calls,
    claim_scorer_factory: ClaimScorerFactory = build_live_claim_scorer,
    tracking_sink: TrackingSink | None = None,
    calibration_projection: Mapping[str, Any] | None = None,
    provenance: Literal["live", "fake"] = "live",
    require_deepeval: bool = True,
    environment: Mapping[str, str] | None = None,
) -> FullOutcome:
    """Execute exactly 45 paired main runs plus nine fixed-snapshot warm runs."""
    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    if output.exists() and not resume:
        raise FileExistsError("full output already exists; review it before --resume")
    if resume and not output.is_dir():
        raise FullRunnerError("full resume requires an existing output directory")
    load_dotenv(root / ".env", override=False)
    active_environment = dict(os.environ if environment is None else environment)
    plan_path = root / "tests/evaluation/full_plan.v1.json"
    ablation_path = root / "tests/evaluation/ablations.v1.json"
    plan = load_full_plan(plan_path)
    hard_limit = int(plan["token_budget"]["hard_stop_tokens"])
    soft_limit = int(plan["token_budget"]["soft_stop_tokens"])
    per_run_limit = int(plan["token_budget"]["per_research_run_tokens"])
    if requested_max_tokens != hard_limit or hard_limit != 42_000_000:
        raise FullRunnerError("full evaluation requires the fixed 42000000-token hard limit")
    if soft_limit != 36_000_000 or per_run_limit != 800_000:
        raise FullRunnerError("full evaluation token policy has drifted")
    if dataset_version != plan["dataset_version"]:
        raise FullRunnerError("full evaluation dataset version differs from plan")
    if provenance not in {"live", "fake"}:
        raise FullRunnerError("full evaluation provenance must be live or fake")
    uses_live_execution_core = (
        research_executor is execute_live_research
        and metric_factory is build_live_metric_calls
        and claim_scorer_factory is build_live_claim_scorer
    )
    expected_provenance = "live" if uses_live_execution_core else "fake"
    if provenance != expected_provenance:
        raise FullRunnerError(
            "full evaluation provenance does not match its execution core"
        )
    models = resolve_models(plan, active_environment)
    variants = load_variants(ablation_path)
    # Reuse the frozen retry/runtime fairness checks while selecting all rows.
    validate_calibration_matrix(plan, variants, list(plan["calibration"]["variants"]))
    if tuple(item.variant_id for item in variants) != VARIANT_ORDER:
        raise FullRunnerError("full evaluation requires the exact five-variant order")
    definitions = build_full_run_definitions(plan)
    cases = merge_evaluation_dataset(
        root / "tests/baseline/cases.jsonl",
        root / "tests/evaluation/goldens.v1.jsonl",
        dataset_version=dataset_version,
    )
    by_case = {item.case_id: item for item in cases}
    try:
        selected_cases = [by_case[item] for item in plan["case_ids"]]
    except KeyError as error:
        raise FullRunnerError(f"planned full case is missing: {error.args[0]}") from error
    if any(item.network_policy != "live_allowed" for item in selected_cases):
        raise FullRunnerError("full evaluation contains an offline-only case")
    if require_deepeval and deepeval_version() != EXPECTED_DEEPEVAL_VERSION:
        raise FullRunnerError(
            f"full evaluation requires deepeval=={EXPECTED_DEEPEVAL_VERSION}"
        )

    identity = capture_experiment_identity(
        root,
        plan_path=plan_path,
        ablation_path=ablation_path,
        dataset_id=dataset_version,
        model_ids={
            **models,
            "protocol": "phase7-full-v1",
            "provenance": provenance,
        },
        exclude_untracked_paths=_output_exclusion(root, output),
    )
    runtime_root = _full_private_runtime_root(root, output, identity.experiment_id)
    _claim_runtime_root(
        runtime_root,
        output=output,
        identity=identity,
        resume=resume,
    )
    journal_path = output / "journal.json"
    budget_path = output / "budget.json"
    budget_store = CalibrationBudgetStore(
        budget_path,
        identity=identity,
        hard_token_limit=hard_limit,
        per_run_token_limit=per_run_limit,
    )
    tracking = _TrackingState(sink=tracking_sink, output=output, errors=[])
    ledger: Any
    if resume:
        store = CalibrationJournalStore(journal_path)
        journal = store.load()
        if journal.identity != identity:
            raise FullRunnerError("current source/plan/model identity differs from full journal")
        if not budget_path.is_file():
            raise FullRunnerError("full resume requires its persisted token ledger")
        ledger = _SoftDispatchLedger(
            LiveTokenReservationLedger.from_snapshot(budget_store.load()),
            soft_token_limit=soft_limit,
        )
        budget_store.persist(ledger.snapshot())
        if calibration_projection is not None:
            persisted_experiment = json.loads(
                (output / "experiment.json").read_text(encoding="utf-8")
            )
            if persisted_experiment.get("calibration_projection") != sanitize_evaluation_value(
                calibration_projection
            ):
                raise FullRunnerError(
                    "calibration projection differs from the persisted full experiment"
                )
        tracking_path = output / "tracking-errors.jsonl"
        if tracking_path.is_file():
            tracking.errors = [
                json.loads(line)
                for line in tracking_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
    else:
        output.mkdir(parents=True, exist_ok=False)
        store = CalibrationJournalStore.create(
            journal_path,
            identity=identity,
            runs=[item.to_journal_definition() for item in definitions],
            judge_metric_names=FULL_JUDGE_STEP_NAMES,
        )
        ledger = _SoftDispatchLedger(
            LiveTokenReservationLedger(
                hard_token_limit=hard_limit,
                per_run_token_limit=per_run_limit,
            ),
            soft_token_limit=soft_limit,
        )
        budget_store.create(ledger.snapshot())
        experiment = {
            "schema_version": "1.0",
            "experiment_id": identity.experiment_id,
            "mode": "full",
            "status": "running",
            "dataset_version": dataset_version,
            "case_ids": list(plan["case_ids"]),
            "variants": list(plan["variants"]),
            "repeats": 3,
            "planned_runs": 54,
            "paired_main_runs": 45,
            "additional_warm_runs": 9,
            "soft_token_limit": soft_limit,
            "hard_token_limit": hard_limit,
            "per_run_token_limit": per_run_limit,
            "model_ids": models,
            "provenance": provenance,
            "plan_sha256": identity.plan_sha256,
            "ablation_sha256": identity.ablation_sha256,
            "git_head": identity.git_head,
            "dirty_diff_sha256": identity.dirty_diff_sha256,
            "calibration_projection": sanitize_evaluation_value(
                calibration_projection
                if calibration_projection is not None
                else {
                    "status": "not_supplied",
                    "note": "execution core does not infer calibration success",
                }
            ),
            "claims": {
                "full_matrix_complete": False,
                "quality_uplift_established": False,
                "cold_warm_established": False,
            },
        }
        _atomic_write_json(output / "experiment.json", experiment)
        await _track(tracking, "experiment", experiment)

    def persist_budget(snapshot: dict[str, Any]) -> None:
        _retry_permission(lambda: budget_store.persist(snapshot))

    journal = store.load()
    run_plan_by_key = {
        (item.case_id, item.variant_id, item.repeat): item for item in journal.runs
    }
    records = _load_completed_runs(output)
    _recover_durable_steps(store, output, records)
    journal = store.load()
    record_by_id = {item.run_id: item for item in records}
    records = [
        record_by_id[item.run_id]
        for item in journal.runs
        if item.run_id in record_by_id
    ]
    if records:
        _write_runs(output, records)
    _reconcile_journal_and_budget(store=store, ledger=ledger, records=records)
    try:
        store.assert_resumable()
    except BaseException as error:
        _persist_summary(
            output=output,
            identity=identity,
            dataset_version=dataset_version,
            records=records,
            ledger=ledger,
            tracking_errors=tracking.errors,
            status="stopped",
            stopped_reason=f"resume_state:{type(error).__name__}",
        )
        raise FullRunnerError("full resume is blocked by an unsafe paid-step state") from error

    snapshots_root = runtime_root / "snapshots"
    initial_source = runtime_root / "initial-source"
    initial_source.mkdir(parents=True, exist_ok=True)
    fixed_initial_snapshot = snapshots_root / "fixed-initial"
    fixed_initial_sha256 = _capture_runtime_snapshot(
        initial_source, fixed_initial_snapshot
    )
    case_by_id = {item.case_id: item for item in selected_cases}
    variant_by_id = {item.variant_id: item for item in variants}
    warm_keys = {
        (item.case_id, item.variant_id, item.repeat)
        for item in definitions
        if item.phase == "warm"
    }

    for definition in definitions:
        run_plan = run_plan_by_key[
            (definition.case_id, definition.journal_variant_id, definition.repeat)
        ]
        run_id = run_plan.run_id
        if store.should_skip_run(run_id):
            if run_id not in record_by_id:
                raise FullRunnerError(f"terminal full run lacks artifact: {run_id}")
            continue
        stop = _dispatch_stop_reason(ledger, plan) or _failure_circuit_reason(records, plan)
        if stop:
            _persist_summary(
                output=output,
                identity=identity,
                dataset_version=dataset_version,
                records=records,
                ledger=ledger,
                tracking_errors=tracking.errors,
                status="stopped",
                stopped_reason=stop,
            )
            return _outcome(
                status="stopped",
                identity=identity,
                records=records,
                ledger=ledger,
                output=output,
                stopped_reason=stop,
            )
        case = case_by_id[definition.case_id]
        variant = variant_by_id[definition.variant_id]
        runtime_dir = runtime_root / run_id
        runtime_was_present = runtime_dir.is_dir()
        runtime_dir.mkdir(parents=True, exist_ok=True)
        is_cold_warm_member = (
            definition.phase == "warm"
            or (
                definition.kind == "main"
                and (definition.case_id, definition.variant_id, definition.repeat)
                in warm_keys
            )
        )
        snapshot_sha256 = fixed_initial_sha256 if is_cold_warm_member else None
        runtime_state_sha256: str | None = None
        existing_research = _find_research_event(store, run_id)
        if definition.phase == "warm":
            cold = cold_definition_for(definition, definitions)
            cold_plan = run_plan_by_key[
                (cold.case_id, cold.journal_variant_id, cold.repeat)
            ]
            cold_record = record_by_id.get(cold_plan.run_id)
            if cold_record is None or cold_record.trace.get("execution_success") is not True:
                raise FullRunnerError("warm run requires a successful persisted cold run")
            pair_snapshot = snapshots_root / definition.pair_id
            runtime_state_sha256 = (
                _validated_snapshot_hash(pair_snapshot)
                if existing_research is not None
                else _restore_runtime_snapshot(pair_snapshot, runtime_dir)
            )
            if (
                cold_record.trace.get("protocol", {}).get("runtime_state_sha256")
                != runtime_state_sha256
            ):
                raise FullRunnerError("warm runtime state differs from its cold record")
        elif is_cold_warm_member and existing_research is None:
            restored = _restore_runtime_snapshot(fixed_initial_snapshot, runtime_dir)
            if restored != fixed_initial_sha256:
                raise FullRunnerError("cold run did not use the fixed initial snapshot")
        config = build_variant_config(
            plan=plan,
            variant=variant,
            models=models,
            run_id=run_id,
            runtime_root=runtime_root,
            experiment_id=identity.experiment_id,
        )
        actual_registry = await runtime_tool_names(config)
        if actual_registry != variant.available_tools:
            raise FullRunnerError(f"runtime tool registry drift for {variant.variant_id}")
        if existing_research is not None:
            observation = _observation_from_payload(
                _read_hashed_json(_research_path(output, run_id))
            )
        else:
            inject_governed_runtime(config)
            store.start_research(run_id)
            before = _category_actual(ledger.snapshot(), "research")
            try:
                observation = await research_executor(
                    case=case,
                    config=config,
                    ledger=ledger,
                    evaluation_run_id=run_id,
                    persist_budget=persist_budget,
                    timeout_seconds=variant.budget["timeout_seconds"],
                )
                usage = _delta(before, _category_actual(ledger.snapshot(), "research"))
                if observation.telemetry.total_tokens != usage[2]:
                    raise FullRunnerError("research telemetry and token ledger disagree")
                _retry_permission(
                    lambda: _write_hashed_json(
                        _research_path(output, run_id),
                        _observation_payload(run_id, observation),
                    )
                )
                _retry_permission(
                    lambda: store.complete_research(
                        run_id,
                        input_tokens=usage[0],
                        output_tokens=usage[1],
                        total_tokens=usage[2],
                    )
                )
            except BaseException as error:
                _settle_active_run_reservations(
                    ledger=ledger,
                    run_id=run_id,
                    persist_budget=persist_budget,
                    error_type=type(error).__name__,
                )
                persist_budget(ledger.snapshot())
                known = not ledger.snapshot()["unknown_usage"]
                usage = _delta(before, _category_actual(ledger.snapshot(), "research"))
                terminal = _failure_terminal_status(error)
                failure = _full_failure_record(
                    identity=identity,
                    dataset_version=dataset_version,
                    case=case,
                    variant=variant,
                    definition=definition,
                    run_id=run_id,
                    provenance=provenance,
                    snapshot_sha256=snapshot_sha256,
                    runtime_state_sha256=runtime_state_sha256,
                    observation=None,
                    metric_results=[],
                    failed_step="research",
                    error=error,
                    terminal_status=terminal,
                    research_usage=usage if known else None,
                    judge_usage=(0, 0, 0),
                    research_model_calls=int(
                        ledger.snapshot()["runs"].get(run_id, {}).get("dispatched_calls", 0)
                    ),
                    judge_model_calls=0,
                    actual_registry=actual_registry,
                )
                records = _persist_run_record(
                    output=output,
                    journal_runs=journal.runs,
                    record_by_id=record_by_id,
                    record=failure,
                )
                store.complete_research(
                    run_id,
                    input_tokens=usage[0] if known else None,
                    output_tokens=usage[1] if known else None,
                    total_tokens=usage[2] if known else None,
                    error_fingerprint=str(failure.trace["error_fingerprint"]),
                )
                store.complete_run(
                    run_id,
                    status=terminal,
                    error_fingerprint=str(failure.trace["error_fingerprint"]),
                )
                await _track(tracking, "run", failure.model_dump(mode="json"))
                stop = (
                    f"research:{type(error).__name__}"
                    if isinstance(error, LiveTokenBudgetError)
                    else _dispatch_stop_reason(ledger, plan)
                    or _failure_circuit_reason(records, plan)
                )
                _persist_summary(
                    output=output,
                    identity=identity,
                    dataset_version=dataset_version,
                    records=records,
                    ledger=ledger,
                    tracking_errors=tracking.errors,
                    status="stopped" if stop else "running",
                    stopped_reason=stop,
                )
                if stop:
                    return _outcome(
                        status="stopped",
                        identity=identity,
                        records=records,
                        ledger=ledger,
                        output=output,
                        stopped_reason=stop,
                    )
                continue

        if (
            definition.kind == "main"
            and (definition.case_id, definition.variant_id, definition.repeat)
            in warm_keys
        ):
            pair_snapshot = snapshots_root / definition.pair_id
            if (
                existing_research is not None
                and not pair_snapshot.exists()
                and not runtime_was_present
            ):
                raise FullRunnerError(
                    "completed cold research lacks both runtime state and snapshot"
                )
            runtime_state_sha256 = _capture_runtime_snapshot(
                runtime_dir, pair_snapshot
            )

        try:
            # Coverage is project-owned and free to validate. Do it before any
            # of the seven DeepEval judge calls so an unsupported report shape
            # cannot consume judge tokens and only then fail in the scorer.
            validate_claim_scorer_coverage(
                observation.output,
                batch_size=plan["runtime_limits"]["claim_scorer_batch_size"],
                max_provider_calls=plan["runtime_limits"][
                    "claim_scorer_max_provider_calls"
                ],
            )
            metric_calls = metric_factory(
                case=case,
                variant=variant,
                observation=observation,
                models=models,
                plan=plan,
                ledger=ledger,
                run_id=run_id,
                persist_budget=persist_budget,
                project_root=root,
            )
            if set(metric_calls) != set(FULL_METRIC_NAMES):
                raise FullRunnerError("metric factory did not provide all seven metrics")
            claim_scorer = claim_scorer_factory(
                models=models,
                plan=plan,
                ledger=ledger,
                run_id=run_id,
                persist_budget=persist_budget,
                project_root=root,
            )
            if not isinstance(claim_scorer, ClaimCitationScorer):
                raise FullRunnerError(
                    "claim scorer factory did not provide the async scorer contract"
                )
        except BaseException as error:
            research_usage = _known_usage(_find_research_event(store, run_id))
            failure = _full_failure_record(
                identity=identity,
                dataset_version=dataset_version,
                case=case,
                variant=variant,
                definition=definition,
                run_id=run_id,
                provenance=provenance,
                snapshot_sha256=snapshot_sha256,
                runtime_state_sha256=runtime_state_sha256,
                observation=observation,
                metric_results=[],
                failed_step="judge_setup",
                error=error,
                terminal_status="failed",
                research_usage=research_usage,
                judge_usage=(0, 0, 0),
                research_model_calls=observation.telemetry.model_calls,
                judge_model_calls=0,
                actual_registry=actual_registry,
            )
            records = _persist_run_record(
                output=output,
                journal_runs=journal.runs,
                record_by_id=record_by_id,
                record=failure,
            )
            store.complete_run(
                run_id,
                status="failed",
                error_fingerprint=str(failure.trace["error_fingerprint"]),
            )
            await _track(tracking, "run", failure.model_dump(mode="json"))
            stop = (
                f"judge:{CLAIM_SCORER_STEP_NAME}:{type(error).__name__}"
                if isinstance(error, ClaimScorerError)
                else _failure_circuit_reason(records, plan)
            )
            _persist_summary(
                output=output,
                identity=identity,
                dataset_version=dataset_version,
                records=records,
                ledger=ledger,
                tracking_errors=tracking.errors,
                status="stopped" if stop else "running",
                stopped_reason=stop,
            )
            if stop:
                return _outcome(
                    status="stopped",
                    identity=identity,
                    records=records,
                    ledger=ledger,
                    output=output,
                    stopped_reason=stop,
                )
            continue

        metric_results: list[ExperimentMetricResult] = []
        metric_error: BaseException | None = None
        failed_metric: str | None = None
        for metric_name in FULL_METRIC_NAMES:
            metric_path = _metric_path(output, run_id, metric_name)
            step_id = run_plan.judge_step_ids[metric_name]
            if store.should_skip_metric(step_id):
                recovered_result = ExperimentMetricResult.model_validate(
                    _read_hashed_json(metric_path)["result"]
                )
                metric_results.append(recovered_result)
                if recovered_result.status is EvaluationStatus.ERROR:
                    metric_error = FullRunnerError(
                        "recovered terminal DeepEval judge failure"
                    )
                    failed_metric = metric_name
                    break
                continue
            stop = _dispatch_stop_reason(ledger, plan)
            if stop:
                _persist_summary(
                    output=output,
                    identity=identity,
                    dataset_version=dataset_version,
                    records=records,
                    ledger=ledger,
                    tracking_errors=tracking.errors,
                    status="stopped",
                    stopped_reason=stop,
                )
                return _outcome(
                    status="stopped",
                    identity=identity,
                    records=records,
                    ledger=ledger,
                    output=output,
                    stopped_reason=stop,
                )
            store.start_judge_metric(run_id, metric_name)
            before = _category_actual(ledger.snapshot(), "judge")
            started = time.perf_counter()
            try:
                result = await metric_calls[metric_name]()
                usage = _delta(before, _category_actual(ledger.snapshot(), "judge"))
                result = result.model_copy(
                    update={
                        "input_tokens": usage[0],
                        "output_tokens": usage[1],
                        "total_tokens": usage[2],
                    }
                )
                payload = {
                    "schema_version": "1.0",
                    "run_id": run_id,
                    "metric_name": metric_name,
                    "duration_ms": (time.perf_counter() - started) * 1000,
                    "result": result.model_dump(mode="json"),
                }
                _retry_permission(lambda: _write_hashed_json(metric_path, payload))
                _retry_permission(
                    lambda: store.complete_judge_metric(
                        run_id,
                        metric_name,
                        status=result.status.value,
                        input_tokens=usage[0],
                        output_tokens=usage[1],
                        total_tokens=usage[2],
                    )
                )
                metric_results.append(result)
                await _track(
                    tracking,
                    "metric",
                    {
                        "run_id": run_id,
                        "metric_name": metric_name,
                        **result.model_dump(mode="json"),
                    },
                )
            except BaseException as error:
                usage = _delta(before, _category_actual(ledger.snapshot(), "judge"))
                known = not ledger.snapshot()["unknown_usage"]
                fingerprint = _error_fingerprint(f"judge:{metric_name}", error)
                failed = ExperimentMetricResult(
                    metric_name=metric_name,
                    metric_version=f"deepeval-{EXPECTED_DEEPEVAL_VERSION}",
                    status=EvaluationStatus.ERROR,
                    reason=type(error).__name__,
                    deterministic=False,
                    judge_model=models["judge"],
                    input_tokens=usage[0] if known else None,
                    output_tokens=usage[1] if known else None,
                    total_tokens=usage[2] if known else None,
                )
                _write_hashed_json(
                    metric_path,
                    {
                        "schema_version": "1.0",
                        "run_id": run_id,
                        "metric_name": metric_name,
                        "duration_ms": (time.perf_counter() - started) * 1000,
                        "result": failed.model_dump(mode="json"),
                        "error_fingerprint": fingerprint,
                    },
                )
                store.complete_judge_metric(
                    run_id,
                    metric_name,
                    status="error",
                    input_tokens=usage[0] if known else None,
                    output_tokens=usage[1] if known else None,
                    total_tokens=usage[2] if known else None,
                    error_fingerprint=fingerprint,
                )
                metric_results.append(failed)
                await _track(
                    tracking,
                    "metric",
                    {
                        "run_id": run_id,
                        "metric_name": metric_name,
                        **failed.model_dump(mode="json"),
                    },
                )
                metric_error = error
                failed_metric = metric_name
                break

        if metric_error is not None:
            research_usage = _known_usage(_find_research_event(store, run_id))
            judge_usage = (
                (
                    sum(int(item.input_tokens or 0) for item in metric_results),
                    sum(int(item.output_tokens or 0) for item in metric_results),
                    sum(int(item.total_tokens or 0) for item in metric_results),
                )
                if all(item.total_tokens is not None for item in metric_results)
                else None
            )
            terminal = _failure_terminal_status(metric_error)
            run_counts = ledger.snapshot()["runs"].get(run_id, {})
            failure = _full_failure_record(
                identity=identity,
                dataset_version=dataset_version,
                case=case,
                variant=variant,
                definition=definition,
                run_id=run_id,
                provenance=provenance,
                snapshot_sha256=snapshot_sha256,
                runtime_state_sha256=runtime_state_sha256,
                observation=observation,
                metric_results=metric_results,
                failed_step=f"judge:{failed_metric}",
                error=metric_error,
                terminal_status=terminal,
                research_usage=research_usage,
                judge_usage=judge_usage,
                research_model_calls=observation.telemetry.model_calls,
                judge_model_calls=max(
                    0,
                    int(run_counts.get("dispatched_calls", 0))
                    - observation.telemetry.model_calls,
                ),
                actual_registry=actual_registry,
            )
            records = _persist_run_record(
                output=output,
                journal_runs=journal.runs,
                record_by_id=record_by_id,
                record=failure,
            )
            store.complete_run(
                run_id,
                status=terminal,
                error_fingerprint=str(failure.trace["error_fingerprint"]),
            )
            await _track(tracking, "run", failure.model_dump(mode="json"))
            stop = (
                f"judge:{failed_metric}:{type(metric_error).__name__}"
                if isinstance(metric_error, LiveTokenBudgetError)
                else _dispatch_stop_reason(ledger, plan)
                or _failure_circuit_reason(records, plan)
            )
            _persist_summary(
                output=output,
                identity=identity,
                dataset_version=dataset_version,
                records=records,
                ledger=ledger,
                tracking_errors=tracking.errors,
                status="stopped" if stop else "running",
                stopped_reason=stop,
            )
            if stop:
                return _outcome(
                    status="stopped",
                    identity=identity,
                    records=records,
                    ledger=ledger,
                    output=output,
                    stopped_reason=stop,
                )
            continue

        claim_path = _metric_path(output, run_id, CLAIM_SCORER_STEP_NAME)
        claim_step_id = run_plan.judge_step_ids[CLAIM_SCORER_STEP_NAME]
        claim_result: ClaimScorerResult | None = None
        claim_error: BaseException | None = None
        claim_fingerprint: str | None = None
        claim_terminal_status = "failed"
        if store.should_skip_metric(claim_step_id):
            claim_payload = _read_hashed_json(claim_path)
            if claim_payload.get("status") == "passed":
                claim_result = _claim_result_from_payload(claim_payload)
            else:
                claim_error = FullRunnerError(
                    "recovered terminal claim scorer failure"
                )
                claim_fingerprint = str(
                    claim_payload.get("error_fingerprint") or ""
                )
                claim_terminal_status = str(
                    claim_payload.get("run_terminal_status") or "failed"
                )
        else:
            stop = _dispatch_stop_reason(ledger, plan)
            if stop:
                _persist_summary(
                    output=output,
                    identity=identity,
                    dataset_version=dataset_version,
                    records=records,
                    ledger=ledger,
                    tracking_errors=tracking.errors,
                    status="stopped",
                    stopped_reason=stop,
                )
                return _outcome(
                    status="stopped",
                    identity=identity,
                    records=records,
                    ledger=ledger,
                    output=output,
                    stopped_reason=stop,
                )
            else:
                store.start_judge_metric(run_id, CLAIM_SCORER_STEP_NAME)
                before_claim = _category_actual(ledger.snapshot(), "judge")
                started = time.perf_counter()
                try:
                    claim_result = await claim_scorer.score(
                        prompt=case.prompt,
                        report=observation.output,
                        retrieval_context=observation.trace.retrieval_context,
                    )
                    claim_usage = _delta(
                        before_claim,
                        _category_actual(ledger.snapshot(), "judge"),
                    )
                    _retry_permission(
                        lambda: _write_hashed_json(
                            claim_path,
                            {
                                "schema_version": "1.0",
                                "run_id": run_id,
                                "metric_name": CLAIM_SCORER_STEP_NAME,
                                "status": "passed",
                                "duration_ms": (
                                    time.perf_counter() - started
                                )
                                * 1000,
                                "input_tokens": claim_usage[0],
                                "output_tokens": claim_usage[1],
                                "total_tokens": claim_usage[2],
                                "result": claim_result.model_dump(mode="json"),
                            },
                        )
                    )
                    _retry_permission(
                        lambda: store.complete_judge_metric(
                            run_id,
                            CLAIM_SCORER_STEP_NAME,
                            status="passed",
                            input_tokens=claim_usage[0],
                            output_tokens=claim_usage[1],
                            total_tokens=claim_usage[2],
                        )
                    )
                except BaseException as error:
                    _settle_active_run_reservations(
                        ledger=ledger,
                        run_id=run_id,
                        persist_budget=persist_budget,
                        error_type=type(error).__name__,
                    )
                    snapshot = ledger.snapshot()
                    known = not snapshot["unknown_usage"]
                    claim_usage = _delta(
                        before_claim,
                        _category_actual(snapshot, "judge"),
                    )
                    claim_error = error
                    claim_fingerprint = _error_fingerprint(
                        f"judge:{CLAIM_SCORER_STEP_NAME}", error
                    )
                    claim_terminal_status = _failure_terminal_status(error)
                    _retry_permission(
                        lambda: _write_hashed_json(
                            claim_path,
                            {
                                "schema_version": "1.0",
                                "run_id": run_id,
                                "metric_name": CLAIM_SCORER_STEP_NAME,
                                "status": "error",
                                "run_terminal_status": claim_terminal_status,
                                "duration_ms": (
                                    time.perf_counter() - started
                                )
                                * 1000,
                                "input_tokens": claim_usage[0] if known else None,
                                "output_tokens": claim_usage[1] if known else None,
                                "total_tokens": claim_usage[2] if known else None,
                                "error_fingerprint": claim_fingerprint,
                            },
                        )
                    )
                    _retry_permission(
                        lambda: store.complete_judge_metric(
                            run_id,
                            CLAIM_SCORER_STEP_NAME,
                            status="error",
                            input_tokens=claim_usage[0] if known else None,
                            output_tokens=claim_usage[1] if known else None,
                            total_tokens=claim_usage[2] if known else None,
                            error_fingerprint=claim_fingerprint,
                        )
                    )

        if claim_error is not None:
            if claim_step_id not in set(
                store.resume_summary().completed_metric_step_ids
            ) and not isinstance(claim_error, LiveTokenBudgetError):
                raise FullRunnerError(
                    "claim scorer failure is missing its durable journal terminal"
                )
            if claim_fingerprint is None:
                claim_fingerprint = _error_fingerprint(
                    f"judge:{CLAIM_SCORER_STEP_NAME}", claim_error
                )
            research_usage = _known_usage(_find_research_event(store, run_id))
            judge_usage = _judge_usage_from_store(store, run_id)
            run_counts = ledger.snapshot()["runs"].get(run_id, {})
            failure = _full_failure_record(
                identity=identity,
                dataset_version=dataset_version,
                case=case,
                variant=variant,
                definition=definition,
                run_id=run_id,
                provenance=provenance,
                snapshot_sha256=snapshot_sha256,
                runtime_state_sha256=runtime_state_sha256,
                observation=observation,
                metric_results=metric_results,
                failed_step=f"judge:{CLAIM_SCORER_STEP_NAME}",
                error=claim_error,
                terminal_status=claim_terminal_status,
                research_usage=research_usage,
                judge_usage=judge_usage,
                research_model_calls=observation.telemetry.model_calls,
                judge_model_calls=max(
                    0,
                    int(run_counts.get("dispatched_calls", 0))
                    - observation.telemetry.model_calls,
                ),
                actual_registry=actual_registry,
            )
            failure = failure.model_copy(
                update={
                    "error": (
                        f"judge:{CLAIM_SCORER_STEP_NAME}:"
                        f"{type(claim_error).__name__}:{claim_fingerprint}"
                    ),
                    "trace": {
                        **failure.trace,
                        "error_fingerprint": claim_fingerprint,
                    },
                }
            )
            records = _persist_run_record(
                output=output,
                journal_runs=journal.runs,
                record_by_id=record_by_id,
                record=failure,
            )
            store.complete_run(
                run_id,
                status=(
                    "budget_stopped"
                    if claim_terminal_status == "budget_stopped"
                    else "failed"
                ),
                error_fingerprint=claim_fingerprint,
            )
            await _track(tracking, "run", failure.model_dump(mode="json"))
            stop = (
                f"judge:{CLAIM_SCORER_STEP_NAME}:{type(claim_error).__name__}"
                if isinstance(claim_error, LiveTokenBudgetError | ClaimScorerError)
                else _dispatch_stop_reason(ledger, plan)
                or _failure_circuit_reason(records, plan)
            )
            _persist_summary(
                output=output,
                identity=identity,
                dataset_version=dataset_version,
                records=records,
                ledger=ledger,
                tracking_errors=tracking.errors,
                status="stopped" if stop else "running",
                stopped_reason=stop,
            )
            if stop:
                return _outcome(
                    status="stopped",
                    identity=identity,
                    records=records,
                    ledger=ledger,
                    output=output,
                    stopped_reason=stop,
                )
            continue

        research_usage = _known_usage(_find_research_event(store, run_id))
        if research_usage is None:
            raise FullRunnerError("completed research has unknown token usage")
        if claim_result is None:
            raise FullRunnerError("claim scorer completed without a result")
        judge_usage = _judge_usage_from_store(store, run_id)
        if judge_usage is None:
            raise FullRunnerError("completed judge steps have unknown token usage")
        retry = ledger.snapshot()["categories"]["retry"]
        retry_limit = int(
            hard_limit * float(plan["failure_policy"]["max_retry_token_fraction"])
        )
        if retry["committed_tokens"] > retry_limit:
            raise FullRunnerError("full retry-token ceiling exceeded")
        if retry["dispatched_calls"] != 0:
            raise FullRunnerError("full evaluation forbids retry-category dispatches")
        record = _success_record(
            identity=identity,
            dataset_version=dataset_version,
            case=case,
            variant=variant,
            definition=definition,
            run_id=run_id,
            provenance=provenance,
            snapshot_sha256=snapshot_sha256,
            runtime_state_sha256=runtime_state_sha256,
            observation=observation,
            claim_result=claim_result,
            metric_results=metric_results,
            research_usage=research_usage,
            judge_usage=judge_usage,
            ledger=ledger,
            actual_registry=actual_registry,
        )
        records = _retry_permission(
            lambda: _persist_run_record(
                output=output,
                journal_runs=journal.runs,
                record_by_id=record_by_id,
                record=record,
            )
        )
        _retry_permission(lambda: store.complete_run(run_id, status="completed"))
        await _track(tracking, "run", record.model_dump(mode="json"))
        _persist_summary(
            output=output,
            identity=identity,
            dataset_version=dataset_version,
            records=records,
            ledger=ledger,
            tracking_errors=tracking.errors,
            status="running",
            stopped_reason=None,
        )

    terminal_reason = _dispatch_stop_reason(ledger, plan)
    unique_run_ids = {item.run_id for item in records}
    technical_failures = [
        item for item in records if item.trace.get("execution_success") is not True
    ]
    if terminal_reason is not None:
        terminal_status = "stopped"
    elif technical_failures:
        terminal_status = "stopped"
        terminal_reason = f"terminal_failures:{len(technical_failures)}"
    elif len(unique_run_ids) != len(definitions):
        terminal_status = "stopped"
        terminal_reason = "incomplete_matrix"
    else:
        terminal_status = "completed"
    _persist_summary(
        output=output,
        identity=identity,
        dataset_version=dataset_version,
        records=records,
        ledger=ledger,
        tracking_errors=tracking.errors,
        status=terminal_status,
        stopped_reason=terminal_reason,
    )
    return _outcome(
        status=terminal_status,
        identity=identity,
        records=records,
        ledger=ledger,
        output=output,
        stopped_reason=terminal_reason,
    )


async def run_full_matrix(
    *,
    project_root: str | Path,
    output_dir: str | Path,
    dataset_version: str = "v1",
    requested_max_tokens: int = 42_000_000,
    resume: bool = False,
    research_executor: ResearchExecutor = execute_live_research,
    metric_factory: MetricFactory = build_live_metric_calls,
    claim_scorer_factory: ClaimScorerFactory = build_live_claim_scorer,
    tracking_sink: TrackingSink | None = None,
    calibration_projection: Mapping[str, Any] | None = None,
    provenance: Literal["live", "fake"] = "live",
    require_deepeval: bool = True,
    environment: Mapping[str, str] | None = None,
) -> FullOutcome:
    """Run the fixed matrix while excluding concurrent paid dispatches."""
    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    with _exclusive_full_run_lease(root, output):
        return await _run_full_matrix_locked(
            project_root=root,
            output_dir=output,
            dataset_version=dataset_version,
            requested_max_tokens=requested_max_tokens,
            resume=resume,
            research_executor=research_executor,
            metric_factory=metric_factory,
            claim_scorer_factory=claim_scorer_factory,
            tracking_sink=tracking_sink,
            calibration_projection=calibration_projection,
            provenance=provenance,
            require_deepeval=require_deepeval,
            environment=environment,
        )


__all__ = [
    "FullOutcome",
    "FullRunnerError",
    "run_full_matrix",
]
