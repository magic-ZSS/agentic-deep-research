"""Secret-free, crash-recoverable state for paid evaluation calibration.

This module deliberately has no graph, model, or network imports.  It provides
the durable identity and journal primitives that a separately authorized runner
can use to avoid repeating paid work after a process interruption.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CALIBRATION_JOURNAL_SCHEMA_VERSION: Literal["1.0"] = "1.0"
_HEX_64 = r"^[0-9a-f]{64}$"
_GIT_HEAD = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
_SAFE_NAME = r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$"
_SECRET_OR_ENDPOINT = re.compile(
    r"(?:"
    r"://|"
    r"\b(?:api[_-]?key|access[_-]?token|auth(?:orization)?|bearer|password|secret)\b|"
    r"\bsk-[A-Za-z0-9_-]{8,}|"
    r"\b(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?\b"
    r")",
    re.IGNORECASE,
)
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[Path, threading.RLock] = {}


class CalibrationStateError(RuntimeError):
    """Base class for invalid or unsafe calibration state."""


class CalibrationJournalError(CalibrationStateError):
    """Raised when journal contents or event ordering are invalid."""


class CalibrationInFlightError(CalibrationStateError):
    """Raised when an interrupted paid step cannot be retried automatically."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}-{_canonical_digest([prefix, *parts])[:32]}"


def sha256_path(path: str | Path) -> str:
    """Hash a file as raw bytes without storing its contents."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_bytes(project_root: Path, *arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=project_root,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CalibrationStateError(
            f"cannot capture git identity with command: git {' '.join(arguments)}"
        ) from exc
    return completed.stdout


def git_head(project_root: str | Path) -> str:
    """Return the exact repository HEAD used by the experiment identity."""
    value = _git_bytes(Path(project_root).resolve(), "rev-parse", "HEAD").decode(
        "ascii", errors="strict"
    ).strip()
    if not re.fullmatch(_GIT_HEAD, value):
        raise CalibrationStateError("git HEAD is not a supported object identifier")
    return value


def _normalize_excluded_paths(paths: Iterable[str | Path]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in paths:
        text = PurePosixPath(str(value).replace("\\", "/")).as_posix().strip("/")
        if not text or text == "." or text.startswith("../") or "/../" in text:
            raise ValueError("excluded untracked paths must stay inside the repository")
        normalized.append(text)
    return tuple(sorted(set(normalized)))


def _is_excluded(relative_path: str, excluded: tuple[str, ...]) -> bool:
    return any(
        relative_path == item or relative_path.startswith(item.rstrip("/") + "/")
        for item in excluded
    )


def dirty_diff_fingerprint(
    project_root: str | Path,
    *,
    exclude_untracked_paths: Iterable[str | Path] = (),
) -> str:
    """Hash tracked changes plus untracked, non-ignored file identities.

    Ignored files (including ``.env``) are never read.  Callers should exclude
    the calibration artifact directory so that writing the journal does not
    change the identity that the journal is intended to preserve.
    """
    root = Path(project_root).resolve()
    excluded = _normalize_excluded_paths(exclude_untracked_paths)
    digest = hashlib.sha256(b"odr-calibration-worktree-v1\0")
    digest.update(_git_bytes(root, "diff", "--binary", "--no-ext-diff", "HEAD", "--"))
    untracked = _git_bytes(
        root, "ls-files", "--others", "--exclude-standard", "-z"
    ).split(b"\0")
    for raw_path in sorted(item for item in untracked if item):
        relative = raw_path.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        if _is_excluded(relative, excluded):
            continue
        candidate = root / Path(relative)
        digest.update(b"untracked\0")
        digest.update(raw_path)
        digest.update(b"\0")
        if candidate.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(candidate).encode("utf-8", errors="surrogateescape"))
        elif candidate.is_file():
            digest.update(bytes.fromhex(sha256_path(candidate)))
        else:
            raise CalibrationStateError(
                f"untracked calibration identity input is not a regular file: {relative}"
            )
    return digest.hexdigest()


def _validate_public_identifier(kind: str, value: str) -> str:
    normalized = value.strip()
    if not re.fullmatch(_SAFE_NAME, normalized):
        raise ValueError(f"{kind} must be a short public identifier")
    if _SECRET_OR_ENDPOINT.search(normalized):
        raise ValueError(f"{kind} must not contain a secret or endpoint")
    return normalized


class CalibrationExperimentIdentity(_StrictModel):
    """Content-derived identity for one calibration experiment."""

    schema_version: Literal["1.0"] = CALIBRATION_JOURNAL_SCHEMA_VERSION
    experiment_id: str = Field(pattern=r"^cal-[0-9a-f]{32}$")
    git_head: str = Field(pattern=_GIT_HEAD)
    dirty_diff_sha256: str = Field(pattern=_HEX_64)
    plan_sha256: str = Field(pattern=_HEX_64)
    ablation_sha256: str = Field(pattern=_HEX_64)
    dataset_id: str = Field(pattern=_SAFE_NAME)
    model_ids: dict[str, str] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_private_configuration(self) -> CalibrationExperimentIdentity:
        """Require canonical public identifiers and verify the derived ID."""
        _validate_public_identifier("dataset_id", self.dataset_id)
        canonical: dict[str, str] = {}
        for role, identifier in self.model_ids.items():
            safe_role = _validate_public_identifier("model role", role)
            safe_identifier = _validate_public_identifier("model identifier", identifier)
            canonical[safe_role] = safe_identifier
        if canonical != dict(sorted(self.model_ids.items())):
            raise ValueError("model_ids must be sorted canonical public identifiers")
        expected = _stable_id(
            "cal",
            self.git_head,
            self.dirty_diff_sha256,
            self.plan_sha256,
            self.ablation_sha256,
            self.dataset_id,
            canonical,
        )
        if self.experiment_id != expected:
            raise ValueError("experiment_id does not match its identity inputs")
        return self


def make_experiment_identity(
    *,
    git_head_value: str,
    dirty_diff_sha256: str,
    plan_sha256: str,
    ablation_sha256: str,
    dataset_id: str,
    model_ids: Mapping[str, str],
) -> CalibrationExperimentIdentity:
    """Build an identity from already captured, secret-free inputs."""
    safe_dataset = _validate_public_identifier("dataset_id", dataset_id)
    safe_models = dict(
        sorted(
            (
                _validate_public_identifier("model role", role),
                _validate_public_identifier("model identifier", identifier),
            )
            for role, identifier in model_ids.items()
        )
    )
    if not safe_models:
        raise ValueError("at least one model identifier is required")
    experiment_id = _stable_id(
        "cal",
        git_head_value,
        dirty_diff_sha256,
        plan_sha256,
        ablation_sha256,
        safe_dataset,
        safe_models,
    )
    return CalibrationExperimentIdentity(
        experiment_id=experiment_id,
        git_head=git_head_value,
        dirty_diff_sha256=dirty_diff_sha256,
        plan_sha256=plan_sha256,
        ablation_sha256=ablation_sha256,
        dataset_id=safe_dataset,
        model_ids=safe_models,
    )


def capture_experiment_identity(
    project_root: str | Path,
    *,
    plan_path: str | Path,
    ablation_path: str | Path,
    dataset_id: str,
    model_ids: Mapping[str, str],
    exclude_untracked_paths: Iterable[str | Path] = (),
) -> CalibrationExperimentIdentity:
    """Capture all immutable inputs before the first paid dispatch."""
    root = Path(project_root).resolve()
    return make_experiment_identity(
        git_head_value=git_head(root),
        dirty_diff_sha256=dirty_diff_fingerprint(
            root, exclude_untracked_paths=exclude_untracked_paths
        ),
        plan_sha256=sha256_path(plan_path),
        ablation_sha256=sha256_path(ablation_path),
        dataset_id=dataset_id,
        model_ids=model_ids,
    )


def stable_run_id(
    identity: CalibrationExperimentIdentity,
    *,
    case_id: str,
    variant_id: str,
    repeat: int,
) -> str:
    """Derive a run ID transitively bound to every experiment identity input."""
    if repeat < 1:
        raise ValueError("repeat must be positive")
    case = _validate_public_identifier("case_id", case_id)
    variant = _validate_public_identifier("variant_id", variant_id)
    return _stable_id("run", identity.experiment_id, case, variant, repeat)


def stable_step_id(
    run_id: str,
    *,
    step_kind: Literal["research", "judge", "run_terminal"],
    metric_name: str | None = None,
) -> str:
    """Derive a stable research, judge, or terminal step ID."""
    if not re.fullmatch(r"^run-[0-9a-f]{32}$", run_id):
        raise ValueError("run_id is not a calibration run identifier")
    if step_kind == "judge":
        if metric_name is None:
            raise ValueError("judge steps require metric_name")
        metric = _validate_public_identifier("metric_name", metric_name)
    elif metric_name is not None:
        raise ValueError("only judge steps accept metric_name")
    else:
        metric = None
    return _stable_id("step", run_id, step_kind, metric)


class CalibrationRunDefinition(_StrictModel):
    """One requested case/variant/repeat tuple before IDs are derived."""

    case_id: str = Field(pattern=_SAFE_NAME)
    variant_id: str = Field(pattern=_SAFE_NAME)
    repeat: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_names(self) -> CalibrationRunDefinition:
        """Reject identifiers that could smuggle private configuration."""
        _validate_public_identifier("case_id", self.case_id)
        _validate_public_identifier("variant_id", self.variant_id)
        return self


class CalibrationRunPlan(_StrictModel):
    """Stable IDs for every paid or terminal step in a run."""

    run_id: str = Field(pattern=r"^run-[0-9a-f]{32}$")
    case_id: str = Field(pattern=_SAFE_NAME)
    variant_id: str = Field(pattern=_SAFE_NAME)
    repeat: int = Field(ge=1)
    research_step_id: str = Field(pattern=r"^step-[0-9a-f]{32}$")
    judge_step_ids: dict[str, str]
    run_terminal_step_id: str = Field(pattern=r"^step-[0-9a-f]{32}$")


EventType = Literal[
    "planned",
    "started",
    "research_completed",
    "judge_metric_terminal",
    "run_terminal",
]
MetricTerminalStatus = Literal["passed", "failed", "skipped", "error"]
RunTerminalStatus = Literal["completed", "failed", "cancelled", "budget_stopped"]


class CalibrationJournalEvent(_StrictModel):
    """A narrowly typed event that cannot carry prompts, outputs, or credentials."""

    event_id: str = Field(pattern=r"^event-[0-9a-f]{32}$")
    sequence: int = Field(ge=1)
    recorded_at: datetime
    event_type: EventType
    run_id: str = Field(pattern=r"^run-[0-9a-f]{32}$")
    step_id: str | None = Field(default=None, pattern=r"^step-[0-9a-f]{32}$")
    metric_name: str | None = Field(default=None, pattern=_SAFE_NAME)
    terminal_status: MetricTerminalStatus | RunTerminalStatus | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    error_fingerprint: str | None = Field(default=None, pattern=_HEX_64)

    @model_validator(mode="after")
    def validate_shape(self) -> CalibrationJournalEvent:
        """Keep each event variant narrow and token accounting explicit."""
        token_values = (self.input_tokens, self.output_tokens, self.total_tokens)
        provided = [value is not None for value in token_values]
        if any(provided) and not all(provided):
            raise ValueError("token usage must be wholly known or wholly unknown")
        if all(provided) and self.total_tokens != self.input_tokens + self.output_tokens:  # type: ignore[operator]
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        if self.event_type == "planned":
            if any(
                value is not None
                for value in (
                    self.step_id,
                    self.metric_name,
                    self.terminal_status,
                    self.input_tokens,
                    self.error_fingerprint,
                )
            ):
                raise ValueError("planned events contain only a run identity")
        elif self.event_type == "started":
            if self.step_id is None or any(
                value is not None
                for value in (
                    self.terminal_status,
                    self.input_tokens,
                    self.error_fingerprint,
                )
            ):
                raise ValueError("started events contain only step identity")
        elif self.event_type == "research_completed":
            if self.step_id is None or self.metric_name is not None or self.terminal_status is not None:
                raise ValueError("research_completed has an invalid shape")
        elif self.event_type == "judge_metric_terminal":
            if self.step_id is None or self.metric_name is None:
                raise ValueError("judge_metric_terminal requires step and metric")
            if self.terminal_status not in {"passed", "failed", "skipped", "error"}:
                raise ValueError("judge metric terminal status is invalid")
        elif self.event_type == "run_terminal":
            if self.step_id is None or self.metric_name is not None:
                raise ValueError("run_terminal requires only its terminal step")
            if self.terminal_status not in {
                "completed",
                "failed",
                "cancelled",
                "budget_stopped",
            }:
                raise ValueError("run terminal status is invalid")
        return self


class CalibrationJournal(_StrictModel):
    """Complete recoverable state, replaced atomically after every event."""

    schema_version: Literal["1.0"] = CALIBRATION_JOURNAL_SCHEMA_VERSION
    identity: CalibrationExperimentIdentity
    runs: list[CalibrationRunPlan] = Field(min_length=1)
    events: list[CalibrationJournalEvent] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_event_chain(self) -> CalibrationJournal:
        """Reject tampering, duplicates, and unsafe event ordering."""
        _validate_event_chain(self)
        return self


class CalibrationResumeSummary(_StrictModel):
    """Machine-readable decision about what may safely resume."""

    experiment_id: str
    completed_run_ids: list[str]
    terminal_noncompleted_run_ids: list[str]
    completed_metric_step_ids: list[str]
    pending_step_ids: list[str]
    blocked_in_flight_step_ids: list[str]
    unknown_usage_step_ids: list[str]
    can_resume: bool

    def assert_resumable(self) -> None:
        """Fail closed rather than repeating an interrupted or unmetered call."""
        unsafe = [*self.blocked_in_flight_step_ids, *self.unknown_usage_step_ids]
        if unsafe:
            raise CalibrationInFlightError(
                "calibration journal contains unsafe paid steps: " + ", ".join(unsafe)
            )


def _run_plan(
    identity: CalibrationExperimentIdentity,
    definition: CalibrationRunDefinition,
    metric_names: tuple[str, ...],
) -> CalibrationRunPlan:
    run_id = stable_run_id(
        identity,
        case_id=definition.case_id,
        variant_id=definition.variant_id,
        repeat=definition.repeat,
    )
    return CalibrationRunPlan(
        run_id=run_id,
        case_id=definition.case_id,
        variant_id=definition.variant_id,
        repeat=definition.repeat,
        research_step_id=stable_step_id(run_id, step_kind="research"),
        judge_step_ids={
            metric: stable_step_id(
                run_id, step_kind="judge", metric_name=metric
            )
            for metric in metric_names
        },
        run_terminal_step_id=stable_step_id(run_id, step_kind="run_terminal"),
    )


def _event_id(event_type: EventType, run_id: str, step_id: str | None) -> str:
    return _stable_id("event", event_type, run_id, step_id)


def _new_event(
    journal: CalibrationJournal,
    *,
    event_type: EventType,
    run_id: str,
    step_id: str | None = None,
    metric_name: str | None = None,
    terminal_status: MetricTerminalStatus | RunTerminalStatus | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    error_fingerprint: str | None = None,
) -> CalibrationJournalEvent:
    return CalibrationJournalEvent(
        event_id=_event_id(event_type, run_id, step_id),
        sequence=len(journal.events) + 1,
        recorded_at=datetime.now(UTC),
        event_type=event_type,
        run_id=run_id,
        step_id=step_id,
        metric_name=metric_name,
        terminal_status=terminal_status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        error_fingerprint=error_fingerprint,
    )


def _validate_run_plan(identity: CalibrationExperimentIdentity, plan: CalibrationRunPlan) -> None:
    expected_run = stable_run_id(
        identity,
        case_id=plan.case_id,
        variant_id=plan.variant_id,
        repeat=plan.repeat,
    )
    if plan.run_id != expected_run:
        raise ValueError("run ID is not bound to the experiment identity")
    if plan.research_step_id != stable_step_id(plan.run_id, step_kind="research"):
        raise ValueError("research step ID is invalid")
    expected_metrics = {
        metric: stable_step_id(plan.run_id, step_kind="judge", metric_name=metric)
        for metric in plan.judge_step_ids
    }
    if plan.judge_step_ids != expected_metrics:
        raise ValueError("judge step IDs are invalid")
    if plan.run_terminal_step_id != stable_step_id(
        plan.run_id, step_kind="run_terminal"
    ):
        raise ValueError("run terminal step ID is invalid")


@dataclass
class _RunEventState:
    planned: bool = False
    research_started: bool = False
    research_completed: bool = False
    metric_started: set[str] = field(default_factory=set)
    metric_terminal: set[str] = field(default_factory=set)
    run_terminal: str | None = None


def _validate_event_chain(journal: CalibrationJournal) -> None:
    plans = {plan.run_id: plan for plan in journal.runs}
    if len(plans) != len(journal.runs):
        raise ValueError("duplicate calibration run plans")
    for plan in journal.runs:
        _validate_run_plan(journal.identity, plan)

    seen_event_ids: set[str] = set()
    states: dict[str, _RunEventState] = {
        run_id: _RunEventState() for run_id in plans
    }
    for expected_sequence, event in enumerate(journal.events, start=1):
        if event.sequence != expected_sequence:
            raise ValueError("journal event sequence is not contiguous")
        if event.event_id in seen_event_ids:
            raise ValueError("duplicate journal event ID")
        seen_event_ids.add(event.event_id)
        if event.event_id != _event_id(event.event_type, event.run_id, event.step_id):
            raise ValueError("journal event ID is not stable")
        event_plan = plans.get(event.run_id)
        if event_plan is None:
            raise ValueError("journal event references an unknown run")
        state = states[event.run_id]
        if state.run_terminal is not None:
            raise ValueError("journal contains an event after run terminal")

        if event.event_type == "planned":
            if state.planned:
                raise ValueError("run was planned more than once")
            state.planned = True
            continue
        if not state.planned:
            raise ValueError("run event occurred before planned")

        metric_for_step = next(
            (
                metric
                for metric, step_id in event_plan.judge_step_ids.items()
                if step_id == event.step_id
            ),
            None,
        )
        if event.event_type == "started":
            if event.step_id == event_plan.research_step_id:
                if event.metric_name is not None or state.research_started:
                    raise ValueError("research step start is invalid or duplicated")
                state.research_started = True
            elif metric_for_step is not None:
                if event.metric_name != metric_for_step:
                    raise ValueError("judge start metric does not match its step")
                if not state.research_completed:
                    raise ValueError("judge metric started before research completed")
                if metric_for_step in state.metric_started:
                    raise ValueError("judge metric was started more than once")
                state.metric_started.add(metric_for_step)
            else:
                raise ValueError("started event references an unknown paid step")
        elif event.event_type == "research_completed":
            if event.step_id != event_plan.research_step_id:
                raise ValueError("research completion references the wrong step")
            if not state.research_started or state.research_completed:
                raise ValueError("research completion is missing start or duplicated")
            state.research_completed = True
        elif event.event_type == "judge_metric_terminal":
            if metric_for_step is None or event.metric_name != metric_for_step:
                raise ValueError("judge terminal references the wrong metric step")
            if metric_for_step not in state.metric_started:
                raise ValueError("judge terminal is missing its start")
            if metric_for_step in state.metric_terminal:
                raise ValueError("judge metric terminal is duplicated")
            state.metric_terminal.add(metric_for_step)
        elif event.event_type == "run_terminal":
            if event.step_id != event_plan.run_terminal_step_id:
                raise ValueError("run terminal references the wrong step")
            if not state.research_started:
                raise ValueError("run cannot terminate before it starts")
            if event.terminal_status == "completed" and (
                not state.research_completed
                or state.metric_terminal != set(event_plan.judge_step_ids)
            ):
                raise ValueError("completed run is missing research or metric terminals")
            state.run_terminal = event.terminal_status

    if {run_id for run_id, state in states.items() if state.planned} != set(plans):
        raise ValueError("every calibration run must have a planned event")


def _path_lock(path: Path) -> threading.RLock:
    resolved = path.resolve()
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(resolved, threading.RLock())


def _write_journal_atomic(path: Path, journal: CalibrationJournal) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        journal.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
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
                # Antivirus/indexers can briefly hold the old journal on
                # Windows. Retrying this local replace cannot repeat paid work.
                time.sleep(min(0.02 * (attempt + 1), 0.1))
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _read_journal(path: Path) -> CalibrationJournal:
    try:
        return CalibrationJournal.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CalibrationJournalError(f"cannot recover calibration journal: {path}") from exc


class CalibrationJournalStore:
    """Atomic journal API with deterministic, fail-closed resume decisions."""

    def __init__(self, path: str | Path) -> None:
        """Bind the store to one journal path without reading it yet."""
        self.path = Path(path)

    @classmethod
    def create(
        cls,
        path: str | Path,
        *,
        identity: CalibrationExperimentIdentity,
        runs: Iterable[CalibrationRunDefinition],
        judge_metric_names: Iterable[str],
    ) -> CalibrationJournalStore:
        """Create a new journal and atomically persist every planned run."""
        target = Path(path)
        definitions = list(runs)
        if not definitions:
            raise ValueError("calibration journal requires at least one run")
        metrics = tuple(
            sorted(
                {
                    _validate_public_identifier("metric_name", name)
                    for name in judge_metric_names
                }
            )
        )
        if not metrics:
            raise ValueError("calibration journal requires at least one judge metric")
        plans = [_run_plan(identity, definition, metrics) for definition in definitions]
        if len({plan.run_id for plan in plans}) != len(plans):
            raise ValueError("duplicate calibration run definition")
        planned_events = [
            CalibrationJournalEvent(
                event_id=_event_id("planned", plan.run_id, None),
                sequence=index,
                recorded_at=datetime.now(UTC),
                event_type="planned",
                run_id=plan.run_id,
            )
            for index, plan in enumerate(plans, start=1)
        ]
        journal = CalibrationJournal(identity=identity, runs=plans, events=planned_events)
        store = cls(target)
        with _path_lock(target):
            if target.exists():
                raise FileExistsError(f"calibration journal already exists: {target}")
            _write_journal_atomic(target, journal)
        return store

    def load(self) -> CalibrationJournal:
        """Recover and fully validate the latest atomic state."""
        with _path_lock(self.path):
            return _read_journal(self.path)

    def _append(self, **event_fields: object) -> CalibrationJournalEvent:
        with _path_lock(self.path):
            journal = _read_journal(self.path)
            event = _new_event(journal, **event_fields)  # type: ignore[arg-type]
            if any(existing.event_id == event.event_id for existing in journal.events):
                raise CalibrationJournalError(
                    f"terminal or start event already recorded: {event.event_id}"
                )
            updated = CalibrationJournal(
                identity=journal.identity,
                runs=journal.runs,
                events=[*journal.events, event],
            )
            _write_journal_atomic(self.path, updated)
            return event

    def start_research(self, run_id: str) -> CalibrationJournalEvent:
        """Persist intent immediately before the research external call."""
        plan = self._plan(run_id)
        return self._append(
            event_type="started", run_id=run_id, step_id=plan.research_step_id
        )

    def complete_research(
        self,
        run_id: str,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
        error_fingerprint: str | None = None,
    ) -> CalibrationJournalEvent:
        """Persist research output durability without storing the output itself."""
        plan = self._plan(run_id)
        return self._append(
            event_type="research_completed",
            run_id=run_id,
            step_id=plan.research_step_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            error_fingerprint=error_fingerprint,
        )

    def start_judge_metric(self, run_id: str, metric_name: str) -> CalibrationJournalEvent:
        """Persist intent immediately before one judge metric call."""
        plan = self._plan(run_id)
        metric = _validate_public_identifier("metric_name", metric_name)
        try:
            step_id = plan.judge_step_ids[metric]
        except KeyError as exc:
            raise CalibrationJournalError(f"unknown judge metric: {metric}") from exc
        return self._append(
            event_type="started",
            run_id=run_id,
            step_id=step_id,
            metric_name=metric,
        )

    def complete_judge_metric(
        self,
        run_id: str,
        metric_name: str,
        *,
        status: MetricTerminalStatus,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
        error_fingerprint: str | None = None,
    ) -> CalibrationJournalEvent:
        """Persist one metric terminal so it is never judged twice on resume."""
        plan = self._plan(run_id)
        metric = _validate_public_identifier("metric_name", metric_name)
        try:
            step_id = plan.judge_step_ids[metric]
        except KeyError as exc:
            raise CalibrationJournalError(f"unknown judge metric: {metric}") from exc
        return self._append(
            event_type="judge_metric_terminal",
            run_id=run_id,
            step_id=step_id,
            metric_name=metric,
            terminal_status=status,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            error_fingerprint=error_fingerprint,
        )

    def complete_run(
        self,
        run_id: str,
        *,
        status: RunTerminalStatus,
        error_fingerprint: str | None = None,
    ) -> CalibrationJournalEvent:
        """Persist a run terminal; every terminal run is skipped automatically."""
        plan = self._plan(run_id)
        return self._append(
            event_type="run_terminal",
            run_id=run_id,
            step_id=plan.run_terminal_step_id,
            terminal_status=status,
            error_fingerprint=error_fingerprint,
        )

    def _plan(self, run_id: str) -> CalibrationRunPlan:
        journal = self.load()
        for plan in journal.runs:
            if plan.run_id == run_id:
                return plan
        raise CalibrationJournalError(f"unknown calibration run: {run_id}")

    def resume_summary(self) -> CalibrationResumeSummary:
        """Summarize safe skips, next steps, and interruption blockers."""
        journal = self.load()
        events_by_run: dict[str, list[CalibrationJournalEvent]] = {
            plan.run_id: [] for plan in journal.runs
        }
        for event in journal.events:
            events_by_run[event.run_id].append(event)

        completed_runs: list[str] = []
        terminal_noncompleted: list[str] = []
        completed_metrics: list[str] = []
        pending_steps: list[str] = []
        in_flight: list[str] = []
        unknown_usage: list[str] = []
        for plan in journal.runs:
            events = events_by_run[plan.run_id]
            started = {
                event.step_id for event in events if event.event_type == "started"
            }
            research_terminal = next(
                (
                    event
                    for event in events
                    if event.event_type == "research_completed"
                ),
                None,
            )
            metric_terminals: dict[str, CalibrationJournalEvent] = {
                event.step_id: event
                for event in events
                if event.event_type == "judge_metric_terminal"
                and event.step_id is not None
            }
            run_terminal = next(
                (event for event in events if event.event_type == "run_terminal"),
                None,
            )

            terminal_step_ids = set(metric_terminals)
            if research_terminal is not None:
                terminal_step_ids.add(plan.research_step_id)
            in_flight.extend(
                sorted(
                    step_id
                    for step_id in started
                    if step_id is not None and step_id not in terminal_step_ids
                )
            )
            for usage_event in [research_terminal, *metric_terminals.values()]:
                if usage_event is not None and usage_event.total_tokens is None:
                    unknown_usage.append(
                        usage_event.step_id or plan.research_step_id
                    )

            completed_metrics.extend(sorted(metric_terminals))
            if run_terminal is not None:
                if run_terminal.terminal_status == "completed":
                    completed_runs.append(plan.run_id)
                else:
                    terminal_noncompleted.append(plan.run_id)
                continue
            if plan.research_step_id not in started:
                pending_steps.append(plan.research_step_id)
                continue
            if research_terminal is None:
                continue
            for step_id in plan.judge_step_ids.values():
                if step_id not in started and step_id not in metric_terminals:
                    pending_steps.append(step_id)
            if set(metric_terminals) == set(plan.judge_step_ids.values()):
                pending_steps.append(plan.run_terminal_step_id)

        blocked = sorted(set(in_flight))
        unknown = sorted(set(unknown_usage))
        return CalibrationResumeSummary(
            experiment_id=journal.identity.experiment_id,
            completed_run_ids=sorted(completed_runs),
            terminal_noncompleted_run_ids=sorted(terminal_noncompleted),
            completed_metric_step_ids=sorted(completed_metrics),
            pending_step_ids=sorted(set(pending_steps)),
            blocked_in_flight_step_ids=blocked,
            unknown_usage_step_ids=unknown,
            can_resume=not blocked and not unknown,
        )

    def should_skip_run(self, run_id: str) -> bool:
        """Return true for every terminal run, including failures."""
        summary = self.resume_summary()
        return run_id in {
            *summary.completed_run_ids,
            *summary.terminal_noncompleted_run_ids,
        }

    def should_skip_metric(self, step_id: str) -> bool:
        """Return true once a judge metric has any recorded terminal."""
        return step_id in set(self.resume_summary().completed_metric_step_ids)

    def assert_resumable(self) -> CalibrationResumeSummary:
        """Return the summary or fail closed on unsafe interrupted work."""
        summary = self.resume_summary()
        summary.assert_resumable()
        return summary
