from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import types
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

# Some locked-down Windows hosts reject the optional native ``uuid_utils`` DLL.
# These tests never exercise UUID generation semantics, so use a test-local
# pure-Python compatibility module rather than weakening production imports.
try:
    import uuid_utils  # noqa: F401
except ImportError:
    uuid_fallback = types.ModuleType("uuid_utils")
    uuid_compat = types.ModuleType("uuid_utils.compat")
    setattr(uuid_compat, "uuid7", uuid.uuid4)
    setattr(uuid_fallback, "uuid7", uuid.uuid4)
    setattr(uuid_fallback, "compat", uuid_compat)
    sys.modules["uuid_utils"] = uuid_fallback
    sys.modules["uuid_utils.compat"] = uuid_compat

from open_deep_research.evaluation.calibration_runner import ResearchObservation
from open_deep_research.evaluation.claim_scorer import (
    CLAIM_SCORER_VERSION,
    ClaimScorerResult,
    ClaimSourceAuthority,
    ClaimValidationStatus,
    ScoredClaim,
    report_candidate_units,
    stable_evaluation_claim_id,
)
from open_deep_research.evaluation.experiment_models import (
    EvaluationStatus,
    ExperimentMetricResult,
    ExperimentRun,
)
from open_deep_research.evaluation.full_metrics import FULL_METRIC_NAMES
from open_deep_research.evaluation.full_runner import (
    FullRunnerError,
    _exclusive_full_run_lease,
    _full_private_runtime_root,
    _SoftDispatchLedger,
    run_full_matrix,
)
from open_deep_research.evaluation.full_state import (
    build_full_run_definitions,
    cold_definition_for,
)
from open_deep_research.evaluation.live_budget import (
    LiveTokenBudgetExceeded,
    LiveTokenReservationLedger,
    TokenUsageCategory,
)
from open_deep_research.evaluation.models import RunStatus, RunTelemetry
from open_deep_research.evaluation.reporting import validate_artifact_manifest
from open_deep_research.evaluation.trace_adapter import NormalizedTrace
from open_deep_research.evaluation.tracking import LocalTrackingSink

ROOT = Path(__file__).resolve().parents[2]
ENV = {
    "SUMMARIZATION_MODEL": "openai:qwen3.7-plus",
    "RESEARCH_MODEL": "openai:qwen3.7-plus",
    "COMPRESSION_MODEL": "openai:qwen3.7-plus",
    "FINAL_REPORT_MODEL": "openai:qwen3.7-plus",
}
PROJECTION = {
    "status": "authorized_fixture",
    "projected_tokens": 12_000,
    "source_experiment_id": "cal-fixture",
}


def _variant_id(config: dict) -> str:
    values = config["configurable"]
    if values["citation_validation_mode"] == "enforce":
        return "citation_validator"
    if values["enable_memory"]:
        return "memory"
    if values["enable_agentic_rag"]:
        return "agentic_rag"
    if values["enable_paperqa_retrieval"]:
        return "paperqa"
    return "baseline"


def _citation_artifact() -> dict:
    return {
        "claims": [
            {
                "claim_id": "claim-1",
                "claim_type": "factual",
                "cited_citation_keys": ["source-1:version-1"],
            }
        ],
        "results": [
            {
                "claim_id": "claim-1",
                "status": "fully_supported",
                "required_action": "keep",
                "links": [
                    {
                        "accepted": True,
                        "authority_status": "sufficient",
                    }
                ],
            }
        ],
    }


class FakeFullExecutors:
    def __init__(
        self,
        *,
        fail_research_first: int = 0,
        unknown_research_first: bool = False,
        overflow_research_first: bool = False,
        fail_judge_first_runs: int = 0,
        write_runtime_state: bool = False,
    ) -> None:
        self.research_calls: list[tuple[str, str, str]] = []
        self.metric_calls: list[tuple[str, str]] = []
        self.claim_calls: list[str] = []
        self.runtime_paths: list[str] = []
        self.fail_research_first = fail_research_first
        self.unknown_research_first = unknown_research_first
        self.overflow_research_first = overflow_research_first
        self.fail_judge_first_runs = fail_judge_first_runs
        self.write_runtime_state = write_runtime_state
        self._judge_failed_runs: set[str] = set()

    async def research(self, **kwargs):
        case = kwargs["case"]
        config = kwargs["config"]
        ledger = kwargs["ledger"]
        run_id = kwargs["evaluation_run_id"]
        persist = kwargs["persist_budget"]
        variant = _variant_id(config)
        knowledge_path = config["configurable"]["knowledge_db_path"]
        self.runtime_paths.append(knowledge_path)
        self.research_calls.append((case.case_id, variant, run_id))
        if self.write_runtime_state:
            runtime_state = Path(knowledge_path)
            runtime_state.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(runtime_state) as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS fake_full_state "
                    "(run_id TEXT PRIMARY KEY)"
                )
                connection.execute(
                    "INSERT OR REPLACE INTO fake_full_state(run_id) VALUES (?)",
                    (run_id,),
                )
        call = len(self.research_calls)
        if call <= self.fail_research_first:
            raise RuntimeError("same fake research failure")
        if self.overflow_research_first and call == 1:
            ledger.reserve_before_call(
                run_id=run_id,
                category=TokenUsageCategory.RESEARCH,
                input_upper_bound=800_001,
                output_upper_bound=1,
                reservation_id=f"overflow:{run_id}",
            )
        reservation = ledger.reserve_before_call(
            run_id=run_id,
            category=TokenUsageCategory.RESEARCH,
            input_upper_bound=100,
            output_upper_bound=50,
            reservation_id=f"research:{run_id}",
        )
        persist(ledger.snapshot())
        if self.unknown_research_first and call == 1:
            ledger.settle_error(
                reservation.reservation_id,
                error_signature="FakeUnknownUsage",
            )
            persist(ledger.snapshot())
            raise RuntimeError("provider failed without trustworthy usage")
        ledger.settle_success(
            reservation.reservation_id,
            actual_input_tokens=10,
            actual_output_tokens=5,
        )
        persist(ledger.snapshot())
        now = datetime.now(UTC)
        return ResearchObservation(
            output=(
                f"# Result\n{case.case_id} {variant} is supported [1]."
                "\n\n## Sources\n[1] Official fixture"
            ),
            telemetry=RunTelemetry(
                started_at=now,
                finished_at=now,
                wall_time_ms=1,
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                model_calls=1,
                model_calls_with_usage=1,
                status=RunStatus.COMPLETED,
            ),
            trace=NormalizedTrace(
                plan=["inspect", "answer"],
                trace_dict={"name": "fake", "plan": ["inspect", "answer"]},
                retrieval_context=["[1] Official fixture evidence"],
            ),
            state_artifacts={"citation_validation_artifact": _citation_artifact()},
            researcher_runs=1,
            search_calls=0,
        )

    def metrics(self, **kwargs):
        run_id = kwargs["run_id"]
        ledger = kwargs["ledger"]
        persist = kwargs["persist_budget"]

        def build(name: str):
            async def measure():
                self.metric_calls.append((run_id, name))
                if (
                    name == FULL_METRIC_NAMES[0]
                    and len(self._judge_failed_runs) < self.fail_judge_first_runs
                    and run_id not in self._judge_failed_runs
                ):
                    self._judge_failed_runs.add(run_id)
                    raise RuntimeError("same fake judge failure")
                reservation = ledger.reserve_before_call(
                    run_id=run_id,
                    category=TokenUsageCategory.JUDGE,
                    input_upper_bound=20,
                    output_upper_bound=10,
                    reservation_id=f"judge:{run_id}:{name}",
                )
                persist(ledger.snapshot())
                ledger.settle_success(
                    reservation.reservation_id,
                    actual_input_tokens=3,
                    actual_output_tokens=2,
                )
                persist(ledger.snapshot())
                return ExperimentMetricResult(
                    metric_name=name,
                    metric_version="fake-1",
                    score=1.0,
                    threshold=0.5,
                    status=EvaluationStatus.PASSED,
                    reason="offline fake metric",
                    deterministic=False,
                    judge_model="openai:qwen3.7-plus",
                )

            return measure

        return {name: build(name) for name in FULL_METRIC_NAMES}

    def claim_scorer(self, **kwargs):
        run_id = kwargs["run_id"]
        ledger = kwargs["ledger"]
        persist = kwargs["persist_budget"]
        owner = self

        class OfflineClaimScorer:
            async def score(self, *, prompt, report, retrieval_context):
                del prompt, retrieval_context
                owner.claim_calls.append(run_id)
                reservation = ledger.reserve_before_call(
                    run_id=run_id,
                    category=TokenUsageCategory.JUDGE,
                    input_upper_bound=20,
                    output_upper_bound=10,
                    reservation_id=f"claim:{run_id}",
                )
                persist(ledger.snapshot())
                ledger.settle_success(
                    reservation.reservation_id,
                    actual_input_tokens=3,
                    actual_output_tokens=2,
                )
                persist(ledger.snapshot())
                candidates = report_candidate_units(report)
                return ClaimScorerResult(
                    report_sha256=hashlib.sha256(report.encode("utf-8")).hexdigest(),
                    candidate_count=len(candidates),
                    bound_context_count=1,
                    unbound_context_count=0,
                    claims=tuple(
                        ScoredClaim(
                            claim_id=stable_evaluation_claim_id(
                                candidate.ordinal,
                                candidate.text,
                            ),
                            text=candidate.text,
                            checkable=bool(candidate.citation_ids),
                            citation_ids=candidate.citation_ids,
                            validation_status=(
                                ClaimValidationStatus.FULLY_SUPPORTED
                                if candidate.citation_ids
                                else ClaimValidationStatus.NOT_CHECKABLE
                            ),
                            evidence_valid=bool(candidate.citation_ids),
                            source_authority=(
                                ClaimSourceAuthority.OFFICIAL
                                if candidate.citation_ids
                                else ClaimSourceAuthority.UNKNOWN
                            ),
                            correctly_qualified=False,
                        )
                        for candidate in candidates
                    ),
                )

        return OfflineClaimScorer()


class OneFailureTrackingSink(LocalTrackingSink):
    def __init__(self) -> None:
        self.failed = False

    def track_metric(self, payload):
        if not self.failed:
            self.failed = True
            raise RuntimeError("offline tracking failure")
        return super().track_metric(payload)


def _load_runs(output: Path) -> list[ExperimentRun]:
    return [
        ExperimentRun.model_validate_json(line)
        for line in (output / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_full_state_fixes_45_main_and_9_warm_identities():
    plan = json.loads(
        (ROOT / "tests/evaluation/full_plan.v1.json").read_text(encoding="utf-8")
    )
    definitions = build_full_run_definitions(plan)
    assert len(definitions) == 54
    assert sum(item.kind == "main" for item in definitions) == 45
    assert sum(item.phase == "warm" for item in definitions) == 9
    assert len(
        {
            (item.case_id, item.journal_variant_id, item.repeat)
            for item in definitions
        }
    ) == 54
    for warm in definitions[-9:]:
        cold = cold_definition_for(warm, definitions)
        assert cold.pair_id == warm.pair_id
        assert cold.paired_key == warm.paired_key
        assert cold.phase == "cold"


def test_soft_dispatch_limit_is_enforced_on_every_inner_paid_reservation():
    base = LiveTokenReservationLedger(
        hard_token_limit=1_000,
        per_run_token_limit=1_000,
    )
    ledger = _SoftDispatchLedger(base, soft_token_limit=100)
    first = ledger.reserve_before_call(
        run_id="research-run",
        category=TokenUsageCategory.RESEARCH,
        input_upper_bound=60,
        output_upper_bound=20,
    )
    ledger.settle_success(
        first.reservation_id,
        actual_input_tokens=60,
        actual_output_tokens=20,
    )
    with pytest.raises(LiveTokenBudgetExceeded, match="soft"):
        ledger.reserve_before_call(
            run_id="research-run",
            category=TokenUsageCategory.RESEARCH,
            input_upper_bound=20,
            output_upper_bound=1,
        )
    snapshot = ledger.snapshot()
    assert snapshot["committed_tokens"] == 80
    assert snapshot["dispatched_calls"] == 1
    assert snapshot["active_calls"] == 0


def test_private_runtime_root_is_bound_to_the_exact_output_path(tmp_path: Path):
    first = _full_private_runtime_root(ROOT, tmp_path / "one", "same-experiment")
    second = _full_private_runtime_root(ROOT, tmp_path / "two", "same-experiment")
    assert first != second


def test_full_output_lease_rejects_a_second_process(tmp_path: Path):
    output = tmp_path / "leased-output"
    script = """
import sys, types, uuid
uuid_utils = types.ModuleType('uuid_utils')
uuid_compat = types.ModuleType('uuid_utils.compat')
uuid_compat.uuid7 = uuid.uuid4
uuid_utils.uuid7 = uuid.uuid4
uuid_utils.compat = uuid_compat
sys.modules['uuid_utils'] = uuid_utils
sys.modules['uuid_utils.compat'] = uuid_compat
from pathlib import Path
from open_deep_research.evaluation.full_runner import _exclusive_full_run_lease
try:
    with _exclusive_full_run_lease(Path(sys.argv[1]), Path(sys.argv[2])):
        pass
except Exception as error:
    print(type(error).__name__ + ':' + str(error))
    raise SystemExit(23)
raise SystemExit(0)
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [
            str(ROOT / "src"),
            environment.get("PYTHONPATH", ""),
        ]
    )
    with _exclusive_full_run_lease(ROOT, output):
        result = subprocess.run(
            [sys.executable, "-c", script, str(ROOT), str(output)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    assert result.returncode == 23
    assert "already owns this output lease" in result.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provenance", "use_fake", "message"),
    [
        ("live", True, "execution core"),
        ("fake", False, "execution core"),
    ],
)
async def test_provenance_must_match_the_execution_core(
    tmp_path: Path,
    provenance: Literal["live", "fake"],
    use_fake: bool,
    message: str,
):
    fake = FakeFullExecutors()
    with pytest.raises(FullRunnerError, match=message):
        if use_fake:
            await run_full_matrix(
                project_root=ROOT,
                output_dir=tmp_path / f"provenance-{provenance}-{use_fake}",
                research_executor=fake.research,
                metric_factory=fake.metrics,
                claim_scorer_factory=fake.claim_scorer,
                calibration_projection=PROJECTION,
                provenance=provenance,
                require_deepeval=False,
                environment=ENV,
            )
        else:
            await run_full_matrix(
                project_root=ROOT,
                output_dir=tmp_path / f"provenance-{provenance}-{use_fake}",
                calibration_projection=PROJECTION,
                provenance=provenance,
                require_deepeval=False,
                environment=ENV,
            )


@pytest.mark.asyncio
async def test_fake_full_e2e_writes_exact_matrix_snapshots_and_tracking_errors(
    tmp_path: Path, monkeypatch,
):
    fake = FakeFullExecutors()
    output = tmp_path / "full"
    outcome = await run_full_matrix(
        project_root=ROOT,
        output_dir=output,
        research_executor=fake.research,
        metric_factory=fake.metrics,
        claim_scorer_factory=fake.claim_scorer,
        tracking_sink=OneFailureTrackingSink(),
        calibration_projection=PROJECTION,
        provenance="fake",
        require_deepeval=False,
        environment=ENV,
    )

    assert outcome.status == "completed"
    assert outcome.completed_runs == outcome.planned_runs == 54
    assert outcome.committed_tokens == 54 * (15 + 8 * 5)
    assert len(fake.research_calls) == 54
    assert len(fake.metric_calls) == 54 * 7
    assert len(fake.claim_calls) == 54
    assert len(fake.runtime_paths) == len(set(fake.runtime_paths)) == 54
    runs = _load_runs(output)
    assert len({item.run_id for item in runs}) == 54
    assert sum(item.trace["protocol"]["kind"] == "main" for item in runs) == 45
    assert sum(item.trace["protocol"]["phase"] == "warm" for item in runs) == 9
    assert all(item.trace["evaluation_provenance"] == "fake" for item in runs)
    assert all(item.trace["expected_output_present"] for item in runs)
    assert all(item.trace["evaluation_claim_results"] for item in runs)
    assert {
        result["scorer_version"]
        for item in runs
        for result in item.trace["evaluation_claim_results"]
    } == {CLAIM_SCORER_VERSION}
    assert all(item.telemetry.estimated_cost_usd is None for item in runs)

    cold = {
        item.trace["protocol"]["pair_id"]: item
        for item in runs
        if item.trace["protocol"]["kind"] == "main"
        and item.trace["protocol"]["snapshot_sha256"] is not None
    }
    warm = [item for item in runs if item.trace["protocol"]["phase"] == "warm"]
    assert len(cold) == len(warm) == 9
    assert len(
        {
            item.trace["protocol"]["snapshot_sha256"]
            for item in [*cold.values(), *warm]
        }
    ) == 1
    for item in warm:
        source = cold[item.trace["protocol"]["pair_id"]]
        assert item.trace["protocol"]["runtime_state_sha256"] == source.trace[
            "protocol"
        ]["runtime_state_sha256"]

    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["run_count"] == 54
    assert set(report["acceptance"].values()) == {"not_evaluable"}
    assert report["calibration_projection"] == PROJECTION
    experiment = json.loads(
        (output / "experiment.json").read_text(encoding="utf-8")
    )
    assert experiment["status"] == "completed"
    assert experiment["planned_runs"] == 54
    assert experiment["calibration_projection"] == PROJECTION
    assert (output / "tracking-errors.jsonl").read_text(encoding="utf-8").count("\n") == 1
    assert validate_artifact_manifest(output) == []

    # A terminal artifact may be skipped, but corruption must never be resumed.
    import open_deep_research.evaluation.full_runner as full_runner

    persisted_identity = json.loads(
        (output / "journal.json").read_text(encoding="utf-8")
    )["identity"]
    from open_deep_research.evaluation.calibration_state import (
        CalibrationExperimentIdentity,
    )

    monkeypatch.setattr(
        full_runner,
        "capture_experiment_identity",
        lambda *args, **kwargs: CalibrationExperimentIdentity.model_validate(
            persisted_identity
        ),
    )
    first_record = next((output / "run-records").glob("*.json"))
    first_record.write_text("{}", encoding="utf-8")
    with pytest.raises(Exception, match="hash mismatch"):
        await run_full_matrix(
            project_root=ROOT,
            output_dir=output,
            resume=True,
            research_executor=fake.research,
            metric_factory=fake.metrics,
            claim_scorer_factory=fake.claim_scorer,
            calibration_projection=PROJECTION,
            provenance="fake",
            require_deepeval=False,
            environment=ENV,
        )


@pytest.mark.asyncio
async def test_resume_skips_every_terminal_paid_step(tmp_path: Path, monkeypatch):
    import open_deep_research.evaluation.full_runner as full_runner

    fake = FakeFullExecutors()
    output = tmp_path / "resume"
    original = full_runner._dispatch_stop_reason

    def stop_before_first_claim_scorer(ledger, plan):
        normal = original(ledger, plan)
        if normal:
            return normal
        # One fake research call (15 tokens) plus the seven DeepEval metric
        # calls (7 * 5 tokens) are durable.  Stop before the separately
        # metered claim/citation scorer so resume has to continue mid-run.
        if ledger.snapshot()["committed_tokens"] >= 50:
            return "fixture_pause"
        return None

    monkeypatch.setattr(
        full_runner,
        "_dispatch_stop_reason",
        stop_before_first_claim_scorer,
    )
    first = await run_full_matrix(
        project_root=ROOT,
        output_dir=output,
        research_executor=fake.research,
        metric_factory=fake.metrics,
        claim_scorer_factory=fake.claim_scorer,
        calibration_projection=PROJECTION,
        provenance="fake",
        require_deepeval=False,
        environment=ENV,
    )
    assert first.status == "stopped"
    assert first.completed_runs == 0
    assert len(fake.research_calls) == 1
    assert len(fake.metric_calls) == 7
    assert len(fake.claim_calls) == 0

    monkeypatch.setattr(full_runner, "_dispatch_stop_reason", original)
    resumed = await run_full_matrix(
        project_root=ROOT,
        output_dir=output,
        resume=True,
        research_executor=fake.research,
        metric_factory=fake.metrics,
        claim_scorer_factory=fake.claim_scorer,
        calibration_projection=PROJECTION,
        provenance="fake",
        require_deepeval=False,
        environment=ENV,
    )
    assert resumed.status == "completed"
    assert resumed.completed_runs == 54
    assert len(fake.research_calls) == 54
    assert len({item[2] for item in fake.research_calls}) == 54
    assert len(fake.metric_calls) == 54 * 7
    assert len(fake.claim_calls) == 54


@pytest.mark.asyncio
async def test_resume_preserves_post_research_runtime_before_snapshot(
    tmp_path: Path, monkeypatch
):
    import open_deep_research.evaluation.full_runner as full_runner

    fake = FakeFullExecutors(write_runtime_state=True)
    output = tmp_path / "post-research-resume"
    original_definitions = full_runner.build_full_run_definitions
    original_capture = full_runner._capture_runtime_snapshot

    def warm_source_first(plan):
        definitions = original_definitions(plan)
        warm_keys = {
            (item.case_id, item.variant_id, item.repeat)
            for item in definitions
            if item.phase == "warm"
        }
        cold = next(
            item
            for item in definitions
            if item.kind == "main"
            and (item.case_id, item.variant_id, item.repeat) in warm_keys
        )
        return (cold, *(item for item in definitions if item is not cold))

    crashed = False

    def crash_before_pair_snapshot(runtime_dir, snapshot_dir):
        nonlocal crashed
        if snapshot_dir.name.startswith("pair-") and not crashed:
            crashed = True
            raise SystemExit("simulated process crash after durable research")
        return original_capture(runtime_dir, snapshot_dir)

    monkeypatch.setattr(full_runner, "build_full_run_definitions", warm_source_first)
    monkeypatch.setattr(full_runner, "_capture_runtime_snapshot", crash_before_pair_snapshot)
    with pytest.raises(SystemExit, match="simulated process crash"):
        await run_full_matrix(
            project_root=ROOT,
            output_dir=output,
            research_executor=fake.research,
            metric_factory=fake.metrics,
            claim_scorer_factory=fake.claim_scorer,
            calibration_projection=PROJECTION,
            provenance="fake",
            require_deepeval=False,
            environment=ENV,
        )
    assert len(fake.research_calls) == 1

    monkeypatch.setattr(full_runner, "_capture_runtime_snapshot", original_capture)
    resumed = await run_full_matrix(
        project_root=ROOT,
        output_dir=output,
        resume=True,
        research_executor=fake.research,
        metric_factory=fake.metrics,
        claim_scorer_factory=fake.claim_scorer,
        calibration_projection=PROJECTION,
        provenance="fake",
        require_deepeval=False,
        environment=ENV,
    )
    assert resumed.status == "completed"
    assert len(fake.research_calls) == 54


@pytest.mark.asyncio
async def test_one_technical_failure_cannot_be_hidden_by_later_successes(
    tmp_path: Path,
):
    fake = FakeFullExecutors(fail_research_first=1)
    outcome = await run_full_matrix(
        project_root=ROOT,
        output_dir=tmp_path / "one-terminal-failure",
        research_executor=fake.research,
        metric_factory=fake.metrics,
        claim_scorer_factory=fake.claim_scorer,
        calibration_projection=PROJECTION,
        provenance="fake",
        require_deepeval=False,
        environment=ENV,
    )
    assert outcome.status == "stopped"
    assert outcome.completed_runs == 54
    assert outcome.stopped_reason == "terminal_failures:1"
    report = json.loads(
        (Path(outcome.output_dir) / "report.json").read_text(encoding="utf-8")
    )
    assert report["claims"]["full_matrix_complete"] is False


@pytest.mark.asyncio
async def test_repeated_research_and_judge_failures_open_circuits(tmp_path: Path):
    research_failure = FakeFullExecutors(fail_research_first=2)
    research = await run_full_matrix(
        project_root=ROOT,
        output_dir=tmp_path / "research-failure",
        research_executor=research_failure.research,
        metric_factory=research_failure.metrics,
        claim_scorer_factory=research_failure.claim_scorer,
        calibration_projection=PROJECTION,
        provenance="fake",
        require_deepeval=False,
        environment=ENV,
    )
    assert research.status == "stopped"
    assert research.completed_runs == 2
    assert research.stopped_reason == "circuit:consecutive_failures"
    assert research_failure.metric_calls == []

    judge_failure = FakeFullExecutors(fail_judge_first_runs=2)
    judge = await run_full_matrix(
        project_root=ROOT,
        output_dir=tmp_path / "judge-failure",
        research_executor=judge_failure.research,
        metric_factory=judge_failure.metrics,
        claim_scorer_factory=judge_failure.claim_scorer,
        calibration_projection=PROJECTION,
        provenance="fake",
        require_deepeval=False,
        environment=ENV,
    )
    assert judge.status == "stopped"
    assert judge.completed_runs == 2
    assert judge.stopped_reason == "circuit:consecutive_failures"
    runs = _load_runs(Path(judge.output_dir))
    assert all(item.status is EvaluationStatus.ERROR for item in runs)
    assert all(
        next(
            metric
            for metric in item.metric_results
            if metric.metric_name == "task_completion"
        ).status
        is EvaluationStatus.ERROR
        for item in runs
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["unknown", "overflow"])
async def test_budget_uncertainty_and_per_run_overflow_stop_before_next_run(
    tmp_path: Path, kind: str
):
    fake = FakeFullExecutors(
        unknown_research_first=kind == "unknown",
        overflow_research_first=kind == "overflow",
    )
    outcome = await run_full_matrix(
        project_root=ROOT,
        output_dir=tmp_path / kind,
        research_executor=fake.research,
        metric_factory=fake.metrics,
        claim_scorer_factory=fake.claim_scorer,
        calibration_projection=PROJECTION,
        provenance="fake",
        require_deepeval=False,
        environment=ENV,
    )
    assert outcome.status == "stopped"
    assert outcome.completed_runs == 1
    assert len(fake.research_calls) == 1
    assert fake.metric_calls == []
    budget = json.loads(
        (Path(outcome.output_dir) / "budget.json").read_text(encoding="utf-8")
    )["ledger"]
    assert budget["active_calls"] == 0
    if kind == "unknown":
        assert budget["unknown_usage"] is True
        assert budget["fail_closed"] is True
    else:
        assert budget["unknown_usage"] is False
        assert budget["committed_tokens"] == 0


@pytest.mark.asyncio
async def test_full_runner_rejects_nonfixed_hard_limit_before_output(tmp_path: Path):
    fake = FakeFullExecutors()
    with pytest.raises(FullRunnerError, match="42000000"):
        await run_full_matrix(
            project_root=ROOT,
            output_dir=tmp_path / "invalid",
            requested_max_tokens=41_999_999,
            research_executor=fake.research,
            metric_factory=fake.metrics,
            claim_scorer_factory=fake.claim_scorer,
            calibration_projection=PROJECTION,
            provenance="fake",
            require_deepeval=False,
            environment=ENV,
        )
    assert not (tmp_path / "invalid").exists()
