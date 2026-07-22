"""Sequential, crash-safe execution of the authorized Phase 7 calibration."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

from open_deep_research.configuration import Configuration
from open_deep_research.evaluation.artifact_safety import (
    redact_evaluation_text,
    sanitize_evaluation_value,
)
from open_deep_research.evaluation.budget import (
    load_full_plan,
    resolve_models,
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
    CalibrationRunDefinition,
    capture_experiment_identity,
)
from open_deep_research.evaluation.claim_scorer import (
    CLAIM_SCORER_STEP_NAME,
    ClaimCitationScorer,
    ClaimScorerResult,
    build_live_qwen_claim_scorer,
    validate_claim_scorer_coverage,
)
from open_deep_research.evaluation.custom_metrics import (
    SCORER_VERSION,
    cost_completeness_metric,
    score_citations,
    source_numbering_metric,
    source_quality_metric,
)
from open_deep_research.evaluation.dataset import merge_evaluation_dataset
from open_deep_research.evaluation.deepeval_adapter import (
    EXPECTED_DEEPEVAL_VERSION,
    _guarded_deepeval_import,
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
    build_full_metrics,
    metric_result_from_deepeval,
)
from open_deep_research.evaluation.live_budget import (
    LiveTokenBudgetError,
    LiveTokenReservationLedger,
    TokenUsageCategory,
)
from open_deep_research.evaluation.live_callbacks import LiveModelBudgetCallback
from open_deep_research.evaluation.live_trace import LiveTraceCollector
from open_deep_research.evaluation.models import RunTelemetry
from open_deep_research.evaluation.process_lease import (
    EvaluationProcessLeaseError,
    evaluation_process_lease,
)
from open_deep_research.evaluation.qwen_judge import (
    JudgeReservation,
    JudgeReservationRequest,
    JudgeTokenUsage,
    QwenJudgeAdapter,
    build_deepeval_qwen_model,
)
from open_deep_research.evaluation.reporting import (
    aggregate_runs,
    render_markdown,
    write_artifact_manifest,
)
from open_deep_research.evaluation.storage import load_jsonl
from open_deep_research.evaluation.telemetry import (
    EvaluationTelemetryCollector,
    ainvoke_with_evaluation_telemetry,
)
from open_deep_research.evaluation.trace_adapter import (
    NormalizedTrace,
    TraceEvent,
    normalize_trace,
    to_deepeval_full_case,
)
from open_deep_research.evaluation.variants import load_variants
from open_deep_research.runtime.graph_factory import open_deep_research_graph


class CalibrationRunnerError(RuntimeError):
    """Calibration could not safely continue to another external call."""


@dataclass(frozen=True, slots=True)
class ResearchObservation:
    """Durable projection of one live graph execution."""

    output: str
    telemetry: RunTelemetry
    trace: NormalizedTrace
    state_artifacts: dict[str, Any]
    researcher_runs: int | None
    search_calls: int


@dataclass(frozen=True, slots=True)
class CalibrationOutcome:
    """Machine-readable terminal result for the CLI."""

    status: str
    experiment_id: str
    completed_runs: int
    planned_runs: int
    committed_tokens: int
    output_dir: str
    stopped_reason: str | None = None


ResearchExecutor = Callable[..., Awaitable[ResearchObservation]]
MetricCall = Callable[[], Awaitable[ExperimentMetricResult]]
MetricFactory = Callable[..., Mapping[str, MetricCall]]
ClaimScorerFactory = Callable[..., ClaimCitationScorer]

_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[Path, threading.RLock] = {}


def _path_lock(path: Path) -> threading.RLock:
    resolved = path.resolve()
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(resolved, threading.RLock())


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    with _path_lock(path):
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            for attempt in range(8):
                try:
                    os.replace(temporary_name, path)
                    break
                except PermissionError:
                    if attempt == 7:
                        raise
                    # Windows antivirus/indexers can briefly hold either the
                    # destination or the freshly fsynced temporary file.  This
                    # retries persistence only; it can never repeat a paid
                    # model/search call.
                    time.sleep(min(0.02 * (attempt + 1), 0.1))
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: Any) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(path, encoded)


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def _write_hashed_json(path: Path, payload: Any) -> None:
    _atomic_write_json(path, payload)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    _atomic_write_text(path.with_suffix(path.suffix + ".sha256"), digest + "\n")


class CalibrationBudgetStore:
    """Persist only monotonic token-ledger snapshots bound to one experiment."""

    def __init__(
        self,
        path: Path,
        *,
        identity: CalibrationExperimentIdentity,
        hard_token_limit: int,
        per_run_token_limit: int,
    ) -> None:
        """Bind expected immutable identity and ceilings before reading state."""
        self.path = path
        self._identity = identity.model_dump(mode="json")
        self._hard_token_limit = hard_token_limit
        self._per_run_token_limit = per_run_token_limit
        self._last_revision = -1
        self._last_digest: str | None = None

    @staticmethod
    def _revision(snapshot: Mapping[str, Any]) -> int:
        revision = snapshot.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise CalibrationRunnerError("token ledger snapshot has no valid revision")
        return revision

    def _payload(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        if snapshot.get("hard_token_limit") != self._hard_token_limit:
            raise CalibrationRunnerError("token ledger hard ceiling drift")
        if snapshot.get("per_run_token_limit") != self._per_run_token_limit:
            raise CalibrationRunnerError("token ledger per-run ceiling drift")
        return {
            "schema_version": "1.0",
            "calibration_identity": self._identity,
            "ledger": dict(snapshot),
        }

    @staticmethod
    def _digest(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def create(self, snapshot: Mapping[str, Any]) -> None:
        """Create the first bound snapshot without overwriting prior evidence."""
        payload = self._payload(snapshot)
        revision = self._revision(snapshot)
        digest = self._digest(payload)
        with _path_lock(self.path):
            if self.path.exists():
                raise FileExistsError(f"token ledger already exists: {self.path}")
            _atomic_write_json(self.path, payload)
            self._last_revision = revision
            self._last_digest = digest

    def load(self) -> dict[str, Any]:
        """Load and validate a snapshot before any resume dispatch."""
        with _path_lock(self.path):
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise CalibrationRunnerError("cannot recover token ledger") from exc
            if not isinstance(payload, dict):
                raise CalibrationRunnerError("token ledger must be a JSON object")
            if set(payload) != {
                "schema_version",
                "calibration_identity",
                "ledger",
            }:
                raise CalibrationRunnerError("token ledger envelope has schema drift")
            if payload.get("schema_version") != "1.0":
                raise CalibrationRunnerError("token ledger envelope version drift")
            if payload.get("calibration_identity") != self._identity:
                raise CalibrationRunnerError("token ledger experiment identity drift")
            ledger = payload.get("ledger")
            if not isinstance(ledger, dict):
                raise CalibrationRunnerError("token ledger payload is invalid")
            normalized = self._payload(ledger)
            revision = self._revision(ledger)
            self._last_revision = revision
            self._last_digest = self._digest(normalized)
            return ledger

    def persist(self, snapshot: Mapping[str, Any]) -> None:
        """Ignore stale concurrent snapshots and reject same-revision divergence."""
        payload = self._payload(snapshot)
        revision = self._revision(snapshot)
        digest = self._digest(payload)
        with _path_lock(self.path):
            if revision < self._last_revision:
                return
            if revision == self._last_revision:
                if digest != self._last_digest:
                    raise CalibrationRunnerError(
                        "token ledger diverged at the same revision"
                    )
                return
            _atomic_write_json(self.path, payload)
            self._last_revision = revision
            self._last_digest = digest


def _read_hashed_json(path: Path) -> dict[str, Any]:
    expected_path = path.with_suffix(path.suffix + ".sha256")
    if not path.is_file() or not expected_path.is_file():
        raise CalibrationRunnerError(f"missing completed-step artifact: {path.name}")
    expected = expected_path.read_text(encoding="utf-8").strip()
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected != actual:
        raise CalibrationRunnerError(f"completed-step artifact hash mismatch: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CalibrationRunnerError(f"invalid completed-step artifact: {path.name}")
    return value


def _error_fingerprint(stage: str, error: BaseException) -> str:
    return hashlib.sha256(f"{stage}:{type(error).__name__}".encode()).hexdigest()


def _failure_terminal_status(
    error: BaseException,
) -> Literal["failed", "budget_stopped"]:
    return (
        "budget_stopped"
        if isinstance(error, LiveTokenBudgetError)
        else "failed"
    )


def _safe_value(value: Any) -> Any:
    return sanitize_evaluation_value(value)


def _state_artifacts(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    id_fields = (
        "requirement_ids",
        "source_ids",
        "evidence_ids",
        "run_evidence_ids",
        "coverage_assessment_ids",
        "retrieval_decision_ids",
        "citation_validation_artifact",
        "citation_claim_ids",
        "citation_validation_result_ids",
        "citation_registry_keys",
    )
    result = {
        name: _safe_value(state[name])
        for name in id_fields
        if state.get(name) is not None
    }
    requirement_set = state.get("requirement_set")
    object_requirements = getattr(requirement_set, "requirements", None)
    if object_requirements is not None:
        result["requirement_count"] = len(object_requirements)
    elif isinstance(requirement_set, dict):
        requirements = requirement_set.get("requirements")
        if isinstance(requirements, list):
            result["requirement_count"] = len(requirements)
    brief = state.get("research_brief")
    if brief:
        normalized = redact_evaluation_text(str(brief))
        result["research_brief_sha256"] = hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest()
    return result


def _extract_output(state: Any) -> str | None:
    if not isinstance(state, dict):
        return redact_evaluation_text(str(state)) if state is not None else None
    report = state.get("final_report")
    if report:
        return redact_evaluation_text(str(report))
    messages = state.get("messages") or []
    if not messages:
        return None
    last = messages[-1]
    if isinstance(last, dict):
        value = str(last.get("content") or "")
    else:
        value = str(getattr(last, "content", ""))
    return redact_evaluation_text(value) or None


def _graph_plan(state: Any) -> Any:
    if not isinstance(state, dict):
        return None
    if state.get("research_brief"):
        return state["research_brief"]
    requirement_set = state.get("requirement_set")
    object_requirements = getattr(requirement_set, "requirements", None)
    if object_requirements is not None:
        return [item.description for item in object_requirements]
    if isinstance(requirement_set, dict):
        return [
            item.get("description", "")
            for item in requirement_set.get("requirements", [])
            if isinstance(item, dict) and item.get("description")
        ]
    return None


def _actual_search_calls(events: list[TraceEvent]) -> int:
    count = 0
    for event in events:
        if event.name in {"tavily_search", "web_search"} and event.status == "completed":
            count += 1
        if event.name != "governed_retrieval" or event.status != "completed":
            continue
        try:
            payload = json.loads(str(event.output))
        except (TypeError, ValueError):
            continue
        web_calls = payload.get("web_call_count") if isinstance(payload, dict) else None
        if isinstance(web_calls, int) and not isinstance(web_calls, bool):
            count += max(web_calls, 0)
    return count


async def execute_live_research(
    *,
    case: MergedEvaluationCase,
    config: dict[str, Any],
    ledger: LiveTokenReservationLedger,
    evaluation_run_id: str,
    persist_budget: Callable[[dict[str, Any]], None],
    timeout_seconds: float,
) -> ResearchObservation:
    """Run one graph with local telemetry, trace, timeout and token reservations."""
    configuration = Configuration.from_runnable_config(config)
    telemetry = EvaluationTelemetryCollector()
    trace = LiveTraceCollector()
    budget = LiveModelBudgetCallback(
        ledger=ledger,
        evaluation_run_id=evaluation_run_id,
        default_output_upper_bound=max(
            configuration.research_model_max_tokens,
            configuration.compression_model_max_tokens,
            configuration.final_report_model_max_tokens,
            configuration.summarization_model_max_tokens,
        ),
        persist_snapshot=persist_budget,
    )
    invocation_config = dict(config)
    invocation_config["callbacks"] = [budget, trace]
    result: Any = None
    async with open_deep_research_graph(configuration) as managed:
        result = await asyncio.wait_for(
            ainvoke_with_evaluation_telemetry(
                managed.graph,
                {"messages": [HumanMessage(content=case.prompt)]},
                invocation_config,
                enabled=True,
                collector=telemetry,
            ),
            timeout=timeout_seconds,
        )
    observed = telemetry.telemetry
    if observed is None:
        raise CalibrationRunnerError("research completed without telemetry")
    if observed.total_tokens is None:
        raise CalibrationRunnerError("research telemetry omitted complete token usage")
    output = _extract_output(result)
    if not output:
        raise CalibrationRunnerError("research completed without a report")
    trace.add_plan(_graph_plan(result))
    events = trace.events
    raw_notes = result.get("raw_notes") if isinstance(result, dict) else None
    researcher_runs = len(raw_notes) if isinstance(raw_notes, list) else None
    return ResearchObservation(
        output=output,
        telemetry=observed,
        trace=normalize_trace(events),
        state_artifacts=_state_artifacts(result),
        researcher_runs=researcher_runs,
        search_calls=_actual_search_calls(events),
    )


class _JudgeLedgerReservation(JudgeReservation):
    def __init__(
        self,
        *,
        ledger: LiveTokenReservationLedger,
        reservation_id: str,
        persist_budget: Callable[[dict[str, Any]], None],
    ) -> None:
        self._ledger = ledger
        self._reservation_id = reservation_id
        self._persist_budget = persist_budget
        self._settled = False

    def settle(
        self, usage: JudgeTokenUsage | None, *, error_type: str | None
    ) -> None:
        if self._settled:
            raise CalibrationRunnerError("judge reservation settled twice")
        self._settled = True
        try:
            if usage is None:
                self._ledger.settle_error(
                    self._reservation_id,
                    error_signature=error_type or "UnknownJudgeError",
                )
            elif error_type is not None:
                self._ledger.settle_known_error(
                    self._reservation_id,
                    actual_input_tokens=usage.input_tokens,
                    actual_output_tokens=usage.output_tokens,
                    error_signature=error_type,
                )
            else:
                self._ledger.settle_success(
                    self._reservation_id,
                    actual_input_tokens=usage.input_tokens,
                    actual_output_tokens=usage.output_tokens,
                )
        except BaseException as settlement_error:
            try:
                self._persist_budget(self._ledger.snapshot())
            except BaseException as persistence_error:
                settlement_error.add_note(
                    "token ledger persistence also failed: "
                    f"{type(persistence_error).__name__}"
                )
            raise
        self._persist_budget(self._ledger.snapshot())


def _judge_reserver(
    *,
    ledger: LiveTokenReservationLedger,
    run_id: str,
    persist_budget: Callable[[dict[str, Any]], None],
) -> Callable[[JudgeReservationRequest], JudgeReservation]:
    def reserve(request: JudgeReservationRequest) -> JudgeReservation:
        reservation = ledger.reserve_before_call(
            run_id=run_id,
            category=TokenUsageCategory.JUDGE,
            input_upper_bound=request.input_upper_bound,
            output_upper_bound=request.output_upper_bound,
            reservation_id=f"judge:{run_id}:{request.call_id}",
        )
        try:
            persist_budget(ledger.snapshot())
        except BaseException:
            ledger.settle_success(
                reservation.reservation_id,
                actual_input_tokens=0,
                actual_output_tokens=0,
            )
            raise
        return _JudgeLedgerReservation(
            ledger=ledger,
            reservation_id=reservation.reservation_id,
            persist_budget=persist_budget,
        )

    return reserve


def build_live_claim_scorer(
    *,
    models: dict[str, str],
    plan: dict[str, Any],
    ledger: LiveTokenReservationLedger,
    run_id: str,
    persist_budget: Callable[[dict[str, Any]], None],
    project_root: Path,
) -> ClaimCitationScorer:
    """Create the uniform metered claim scorer for one evaluation run."""
    return build_live_qwen_claim_scorer(
        audit_model_id=models["judge"],
        dotenv_path=project_root / ".env",
        reservation_callback=_judge_reserver(
            ledger=ledger,
            run_id=run_id,
            persist_budget=persist_budget,
        ),
        max_output_tokens=plan["runtime_limits"]["judge_model_max_tokens"],
        timeout_seconds=60,
        batch_size=plan["runtime_limits"]["claim_scorer_batch_size"],
        max_provider_calls=plan["runtime_limits"][
            "claim_scorer_max_provider_calls"
        ],
    )


def build_live_metric_calls(
    *,
    case: MergedEvaluationCase,
    variant: ExperimentVariant,
    observation: ResearchObservation,
    models: dict[str, str],
    plan: dict[str, Any],
    ledger: LiveTokenReservationLedger,
    run_id: str,
    persist_budget: Callable[[dict[str, Any]], None],
    project_root: Path,
) -> Mapping[str, MetricCall]:
    """Create seven lazy metric calls backed by one metered Qwen judge."""
    adapter = QwenJudgeAdapter(
        audit_model_id=models["judge"],
        dotenv_path=project_root / ".env",
        reservation_callback=_judge_reserver(
            ledger=ledger, run_id=run_id, persist_budget=persist_budget
        ),
        max_output_tokens=plan["runtime_limits"]["judge_model_max_tokens"],
        timeout_seconds=60,
    )
    judge = build_deepeval_qwen_model(adapter)
    with _guarded_deepeval_import():
        test_case = to_deepeval_full_case(
            case,
            observation.output,
            observation.trace,
            available_tools=variant.available_tools,
            variant_id=variant.variant_id,
        )
        metrics = build_full_metrics(
            judge_model=judge,
            available_tool_names=variant.available_tools,
        )

    result: dict[str, MetricCall] = {}
    rag_metrics = {"faithfulness", "contextual_precision", "contextual_recall"}
    for metric_name, metric in zip(FULL_METRIC_NAMES, metrics, strict=True):
        if metric_name in rag_metrics and not observation.trace.retrieval_context:

            async def skipped(name: str = metric_name) -> ExperimentMetricResult:
                return ExperimentMetricResult(
                    metric_name=name,
                    metric_version=f"deepeval-{EXPECTED_DEEPEVAL_VERSION}",
                    status=EvaluationStatus.SKIPPED,
                    reason="no actual retrieval context was emitted by this run",
                    deterministic=False,
                    judge_model=models["judge"],
                )

            result[metric_name] = skipped
            continue

        async def measure(
            active_metric: Any = metric,
        ) -> ExperimentMetricResult:
            with _guarded_deepeval_import():
                measured = active_metric.a_measure(
                    test_case,
                    _show_indicator=False,
                    _log_metric_to_confident=False,
                )
                if inspect.isawaitable(measured):
                    await measured
            return metric_result_from_deepeval(
                active_metric, plan_present=bool(observation.trace.plan)
            )

        result[metric_name] = measure
    return result


def _category_actual(snapshot: dict[str, Any], category: str) -> tuple[int, int]:
    values = snapshot["categories"][category]
    return values["actual_input_tokens"], values["actual_output_tokens"]


def _delta(before: tuple[int, int], after: tuple[int, int]) -> tuple[int, int, int]:
    input_tokens = after[0] - before[0]
    output_tokens = after[1] - before[1]
    if input_tokens < 0 or output_tokens < 0:
        raise CalibrationRunnerError("token ledger moved backward")
    return input_tokens, output_tokens, input_tokens + output_tokens


def _settle_active_run_reservations(
    *,
    ledger: LiveTokenReservationLedger,
    run_id: str,
    persist_budget: Callable[[dict[str, Any]], None],
    error_type: str,
) -> None:
    """Conservatively close callback reservations left by cancellation/crash paths."""
    active = [
        item
        for item in ledger.snapshot()["active_reservations"]
        if item["run_id"] == run_id
    ]
    for item in active:
        ledger.settle_error(
            item["reservation_id"],
            error_signature=error_type,
        )
        persist_budget(ledger.snapshot())


def _observation_payload(run_id: str, observation: ResearchObservation) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "output": observation.output,
        "telemetry": observation.telemetry.model_dump(mode="json"),
        "trace": observation.trace.model_dump(mode="json"),
        "state_artifacts": observation.state_artifacts,
        "researcher_runs": observation.researcher_runs,
        "search_calls": observation.search_calls,
    }


def _observation_from_payload(payload: dict[str, Any]) -> ResearchObservation:
    return ResearchObservation(
        output=str(payload["output"]),
        telemetry=RunTelemetry.model_validate(payload["telemetry"]),
        trace=NormalizedTrace.model_validate(payload["trace"]),
        state_artifacts=dict(payload.get("state_artifacts", {})),
        researcher_runs=payload.get("researcher_runs"),
        search_calls=int(payload.get("search_calls", 0)),
    )


def _metric_path(output: Path, run_id: str, metric_name: str) -> Path:
    return output / "steps" / run_id / "metrics" / f"{metric_name}.json"


def _claim_result_from_payload(payload: Mapping[str, Any]) -> ClaimScorerResult:
    """Recover the immutable scorer result from one hashed paid-step artifact."""
    if payload.get("metric_name") != CLAIM_SCORER_STEP_NAME:
        raise CalibrationRunnerError("claim scorer artifact has the wrong step name")
    try:
        return ClaimScorerResult.model_validate(payload["result"])
    except (KeyError, ValueError) as error:
        raise CalibrationRunnerError("claim scorer artifact is invalid") from error


def _judge_usage_from_store(
    store: CalibrationJournalStore,
    run_id: str,
) -> tuple[int, int, int] | None:
    """Sum every durable judge step, including the claim scorer."""
    events = [
        item
        for item in store.load().events
        if item.run_id == run_id and item.event_type == "judge_metric_terminal"
    ]
    if any(item.total_tokens is None for item in events):
        return None
    return (
        sum(int(item.input_tokens or 0) for item in events),
        sum(int(item.output_tokens or 0) for item in events),
        sum(int(item.total_tokens or 0) for item in events),
    )


def _research_path(output: Path, run_id: str) -> Path:
    return output / "steps" / run_id / "research.json"


def _run_path(output: Path, run_id: str) -> Path:
    return output / "run-records" / f"{run_id}.json"


def _load_completed_runs(output: Path) -> list[ExperimentRun]:
    path = output / "runs.jsonl"
    records = load_jsonl(path, ExperimentRun) if path.is_file() else []
    by_id = {item.run_id: item for item in records}
    run_directory = output / "run-records"
    for artifact in sorted(run_directory.glob("*.json")) if run_directory.is_dir() else []:
        persisted = ExperimentRun.model_validate(_read_hashed_json(artifact))
        existing = by_id.get(persisted.run_id)
        if existing is not None and persisted != existing:
            raise CalibrationRunnerError(
                f"run record and JSONL diverged: {persisted.run_id}"
            )
        by_id[persisted.run_id] = persisted
    return list(by_id.values())


def _reconcile_journal_and_budget(
    *,
    store: CalibrationJournalStore,
    ledger: LiveTokenReservationLedger,
    records: list[ExperimentRun],
) -> None:
    """Reject swapped or understated budget state before another paid call."""
    journal = store.load()
    snapshot = ledger.snapshot()
    planned = {item.run_id for item in journal.runs}
    if len({item.run_id for item in records}) != len(records):
        raise CalibrationRunnerError("duplicate run records in calibration JSONL")
    if any(item.run_id not in planned for item in records):
        raise CalibrationRunnerError("run record is absent from calibration journal")
    if any(item.experiment_id != journal.identity.experiment_id for item in records):
        raise CalibrationRunnerError("run record experiment identity drift")

    if snapshot["unknown_usage"]:
        return
    expected = {
        "research": {"input": 0, "output": 0},
        "judge": {"input": 0, "output": 0},
    }
    expected_by_run = {run_id: 0 for run_id in planned}
    for event in journal.events:
        if event.event_type == "research_completed":
            category = "research"
        elif event.event_type == "judge_metric_terminal":
            category = "judge"
        else:
            continue
        if event.total_tokens is None:
            raise CalibrationRunnerError(
                "journal has unknown usage but token ledger does not"
            )
        expected[category]["input"] += int(event.input_tokens or 0)
        expected[category]["output"] += int(event.output_tokens or 0)
        expected_by_run[event.run_id] += int(event.total_tokens)
    for category, totals in expected.items():
        observed = snapshot["categories"][category]
        if (
            observed["actual_input_tokens"] != totals["input"]
            or observed["actual_output_tokens"] != totals["output"]
        ):
            raise CalibrationRunnerError(
                f"journal and token ledger usage diverged: {category}"
            )
    observed_runs = snapshot["runs"]
    if any(run_id not in planned for run_id in observed_runs):
        raise CalibrationRunnerError("token ledger contains an unplanned run")
    for run_id, expected_tokens in expected_by_run.items():
        observed_tokens = int(
            observed_runs.get(run_id, {}).get("committed_tokens", 0)
        )
        if observed_tokens != expected_tokens:
            raise CalibrationRunnerError(
                f"journal and token ledger per-run usage diverged: {run_id}"
            )
    record_by_id = {item.run_id: item for item in records}
    for run_id, record in record_by_id.items():
        if (
            record.telemetry.total_tokens is not None
            and int(record.telemetry.total_tokens) != expected_by_run[run_id]
        ):
            raise CalibrationRunnerError(
                f"run record and journal token usage diverged: {run_id}"
            )
    if snapshot["committed_tokens"] != (
        snapshot["actual_input_tokens"] + snapshot["actual_output_tokens"]
    ):
        raise CalibrationRunnerError(
            "known token ledger committed total differs from actual usage"
        )


def _write_runs(output: Path, runs: list[ExperimentRun]) -> None:
    lines = "".join(
        json.dumps(item.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        + "\n"
        for item in runs
    )
    _atomic_write_text(output / "runs.jsonl", lines)


def _persist_run_record(
    *,
    output: Path,
    journal_runs: list[Any],
    record_by_id: dict[str, ExperimentRun],
    record: ExperimentRun,
) -> list[ExperimentRun]:
    """Write the content-addressed record before exposing it in JSONL."""
    _write_hashed_json(
        _run_path(output, record.run_id), record.model_dump(mode="json")
    )
    record_by_id[record.run_id] = record
    ordered = [
        record_by_id[item.run_id]
        for item in journal_runs
        if item.run_id in record_by_id
    ]
    _write_runs(output, ordered)
    return ordered


def _run_status(metrics: list[ExperimentMetricResult]) -> EvaluationStatus:
    statuses = {item.status for item in metrics}
    if EvaluationStatus.ERROR in statuses:
        return EvaluationStatus.ERROR
    if EvaluationStatus.FAILED in statuses:
        return EvaluationStatus.FAILED
    if EvaluationStatus.SKIPPED in statuses:
        return EvaluationStatus.SKIPPED
    return EvaluationStatus.PASSED


def _complete_failure_metrics(
    existing: list[ExperimentMetricResult],
    *,
    reason: str,
    judge_model: str,
) -> list[ExperimentMetricResult]:
    """Represent every unexecuted metric as a zero-token skip, never a pass."""
    by_name = {item.metric_name: item for item in existing}
    return [
        by_name.get(name)
        or ExperimentMetricResult(
            metric_name=name,
            metric_version=f"deepeval-{EXPECTED_DEEPEVAL_VERSION}",
            status=EvaluationStatus.SKIPPED,
            reason=reason,
            deterministic=False,
            judge_model=judge_model,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
        )
        for name in FULL_METRIC_NAMES
    ]


def _failure_run_record(
    *,
    identity: CalibrationExperimentIdentity,
    dataset_version: str,
    case: MergedEvaluationCase,
    variant: ExperimentVariant,
    run_id: str,
    observation: ResearchObservation | None,
    metric_results: list[ExperimentMetricResult],
    failed_step: str,
    error_type: str,
    error_fingerprint: str,
    terminal_status: str,
    research_usage: tuple[int, int, int] | None,
    judge_usage: tuple[int, int, int] | None,
    research_model_calls: int | None,
    judge_model_calls: int | None,
    actual_registry: list[str],
) -> ExperimentRun:
    """Build a secret-free machine record before journal terminalization."""
    now = datetime.now(UTC)
    safe_output = observation.output if observation is not None else None
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
        input_tokens = None
        output_tokens = None
        total_tokens = None
    return ExperimentRun(
        experiment_id=identity.experiment_id,
        run_id=run_id,
        variant_id=variant.variant_id,
        case_id=case.case_id,
        difficulty=case.difficulty,
        repeat=1,
        mode="calibration",
        project_commit=identity.git_head,
        dataset_version=dataset_version,
        scorer_version=SCORER_VERSION,
        output=safe_output,
        output_sha256=(
            hashlib.sha256(safe_output.encode("utf-8")).hexdigest()
            if safe_output is not None
            else None
        ),
        trace={
            "evaluation_provenance": identity.model_ids.get("provenance"),
            "execution_success": False,
            "failed_step": failed_step,
            "error_type": error_type,
            "error_fingerprint": error_fingerprint,
            "terminal_status": terminal_status,
            "registry": actual_registry,
            "normalized": (
                observation.trace.model_dump(mode="json")
                if observation is not None
                else {}
            ),
            "state_artifacts": (
                observation.state_artifacts if observation is not None else {}
            ),
        },
        retrieval_context=(
            [redact_evaluation_text(item) for item in observation.trace.retrieval_context]
            if observation is not None
            else []
        ),
        telemetry=ExperimentTelemetry(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            research_input_tokens=(research_usage[0] if research_usage else None),
            research_output_tokens=(research_usage[1] if research_usage else None),
            research_total_tokens=(research_usage[2] if research_usage else None),
            judge_input_tokens=(judge_usage[0] if judge_usage else None),
            judge_output_tokens=(judge_usage[1] if judge_usage else None),
            judge_total_tokens=(judge_usage[2] if judge_usage else None),
            retry_tokens=0,
            estimated_cost_usd=None,
            wall_time_ms=(
                observation.telemetry.wall_time_ms
                if observation is not None
                else None
            ),
            research_model_calls=research_model_calls,
            judge_model_calls=judge_model_calls,
            tool_calls_by_name=(
                observation.telemetry.tool_calls_by_name
                if observation is not None
                else {}
            ),
            search_calls=(observation.search_calls if observation is not None else None),
            researcher_runs=(
                observation.researcher_runs if observation is not None else None
            ),
        ),
        metric_results=complete_metrics,
        status=EvaluationStatus.ERROR,
        error=f"{failed_step}:{error_type}:{error_fingerprint}",
        started_at=(
            observation.telemetry.started_at if observation is not None else now
        ),
        finished_at=now,
    )


def _find_research_event(store: CalibrationJournalStore, run_id: str) -> Any | None:
    return next(
        (
            event
            for event in store.load().events
            if event.run_id == run_id and event.event_type == "research_completed"
        ),
        None,
    )


def _recover_failure_terminal(
    store: CalibrationJournalStore,
    record: ExperimentRun,
    *,
    output: Path,
) -> None:
    """Finish journal-only bookkeeping from a pre-written failure record."""
    if record.status is not EvaluationStatus.ERROR:
        return
    journal = store.load()
    events = [item for item in journal.events if item.run_id == record.run_id]
    if any(item.event_type == "run_terminal" for item in events):
        return
    fingerprint = str(record.trace.get("error_fingerprint") or "")
    if len(fingerprint) != 64:
        raise CalibrationRunnerError("failure record has no valid fingerprint")
    research_terminal = next(
        (item for item in events if item.event_type == "research_completed"),
        None,
    )
    if research_terminal is None:
        telemetry = record.telemetry
        store.complete_research(
            record.run_id,
            input_tokens=telemetry.research_input_tokens,
            output_tokens=telemetry.research_output_tokens,
            total_tokens=telemetry.research_total_tokens,
            error_fingerprint=fingerprint,
        )
    failed_step = str(record.trace.get("failed_step") or "")
    if failed_step.startswith("judge:"):
        metric_name = failed_step.split(":", 1)[1]
        events = [
            item for item in store.load().events if item.run_id == record.run_id
        ]
        already_terminal = any(
            item.event_type == "judge_metric_terminal"
            and item.metric_name == metric_name
            for item in events
        )
        if not already_terminal:
            if metric_name == CLAIM_SCORER_STEP_NAME:
                payload = _read_hashed_json(
                    _metric_path(
                        output=output,
                        run_id=record.run_id,
                        metric_name=metric_name,
                    )
                )
                store.complete_judge_metric(
                    record.run_id,
                    metric_name,
                    status="error",
                    input_tokens=payload.get("input_tokens"),
                    output_tokens=payload.get("output_tokens"),
                    total_tokens=payload.get("total_tokens"),
                    error_fingerprint=fingerprint,
                )
            else:
                result = next(
                    (
                        item
                        for item in record.metric_results
                        if item.metric_name == metric_name
                    ),
                    None,
                )
                if result is None:
                    raise CalibrationRunnerError(
                        "failure record omits its failed judge metric"
                    )
                store.complete_judge_metric(
                    record.run_id,
                    metric_name,
                    status="error",
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    total_tokens=result.total_tokens,
                    error_fingerprint=fingerprint,
                )
    terminal_status = record.trace.get("terminal_status")
    store.complete_run(
        record.run_id,
        status=("budget_stopped" if terminal_status == "budget_stopped" else "failed"),
        error_fingerprint=fingerprint,
    )


def _recover_durable_paid_steps(
    store: CalibrationJournalStore,
    output: Path,
) -> None:
    """Close a journal gap only from a complete content-hashed step artifact."""
    for plan in store.load().runs:
        events = [item for item in store.load().events if item.run_id == plan.run_id]
        research_started = any(
            item.event_type == "started" and item.step_id == plan.research_step_id
            for item in events
        )
        research_terminal = any(
            item.event_type == "research_completed" for item in events
        )
        research_artifact = _research_path(output, plan.run_id)
        if research_started and not research_terminal and research_artifact.is_file():
            observation = _observation_from_payload(
                _read_hashed_json(research_artifact)
            )
            telemetry = observation.telemetry
            store.complete_research(
                plan.run_id,
                input_tokens=telemetry.input_tokens,
                output_tokens=telemetry.output_tokens,
                total_tokens=telemetry.total_tokens,
            )

        for metric_name, step_id in plan.judge_step_ids.items():
            events = [
                item for item in store.load().events if item.run_id == plan.run_id
            ]
            started = any(
                item.event_type == "started" and item.step_id == step_id
                for item in events
            )
            terminal = any(
                item.event_type == "judge_metric_terminal" and item.step_id == step_id
                for item in events
            )
            artifact = _metric_path(output, plan.run_id, metric_name)
            if not started or terminal or not artifact.is_file():
                continue
            payload = _read_hashed_json(artifact)
            if metric_name == CLAIM_SCORER_STEP_NAME:
                status = str(payload.get("status", ""))
                if status == "passed":
                    _claim_result_from_payload(payload)
                    fingerprint = None
                elif status == "error":
                    fingerprint = str(payload.get("error_fingerprint") or "")
                    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                        raise CalibrationRunnerError(
                            "claim scorer failure artifact has no valid fingerprint"
                        )
                else:
                    raise CalibrationRunnerError(
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
                continue
            result = ExperimentMetricResult.model_validate(payload["result"])
            store.complete_judge_metric(
                plan.run_id,
                metric_name,
                status=result.status.value,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                total_tokens=result.total_tokens,
                error_fingerprint=(
                    str(payload.get("error_fingerprint"))
                    if result.status is EvaluationStatus.ERROR
                    else None
                ),
            )


def _persist_summary(
    *,
    output: Path,
    identity: CalibrationExperimentIdentity,
    dataset_version: str,
    runs: list[ExperimentRun],
    ledger: LiveTokenReservationLedger,
    planned_runs: int,
    status: str,
    stopped_reason: str | None,
) -> None:
    report = aggregate_runs(runs)
    report.update(
        {
            "mode": "calibration",
            "provenance": identity.model_ids.get("provenance"),
            "calibration_status": status,
            "planned_runs": planned_runs,
            "completed_run_records": len(runs),
            "token_budget": ledger.snapshot(),
            "stopped_reason": stopped_reason,
            "limitations": [
                "Calibration covers two variants and one repeat; it is not full-matrix uplift evidence.",
                "No cold/warm conclusion or Phase 7 completion claim is permitted from this artifact.",
                "Unknown dollar pricing remains null rather than being reported as zero.",
            ],
        }
    )
    _atomic_write_json(output / "report.json", report)
    _atomic_write_text(output / "report.md", render_markdown(report))
    experiment_path = output / "experiment.json"
    experiment = (
        json.loads(experiment_path.read_text(encoding="utf-8"))
        if experiment_path.is_file()
        else {}
    )
    experiment.update(
        {
            "status": status,
            "completed_run_records": len(runs),
            "stopped_reason": stopped_reason,
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )
    _atomic_write_json(experiment_path, experiment)
    write_artifact_manifest(
        output,
        experiment_id=identity.experiment_id,
        dataset_version=dataset_version,
        project_commit=identity.git_head,
    )


def _output_exclusion(project_root: Path, output: Path) -> list[str]:
    try:
        return [output.relative_to(project_root).as_posix()]
    except ValueError:
        return []


def _private_runtime_root(
    project_root: Path,
    output: Path,
    experiment_id: str,
) -> Path:
    """Keep SQLite/blob/checkpoint state outside publishable artifacts."""
    output_key = hashlib.sha256(
        str(output.resolve()).replace("\\", "/").casefold().encode("utf-8")
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
        / "phase7-calibration"
        / output_key
        / experiment_id
    )


def _claim_calibration_runtime_root(
    runtime_root: Path,
    *,
    output: Path,
    identity: CalibrationExperimentIdentity,
    resume: bool,
) -> None:
    """Bind mutable calibration state to one exact public output."""
    expected = {
        "schema_version": "1.0",
        "experiment_id": identity.experiment_id,
        "output_path_sha256": hashlib.sha256(
            str(output.resolve()).replace("\\", "/").casefold().encode("utf-8")
        ).hexdigest(),
    }
    marker = runtime_root / ".phase7-calibration-owner.json"
    if resume:
        try:
            observed = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise CalibrationRunnerError(
                "calibration runtime owner marker is missing or invalid"
            ) from error
        if observed != expected:
            raise CalibrationRunnerError(
                "calibration runtime owner differs from the persisted output"
            )
        return
    if runtime_root.exists():
        raise CalibrationRunnerError(
            "calibration runtime root already exists for this output identity"
        )
    runtime_root.mkdir(parents=True, exist_ok=False)
    _atomic_write_json(marker, expected)


async def _run_calibration_locked(
    *,
    project_root: str | Path,
    output_dir: str | Path,
    dataset_version: str,
    variant_ids: list[str] | None,
    requested_max_tokens: int,
    resume: bool = False,
    research_executor: ResearchExecutor = execute_live_research,
    metric_factory: MetricFactory = build_live_metric_calls,
    claim_scorer_factory: ClaimScorerFactory = build_live_claim_scorer,
    provenance: Literal["live", "fake"] = "live",
    require_deepeval: bool = True,
    environment: Mapping[str, str] | None = None,
    evaluation_environment: Mapping[str, Any] | None = None,
    source_attestation: Mapping[str, Any] | None = None,
) -> CalibrationOutcome:
    """Run six authorized steps sequentially and stop on the first unsafe result."""
    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    injected_executor = research_executor is not execute_live_research
    injected_metrics = metric_factory is not build_live_metric_calls
    injected_claim_scorer = claim_scorer_factory is not build_live_claim_scorer
    injected_components = (
        injected_executor,
        injected_metrics,
        injected_claim_scorer,
    )
    if provenance == "live" and any(injected_components):
        raise CalibrationRunnerError(
            "injected calibration executors must use fake provenance"
        )
    if provenance == "fake" and not all(injected_components):
        raise CalibrationRunnerError(
            "fake calibration provenance requires all external executors to be injected"
        )
    load_dotenv(root / ".env", override=False)
    active_environment = dict(os.environ if environment is None else environment)
    plan_path = root / "tests/evaluation/full_plan.v1.json"
    ablation_path = root / "tests/evaluation/ablations.v1.json"
    plan = load_full_plan(plan_path)
    if requested_max_tokens != plan["calibration"]["hard_token_limit"]:
        raise CalibrationRunnerError(
            "calibration must use the explicitly authorized 3000000-token ceiling"
        )
    if dataset_version != plan["dataset_version"]:
        raise CalibrationRunnerError("calibration dataset version differs from plan")
    models = resolve_models(plan, active_environment)
    variants = load_variants(ablation_path)
    selected_variants = validate_calibration_matrix(plan, variants, variant_ids)
    cases = merge_evaluation_dataset(
        root / "tests/baseline/cases.jsonl",
        root / "tests/evaluation/goldens.v1.jsonl",
        dataset_version=dataset_version,
    )
    by_case = {item.case_id: item for item in cases}
    try:
        selected_cases = [by_case[item] for item in plan["calibration"]["case_ids"]]
    except KeyError as exc:
        raise CalibrationRunnerError(f"planned calibration case is missing: {exc.args[0]}") from exc
    if any(case.network_policy != "live_allowed" for case in selected_cases):
        raise CalibrationRunnerError("calibration contains an offline-only case")
    if require_deepeval and deepeval_version() != EXPECTED_DEEPEVAL_VERSION:
        raise CalibrationRunnerError(
            f"calibration requires deepeval=={EXPECTED_DEEPEVAL_VERSION}"
        )

    identity = capture_experiment_identity(
        root,
        plan_path=plan_path,
        ablation_path=ablation_path,
        dataset_id=dataset_version,
        model_ids={
            **models,
            "protocol": "phase7-calibration-v1",
            "provenance": provenance,
        },
        exclude_untracked_paths=_output_exclusion(root, output),
    )
    definitions = [
        CalibrationRunDefinition(
            case_id=case.case_id,
            variant_id=variant.variant_id,
            repeat=1,
        )
        for case in selected_cases
        for variant in selected_variants
    ]
    journal_path = output / "journal.json"
    budget_path = output / "budget.json"
    per_run_token_limit = plan["token_budget"]["per_research_run_tokens"]
    budget_store = CalibrationBudgetStore(
        budget_path,
        identity=identity,
        hard_token_limit=requested_max_tokens,
        per_run_token_limit=per_run_token_limit,
    )
    if output.exists() and not resume:
        raise FileExistsError(
            "calibration output already exists; use --resume only after reviewing its journal"
        )
    if resume and not output.is_dir():
        raise CalibrationRunnerError(
            "calibration resume requires an existing output directory"
        )
    runtime_root = _private_runtime_root(root, output, identity.experiment_id)
    _claim_calibration_runtime_root(
        runtime_root,
        output=output,
        identity=identity,
        resume=resume,
    )
    if resume:
        store = CalibrationJournalStore(journal_path)
        journal = store.load()
        if journal.identity != identity:
            raise CalibrationRunnerError(
                "current source/plan/model identity does not match calibration journal"
            )
        if not budget_path.is_file():
            raise CalibrationRunnerError("resume requires the persisted token ledger")
        ledger = LiveTokenReservationLedger.from_snapshot(
            budget_store.load()
        )
        budget_store.persist(ledger.snapshot())
    else:
        output.mkdir(parents=True, exist_ok=False)
        store = CalibrationJournalStore.create(
            journal_path,
            identity=identity,
            runs=definitions,
            judge_metric_names=FULL_JUDGE_STEP_NAMES,
        )
        ledger = LiveTokenReservationLedger(
            hard_token_limit=requested_max_tokens,
            per_run_token_limit=per_run_token_limit,
        )
        budget_store.create(ledger.snapshot())
        _atomic_write_json(
            output / "experiment.json",
            {
                "schema_version": "1.0",
                "experiment_id": identity.experiment_id,
                "mode": "calibration",
                "status": "running",
                "dataset_version": dataset_version,
                "case_ids": plan["calibration"]["case_ids"],
                "variants": plan["calibration"]["variants"],
                "repeats": 1,
                "planned_runs": len(definitions),
                "hard_token_limit": requested_max_tokens,
                "model_ids": identity.model_ids,
                "provenance": provenance,
                "plan_sha256": identity.plan_sha256,
                "ablation_sha256": identity.ablation_sha256,
                "git_head": identity.git_head,
                "dirty_diff_sha256": identity.dirty_diff_sha256,
                "evaluation_environment": sanitize_evaluation_value(
                    evaluation_environment
                ),
                "source_attestation": sanitize_evaluation_value(
                    source_attestation
                ),
                "claims": {
                    "full_matrix_complete": False,
                    "quality_uplift_established": False,
                    "cold_warm_established": False,
                },
            },
        )

    def persist_budget(snapshot: dict[str, Any]) -> None:
        budget_store.persist(snapshot)

    journal = store.load()
    run_plan_by_key = {
        (item.case_id, item.variant_id, item.repeat): item for item in journal.runs
    }
    case_by_id = {item.case_id: item for item in selected_cases}
    variant_by_id = {item.variant_id: item for item in selected_variants}
    records = _load_completed_runs(output)
    record_by_id = {item.run_id: item for item in records}
    for record in records:
        _recover_failure_terminal(store, record, output=output)
    _recover_durable_paid_steps(store, output)
    journal = store.load()
    records = [
        record_by_id[item.run_id]
        for item in journal.runs
        if item.run_id in record_by_id
    ]
    if records:
        _write_runs(output, records)
    _reconcile_journal_and_budget(store=store, ledger=ledger, records=records)
    stopped_reason: str | None = None

    try:
        store.assert_resumable()
    except BaseException as error:
        stopped_reason = f"resume_state:{type(error).__name__}"
        _persist_summary(
            output=output,
            identity=identity,
            dataset_version=dataset_version,
            runs=records,
            ledger=ledger,
            planned_runs=len(definitions),
            status="stopped",
            stopped_reason=stopped_reason,
        )
        raise CalibrationRunnerError(
            "calibration resume is blocked by an unsafe paid-step state"
        ) from error
    if ledger.snapshot()["fail_closed"]:
        stopped_reason = "resume_state:token_ledger_fail_closed"
        _persist_summary(
            output=output,
            identity=identity,
            dataset_version=dataset_version,
            runs=records,
            ledger=ledger,
            planned_runs=len(definitions),
            status="stopped",
            stopped_reason=stopped_reason,
        )
        raise CalibrationRunnerError("calibration token ledger is fail-closed")

    for index, definition in enumerate(definitions):
        run_plan = run_plan_by_key[
            (definition.case_id, definition.variant_id, definition.repeat)
        ]
        run_id = run_plan.run_id
        if store.should_skip_run(run_id):
            if run_id not in record_by_id:
                raise CalibrationRunnerError(
                    f"terminal journal run lacks its run artifact: {run_id}"
                )
            continue
        store.assert_resumable()
        case = case_by_id[definition.case_id]
        variant = variant_by_id[definition.variant_id]
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
            raise CalibrationRunnerError(
                f"runtime tool registry drift for {variant.variant_id}"
            )
        research_artifact = _research_path(output, run_id)
        existing_research = _find_research_event(store, run_id)
        if existing_research is not None:
            observation = _observation_from_payload(
                _read_hashed_json(research_artifact)
            )
        else:
            inject_governed_runtime(config)
            store.start_research(run_id)
            before_research = _category_actual(ledger.snapshot(), "research")
            try:
                observation = await research_executor(
                    case=case,
                    config=config,
                    ledger=ledger,
                    evaluation_run_id=run_id,
                    persist_budget=persist_budget,
                    timeout_seconds=variant.budget["timeout_seconds"],
                )
                after_research = _category_actual(ledger.snapshot(), "research")
                research_usage = _delta(before_research, after_research)
                if observation.telemetry.total_tokens != research_usage[2]:
                    raise CalibrationRunnerError(
                        "research callback and token ledger totals disagree"
                    )
                _write_hashed_json(
                    research_artifact, _observation_payload(run_id, observation)
                )
                store.complete_research(
                    run_id,
                    input_tokens=research_usage[0],
                    output_tokens=research_usage[1],
                    total_tokens=research_usage[2],
                )
            except BaseException as error:
                _settle_active_run_reservations(
                    ledger=ledger,
                    run_id=run_id,
                    persist_budget=persist_budget,
                    error_type=type(error).__name__,
                )
                snapshot = ledger.snapshot()
                persist_budget(snapshot)
                known = not snapshot["unknown_usage"]
                after_research = _category_actual(snapshot, "research")
                research_usage = _delta(before_research, after_research)
                fingerprint = _error_fingerprint("research", error)
                terminal = _failure_terminal_status(error)
                run_counts = snapshot["runs"].get(run_id, {})
                failure_record = _failure_run_record(
                    identity=identity,
                    dataset_version=dataset_version,
                    case=case,
                    variant=variant,
                    run_id=run_id,
                    observation=None,
                    metric_results=[],
                    failed_step="research",
                    error_type=type(error).__name__,
                    error_fingerprint=fingerprint,
                    terminal_status=terminal,
                    research_usage=research_usage if known else None,
                    judge_usage=(0, 0, 0),
                    research_model_calls=run_counts.get("dispatched_calls", 0),
                    judge_model_calls=0,
                    actual_registry=actual_registry,
                )
                records = _persist_run_record(
                    output=output,
                    journal_runs=journal.runs,
                    record_by_id=record_by_id,
                    record=failure_record,
                )
                store.complete_research(
                    run_id,
                    input_tokens=research_usage[0] if known else None,
                    output_tokens=research_usage[1] if known else None,
                    total_tokens=research_usage[2] if known else None,
                    error_fingerprint=fingerprint,
                )
                store.complete_run(run_id, status=terminal, error_fingerprint=fingerprint)
                stopped_reason = f"research:{type(error).__name__}"
                _persist_summary(
                    output=output,
                    identity=identity,
                    dataset_version=dataset_version,
                    runs=records,
                    ledger=ledger,
                    planned_runs=len(definitions),
                    status="stopped",
                    stopped_reason=stopped_reason,
                )
                return CalibrationOutcome(
                    status="stopped",
                    experiment_id=identity.experiment_id,
                    completed_runs=len(records),
                    planned_runs=len(definitions),
                    committed_tokens=ledger.snapshot()["committed_tokens"],
                    output_dir=str(output),
                    stopped_reason=stopped_reason,
                )

        try:
            # Reject unsupported report shapes before constructing or dispatching
            # any of the seven paid DeepEval judge metrics. The scorer repeats
            # this deterministic projection later as its own integrity check.
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
                raise CalibrationRunnerError(
                    "metric factory did not provide all seven metrics"
                )
            claim_scorer = claim_scorer_factory(
                models=models,
                plan=plan,
                ledger=ledger,
                run_id=run_id,
                persist_budget=persist_budget,
                project_root=root,
            )
            if not isinstance(claim_scorer, ClaimCitationScorer):
                raise CalibrationRunnerError(
                    "claim scorer factory did not provide the async scorer contract"
                )
        except BaseException as error:
            fingerprint = _error_fingerprint("judge_setup", error)
            research_event = _find_research_event(store, run_id)
            research_usage_for_failure = (
                (
                    int(research_event.input_tokens or 0),
                    int(research_event.output_tokens or 0),
                    int(research_event.total_tokens),
                )
                if research_event is not None
                and research_event.total_tokens is not None
                else None
            )
            snapshot = ledger.snapshot()
            run_counts = snapshot["runs"].get(run_id, {})
            terminal = _failure_terminal_status(error)
            failure_record = _failure_run_record(
                identity=identity,
                dataset_version=dataset_version,
                case=case,
                variant=variant,
                run_id=run_id,
                observation=observation,
                metric_results=[],
                failed_step="judge_setup",
                error_type=type(error).__name__,
                error_fingerprint=fingerprint,
                terminal_status=terminal,
                research_usage=research_usage_for_failure,
                judge_usage=(0, 0, 0),
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
                record=failure_record,
            )
            store.complete_run(
                run_id, status=terminal, error_fingerprint=fingerprint
            )
            stopped_reason = f"judge_setup:{type(error).__name__}"
            _persist_summary(
                output=output,
                identity=identity,
                dataset_version=dataset_version,
                runs=records,
                ledger=ledger,
                planned_runs=len(definitions),
                status="stopped",
                stopped_reason=stopped_reason,
            )
            return CalibrationOutcome(
                status="stopped",
                experiment_id=identity.experiment_id,
                completed_runs=len(records),
                planned_runs=len(definitions),
                committed_tokens=ledger.snapshot()["committed_tokens"],
                output_dir=str(output),
                stopped_reason=stopped_reason,
            )
        metric_results: list[ExperimentMetricResult] = []
        metric_failed = False
        for metric_name in FULL_METRIC_NAMES:
            metric_path = _metric_path(output, run_id, metric_name)
            step_id = run_plan.judge_step_ids[metric_name]
            if store.should_skip_metric(step_id):
                recovered_result = ExperimentMetricResult.model_validate(
                    _read_hashed_json(metric_path)["result"]
                )
                metric_results.append(recovered_result)
                if recovered_result.status is EvaluationStatus.ERROR:
                    error = CalibrationRunnerError(
                        "recovered terminal DeepEval judge failure"
                    )
                    fingerprint = str(
                        _read_hashed_json(metric_path).get("error_fingerprint") or ""
                    )
                    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                        raise CalibrationRunnerError(
                            "recovered judge failure has no durable fingerprint"
                        )
                    research_event = _find_research_event(store, run_id)
                    research_usage_for_failure = (
                        (
                            int(research_event.input_tokens or 0),
                            int(research_event.output_tokens or 0),
                            int(research_event.total_tokens),
                        )
                        if research_event is not None
                        and research_event.total_tokens is not None
                        else None
                    )
                    run_counts = ledger.snapshot()["runs"].get(run_id, {})
                    failure_record = _failure_run_record(
                        identity=identity,
                        dataset_version=dataset_version,
                        case=case,
                        variant=variant,
                        run_id=run_id,
                        observation=observation,
                        metric_results=metric_results,
                        failed_step=f"judge:{metric_name}",
                        error_type=type(error).__name__,
                        error_fingerprint=fingerprint,
                        terminal_status="failed",
                        research_usage=research_usage_for_failure,
                        judge_usage=_judge_usage_from_store(store, run_id),
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
                        record=failure_record,
                    )
                    store.complete_run(
                        run_id,
                        status="failed",
                        error_fingerprint=fingerprint,
                    )
                    stopped_reason = f"judge:{metric_name}:recovered_failure"
                    metric_failed = True
                    break
                continue
            store.start_judge_metric(run_id, metric_name)
            before_judge = _category_actual(ledger.snapshot(), "judge")
            started = time.perf_counter()
            try:
                result = await metric_calls[metric_name]()
                after_judge = _category_actual(ledger.snapshot(), "judge")
                judge_usage = _delta(before_judge, after_judge)
                result = result.model_copy(
                    update={
                        "input_tokens": judge_usage[0],
                        "output_tokens": judge_usage[1],
                        "total_tokens": judge_usage[2],
                    }
                )
                _write_hashed_json(
                    metric_path,
                    {
                        "schema_version": "1.0",
                        "run_id": run_id,
                        "metric_name": metric_name,
                        "duration_ms": (time.perf_counter() - started) * 1000,
                        "result": result.model_dump(mode="json"),
                    },
                )
                store.complete_judge_metric(
                    run_id,
                    metric_name,
                    status=result.status.value,
                    input_tokens=judge_usage[0],
                    output_tokens=judge_usage[1],
                    total_tokens=judge_usage[2],
                )
                metric_results.append(result)
            except BaseException as error:
                snapshot = ledger.snapshot()
                known = not snapshot["unknown_usage"]
                after_judge = _category_actual(snapshot, "judge")
                judge_usage = _delta(before_judge, after_judge)
                fingerprint = _error_fingerprint(f"judge:{metric_name}", error)
                failed = ExperimentMetricResult(
                    metric_name=metric_name,
                    metric_version=f"deepeval-{EXPECTED_DEEPEVAL_VERSION}",
                    status=EvaluationStatus.ERROR,
                    reason=type(error).__name__,
                    deterministic=False,
                    judge_model=models["judge"],
                    input_tokens=judge_usage[0] if known else None,
                    output_tokens=judge_usage[1] if known else None,
                    total_tokens=judge_usage[2] if known else None,
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
                metric_results.append(failed)
                research_event = _find_research_event(store, run_id)
                research_usage_for_failure = (
                    (
                        int(research_event.input_tokens or 0),
                        int(research_event.output_tokens or 0),
                        int(research_event.total_tokens),
                    )
                    if research_event is not None
                    and research_event.total_tokens is not None
                    else None
                )
                judge_usage_for_failure = (
                    (
                        sum(int(item.input_tokens or 0) for item in metric_results),
                        sum(int(item.output_tokens or 0) for item in metric_results),
                        sum(int(item.total_tokens or 0) for item in metric_results),
                    )
                    if all(item.total_tokens is not None for item in metric_results)
                    else None
                )
                run_counts = snapshot["runs"].get(run_id, {})
                research_calls = observation.telemetry.model_calls
                dispatched_calls = int(run_counts.get("dispatched_calls", 0))
                terminal = _failure_terminal_status(error)
                failure_record = _failure_run_record(
                    identity=identity,
                    dataset_version=dataset_version,
                    case=case,
                    variant=variant,
                    run_id=run_id,
                    observation=observation,
                    metric_results=metric_results,
                    failed_step=f"judge:{metric_name}",
                    error_type=type(error).__name__,
                    error_fingerprint=fingerprint,
                    terminal_status=terminal,
                    research_usage=research_usage_for_failure,
                    judge_usage=judge_usage_for_failure,
                    research_model_calls=research_calls,
                    judge_model_calls=max(0, dispatched_calls - research_calls),
                    actual_registry=actual_registry,
                )
                records = _persist_run_record(
                    output=output,
                    journal_runs=journal.runs,
                    record_by_id=record_by_id,
                    record=failure_record,
                )
                store.complete_judge_metric(
                    run_id,
                    metric_name,
                    status="error",
                    input_tokens=judge_usage[0] if known else None,
                    output_tokens=judge_usage[1] if known else None,
                    total_tokens=judge_usage[2] if known else None,
                    error_fingerprint=fingerprint,
                )
                store.complete_run(
                    run_id, status=terminal, error_fingerprint=fingerprint
                )
                stopped_reason = f"judge:{metric_name}:{type(error).__name__}"
                metric_failed = True
                break
        if metric_failed:
            _persist_summary(
                output=output,
                identity=identity,
                dataset_version=dataset_version,
                runs=records,
                ledger=ledger,
                planned_runs=len(definitions),
                status="stopped",
                stopped_reason=stopped_reason,
            )
            return CalibrationOutcome(
                status="stopped",
                experiment_id=identity.experiment_id,
                completed_runs=len(records),
                planned_runs=len(definitions),
                committed_tokens=ledger.snapshot()["committed_tokens"],
                output_dir=str(output),
                stopped_reason=stopped_reason,
            )

        claim_path = _metric_path(output, run_id, CLAIM_SCORER_STEP_NAME)
        claim_step_id = run_plan.judge_step_ids[CLAIM_SCORER_STEP_NAME]
        claim_result: ClaimScorerResult | None = None
        claim_error: BaseException | None = None
        claim_error_fingerprint: str | None = None
        claim_terminal_status = "failed"
        if store.should_skip_metric(claim_step_id):
            claim_payload = _read_hashed_json(claim_path)
            if claim_payload.get("status") == "passed":
                claim_result = _claim_result_from_payload(claim_payload)
            else:
                claim_error = CalibrationRunnerError(
                    "recovered terminal claim scorer failure"
                )
                claim_error_fingerprint = str(
                    claim_payload.get("error_fingerprint") or ""
                )
                claim_terminal_status = str(
                    claim_payload.get("run_terminal_status") or "failed"
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
                _write_hashed_json(
                    claim_path,
                    {
                        "schema_version": "1.0",
                        "run_id": run_id,
                        "metric_name": CLAIM_SCORER_STEP_NAME,
                        "status": "passed",
                        "duration_ms": (time.perf_counter() - started) * 1000,
                        "input_tokens": claim_usage[0],
                        "output_tokens": claim_usage[1],
                        "total_tokens": claim_usage[2],
                        "result": claim_result.model_dump(mode="json"),
                    },
                )
                store.complete_judge_metric(
                    run_id,
                    CLAIM_SCORER_STEP_NAME,
                    status="passed",
                    input_tokens=claim_usage[0],
                    output_tokens=claim_usage[1],
                    total_tokens=claim_usage[2],
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
                claim_error_fingerprint = _error_fingerprint(
                    f"judge:{CLAIM_SCORER_STEP_NAME}", error
                )
                claim_terminal_status = _failure_terminal_status(error)
                _write_hashed_json(
                    claim_path,
                    {
                        "schema_version": "1.0",
                        "run_id": run_id,
                        "metric_name": CLAIM_SCORER_STEP_NAME,
                        "status": "error",
                        "run_terminal_status": claim_terminal_status,
                        "duration_ms": (time.perf_counter() - started) * 1000,
                        "input_tokens": claim_usage[0] if known else None,
                        "output_tokens": claim_usage[1] if known else None,
                        "total_tokens": claim_usage[2] if known else None,
                        "error_fingerprint": claim_error_fingerprint,
                    },
                )
                store.complete_judge_metric(
                    run_id,
                    CLAIM_SCORER_STEP_NAME,
                    status="error",
                    input_tokens=claim_usage[0] if known else None,
                    output_tokens=claim_usage[1] if known else None,
                    total_tokens=claim_usage[2] if known else None,
                    error_fingerprint=claim_error_fingerprint,
                )

        if claim_error is not None:
            if not re.fullmatch(r"[0-9a-f]{64}", claim_error_fingerprint or ""):
                raise CalibrationRunnerError(
                    "claim scorer failure has no durable fingerprint"
                )
            research_event = _find_research_event(store, run_id)
            research_usage_for_failure = (
                (
                    int(research_event.input_tokens or 0),
                    int(research_event.output_tokens or 0),
                    int(research_event.total_tokens),
                )
                if research_event is not None
                and research_event.total_tokens is not None
                else None
            )
            snapshot = ledger.snapshot()
            run_counts = snapshot["runs"].get(run_id, {})
            failure_record = _failure_run_record(
                identity=identity,
                dataset_version=dataset_version,
                case=case,
                variant=variant,
                run_id=run_id,
                observation=observation,
                metric_results=metric_results,
                failed_step=f"judge:{CLAIM_SCORER_STEP_NAME}",
                error_type=type(claim_error).__name__,
                error_fingerprint=claim_error_fingerprint,
                terminal_status=claim_terminal_status,
                research_usage=research_usage_for_failure,
                judge_usage=_judge_usage_from_store(store, run_id),
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
                record=failure_record,
            )
            store.complete_run(
                run_id,
                status=(
                    "budget_stopped"
                    if claim_terminal_status == "budget_stopped"
                    else "failed"
                ),
                error_fingerprint=claim_error_fingerprint,
            )
            stopped_reason = (
                f"judge:{CLAIM_SCORER_STEP_NAME}:{type(claim_error).__name__}"
            )
            _persist_summary(
                output=output,
                identity=identity,
                dataset_version=dataset_version,
                runs=records,
                ledger=ledger,
                planned_runs=len(definitions),
                status="stopped",
                stopped_reason=stopped_reason,
            )
            return CalibrationOutcome(
                status="stopped",
                experiment_id=identity.experiment_id,
                completed_runs=len(records),
                planned_runs=len(definitions),
                committed_tokens=ledger.snapshot()["committed_tokens"],
                output_dir=str(output),
                stopped_reason=stopped_reason,
            )

        research_event = _find_research_event(store, run_id)
        assert research_event is not None and research_event.total_tokens is not None
        if claim_result is None:
            raise CalibrationRunnerError("claim scorer completed without a result")
        durable_judge_usage = _judge_usage_from_store(store, run_id)
        if durable_judge_usage is None:
            raise CalibrationRunnerError("completed judge steps have unknown token usage")
        judge_input, judge_output, judge_total = durable_judge_usage
        research_input = int(research_event.input_tokens or 0)
        research_output = int(research_event.output_tokens or 0)
        claims = claim_result.to_claim_observations()
        custom_metrics = [
            *score_citations(observation.output, claims),
            source_quality_metric(claims),
            source_numbering_metric(observation.output),
            cost_completeness_metric(
                tokens=research_input + research_output + judge_total,
                cost=None,
                pricing_available=False,
            ),
        ]
        all_metrics = [*metric_results, *custom_metrics]
        ledger_snapshot = ledger.snapshot()
        retry_category = ledger_snapshot["categories"]["retry"]
        retry_limit = int(
            requested_max_tokens
            * float(plan["failure_policy"]["max_retry_token_fraction"])
        )
        if retry_category["committed_tokens"] > retry_limit:
            raise CalibrationRunnerError("calibration retry-token ceiling exceeded")
        if retry_category["dispatched_calls"] != 0:
            raise CalibrationRunnerError(
                "calibration forbids paid retry-category dispatches"
            )
        run_counts = ledger_snapshot["runs"].get(run_id, {})
        dispatched_calls = int(run_counts.get("dispatched_calls", 0))
        research_model_calls = observation.telemetry.model_calls
        if dispatched_calls < research_model_calls:
            raise CalibrationRunnerError(
                "token ledger call count is below research telemetry"
            )
        judge_model_calls = dispatched_calls - research_model_calls
        now = datetime.now(UTC)
        report = ExperimentRun(
            experiment_id=identity.experiment_id,
            run_id=run_id,
            variant_id=variant.variant_id,
            case_id=case.case_id,
            difficulty=case.difficulty,
            repeat=1,
            mode="calibration",
            project_commit=identity.git_head,
            dataset_version=dataset_version,
            scorer_version=SCORER_VERSION,
            output=observation.output,
            output_sha256=hashlib.sha256(observation.output.encode()).hexdigest(),
            trace={
                "evaluation_provenance": provenance,
                "normalized": observation.trace.model_dump(mode="json"),
                "state_artifacts": observation.state_artifacts,
                "registry": actual_registry,
                "execution_success": True,
                "evaluation_claim_results": sanitize_evaluation_value(
                    claim_result.observations_payload
                ),
                "claim_observations": sanitize_evaluation_value(
                    claim_result.observations_payload
                ),
            },
            retrieval_context=observation.trace.retrieval_context,
            telemetry=ExperimentTelemetry(
                input_tokens=research_input + judge_input,
                output_tokens=research_output + judge_output,
                total_tokens=research_input
                + research_output
                + judge_input
                + judge_output,
                research_input_tokens=research_input,
                research_output_tokens=research_output,
                research_total_tokens=research_input + research_output,
                judge_input_tokens=judge_input,
                judge_output_tokens=judge_output,
                judge_total_tokens=judge_total,
                retry_tokens=retry_category["committed_tokens"],
                estimated_cost_usd=None,
                wall_time_ms=observation.telemetry.wall_time_ms,
                research_model_calls=research_model_calls,
                judge_model_calls=judge_model_calls,
                tool_calls_by_name=observation.telemetry.tool_calls_by_name,
                search_calls=observation.search_calls,
                researcher_runs=observation.researcher_runs,
            ),
            metric_results=all_metrics,
            status=_run_status(all_metrics),
            started_at=observation.telemetry.started_at,
            finished_at=now,
        )
        records = _persist_run_record(
            output=output,
            journal_runs=journal.runs,
            record_by_id=record_by_id,
            record=report,
        )
        store.complete_run(run_id, status="completed")
        _persist_summary(
            output=output,
            identity=identity,
            dataset_version=dataset_version,
            runs=records,
            ledger=ledger,
            planned_runs=len(definitions),
            status="running" if index + 1 < len(definitions) else "completed",
            stopped_reason=None,
        )

    snapshot = ledger.snapshot()
    return CalibrationOutcome(
        status="completed",
        experiment_id=identity.experiment_id,
        completed_runs=len(records),
        planned_runs=len(definitions),
        committed_tokens=snapshot["committed_tokens"],
        output_dir=str(output),
    )


async def run_calibration(
    *,
    project_root: str | Path,
    output_dir: str | Path,
    dataset_version: str,
    variant_ids: list[str],
    requested_max_tokens: int,
    resume: bool = False,
    research_executor: ResearchExecutor = execute_live_research,
    metric_factory: MetricFactory = build_live_metric_calls,
    claim_scorer_factory: ClaimScorerFactory = build_live_claim_scorer,
    provenance: Literal["live", "fake"] = "live",
    require_deepeval: bool = True,
    environment: Mapping[str, str] | None = None,
    evaluation_environment: Mapping[str, Any] | None = None,
    source_attestation: Mapping[str, Any] | None = None,
) -> CalibrationOutcome:
    """Run calibration while one process exclusively owns the output."""
    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    try:
        with evaluation_process_lease(
            project_root=root,
            output=output,
            kind="calibration",
        ):
            return await _run_calibration_locked(
                project_root=root,
                output_dir=output,
                dataset_version=dataset_version,
                variant_ids=variant_ids,
                requested_max_tokens=requested_max_tokens,
                resume=resume,
                research_executor=research_executor,
                metric_factory=metric_factory,
                claim_scorer_factory=claim_scorer_factory,
                provenance=provenance,
                require_deepeval=require_deepeval,
                environment=environment,
                evaluation_environment=evaluation_environment,
                source_attestation=source_attestation,
            )
    except EvaluationProcessLeaseError as error:
        raise CalibrationRunnerError(str(error)) from error


__all__ = [
    "CalibrationOutcome",
    "CalibrationRunnerError",
    "ResearchObservation",
    "build_live_metric_calls",
    "execute_live_research",
    "run_calibration",
]
