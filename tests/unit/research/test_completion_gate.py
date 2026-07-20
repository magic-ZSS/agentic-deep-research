from __future__ import annotations

import asyncio

import pytest

from open_deep_research.research.completion_gate import (
    CompletionDecision,
    ResearchBudgetSnapshot,
    ResearchCompletionGate,
)
from open_deep_research.research.coverage import (
    CoveragePolicy,
    CoverageReport,
    CoverageStatus,
    RequirementCoverage,
)
from open_deep_research.research.requirements import (
    RequirementDraft,
    RequirementMaterializer,
)


class Extractor:
    def extract(self, **_kwargs):
        return (
            RequirementDraft(text="Required evidence", required=True),
            RequirementDraft(text="Optional context", required=False),
        )


def requirement_set():
    return asyncio.run(
        RequirementMaterializer(
            extractor=Extractor(), extractor_version="fake-v1"
        ).materialize(
            research_brief="Research this subject",
            scope_id="scope-test",
            run_id="run-test",
        )
    )


def report(plan, required_status, optional_status=CoverageStatus.MISSING):
    items = []
    for requirement, status in zip(
        plan.requirements, (required_status, optional_status), strict=True
    ):
        items.append(
            RequirementCoverage(
                requirement_id=requirement.requirement_id,
                required=requirement.required,
                status=status,
                coverage_score=1 if status is CoverageStatus.COVERED else 0,
                missing_aspects=(
                    () if status is CoverageStatus.COVERED else (requirement.text,)
                ),
                reasons=("fixture",),
                policy_version="coverage-policy-v1",
            )
        )
    return CoverageReport(
        plan_id=plan.plan_id,
        scope_id=plan.scope_id,
        run_id=plan.run_id,
        policy_version="coverage-policy-v1",
        assessments=tuple(items),
    )


def test_gate_completes_when_required_requirements_are_covered():
    plan = requirement_set()
    decision = ResearchCompletionGate().evaluate(
        requirement_set=plan,
        coverage=report(plan, CoverageStatus.COVERED),
        budget=ResearchBudgetSnapshot(remaining_units=4),
    )

    assert decision.decision is CompletionDecision.COMPLETE
    assert decision.can_complete
    assert not decision.missing_requirement_ids


def test_gate_refuses_early_completion_while_budget_remains():
    plan = requirement_set()
    decision = ResearchCompletionGate().evaluate(
        requirement_set=plan,
        coverage=report(plan, CoverageStatus.PARTIAL),
        budget=ResearchBudgetSnapshot(remaining_units=1, consumed_units=2),
    )

    assert decision.decision is CompletionDecision.CONTINUE
    assert not decision.can_complete
    assert decision.missing_requirement_ids == (plan.requirements[0].requirement_id,)
    assert decision.explicit_gaps == ("Required evidence",)


def test_gate_allows_explicit_gap_completion_when_budget_is_exhausted():
    plan = requirement_set()
    decision = ResearchCompletionGate().evaluate(
        requirement_set=plan,
        coverage=report(plan, CoverageStatus.MISSING),
        budget=ResearchBudgetSnapshot(remaining_units=0, consumed_units=3),
    )

    assert decision.decision is CompletionDecision.COMPLETE_WITH_GAPS
    assert decision.can_complete
    assert decision.explicit_gaps == ("Required evidence",)


def test_gate_reports_blocked_terminal_state_with_explicit_gaps():
    plan = requirement_set()
    decision = ResearchCompletionGate().evaluate(
        requirement_set=plan,
        coverage=report(plan, CoverageStatus.CONTRADICTED),
        budget=ResearchBudgetSnapshot(remaining_units=3),
        blocked=True,
        blocked_reasons=("conflict_requires_human_review",),
    )

    assert decision.decision is CompletionDecision.BLOCKED
    assert decision.can_complete
    assert decision.reasons == ("conflict_requires_human_review",)


def test_gate_is_deterministic_and_rejects_another_plan_report():
    plan = requirement_set()
    coverage = report(plan, CoverageStatus.PARTIAL)
    gate = ResearchCompletionGate()
    budget = ResearchBudgetSnapshot(remaining_units=2)
    first = gate.evaluate(
        requirement_set=plan, coverage=coverage, budget=budget
    )
    second = gate.evaluate(
        requirement_set=plan, coverage=coverage, budget=budget
    )
    assert first.audit_id == second.audit_id

    wrong = coverage.model_copy(update={"plan_id": "other-plan"})
    with pytest.raises(ValueError, match="does not belong"):
        gate.evaluate(requirement_set=plan, coverage=wrong, budget=budget)
