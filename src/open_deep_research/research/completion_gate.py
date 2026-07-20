"""Programmatic research completion gate driven by requirement coverage."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from open_deep_research.knowledge.ids import stable_id
from open_deep_research.knowledge.models import DomainModel
from open_deep_research.research.coverage import CoverageReport, CoverageStatus
from open_deep_research.research.requirements import RequirementSet


class CompletionDecision(StrEnum):
    """Supervisor-visible terminal or continuation outcomes."""

    CONTINUE = "continue"
    COMPLETE = "complete"
    COMPLETE_WITH_GAPS = "complete_with_gaps"
    BLOCKED = "blocked"


class ResearchBudgetSnapshot(DomainModel):
    """Immutable remaining programmatic budget at a completion attempt."""

    remaining_units: int = Field(ge=0)
    consumed_units: int = Field(default=0, ge=0)

    @property
    def exhausted(self) -> bool:
        """Return whether no further governed research may be scheduled."""
        return self.remaining_units == 0


class ResearchCompletionDecision(DomainModel):
    """Auditable output of the hard completion gate."""

    audit_id: str
    plan_id: str
    decision: CompletionDecision
    covered_requirement_ids: tuple[str, ...]
    missing_requirement_ids: tuple[str, ...]
    remaining_budget: int = Field(ge=0)
    explicit_gaps: tuple[str, ...] = ()
    reasons: tuple[str, ...]

    @property
    def can_complete(self) -> bool:
        """Return whether the Supervisor may take a terminal route."""
        return self.decision is not CompletionDecision.CONTINUE


class ResearchCompletionGate:
    """Refuse premature completion while required gaps remain researchable."""

    def __init__(self, *, policy_version: str = "completion-policy-v1") -> None:
        self._policy_version = policy_version.strip()
        if not self._policy_version:
            raise ValueError("policy_version cannot be blank")

    def evaluate(
        self,
        *,
        requirement_set: RequirementSet,
        coverage: CoverageReport,
        budget: ResearchBudgetSnapshot,
        blocked: bool = False,
        blocked_reasons: tuple[str, ...] = (),
    ) -> ResearchCompletionDecision:
        """Return a deterministic decision from coverage, budget, and blockers."""
        if (
            coverage.plan_id != requirement_set.plan_id
            or coverage.scope_id != requirement_set.scope_id
            or coverage.run_id != requirement_set.run_id
        ):
            raise ValueError("coverage does not belong to the requirement set")
        expected_ids = requirement_set.requirement_ids
        actual_ids = tuple(item.requirement_id for item in coverage.assessments)
        if actual_ids != expected_ids:
            raise ValueError("coverage must contain every requirement in plan order")

        covered = coverage.covered_requirement_ids
        missing = coverage.required_gap_ids
        explicit_gaps = tuple(
            dict.fromkeys(
                aspect
                for item in coverage.assessments
                if item.required and item.status is not CoverageStatus.COVERED
                for aspect in (
                    item.missing_aspects
                    or (f"requirement:{item.requirement_id}",)
                )
            )
        )
        if not missing:
            decision = CompletionDecision.COMPLETE
            reasons = ("all_required_requirements_covered",)
        elif blocked:
            decision = CompletionDecision.BLOCKED
            reasons = blocked_reasons or ("research_blocked_with_required_gaps",)
        elif budget.exhausted:
            decision = CompletionDecision.COMPLETE_WITH_GAPS
            reasons = ("research_budget_exhausted_with_required_gaps",)
        else:
            decision = CompletionDecision.CONTINUE
            reasons = ("required_gaps_remain_with_available_budget",)

        audit_id = stable_id(
            "completion_decision",
            self._policy_version,
            requirement_set.plan_id,
            decision.value,
            covered,
            missing,
            budget.remaining_units,
            budget.consumed_units,
            explicit_gaps,
            reasons,
        )
        return ResearchCompletionDecision(
            audit_id=audit_id,
            plan_id=requirement_set.plan_id,
            decision=decision,
            covered_requirement_ids=covered,
            missing_requirement_ids=missing,
            remaining_budget=budget.remaining_units,
            explicit_gaps=explicit_gaps,
            reasons=tuple(reasons),
        )
