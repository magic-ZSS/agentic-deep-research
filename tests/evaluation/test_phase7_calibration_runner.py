from __future__ import annotations

import hashlib
import sys
import types
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

# This suite does not exercise UUIDv7 semantics. Locked-down Windows hosts may
# reject uuid_utils' optional native DLL; keep the shim test-local while the
# production paid-environment import smoke remains strict.
try:
    import uuid_utils  # noqa: F401
except ImportError:
    uuid_utils = types.ModuleType("uuid_utils")
    uuid_compat = types.ModuleType("uuid_utils.compat")
    uuid_compat.uuid7 = uuid.uuid4
    uuid_utils.uuid7 = uuid.uuid4
    uuid_utils.compat = uuid_compat
    sys.modules["uuid_utils"] = uuid_utils
    sys.modules["uuid_utils.compat"] = uuid_compat

from open_deep_research.evaluation.calibration_runner import (
    CalibrationRunnerError,
    ResearchObservation,
    _private_runtime_root,
    run_calibration,
)
from open_deep_research.evaluation.claim_scorer import (
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
)
from open_deep_research.evaluation.live_budget import TokenUsageCategory
from open_deep_research.evaluation.models import RunStatus, RunTelemetry
from open_deep_research.evaluation.trace_adapter import NormalizedTrace

ROOT = Path(__file__).resolve().parents[2]
ENV = {
    "SUMMARIZATION_MODEL": "openai:qwen3.7-plus",
    "RESEARCH_MODEL": "openai:qwen3.7-plus",
    "COMPRESSION_MODEL": "openai:qwen3.7-plus",
    "FINAL_REPORT_MODEL": "openai:qwen3.7-plus",
}


class FakeExecutors:
    def __init__(self, *, fail_research_at: int | None = None):
        self.research_order = []
        self.metric_order = []
        self.claim_order = []
        self.fail_research_at = fail_research_at

    async def research(self, **kwargs):
        case = kwargs["case"]
        config = kwargs["config"]
        ledger = kwargs["ledger"]
        run_id = kwargs["evaluation_run_id"]
        persist = kwargs["persist_budget"]
        variant = (
            "citation_validator"
            if config["configurable"]["citation_validation_mode"] == "enforce"
            else "baseline"
        )
        self.research_order.append((case.case_id, variant))
        if self.fail_research_at == len(self.research_order):
            raise RuntimeError("fake failure")
        reservation = ledger.reserve_before_call(
            run_id=run_id,
            category=TokenUsageCategory.RESEARCH,
            input_upper_bound=100,
            output_upper_bound=50,
            reservation_id=f"research-{len(self.research_order)}",
        )
        persist(ledger.snapshot())
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
                plan=["fake actual plan"],
                trace_dict={"name": "fake", "plan": ["fake actual plan"]},
                retrieval_context=["[1] Official fixture evidence"],
            ),
            state_artifacts={},
            researcher_runs=1,
            search_calls=0,
        )

    def metrics(self, **kwargs):
        run_id = kwargs["run_id"]
        ledger = kwargs["ledger"]
        persist = kwargs["persist_budget"]

        def build(name):
            async def run():
                self.metric_order.append((run_id, name))
                reservation = ledger.reserve_before_call(
                    run_id=run_id,
                    category=TokenUsageCategory.JUDGE,
                    input_upper_bound=20,
                    output_upper_bound=10,
                    reservation_id=f"judge-{run_id}-{name}",
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
                    score=1,
                    threshold=0.5,
                    status=EvaluationStatus.PASSED,
                    reason="fake",
                    deterministic=False,
                    judge_model="openai:qwen3.7-plus",
                )

            return run

        from open_deep_research.evaluation.full_metrics import FULL_METRIC_NAMES

        return {name: build(name) for name in FULL_METRIC_NAMES}

    def claim_scorer(self, **kwargs):
        run_id = kwargs["run_id"]
        ledger = kwargs["ledger"]
        persist = kwargs["persist_budget"]
        owner = self

        class OfflineClaimScorer:
            async def score(self, *, prompt, report, retrieval_context):
                del prompt, retrieval_context
                owner.claim_order.append(run_id)
                reservation = ledger.reserve_before_call(
                    run_id=run_id,
                    category=TokenUsageCategory.JUDGE,
                    input_upper_bound=20,
                    output_upper_bound=10,
                    reservation_id=f"claim-{run_id}",
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
                                item.ordinal,
                                item.text,
                            ),
                            text=item.text,
                            checkable=bool(item.citation_ids),
                            citation_ids=item.citation_ids,
                            validation_status=(
                                ClaimValidationStatus.FULLY_SUPPORTED
                                if item.citation_ids
                                else ClaimValidationStatus.NOT_CHECKABLE
                            ),
                            evidence_valid=bool(item.citation_ids),
                            source_authority=(
                                ClaimSourceAuthority.OFFICIAL
                                if item.citation_ids
                                else ClaimSourceAuthority.UNKNOWN
                            ),
                            correctly_qualified=False,
                        )
                        for item in candidates
                    ),
                )

        return OfflineClaimScorer()


@pytest.mark.asyncio
async def test_fake_calibration_runs_exact_canary_then_remainder_and_checkpoints(
    tmp_path,
):
    fake = FakeExecutors()
    output = tmp_path / "calibration"
    outcome = await run_calibration(
        project_root=ROOT,
        output_dir=output,
        dataset_version="v1",
        variant_ids=["baseline", "citation_validator"],
        requested_max_tokens=3_000_000,
        research_executor=fake.research,
        metric_factory=fake.metrics,
        claim_scorer_factory=fake.claim_scorer,
        provenance="fake",
        require_deepeval=False,
        environment=ENV,
    )
    assert outcome.status == "completed", outcome
    assert outcome.completed_runs == outcome.planned_runs == 6
    assert fake.research_order[:2] == [
        ("simple-001", "baseline"),
        ("simple-001", "citation_validator"),
    ]
    assert fake.research_order[2:] == [
        ("medium-001", "baseline"),
        ("medium-001", "citation_validator"),
        ("complex-001", "baseline"),
        ("complex-001", "citation_validator"),
    ]
    assert len(fake.metric_order) == 42
    assert len(fake.claim_order) == 6
    assert (output / "journal.json").is_file()
    assert (output / "budget.json").is_file()
    assert (output / "runs.jsonl").read_text(encoding="utf-8").count("\n") == 6
    assert not (output / "runtime").exists()
    private_runtime = _private_runtime_root(
        ROOT,
        output.resolve(),
        outcome.experiment_id,
    )
    assert private_runtime.is_dir()
    assert output.resolve() not in private_runtime.parents
    assert outcome.committed_tokens == 6 * (15 + 8 * 5)


@pytest.mark.asyncio
async def test_canary_execution_error_stops_before_remaining_paid_runs(tmp_path):
    fake = FakeExecutors(fail_research_at=2)
    outcome = await run_calibration(
        project_root=ROOT,
        output_dir=tmp_path / "calibration",
        dataset_version="v1",
        variant_ids=["baseline", "citation_validator"],
        requested_max_tokens=3_000_000,
        research_executor=fake.research,
        metric_factory=fake.metrics,
        claim_scorer_factory=fake.claim_scorer,
        provenance="fake",
        require_deepeval=False,
        environment=ENV,
    )
    assert outcome.status == "stopped"
    assert fake.research_order == [
        ("simple-001", "baseline"),
        ("simple-001", "citation_validator"),
    ]
    assert len(fake.metric_order) == 7
    assert len(fake.claim_order) == 1
    assert outcome.completed_runs == 2
    runs = (Path(outcome.output_dir) / "runs.jsonl").read_text(encoding="utf-8")
    assert runs.count("\n") == 2
    assert '"status":"error"' in runs
    assert "fake failure" not in runs


@pytest.mark.asyncio
async def test_safe_resume_skips_terminal_failure_and_paid_success(tmp_path):
    fake = FakeExecutors(fail_research_at=2)
    output = tmp_path / "calibration"
    first = await run_calibration(
        project_root=ROOT,
        output_dir=output,
        dataset_version="v1",
        variant_ids=["baseline", "citation_validator"],
        requested_max_tokens=3_000_000,
        research_executor=fake.research,
        metric_factory=fake.metrics,
        claim_scorer_factory=fake.claim_scorer,
        provenance="fake",
        require_deepeval=False,
        environment=ENV,
    )
    assert first.status == "stopped"
    resumed = await run_calibration(
        project_root=ROOT,
        output_dir=output,
        dataset_version="v1",
        variant_ids=["baseline", "citation_validator"],
        requested_max_tokens=3_000_000,
        resume=True,
        research_executor=fake.research,
        metric_factory=fake.metrics,
        claim_scorer_factory=fake.claim_scorer,
        provenance="fake",
        require_deepeval=False,
        environment=ENV,
    )
    assert resumed.status == "completed"
    assert resumed.completed_runs == 6
    assert fake.research_order.count(("simple-001", "baseline")) == 1
    assert fake.research_order.count(("simple-001", "citation_validator")) == 1


@pytest.mark.asyncio
async def test_wrong_variant_scope_fails_before_executor_or_output(tmp_path):
    fake = FakeExecutors()
    output = tmp_path / "calibration"
    with pytest.raises(Exception, match="exactly"):
        await run_calibration(
            project_root=ROOT,
            output_dir=output,
            dataset_version="v1",
            variant_ids=["baseline"],
            requested_max_tokens=3_000_000,
            research_executor=fake.research,
            metric_factory=fake.metrics,
            claim_scorer_factory=fake.claim_scorer,
            provenance="fake",
            require_deepeval=False,
            environment=ENV,
        )
    assert fake.research_order == []
    assert not output.exists()


@pytest.mark.asyncio
async def test_calibration_refuses_any_unapproved_token_ceiling(tmp_path):
    fake = FakeExecutors()
    with pytest.raises(CalibrationRunnerError, match="3000000"):
        await run_calibration(
            project_root=ROOT,
            output_dir=tmp_path / "calibration",
            dataset_version="v1",
            variant_ids=["baseline", "citation_validator"],
            requested_max_tokens=2_999_999,
            research_executor=fake.research,
            metric_factory=fake.metrics,
            claim_scorer_factory=fake.claim_scorer,
            provenance="fake",
            require_deepeval=False,
            environment=ENV,
        )


@pytest.mark.asyncio
async def test_injected_calibration_cannot_claim_live_provenance(tmp_path):
    fake = FakeExecutors()
    output = tmp_path / "calibration"

    with pytest.raises(CalibrationRunnerError, match="fake provenance"):
        await run_calibration(
            project_root=ROOT,
            output_dir=output,
            dataset_version="v1",
            variant_ids=["baseline", "citation_validator"],
            requested_max_tokens=3_000_000,
            research_executor=fake.research,
            metric_factory=fake.metrics,
            claim_scorer_factory=fake.claim_scorer,
            require_deepeval=False,
            environment=ENV,
        )

    assert not output.exists()
